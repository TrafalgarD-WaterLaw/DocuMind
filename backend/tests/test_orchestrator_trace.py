"""quick_answer trace 事件与 jsonl 日志测试（fake LLM + 无检索器）"""
import json
from pathlib import Path

import pytest

from core.tracing import RetrievalTrace
from models.response import StreamEventType
from conversation.application.orchestrator import ResearchOrchestrator


class FakeLLM:
    """固定输出的假 LLM（流式逐字）"""

    def build_messages(self, *args, **kwargs):
        return []

    async def chat(self, messages, **kwargs):
        return "good"

    async def chat_stream(self, messages, **kwargs):
        for ch in ["你", "好"]:
            yield ch


class FakeRetriever:
    """带 trace 注入的假检索器（验证 trace 被传递）"""

    async def retrieve(self, query, **kwargs):
        trace: RetrievalTrace | None = kwargs.get("trace")
        if trace is not None:
            trace.record_path("semantic", 2, 1.0)
        return [
            {"id": "c1", "content": "商代青铜鼎。", "source": "s1",
             "paths": ["semantic"], "metadata": {"source": "s1"}},
        ]


async def test_quick_answer_emits_trace_event(tmp_path, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "trace_log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "crag_enabled", False)

    orch = ResearchOrchestrator(llm=FakeLLM(), retriever=FakeRetriever(), knowledge=None)
    events = [ev async for ev in orch.quick_answer("叩鼎是什么朝代")]

    trace_events = [ev for ev in events if ev.type == StreamEventType.TRACE]
    assert len(trace_events) == 1
    data = trace_events[0].data
    assert data["query"] == "叩鼎是什么朝代"
    assert "semantic" in data["paths"]
    assert data["paths"]["semantic"]["hits"] == 2

    # 事件顺序：content 之后；步骤卡已移除（AGENT_STEP 停发，专家/拒答信息
    # 改走 PIPELINE 事件），TRACE 诊断成为最后一个事件
    kinds = [ev.type for ev in events]
    i_trace = kinds.index(StreamEventType.TRACE)
    assert i_trace > kinds.index(StreamEventType.CONTENT)
    assert i_trace == len(kinds) - 1

    # jsonl 落盘
    log_file = Path(tmp_path) / "query_trace.jsonl"
    assert log_file.exists()
    payload = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[0])
    assert payload["trace_id"] == data["trace_id"]
