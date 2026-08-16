"""Graph Provider — Neo4j 知识图谱

读写双通道:
  - 读路径: 手写 Cypher（模板化 T1-T6 查询、知识面板）——neo4j 驱动直连
  - 写路径: neomodel ODM（upsert——类型安全的对象 API,替代手写 MERGE）
ODM 连接与驱动同凭证;neomodel 惰性连接,不阻塞启动。
"""
import logging
from typing import Any

from neo4j import GraphDatabase

from interfaces.graph_store import GraphStore

logger = logging.getLogger(__name__)


class Neo4jStore(GraphStore):
    """Neo4j 知识图谱存储

    快速失败策略：首次连接失败不立即放弃（容忍瞬时抖动），
    但连续失败 2 次后标记不可用，后续调用直接快速失败，
    避免每次查询都触发驱动指数退避重试（最长可达 30s）。
    """

    def __init__(self, uri: str, user: str, password: str):
        self.driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_timeout=3.0,
            max_transaction_retry_time=5.0,
        )
        self._available = True
        self._fail_count = 0
        self._max_failures = 2
        # neomodel ODM 连接（与驱动同凭证;惰性连接,首次查询才握手）
        import neomodel

        neomodel.config.DATABASE_URL = uri.replace("bolt://", f"bolt://{user}:{password}@")
        neomodel.config.AUTO_INSTALL_LABELS = False

    def _read(self, query_func, *args):
        if not self._available:
            raise ConnectionError("Neo4j 不可用（先前连接失败，已快速失败）")
        try:
            with self.driver.session() as session:
                return session.execute_read(query_func, *args)
        except Exception as e:
            self._fail_count += 1
            if self._fail_count >= self._max_failures:
                self._available = False
                logger.warning(
                    f"Neo4j 连续 {self._fail_count} 次失败，标记不可用: {e}"
                )
            raise

    # ── GraphStore interface ───────────────────────

    def count_nodes(self, label: str = "Artifact") -> int:
        """统计指定标签的节点数量"""
        def _q(tx):
            result = tx.run(f"MATCH (n:`{label}`) RETURN count(n) AS c")
            return result.single()["c"]

        return int(self._read(_q))

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """执行只读查询，返回记录列表（供模板化图查询使用）"""
        def _q(tx):
            result = tx.run(cypher, **(params or {}))
            return [dict(r) for r in result]

        return self._read(_q)

    def query_nodes(self, label: str = "Artifact", limit: int = 25) -> list[dict[str, Any]]:
        def _q(tx):
            result = tx.run(f"MATCH (node:{label}) RETURN node LIMIT {limit}")
            return [record["node"] for record in result]

        nodes = []
        for node in self._read(_q):
            props = dict(node)
            if props.get("name"):
                nodes.append(self._pack_node(node, props))
        return nodes

    def expand_node(
        self, node_name: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        def _q(tx, name):
            result = tx.run(
                "MATCH (parent {name: $parent_name})-[r]->(child) "
                "RETURN parent, r, child, type(r) AS relType",
                parent_name=name,
            )
            return [
                {"node": record["parent"], "rel": record["r"],
                 "target": record["child"], "relType": record["relType"]}
                for record in result
            ]

        records = self._read(_q, node_name)
        nodes, links = [], []
        seen_names: set[str] = set()
        seen_rels: set[tuple] = set()

        for rec in records:
            child = rec.get("target")
            if not child:
                continue
            props = dict(child)
            name = props.get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            nodes.append(self._pack_node(child, props))

            rel_key = (node_name, name)
            if rel_key not in seen_rels:
                seen_rels.add(rel_key)
                links.append({
                    "source": node_name,
                    "target": name,
                    "name": rec.get("relType", "关联"),
                })

        return nodes, links

    def search_path(
        self, node_name: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        def _q(tx, q):
            result = tx.run(
                'MATCH (node {name:$q}) '
                "OPTIONAL MATCH (node)-[r]-(target) "
                "WITH DISTINCT node, r, target, type(r) AS relType "
                "RETURN node, r, target, relType",
                q=q,
            )
            return [
                {"node": record["node"], "rel": record["r"],
                 "target": record["target"], "relType": record["relType"]}
                for record in result
            ]

        records = self._read(_q, node_name)
        nodes, links = [], []
        seen_names: set[str] = set()
        seen_rels: set[tuple] = set()

        for rec in records:
            node = rec.get("node")
            if node:
                props = dict(node)
                name = props.get("name")
                if name and name not in seen_names:
                    seen_names.add(name)
                    nodes.append(self._pack_node(node, props))

            target = rec.get("target")
            if node and target:
                sn = dict(node).get("name")
                tn = dict(target).get("name")
                if sn and tn and (sn, tn) not in seen_rels:
                    seen_rels.add((sn, tn))
                    links.append({
                        "source": sn, "target": tn,
                        "name": rec.get("relType", "关联"),
                    })

        return nodes, links

    @staticmethod
    def _pack_node(node, props: dict) -> dict[str, Any]:
        """Neo4j 节点 → 展示字段 dict（query_nodes/expand/search_path 三处共用）"""
        return {
            "name": props["name"],
            "category": ", ".join(node.labels) or "Unknown",
            "image": props.get("image", ""),
            "introduce": props.get("introduce", ""),
            "time": props.get("time", ""),
            "when": props.get("when", ""),
            "where": props.get("where", ""),
        }

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        """批量插入或更新节点（neomodel ODM——get_or_create 幂等 MERGE 语义）

        nodes: [{"name": str, "label": str, "props": dict}]
        label 必须来自模型白名单（防止注入）。
        """
        from graph.infrastructure.graph_models import Artifact, Era, Kiln, Site

        allowed = {"Artifact": Artifact, "Site": Site, "Era": Era, "Kiln": Kiln}
        # 模型声明过的可写属性（未声明键忽略——与旧 MERGE SET 的差异收敛）
        FIELDS = {"name", "introduce", "image", "time", "when", "where"}
        for node in nodes:
            label = node.get("label", "Artifact")
            cls = allowed.get(label)
            if cls is None:
                raise ValueError(f"非法节点标签: {label}")
            name = node.get("name")
            if not name:
                continue
            props = node.get("props", {})
            # neomodel 6.x: get_or_create 返回 list（新 API），取首元素
            inst = cls.get_or_create({"name": name})[0]
            changed = False
            for k, v in props.items():
                if k in FIELDS and getattr(inst, k, None) != v:
                    setattr(inst, k, v)
                    changed = True
            if changed:
                inst.save()
        logger.info(f"Neo4jStore: upserted {len(nodes)} nodes")

    def upsert_relationships(self, relationships: list[dict[str, str]]) -> None:
        """批量插入或更新关系（neomodel ODM——connect 前检查幂等）

        relationships: [{"source": str, "target": str, "type": str}]
        关系类型决定源/目标模型对（Artifact → Era/Site/Kiln）。
        """
        from graph.infrastructure.graph_models import Artifact, Era, Kiln, Site

        allowed = {
            "BELONGS_TO": (Artifact, Era, "era"),
            "EXCAVATED_AT": (Artifact, Site, "site"),
            "BELONGS_TO_KILN": (Artifact, Kiln, "kiln"),
        }
        for rel in relationships:
            rel_type = rel.get("type", "BELONGS_TO")
            mapping = allowed.get(rel_type)
            if mapping is None:
                raise ValueError(f"非法关系类型: {rel_type}")
            src_cls, tgt_cls, rel_attr = mapping
            source = rel.get("source")
            target = rel.get("target")
            if not source or not target:
                continue
            src = src_cls.nodes.get_or_none(name=source)
            tgt = tgt_cls.nodes.get_or_none(name=target)
            if src is not None and tgt is not None:
                rel_mgr = getattr(src, rel_attr)
                if tgt not in rel_mgr:   # 幂等:已存在关系不重复创建
                    rel_mgr.connect(tgt)
        logger.info(f"Neo4jStore: upserted {len(relationships)} relationships")

    def close(self) -> None:
        self.driver.close()
