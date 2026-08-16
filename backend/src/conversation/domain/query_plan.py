# -*- coding: utf-8 -*-
"""查询计划值对象——查询准备的产物（检索与评估的完整决策）

"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QueryPlan:
    """查询准备产物——检索与评估的完整决策(拆解或改写后)

    retrieval_queries: 要检索的查询列表(普通 1 个,复合 N 个子查询改写词)
                       ——调用方逐个检索后合并;len > 1 即视为拆解
                       (前端事件选择 decompose / rewrite);
                       诊断面板改写词 = retrieval_queries[0](拆解时无单一
                       改写词 → None)
    eval_base:         检索质量评估/二次改写的基准词
                       (拆解用原始问题——合并证据无单一改写词;普通用改写词)
    """

    retrieval_queries: list[str]
    eval_base: str
