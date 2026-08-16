"""BM25 惰性重建测试——上传/删除后脏标记，下次检索前重建"""
from retrieval.bm25 import BM25Index


class FakeStore:
    def __init__(self, docs):
        self._docs = list(docs)

    def get_all_documents(self):
        return list(self._docs)


def _doc(did: str, text: str) -> dict:
    return {"id": did, "content": text, "metadata": {"source": f"s-{did}"}}


def test_build_and_retrieve():
    idx = BM25Index()
    idx.build([_doc("1", "商代青铜鼎 叩鼎")])
    assert idx.count == 1
    assert idx.retrieve("叩鼎", top_k=5)


def test_mark_dirty_then_rebuild():
    idx = BM25Index()
    idx.build([_doc("1", "商代青铜鼎")])
    assert not idx.dirty
    idx.mark_dirty()
    assert idx.dirty
    store = FakeStore([_doc("1", "商代青铜鼎"), _doc("2", "宣德青花釉里红")])
    rebuilt = idx.rebuild_if_dirty(store)
    assert rebuilt is True
    assert idx.dirty is False
    assert idx.count == 2
    # 新文档可被关键词召回
    assert any(r["id"] == "2" for r in idx.retrieve("宣德青花", top_k=5))


def test_rebuild_if_clean_does_nothing():
    idx = BM25Index()
    idx.build([_doc("1", "商代青铜鼎")])
    assert idx.rebuild_if_dirty(FakeStore([_doc("1", "商代青铜鼎")])) is False
    assert idx.count == 1
