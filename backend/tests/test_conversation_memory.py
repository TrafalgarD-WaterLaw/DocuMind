# -*- coding: utf-8 -*-
"""多轮记忆测试——滚动摘要压缩（蓝图第 4 步）"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402

from conversation.application.memory import ConversationMemory  # noqa: E402


@pytest.fixture
def memory():
    """阶段 2 注入化:每测试独立实例（缓存为实例状态,天然隔离）"""
    return ConversationMemory()


class _FakeLLM:
    """记录调用次数的假 LLM（可配置响应/失败）"""

    def __init__(self, response="用户持续关注妇好鸮尊的纹饰与铸造工艺。"):
        self.response = response
        self.calls = 0
        self.fail = False
        self.prompts = []

    def build_messages(self, system: str, user: str):
        self.prompts.append(user)
        return [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("LLM 挂了")
        return self.response


def _rounds(n: int) -> list[dict]:
    """构造 n 轮历史（用户问器物，助手答）"""
    history = []
    for i in range(n):
        history.append({"role": "user", "content": f"第{i + 1}问：这件文物的时代？"})
        history.append({"role": "assistant", "content": f"第{i + 1}答：商代晚期。"})
    return history


# ── 短对话（≤6 轮）：零 LLM 调用，行为与旧硬截断一致 ──────

async def test_short_history_no_llm_call(memory):
    llm = _FakeLLM()
    history = _rounds(3)  # 6 条消息 = 3 轮
    out = await memory.build_context("它属于什么朝代", history, llm)
    assert llm.calls == 0  # 不引入摘要 LLM 调用
    assert "历史对话：" in out
    assert "当前问题：它属于什么朝代" in out


async def test_no_history_returns_query(memory):
    llm = _FakeLLM()
    assert await memory.build_context("叩鼎是什么", None, llm) == "叩鼎是什么"
    assert llm.calls == 0


# ── 长对话（>6 轮）：摘要 + 最近 6 轮 ──────────────────────

async def test_long_history_summarizes(memory):
    llm = _FakeLLM()
    history = _rounds(7)  # 14 条消息 = 7 轮 > 6 轮触发摘要（L: 按轮数计）
    out = await memory.build_context("它属于什么朝代", history, llm)
    assert llm.calls == 1  # 摘要恰好一次
    assert "对话摘要：用户持续关注妇好鸮尊" in out
    assert "历史对话：" in out
    # 最近 6 条消息保留原文，最早的 2 条只进摘要
    assert "第7问" in out
    assert "第1问" not in out


async def test_summary_cached_same_prefix(memory):
    llm = _FakeLLM()
    history = _rounds(7)
    await memory.build_context("问题A", history, llm)
    await memory.build_context("问题B", history, llm)  # 相同旧轮次
    assert llm.calls == 1  # 缓存命中，不重复调 LLM


async def test_summary_fallback_on_llm_error(memory):
    llm = _FakeLLM()
    llm.fail = True
    history = _rounds(7)
    out = await memory.build_context("它属于什么朝代", history, llm)
    assert "对话摘要：" not in out  # 回退硬截断
    assert "历史对话：" in out
    assert "第7问" in out


async def test_summary_truncated_to_150(memory):
    llm = _FakeLLM(response="长" * 300)
    history = _rounds(7)
    out = await memory.build_context("q", history, llm)
    summary_line = [l for l in out.splitlines() if l.startswith("对话摘要")][0]
    assert len(summary_line) <= 150 + len("对话摘要：")


# ── build_history_text（synthesizer 用）────────────────────

async def test_history_text_no_question_line(memory):
    llm = _FakeLLM()
    history = _rounds(7)
    out = await memory.build_history_text(history, llm)
    assert "当前问题" not in out
    assert "对话摘要：" in out
    assert llm.calls == 1


async def test_history_text_short_no_llm(memory):
    llm = _FakeLLM()
    out = await memory.build_history_text(_rounds(2), llm)
    assert llm.calls == 0
    assert "历史对话：" in out
