"""RetrievalTrace 发射器测试——pipeline 事件挂载点（方案 B）"""
from core.tracing import RetrievalTrace


def test_record_path_triggers_emitter():
    events = []
    trace = RetrievalTrace(trace_id="t1", query="q")
    trace._emitter = lambda stage, data: events.append((stage, data))
    trace.record_path("semantic", 11, 5627.7)
    assert events == [
        ("path_done", {"name": "semantic", "hits": 11, "took_ms": 5627.7}),
    ]


def test_record_path_without_emitter():
    """无 emitter（旧调用方）行为不变"""
    trace = RetrievalTrace(trace_id="t1", query="q")
    trace.record_path("bm25", 8, 23.9)
    assert trace.paths["bm25"].hits == 8
    assert trace.paths["bm25"].took_ms == 23.9


def test_emitter_exception_swallowed():
    """发射器抛异常不影响检索记录（可观测性优先）"""
    def bad(stage, data):
        raise RuntimeError("boom")

    trace = RetrievalTrace(trace_id="t1", query="q")
    trace._emitter = bad
    trace.record_path("graph", 9, 7625.5)  # 不应抛错
    assert trace.paths["graph"].hits == 9
