"""ChromaStore.count_by_source 测试（真实 Chroma + 临时目录 + 固定向量）"""
import numpy as np

from ingestion.infrastructure.chroma_store import ChromaStore


class _FixedEF:
    """固定维度 embedding（不依赖模型），兼容 ChromaDB 1.5.x（需 name/is_legacy）"""

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [[0.1] * 64 for _ in input]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def name(self) -> str:
        return "fixed-64d"

    def is_legacy(self) -> bool:
        return True


_ef = _FixedEF()


def test_count_by_source(tmp_path):
    store = ChromaStore(persist_dir=str(tmp_path), embedding_function=_ef)
    store.add_documents([
        {"chunk_id": "1", "content": "a", "metadata": {"source": "s1"}},
        {"chunk_id": "2", "content": "b", "metadata": {"source": "s1"}},
        {"chunk_id": "3", "content": "c", "metadata": {"source": "s2"}},
    ])
    assert store.count_by_source("s1") == 2
    assert store.count_by_source("s2") == 1
    assert store.count_by_source("nope") == 0


def test_count_by_source_empty(tmp_path):
    store = ChromaStore(persist_dir=str(tmp_path), embedding_function=_ef)
    assert store.count_by_source("s1") == 0
