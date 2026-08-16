# -*- coding: utf-8 -*-
"""查询理解——改写 / 分解 / 查询计划组装（quick 与 vision 共用）

把"用户问题 → 检索与评估的完整决策"收口于此:
- rewrite_query:  代词消解/表述优化 → 独立检索词（多轮对话）
- decompose_query:复合问题拆子查询（可选增强,保守原则:拆不了就不拆）
- build_query_plan:组装 QueryPlan——要检索的词 + CRAG 评估基准

QuickAnswerService 只做编排,查询理解细节不依赖编排器状态
（只依赖 llm + history,无状态可独立测试）。

设计: docs/superpowers/specs/2026-08-08-query-decomposition-design.md
动机: "妇好鸮尊和司母戊鼎哪个更早"单查询只召回一个实体,
另一个实体无证据 → LLM 编造（faithfulness 违规）。
"""
from __future__ import annotations

import asyncio
import logging

from conversation.domain.query_plan import QueryPlan

from core.json_utils import extract_string_list
from interfaces.llm import LLMProvider
from prompts import render_system, render_user
from core.llm_retry import llm_call_with_retry

logger = logging.getLogger(__name__)

MAX_SUB_QUERIES = 3
MIN_QUERY_LEN = 4


async def rewrite_query(
    query: str, history: list[dict] | None, llm: LLMProvider,
) -> str:
    """多轮查询改写——LLM 将代词消解、上下文补全为独立检索词

    替代粗暴拼接("上轮问题+当前问题"):多轮问"它属于什么朝代"时,
    只拼上一轮问题会让"它"淹没在长文本里;改写后直接是"妇好鸮尊属于什么朝代"。
    LLM 失败时回退到拼接(保持可用)。
    """
    if not history:
        return query
    # 注意:前端传入的 history 包含当前问题本身,需排除 content == query 的条目
    prev_user = next(
        (
            m["content"] for m in reversed(history)
            if m.get("role") == "user" and m.get("content") != query
        ),
        "",
    )
    if not prev_user:
        return query
    try:
        messages = llm.build_messages(
            render_system("query_rewrite"),
            render_user(
                "query_rewrite", history=prev_user[:200], query=query
            ),
        )
        raw = await llm_call_with_retry(
            messages, llm, temperature=0.0,
        )
        rewritten = raw.strip().strip('"').strip("“”")
        if rewritten and len(rewritten) <= 100:
            return rewritten
    except Exception as e:
        logger.warning(f"查询改写失败，回退拼接: {e}")
    return f"{prev_user[:50]} {query}".strip()


async def decompose_query(query: str, llm: LLMProvider) -> list[str] | None:
    """判定并拆解复合问题（保守原则:拆不了就返回 None,零成本零变化）

    幂等调用（temperature=0.0）统一走 llm_call_with_retry（tenacity:
    可重试异常/空响应抖动退避,4xx 立即上抛）;重试耗尽或解析失败仍
    回退原链路。

    Returns:
        子查询列表（复合问题）；None（非复合/解析失败/LLM 异常——走原链路）
    """
    if not query or not llm:
        return None
    messages = llm.build_messages(
        render_system("query_decomposition"),
        render_user("query_decomposition", query=query),
    )
    try:
        # 统一重试:异常/空响应（含空 JSON 字面量）抖动退避;解析失败(None)
        # 是合法"非复合"信号——不属于失败,不重试
        raw = await llm_call_with_retry(
            messages, llm, temperature=0.0,
            response_format={"type": "json_object"},  # DeepSeek JSON Output
        )
    except Exception as e:
        logger.warning(f"查询分解 LLM 失败，回退原链路: {e}")
        return None
    # 结构解析（extract_json + 类型校验 + 清理）在 json_utils;
    # 此处只剩子查询业务规则:全空/超上限 → 宁可不拆
    try:
        cleaned = extract_string_list(
            raw.strip(), "sub_queries", min_len=MIN_QUERY_LEN,
        )
    except Exception as e:
        logger.warning(f"查询分解解析失败，回退原链路: {e}")
        return None
    if not cleaned or len(cleaned) > MAX_SUB_QUERIES:
        return None  # 全空 / 超过上限 → 保守回退（不拆）
    return cleaned


async def build_query_plan(
    query: str, history: list[dict] | None, llm: LLMProvider,
) -> QueryPlan:
    """查询准备:拆解(复合问题)或改写(普通问题),统一返回查询计划

    复合 → N 个子查询改写词(并行);普通 → 1 个改写词。
    eval_base:CRAG 评估/二次改写基准(拆解用原始问题,普通用改写词);
    诊断面板改写词 = retrieval_queries[0](拆解时无单一改写词 → None)。
    """
    sub_queries = await decompose_query(query, llm)
    if sub_queries:
        queries = await asyncio.gather(
            *(rewrite_query(s, history, llm) for s in sub_queries)
        )
        return QueryPlan(retrieval_queries=queries, eval_base=query)
    rewritten = await rewrite_query(query, history, llm)
    return QueryPlan(retrieval_queries=[rewritten], eval_base=rewritten)
