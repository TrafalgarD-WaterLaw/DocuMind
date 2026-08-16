"""quick_answer pipeline 事件流测试（方案 B：检索过程实时事件）"""
from core.tracing import RetrievalTrace
from models.response import StreamEventType
from conversation.application.orchestrator import ResearchOrchestrator


class FakeLLM:
    def build_messages(self, *args, **kwargs):
        return []

    async def chat(self, messages, **kwargs):
        return "good"

    async def chat_stream(self, messages, **kwargs):
        for ch in ["好"]:
            yield ch


class FakeRetriever:
    """模拟五路串行完成（按顺序 record_path）"""

    async def retrieve(self, query, **kwargs):
        trace: RetrievalTrace | None = kwargs.get("trace")
        if trace is not None:
            trace.record_path("semantic", 11, 5627.7)
            trace.record_path("question", 8, 3560.8)
            trace.record_path("bm25", 8, 23.9)
            trace.record_path("graph", 9, 7625.5)
            trace.record_path("entity", 0, 609.0)
        return [
            {"id": "c1", "content": "商代青铜鼎。", "source": "青铜-司母戊鼎",
             "paths": ["semantic"], "metadata": {"source": "青铜-司母戊鼎"}},
        ]


async def test_quick_answer_emits_pipeline_events(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "crag_enabled", False)

    orch = ResearchOrchestrator(llm=FakeLLM(), retriever=FakeRetriever(), knowledge=None)
    events = [ev async for ev in orch.quick_answer("商代青铜鼎的铸造工艺")]

    pipe = [ev for ev in events if ev.type == StreamEventType.PIPELINE]
    assert pipe, "应发出 pipeline 事件"

    # 序列: rewrite → path_done×5（五路顺序）→ fuse → generate
    stages = [ev.data["stage"] for ev in pipe]
    assert stages[0] == "rewrite"
    path_names = [ev.data["name"] for ev in pipe if ev.data["stage"] == "path_done"]
    assert path_names == ["semantic", "question", "bm25", "graph", "entity"]
    assert "fuse" in stages
    assert "generate" in stages

    # path_done 载荷
    sem = next(ev for ev in pipe if ev.data.get("name") == "semantic")
    assert sem.data["hits"] == 11
    assert sem.data["took_ms"] == 5627.7

    # 检索事件先于生成（content 之前）
    kinds = [ev.type for ev in events]
    assert kinds.index(StreamEventType.PIPELINE) < kinds.index(StreamEventType.CONTENT)
    # trace 全量事件仍保留（展开详情数据源）
    assert any(ev.type == StreamEventType.TRACE for ev in events)
