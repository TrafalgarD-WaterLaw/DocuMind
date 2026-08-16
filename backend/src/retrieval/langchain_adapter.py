"""将 Embedder 接口适配为 LangChain Embeddings 兼容类"""
from langchain_core.embeddings import Embeddings

from interfaces.embedder import Embedder


class LangChainEmbeddingAdapter(Embeddings):
    """包装任意 Embedder 实例，使其兼容 LangChain 的 Embeddings 协议"""

    def __init__(self, embedder: Embedder):
        self._embedder = embedder

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.embed_batch(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embedder.embed(text)
