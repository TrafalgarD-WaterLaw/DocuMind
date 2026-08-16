"""混合检索轨迹测试——各路径命中数与耗时记录"""
import pytest

from core.tracing import RetrievalTrace
from retrieval.bm25 import BM25Index
from retrieval.hybrid import HybridRetriever


class FakeStore:
    """最小文档库：semantic(树)/question/bm25/实体 各路径所需方法"""

    def __init__(self, docs, questions=None):
        self._docs = list(docs)
        self._questions = list(questions or [])

    def retrieve(self, query, *, top_k=5, where=None):
        # where 过滤（Chroma 语义子集）：字段精确匹配 / $in 列表 / $and 组合
        docs = [
            d for d in self._docs
            if FakeStore._where_match(d["metadata"], where)
        ]
        return [
            {"id": d["id"], "content": d["content"],
             "source": d["metadata"].get("source", ""), "score": 0.5,
             "metadata": d["metadata"]}
            for d in docs[:top_k]
        ]

    @staticmethod
    def _where_match(meta, where):
        """简化的 where 匹配：{"field": value}、{"field": {"$in": [...]}}、$and 组合"""
        if not where:
            return True
        if "$and" in where:
            return all(
                FakeStore._where_match(meta, w) for w in where["$and"]
            )
        for key, cond in where.items():
            if key == "$and":
                continue
            if isinstance(cond, dict) and "$in" in cond:
                if meta.get(key) not in cond["$in"]:
                    return False
            elif meta.get(key) != cond:
                return False
        return True

    def get_by_ids(self, ids):
        by_id = {d["id"]: d for d in self._docs}
        return [by_id[i] for i in ids if i in by_id]

    def get_by_source_like(self, keyword, limit=50):
        return []

    def get_all_documents(self):
        return [dict(d) for d in self._docs]

    def list_sources(self):
        return [d["metadata"].get("source", "") for d in self._docs]

    def count(self):
        return len(self._docs)


async def test_retrieve_records_trace_paths(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "entity_anchor_enabled", False)
    docs = [
        {"id": "c1", "content": "叩鼎是商代青铜器。", "metadata": {"source": "s1", "chunk_type": "text"}},
        {"id": "c2", "content": "宣德青花瓷釉层。", "metadata": {"source": "s2"}},
    ]
    doc_store = FakeStore(docs)
    bm25 = BM25Index()
    bm25.build(doc_store.get_all_documents())

    retriever = HybridRetriever(
        doc_store=doc_store,
        question_store=FakeStore([], questions=[]),
        bm25=bm25,
        graph=None,          # 图谱路跳过
        llm=None,            # 实体提取跳过
        top_k=4,
    )
    trace = RetrievalTrace(trace_id="t1", query="叩鼎")
    results = await retriever.retrieve("叩鼎", trace=trace)
    assert results  # 至少 semantic 路命中
    assert "semantic" in trace.paths
    assert "bm25" in trace.paths
    assert trace.paths["semantic"].hits >= 1
    assert trace.paths["semantic"].took_ms >= 0


async def test_retrieve_without_trace_backward_compat(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "entity_anchor_enabled", False)
    doc_store = FakeStore([
        {"id": "c1", "content": "叩鼎", "metadata": {"source": "s1", "chunk_type": "text"}},
    ])
    bm25 = BM25Index()
    bm25.build(doc_store.get_all_documents())
    retriever = HybridRetriever(
        doc_store=doc_store, question_store=FakeStore([]),
        bm25=bm25, graph=None, llm=None, top_k=4,
    )
    results = await retriever.retrieve("叩鼎")
    assert isinstance(results, list)


async def test_dirty_rebuild_on_retrieve(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "entity_anchor_enabled", False)
    doc_store = FakeStore([
        {"id": "c1", "content": "新文档内容", "metadata": {"source": "new.pdf", "chunk_type": "text"}},
    ])
    bm25 = BM25Index()
    bm25.build([])  # 空索引 + 脏标记 → retrieve 时自动重建
    bm25.mark_dirty()
    retriever = HybridRetriever(
        doc_store=doc_store, question_store=FakeStore([]),
        bm25=bm25, graph=None, llm=None, top_k=4,
    )
    results = await retriever.retrieve("新文档")
    assert not bm25.dirty
    assert any(r["source"] == "new.pdf" for r in results)


async def test_semantic_path_includes_image_chunks(monkeypatch):
    """图片块走直检通道补回,树剪枝只搜文本块（回归测试）

    树剪枝显式限定 chunk_type=text——补 VLM 描述后图片块语义相似度最高,
    参与树剪枝会占满粗搜名额→分支信号丢失(图片块无 kiln/artifact)→
    剪枝失效+扁平回退拉回图片块,文本块被挤出(实测 Recall 100%→96%)。
    _path_semantic 的图片块直检通道(where={"chunk_type": "image"})负责
    补回图片块,树剪枝结果与直检结果合并。
    """
    from core.config import settings

    monkeypatch.setattr(settings, "entity_anchor_enabled", False)
    # 文本块：带 kiln/artifact（树剪枝枝干来源），8 条保证细搜命中 ≥ top_k
    text_docs = [
        {"id": f"t{i}", "content": f"商代青铜鼎铸造工艺要点{i}",
         "metadata": {"source": "s1", "kiln": "商代窑口", "artifact": "青铜鼎", "chunk_type": "text"}}
        for i in range(8)
    ]
    # 图片块：chunk_type=image，无 kiln/artifact（细搜必然排除）
    image_docs = [
        {"id": f"img{i}", "content": f"【文档图片·第{i}页】器物图注示意图{i}",
         "metadata": {"source": "s2", "chunk_type": "image"}}
        for i in range(3)
    ]
    doc_store = FakeStore(text_docs + image_docs)
    bm25 = BM25Index()
    bm25.build(doc_store.get_all_documents())

    retriever = HybridRetriever(
        doc_store=doc_store,
        question_store=FakeStore([]),
        bm25=bm25,
        graph=None,          # 图谱路跳过
        llm=None,            # 实体提取跳过
        top_k=4,
    )
    trace = RetrievalTrace(trace_id="t-img", query="甲骨文的龙字写法")
    results = await retriever.retrieve("甲骨文的龙字写法", trace=trace)
    # 语义路 = 树剪枝文本块(8) + 图片块直检 3 条(≥ top_k 即图片块生效)
    assert trace.paths["semantic"].hits >= settings.hybrid_path_k + 3
    # 最终结果中至少有一条经语义路召回的图片块
    assert any(
        r["metadata"].get("chunk_type") == "image" and "semantic" in r.get("paths", [])
        for r in results
    )
