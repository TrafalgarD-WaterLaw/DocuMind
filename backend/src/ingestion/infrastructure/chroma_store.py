"""Vector Provider — ChromaDB（持久化 + 元数据过滤）"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from interfaces.vector_store import VectorStore

logger = logging.getLogger(__name__)


class ChromaStore(VectorStore):
    """ChromaDB 向量存储——嵌入模式，本地持久化

    相比 FAISS 的优势:
      - 自动持久化，重启不丢
      - 支持元数据过滤 (source, page, date...)
      - 增量添加/删除无需重建索引
    """

    def __init__(
        self,
        persist_dir: str | Path = "src/data/chroma",
        embedding_function=None,
        collection_name: str = "documents",
    ):
        self.persist_dir = str(persist_dir)
        self._ef = embedding_function
        self.collection_name = collection_name
        self._client = None
        self._collection = None

    @property
    def collection(self):
        if self._collection is None:
            self._init_collection()
        return self._collection

    def _init_collection(self):
        import chromadb

        Path(self.persist_dir).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=self.persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"ChromaStore ready ({self.collection_name}): "
            f"{self._collection.count()} chunks"
        )

    # ── VectorStore interface ──────────────────────

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        # HNSW 探索空间与 n_results 相关：n_results 小则 ef_search 窄，
        # 会漏掉真实近邻（实测 top-8 结果不是 top-30 的子集）。
        # 固定探索空间（至少 30 条）再截断，保证小 top_k 的召回质量。
        fetch_k = max(top_k, 30)
        query_kwargs: dict[str, Any] = {
            "query_texts": [query],
            "n_results": fetch_k,
        }
        if where:
            query_kwargs["where"] = where

        results = self.collection.query(**query_kwargs)
        docs = []
        ids_list = results.get("ids", [[]])[0]
        docs_list = results.get("documents", [[]])[0]
        metas_list = results.get("metadatas", [[]])[0]
        dists_list = results.get("distances", [[]])[0]

        for i in range(min(top_k, len(ids_list))):
            docs.append({
                "id": ids_list[i] if i < len(ids_list) else "",
                "content": docs_list[i] if i < len(docs_list) else "",
                "source": (metas_list[i] or {}).get("source", "") if i < len(metas_list) else "",
                "score": 1.0 - float(dists_list[i]) if i < len(dists_list) else 0.0,
                "metadata": metas_list[i] if i < len(metas_list) else {},
            })
        return docs

    def get_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        """按 id 批量取回文档（不含 embedding）"""
        if not ids:
            return []
        results = self.collection.get(ids=ids)
        return self._pack_docs(results)

    def get_by_source_like(self, keyword: str, limit: int = 50) -> list[dict[str, Any]]:
        """按 source 名子串匹配（文本实体锚定用，不依赖 embedding）

        短条目（一行索引）embedding 信息少，语义检索排不过长文档；
        器名精确匹配是强信号——先在 source 名集合做子串筛选，
        再按 $in 精确取回（Chroma where 不支持 $like/$contains 字符串匹配）。
        """
        if not keyword:
            return []
        sources = self.list_sources()
        matched = [s for s in sources if keyword in s or s in keyword]
        if not matched:
            return []
        results = self.collection.get(
            where={"source": {"$in": matched}}, limit=limit
        )
        return self._pack_docs(results)

    def get_all_documents(self) -> list[dict[str, Any]]:
        """导出全部文档（不含 embedding），用于构建 BM25 等辅助索引"""
        results = self.collection.get()
        return self._pack_docs(results)

    def get_by_where(self, where: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
        """按元数据条件取回文档（不依赖 embedding，支持数组 $contains）

        上传文档实体锚定——metadata.entities 数组匹配
        （Chroma where: {"entities": {"$contains": "妇好鸮尊"}}）
        """
        if not where:
            return []
        results = self.collection.get(where=where, limit=limit)
        return self._pack_docs(results)

    @staticmethod
    def _pack_docs(results: dict) -> list[dict[str, Any]]:
        """将 Chroma get 结果打包为统一 dict 列表"""
        docs = []
        ids_list = results.get("ids", [])
        docs_list = results.get("documents", [])
        metas_list = results.get("metadatas", [])
        for i in range(len(ids_list)):
            docs.append({
                "id": ids_list[i],
                "content": docs_list[i] if i < len(docs_list) else "",
                "metadata": metas_list[i] if i < len(metas_list) else {},
            })
        return docs

    def add_documents(self, documents: list[dict[str, Any]]) -> None:
        ids, texts, metas = [], [], []
        for doc in documents:
            ids.append(doc.get("chunk_id", str(uuid.uuid4())))
            texts.append(doc["content"])
            metas.append(doc.get("metadata", {}))

        if texts:
            self.collection.add(ids=ids, documents=texts, metadatas=metas)
            logger.info(f"ChromaStore: added {len(texts)} chunks")

    def delete(self, source: str) -> int:
        results = self.collection.get(where={"source": source})
        ids = results.get("ids", [])
        if ids:
            self.collection.delete(ids=ids)
            logger.info(f"ChromaStore: deleted {len(ids)} chunks (source={source})")
        return len(ids)

    def delete_by_ids(self, ids: list[str]) -> None:
        """按文档 id 批量删除（A1 接口补齐——问题索引清理等场景）"""
        if ids:
            self.collection.delete(ids=ids)

    def get_all_metadatas(self) -> list[dict[str, Any]]:
        """导出全部块 metadata（A1 接口补齐——批量统计/清理，
        替代调用方直接访问 collection.get）"""
        results = self.collection.get(include=["metadatas"])
        return results.get("metadatas") or []

    def list_sources(self) -> list[str]:
        results = self.collection.get()
        sources: set[str] = set()
        for meta in results.get("metadatas", []):
            src = (meta or {}).get("source", "")
            if src:
                sources.add(src)
        return sorted(sources)

    def count_by_source(self, source: str) -> int:
        if not source:
            return 0
        try:
            results = self.collection.get(where={"source": source})
            return len(results.get("ids", []))
        except Exception as e:
            logger.warning(f"count_by_source 失败 ({source}): {e}")
            return 0

    def count(self) -> int:
        return self.collection.count()

    def close(self) -> None:
        """释放 Chroma 连接（进程退出前调用；未初始化时为空操作）"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._collection = None
