"""知识图谱 API——Neo4j 图谱初始化、展开、搜索"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException

from core.di import container
from interfaces.graph_store import GraphStore
from models.request import KnowledgeSearchRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _get_graph() -> GraphStore:
    """安全获取图存储实例"""
    g = container.graph
    if g is None:
        raise HTTPException(status_code=503, detail="知识图谱服务不可用")
    return g


def _merge_graph_data(
    new_nodes: list[dict], new_links: list[dict],
    existing_nodes: list[dict], existing_links: list[dict],
) -> tuple[list[dict], list[dict]]:
    """合并新数据到已有数据，去重"""
    names = {n["name"] for n in existing_nodes}
    rels = {(l["source"], l.get("target")) for l in existing_links}

    for node in new_nodes:
        if node.get("name") not in names:
            existing_nodes.append(node)
            names.add(node["name"])

    for link in new_links:
        key = (link["source"], link.get("target"))
        if key not in rels:
            existing_links.append(link)
            rels.add(key)

    return existing_nodes, existing_links


def _fetch_representatives(
    label: str, rel: str, limit: int, max_nodes: int | None = None,
) -> list[dict]:
    """每节点代表器物（neomodel ODM 对象 API,替代手写 Cypher）

    label 白名单 → 模型类（Era/Kiln/Site → artifacts 关系）;
    语义与原 Cypher 一致:仅有关系节点参与,按名称排序,每节点取前 limit 件,
    max_nodes 限总量（遗址等海量节点只取前 N 个）。
    同步调用,由调用方经 asyncio.to_thread 执行。
    """
    from graph.infrastructure.graph_models import Era, Kiln, Site

    cls = {"Era": Era, "Kiln": Kiln, "Site": Site}[label]
    rows: list[dict] = []
    for n in cls.nodes.order_by("name"):
        arts = getattr(n, "artifacts").all()[:limit]
        if arts:
            rows.append({"name": n.name, "artifacts": [a.name for a in arts]})
        if max_nodes and len(rows) >= max_nodes:
            break
    return rows


@router.get("/init")
async def init_graph() -> dict[str, list[dict]]:
    """初始化图谱——骨架网络：朝代/窑口/遗址 + 代表器物及其关系

    首屏展示"朝代 → 器物 → 遗址/窑口"的网络结构（而非孤立点）；
    器物海量时只取每个朝代/窑口/遗址的代表（3/3/1 件），点朝代再展开。
    """
    g = _get_graph()
    nodes: list[dict] = []
    links: list[dict] = []
    seen: set[str] = set()

    def add_node(name: str, category: str, **props) -> None:
        if name and name not in seen:
            seen.add(name)
            nodes.append({"name": name, "category": category, **props})

    def add_link(source: str, target: str, rel: str) -> None:
        links.append({"source": source, "target": target, "name": rel})

    try:
        # 朝代/窑口/遗址 → 代表器物（统一 _fetch_representatives——ODM 对象 API,
        # 经 to_thread 不阻塞事件循环;label 白名单写死）
        for label, rel, per, total in (
            ("Era", "BELONGS_TO", 3, None),       # 朝代 → 每朝代 3 件
            ("Kiln", "BELONGS_TO_KILN", 3, None), # 窑口 → 每窑口 3 件
            ("Site", "EXCAVATED_AT", 1, 15),      # 遗址 → 前 15 个,每遗址 1 件
        ):
            rows = await asyncio.to_thread(_fetch_representatives, label, rel, per, total)
            for r in rows:
                add_node(r["name"], label)
                for a in r["artifacts"]:
                    add_node(a, "Artifact")
                    add_link(a, r["name"], rel)

        # 补齐 Artifact 简介（信息面板展示）——ODM filter 批量取回
        from graph.infrastructure.graph_models import Artifact

        artifact_names = [n["name"] for n in nodes if n["category"] == "Artifact"]
        if artifact_names:
            arts = await asyncio.to_thread(
                Artifact.nodes.filter, name__in=artifact_names
            )
            intro_map = {a.name: a.introduce for a in arts}
            for n in nodes:
                if n["category"] == "Artifact":
                    n["introduce"] = intro_map.get(n["name"])

        return {"echarts_data": nodes, "nodes_relation": links}
    except Exception as e:
        logger.exception("知识图谱初始化异常")
        raise HTTPException(status_code=500, detail=f"图谱初始化失败: {str(e)}")


@router.post("/expand")
async def expand_node(req: KnowledgeSearchRequest) -> dict[str, list[dict]]:
    """展开指定节点，获取关联子节点和边"""
    existing_nodes = list(req.node_data)
    existing_links = list(req.link_data)

    try:
        # 同步 Neo4j 调用经 to_thread（expand_node 为同步方法）
        new_nodes, new_links = await asyncio.to_thread(
            _get_graph().expand_node, req.node_name
        )
        nodes, links = _merge_graph_data(new_nodes, new_links, existing_nodes, existing_links)
        return {"echarts_data": nodes, "nodes_relation": links}
    except Exception as e:
        logger.exception("知识图谱展开节点异常")
        raise HTTPException(status_code=500, detail=f"节点展开失败: {str(e)}")


@router.post("/search")
async def search_graph(req: KnowledgeSearchRequest) -> dict[str, list[dict]]:
    """搜索实体及其关联路径

    cypher_query 字段为历史命名——实际承载"节点名搜索词"（模型 description
    已注明;前端沿用该字段名,契约不变;node_name 为空时兼容使用）。
    """
    search_name = req.cypher_query or req.node_name
    try:
        nodes, links = await asyncio.to_thread(
            _get_graph().search_path, search_name
        )
        return {"echarts_data": nodes, "nodes_relation": links}
    except Exception as e:
        logger.exception("知识图谱搜索异常")
        raise HTTPException(status_code=500, detail=f"图谱搜索失败: {str(e)}")
