"""图数据库接口"""
from abc import ABC, abstractmethod
from typing import Any


class GraphStore(ABC):
    """知识图谱存储抽象"""

    @abstractmethod
    def query_nodes(self, label: str, limit: int = 25) -> list[dict[str, Any]]:
        """查询指定标签的节点"""
        ...

    @abstractmethod
    def count_nodes(self, label: str) -> int:
        """统计指定标签的节点数量"""
        ...

    @abstractmethod
    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """执行只读查询（模板白名单内的 Cypher），返回记录列表"""
        ...

    @abstractmethod
    def expand_node(
        self, node_name: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """展开节点关联，返回 (nodes, links)"""
        ...

    @abstractmethod
    def search_path(
        self, node_name: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """搜索节点及路径，返回 (nodes, links)"""
        ...

    @abstractmethod
    def upsert_nodes(
        self, nodes: list[dict[str, Any]]
    ) -> None:
        """批量插入或更新节点"""
        ...

    @abstractmethod
    def upsert_relationships(
        self, relationships: list[dict[str, Any]]
    ) -> None:
        """批量插入或更新关系
        relationships: [{"source": str, "target": str, "type": str,
                         "props": dict[str, Any]}]
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """关闭连接"""
        ...
