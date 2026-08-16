"""BM25 关键词索引——jieba 中文分词 + rank_bm25

与 VectorStore 返回格式兼容，供 HybridRetriever 作为关键词检索路使用。
"""
from __future__ import annotations

import logging
import math
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


def tokenize(text: str) -> list[str]:
    """中文分词：jieba 搜索模式（召回更全），小写归一"""
    return [w.strip().lower() for w in jieba.cut_for_search(text) if w.strip()]


class BM25Index:
    """BM25Okapi 关键词索引（数据量小，构建/重建全量索引即可）"""

    def __init__(self):
        self._corpus: list[dict[str, Any]] = []
        self._bm25: BM25Okapi | None = None
        # 脏标记：上传/删除文档后置位，检索前惰性重建
        # （重建为全量同步构建，毫秒级；幂等无副作用，不设锁）
        self.dirty = False

    @property
    def count(self) -> int:
        return len(self._corpus)

    def build(self, documents: list[dict[str, Any]]) -> None:
        """从文档列表构建索引

        Args:
            documents: [{"id": str, "content": str, "metadata": dict}]
        """
# 图片块不进 BM25——图片由语义直检通道补充（hybrid _path_semantic
        # 的 where chunk_type=image），BM25 命中图注块会造成路径语义混淆
        docs = [
            d for d in documents
            if (d.get("metadata") or {}).get("chunk_type") != "image"
        ]
        self._corpus = docs
        texts = [tokenize(d.get("content", "")) for d in self._corpus]
        self._bm25 = BM25Okapi(texts) if texts else None
        if self._bm25 is not None:
            self._fix_idf(texts)
        logger.info(f"BM25Index built: {len(self._corpus)} docs")
        self.dirty = False

    def add(self, documents: list[dict[str, Any]]) -> None:
        """增量添加文档（内部重建全量索引——数据量小，简单可靠）"""
        self.build(self._corpus + list(documents))

    def mark_dirty(self) -> None:
        """标记索引过期（上传/删除文档后调用）"""
        self.dirty = True

    def rebuild_if_dirty(self, doc_store) -> bool:
        """脏时从向量库全量重建；返回是否实际重建"""
        if not self.dirty:
            return False
        self.build(doc_store.get_all_documents())
        return True

    # ── 检索 ─────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """关键词检索，返回与 VectorStore.retrieve 相同结构"""
        if self._bm25 is None or not query.strip():
            return []

        scores = self._bm25.get_scores(tokenize(query))
        # 用 != 0 而非 > 0：单文档语料下命中的词 IDF 为负（df == doc_count），
        # > 0 会把所有命中文档丢弃；未命中查询分数恒为 0.0，仍会被排除
        indices = [i for i, s in enumerate(scores) if s != 0]
        if where:
            indices = [i for i in indices if self._match(self._corpus[i].get("metadata", {}), where)]

        indices.sort(key=lambda i: scores[i], reverse=True)

        results = []
        for i in indices[:top_k]:
            doc = self._corpus[i]
            metadata = doc.get("metadata", {})
            results.append({
                "id": doc.get("id", ""),
                "content": doc.get("content", ""),
                "source": metadata.get("source", ""),
                "score": float(scores[i]),
                "metadata": metadata,
            })
        return results

    # ── 工具 ─────────────────────────────────────────────

    def _fix_idf(self, texts: list[list[str]]) -> None:
        """修正 rank_bm25（ATIRE 变体）的 idf

        其公式 log(N-df+0.5) - log(df+0.5) + epsilon 下限在小语料下退化：
        df == N/2 时 idf 恰为 0，命中与未命中分数无法区分（如 2 篇文档各含
        不重叠词时全为 0）。这里改用经典公式 log((N-df+0.5)/(df+0.5)) + 1，
        保证命中词分数非零、可排序。
        """
        docs = [set(tokens) for tokens in texts]
        n = len(docs)
        for term in self._bm25.idf:
            df = sum(1 for d in docs if term in d)
            self._bm25.idf[term] = math.log((n - df + 0.5) / (df + 0.5)) + 1

    @staticmethod
    def _match(metadata: dict[str, Any], where: dict[str, Any]) -> bool:
        """简易元数据过滤：支持 {"field": value} 与 {"field": {"$in": [...]}}"""
        for field, cond in where.items():
            value = metadata.get(field)
            if isinstance(cond, dict) and "$in" in cond:
                if value not in cond["$in"]:
                    return False
            elif value != cond:
                return False
        return True
