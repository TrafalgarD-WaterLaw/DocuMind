"""流式失败兜底测试——重试一次，仍失败给兜底回答"""
import pytest

from models.response import StreamEventType
from conversation.application.orchestrator import ResearchOrchestrator


class FailingOnceLLM:
    """首次流式调用失败，重试成功"""

    def __init__(self):
        self.stream_calls = 0

    def build_messages(self, *args, **kwargs):
        return []

    async def chat(self, messages, **kwargs):
        return "good"

    async def chat_stream(self, messages, **kwargs):
        self.stream_calls += 1
        if self.stream_calls == 1:
            raise RuntimeError("network down")
        for ch in ["好"]:
            yield ch


class AlwaysFailLLM:
    """流式永远失败（触发兜底回答）"""

    def build_messages(self, *args, **kwargs):
        return []

    async def chat(self, messages, **kwargs):
        return "good"

    async def chat_stream(self, messages, **kwargs):
        raise RuntimeError("always down")


async def test_retry_once_then_success():
    llm = FailingOnceLLM()
    orch = ResearchOrchestrator(llm=llm, retriever=None, knowledge=None)
    events = [ev async for ev in orch.quick_answer("叩鼎")]
    assert llm.stream_calls == 2  # 失败 1 次 + 重试成功 1 次
    kinds = [ev.type for ev in events]
    assert StreamEventType.ERROR not in kinds
    content = "".join(ev.data for ev in events if ev.type == StreamEventType.CONTENT)
    assert "好" in content


async def test_fallback_content_on_persistent_failure():
    llm = AlwaysFailLLM()
    orch = ResearchOrchestrator(llm=llm, retriever=None, knowledge=None)
    events = [ev async for ev in orch.quick_answer("叩鼎")]
    kinds = [ev.type for ev in events]
    assert kinds.count(StreamEventType.ERROR) == 1
    fallback = [
        ev.data for ev in events
        if ev.type == StreamEventType.CONTENT and "暂时不可用" in str(ev.data)
    ]
    assert fallback


# ── 拒答机制（知识库未覆盖时不编造）────────────────────────

class _EmptyRetriever:
    """模拟检索不到任何资料的 retriever"""

    async def retrieve(self, query, **kwargs):
        return []


class _NoopLLM:
    """不应被调用的 LLM（拒答路径验证用）"""

    def __init__(self):
        self.calls = 0

    def build_messages(self, *args, **kwargs):
        return []

    async def chat_stream(self, messages, **kwargs):
        self.calls += 1
        yield "不应被调用"


async def test_refuse_answer_on_empty_retrieval():
    """检索为空 → 拒答（ERROR + 提示语），不调 LLM"""
    from models.response import StreamEventType
    from conversation.application.orchestrator import ResearchOrchestrator

    llm = _NoopLLM()
    orch = ResearchOrchestrator(llm=llm, retriever=_EmptyRetriever())
    events = [ev async for ev in orch.quick_answer("火星探测器是什么", None)]

    assert llm.calls == 0  # 未调 LLM
    kinds = [ev.type for ev in events]
    assert StreamEventType.ERROR in kinds
    content = "".join(ev.data for ev in events if ev.type == StreamEventType.CONTENT)
    assert "未找到" in content
