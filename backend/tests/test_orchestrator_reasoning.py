"""深度模式 reasoning 事件测试——专家完成后发出带角色标题的摘要"""
import json

import pytest

from models.response import StreamEventType
from conversation.application.orchestrator import ResearchOrchestrator


class _FakeLLM:
    """按调用顺序返回：意图 JSON → 史官分析 → 著述报告"""

    def __init__(self):
        self.calls = 0

    def build_messages(self, *args, **kwargs):
        return []

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return json.dumps(
                {"mode": "deep", "summary": "测试", "agents": ["historian"]},
                ensure_ascii=False,
            )
        if self.calls == 2:
            return "叩鼎为商代晚期青铜礼器，器形为三足圆鼎。"
        return "## 综合报告\n\n叩鼎的史官考证结论。"

    async def chat_stream(self, messages, **kwargs):
        raise AssertionError("深度模式不走 chat_stream")


async def test_deep_research_emits_reasoning():
    orch = ResearchOrchestrator(llm=_FakeLLM(), retriever=None, knowledge=None)
    events = [ev async for ev in orch.deep_research("叩鼎是什么")]

    reasoning = [ev for ev in events if ev.type == StreamEventType.REASONING]
    # 至少史官摘要一条 + 著述提示一条
    assert len(reasoning) >= 2
    assert "史官 · 历史考证" in reasoning[0].data
    assert "叩鼎为商代晚期" in reasoning[0].data
    assert "著述" in reasoning[-1].data

    # reasoning 事件位于该专家 expert(done) 事件之前
    # （步骤卡已移除——AGENT_STEP 改走 PIPELINE expert 事件）
    expert_events = [ev for ev in events if ev.type == StreamEventType.PIPELINE]
    done_events = [ev for ev in expert_events if ev.data.get("status") == "done"]
    done_pos = events.index(done_events[0])  # 第一个专家 done（史官）
    reasoning_pos = events.index(reasoning[0])
    assert reasoning_pos < done_pos
