# -*- coding: utf-8 -*-
"""图谱意图路由规则——结构化信号与特征拦截词表（纯领域规则,无 IO）

规则路由先行（朝代/窑口名 + 关系词），LLM 兜底——词表本身即领域知识。
"""
from __future__ import annotations

import logging

from interfaces.graph_store import GraphStore

logger = logging.getLogger(__name__)

# 结构化信号：关系词（命中任一 → 结构化路由）
# 注:不添加"朝代/年代"——eval 核心集这些用例期望文本检索结果
# （文本块含朝代信息且 bm25/semantic 能召回），图谱路由短路会破坏
# 检索契约（实测 Recall 100%→89%）；图谱类问题由本词表 + 图谱节点名命中覆盖
REL_WORDS = ["出土", "属于", "现藏", "分布", "哪些", "多少", "共几件", "存于", "藏于"]

# 特征描述词：命中任一 → 不路由结构化（"特点/纹饰/胎体"的答案在文本细节里，
# 图谱只有关系/列表——曾误把"洪武青花纹饰特点"路由成器物列表，答非所问）
FEATURE_WORDS = [
    "特点", "特征", "怎么样", "如何", "风格", "胎体", "釉层", "釉色",
    "纹饰", "器型", "工艺", "花纹", "图案", "处理", "鉴定", "怎样",
    "演变", "发展", "变化", "历史", "分析", "研究", "区别", "对比",
]


def is_feature_query(query: str) -> bool:
    """特征描述类（特点/纹饰/胎体…）→ 答案在文本细节,不路由结构化"""
    return any(w in query for w in FEATURE_WORDS)


def is_rel_query(query: str) -> bool:
    """关系词命中 → 结构化路由"""
    return any(w in query for w in REL_WORDS)


def is_structured_query(query: str, graph: GraphStore | None) -> bool:
    """规则路由：查询是否适合结构化图谱问答

    特征描述类先拦截；关系词命中即结构化；否则图谱 Era/Kiln 节点名
    命中亦结构化。图谱不可用/查询失败时不刷屏——失败缓存 60s 冷却
    在 di.py graph property 层已处理。
    """
    if is_feature_query(query):
        return False
    if is_rel_query(query):
        return True
    if graph is None:
        return False
    for label in ("Era", "Kiln"):
        try:
            nodes = graph.query_nodes(label, limit=100)
        except Exception as e:
            logger.warning(f"图谱节点拉取失败，结构化路由降级文本: {e}")
            return False
        for n in nodes:
            if n.get("name") and n["name"] in query:
                return True
    return False
