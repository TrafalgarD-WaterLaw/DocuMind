"""系统统计 API——首页驾驶舱数据"""
import logging
from typing import Any

from fastapi import APIRouter

from core.di import container

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """系统规模统计：向量库、问题索引、知识图谱、文档数

    各组件不可用时降级为 0/None，不影响主流程。
    """
    stats: dict = {}

    # ── 向量库 ──
    try:
        stats["chunks"] = container.vector.count()
    except Exception as e:
        logger.warning(f"stats.vector 失败: {e}")
        stats["chunks"] = 0

    # ── 问题索引 ──
    try:
        stats["questions"] = container.questions.count()
    except Exception as e:
        logger.warning(f"stats.questions 失败: {e}")
        stats["questions"] = 0

    # ── 知识图谱（Neo4j 不可用降级为 None）──
    try:
        graph = container.graph
        if graph is not None:
            stats["graph"] = {
                "artifacts": graph.count_nodes("Artifact"),
                "sites": graph.count_nodes("Site"),
                "eras": graph.count_nodes("Era"),
                "kilns": graph.count_nodes("Kiln"),
            }
        else:
            stats["graph"] = None
    except Exception as e:
        logger.warning(f"stats.graph 失败: {e}")
        stats["graph"] = None

    # ── 文档源 ──
    try:
        sources = container.vector.list_sources()
        stats["documents"] = len(sources)
    except Exception as e:
        logger.warning(f"stats.documents 失败: {e}")
        stats["documents"] = 0

    return stats
