"""嵌入模型接口"""
from abc import ABC, abstractmethod


class Embedder(ABC):
    """文本向量化抽象"""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """单条文本向量化"""
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化"""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度"""
        ...
