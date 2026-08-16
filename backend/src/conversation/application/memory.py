# -*- coding: utf-8 -*-
"""多轮记忆——滚动摘要压缩

历史 > 6 轮时，旧轮次由 LLM 压缩为 ≤150 字摘要进上下文，
替代硬截断（最近 6 轮 × 200 字）——长对话早期实体/问题主线可回溯。

设计: docs/superpowers/specs/2026-08-08-conversation-memory-design.md
缓存: 旧轮次序列 hash → 摘要（LRU 100 条）——同一批旧轮次只调一次 LLM，
     后续请求零成本；后端重启即失（内存态，与 task_manager 同级）。

实例（容器装配 core.di.container.memory）；模块函数为无状态委托入口——
跨调用方共享同一实例,缓存共享语义不变。
"""
from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict

from prompts import render_system, render_user

logger = logging.getLogger(__name__)

RECENT_ROUNDS = 6    # 保留完整轮次数
MAX_SUMMARY_CHARS = 150  # 摘要长度上限
MAX_ROUND_CHARS = 200    # 单轮内容截断上限
CACHE_SIZE = 100


class ConversationMemory:
    """多轮记忆摘要器——LLM 摘要 + LRU 缓存（实例状态）"""

    def __init__(self, max_entries: int = CACHE_SIZE) -> None:
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._max_entries = max_entries

    async def build_context(
        self, query: str, history: list[dict] | None, llm,
    ) -> str:
        """完整上下文：对话摘要（>6 轮时）+ 最近 6 轮 + 当前问题

        ≤6 轮：零 LLM 调用，行为与旧硬截断完全一致。
        >6 轮：旧轮次 LLM 摘要（缓存复用）。
        """
        if not history:
            return query
        return await self._assemble(history, llm, f"\n\n当前问题：{query}")

    async def build_history_text(self, history: list[dict] | None, llm) -> str:
        """仅历史部分（摘要 + 最近 6 轮）——synthesizer 报告上下文用（不含当前问题行）"""
        if not history:
            return ""
        return await self._assemble(history, llm, "")

    async def _assemble(self, history: list[dict], llm, suffix: str) -> str:
        """历史部分组装（两处共用的公共逻辑）：摘要（>6 轮时）+ 最近 6 轮 + 后缀

        轮数按 user 消息计数（history 是消息平铺,user/assistant 交替）。
        摘要失败回退硬截断（保持可用）。
        """
        user_count = sum(1 for m in history if m.get("role") == "user")
        keep_msgs = RECENT_ROUNDS * 2  # 最近 6 轮 ≈ 6 条 user + 6 条 assistant
        recent_text = self._format_rounds(history[-keep_msgs:])
        body = f"历史对话：\n{recent_text}"
        if user_count > RECENT_ROUNDS:
            try:
                summary = await self._get_or_summarize(history[:-keep_msgs], llm)
                body = f"对话摘要：{summary}\n\n{body}"
            except Exception as e:
                logger.warning(f"对话摘要失败，回退硬截断: {e}")
        return f"{body}{suffix}"

    async def _get_or_summarize(self, old: list[dict], llm) -> str:
        """旧轮次 → 摘要（LRU 缓存，命中零 LLM 调用）"""
        key = self._history_key(old)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        conv = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '助手'}: "
            f"{(m.get('content') or '')[:300]}"
            for m in old
        )
        messages = llm.build_messages(
            render_system("conversation_summary"),
            render_user("conversation_summary", conversation=conv),
        )
        raw = await llm.chat(messages, temperature=0.0)
        summary = (raw or "").strip()[:MAX_SUMMARY_CHARS]
        if not summary:
            raise ValueError("对话摘要为空")
        self._cache[key] = summary
        self._cache.move_to_end(key)
        if len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)
        return summary

    @staticmethod
    def _history_key(history: list[dict]) -> str:
        """旧轮次序列 → 缓存 key（(role, content) 序列 hash）"""
        raw = "\x1f".join(
            f"{m.get('role', '')}\x1e{m.get('content', '')}" for m in history
        )
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _format_rounds(rounds: list[dict]) -> str:
        """轮次 → 「角色: 内容」行（各截断 MAX_ROUND_CHARS，空内容跳过）"""
        lines = []
        for m in rounds:
            role = "用户" if m.get("role") == "user" else "助手"
            content = (m.get("content") or "")[:MAX_ROUND_CHARS]
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)


def _default_memory() -> ConversationMemory:
    """组合根装配的默认记忆实例——缓存状态归容器,委托函数无全局状态"""
    from core.di import container

    return container.memory


async def build_context(query: str, history: list[dict] | None, llm) -> str:
    return await _default_memory().build_context(query, history, llm)


async def build_history_text(history: list[dict] | None, llm) -> str:
    return await _default_memory().build_history_text(history, llm)
