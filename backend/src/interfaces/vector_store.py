"""向量存储接口"""
from abc import ABC, abstractmethod
from typing import Any


class VectorStore(ABC):
    """向量检索引擎抽象 — BM25 + 语义混合检索"""

    @abstractmethod
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """检索相关文档块

        Args:
            query: 检索查询
            top_k: 返回数量
            where: 元数据过滤条件（如 {"kiln": {"$in": [...]}}），
                   不支持的实现可忽略该参数

        Returns:
            list[dict]: 每个包含 {"content": str, "source": str, "score": float, ...}
        """
        ...

    @abstractmethod
    def get_by_ids(
        self, ids: list[str]
    ) -> list[dict[str, Any]]:
        """按文档 id 批量取回文档块

        Returns:
            list[dict]: 每个包含 {"id": str, "content": str, "metadata": dict}
        """
        ...

    @abstractmethod
    def get_by_source_like(
        self, keyword: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按 source 名子串匹配取回文档块（文本实体锚定用，不依赖 embedding）

        Args:
            keyword: source 名关键词（双向子串匹配:keyword in s 或 s in keyword）
            limit: 返回数量上限

        Returns:
            list[dict]: 每个包含 {"id": str, "content": str, "metadata": dict}
        """
        ...

    @abstractmethod
    def get_by_where(
        self, where: dict[str, Any], limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按元数据条件取回文档块（不依赖 embedding）

        Args:
            where: 元数据过滤条件（如 {"source": {"$in": [...]}}）
            limit: 返回数量上限

        Returns:
            list[dict]: 每个包含 {"id": str, "content": str, "metadata": dict}
        """
        ...

    @abstractmethod
    def get_all_documents(self) -> list[dict[str, Any]]:
        """导出全部文档块（不含 embedding），用于构建 BM25 等辅助索引

        Returns:
            list[dict]: 每个包含 {"id": str, "content": str, "metadata": dict}
        """
        ...

    @abstractmethod
    def add_documents(
        self, documents: list[dict[str, Any]]
    ) -> None:
        """批量添加文档块到索引

        Args:
            documents: [{"content": str, "metadata": dict, ...}]
        """
        ...

    @abstractmethod
    def delete(self, source: str) -> int:
        """按来源标识删除文档块，返回删除数量"""
        ...

    @abstractmethod
    def delete_by_ids(self, ids: list[str]) -> None:
        """按文档 id 批量删除（问题索引清理等场景）"""
        ...

    @abstractmethod
    def get_all_metadatas(self) -> list[dict[str, Any]]:
        """导出全部块 metadata（批量统计/清理场景，
        替代调用方直接访问 collection.get）"""
        ...

    @abstractmethod
    def list_sources(self) -> list[str]:
        """列出所有已索引的文档来源"""
        ...

    @abstractmethod
    def count(self) -> int:
        """返回索引中总块数"""
        ...

    @abstractmethod
    def count_by_source(self, source: str) -> int:
        """按来源统计文档块数（各实现必须覆盖,未覆盖子类不得静默返回 0）"""
        ...
