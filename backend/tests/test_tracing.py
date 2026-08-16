"""查询轨迹模块测试"""
import json
from pathlib import Path

from core.tracing import (
    RetrievalTrace, new_trace_id, write_trace_jsonl,
)


def test_new_trace_id_unique():
    assert new_trace_id() != new_trace_id()
    assert len(new_trace_id()) >= 8


def test_record_path_and_to_dict():
    trace = RetrievalTrace(trace_id="t1", query="叩鼎")
    trace.record_path("semantic", 5, 12.5)
    trace.record_path("bm25", 3, 4.0)
    trace.set_path_stats({"semantic": 4, "bm25": 2})
    d = trace.to_dict()
    assert d["trace_id"] == "t1"
    assert d["query"] == "叩鼎"
    assert d["paths"]["semantic"]["hits"] == 5
    assert d["paths"]["semantic"]["took_ms"] == 12.5
    assert d["path_stats"] == {"semantic": 4, "bm25": 2}
    assert "total_ms" in d and "llm_usage" in d


def test_write_trace_jsonl(tmp_path):
    trace = RetrievalTrace(trace_id="t1", query="商代", rewritten_query="商代青铜鼎")
    trace.record_path("question", 8, 3.0)
    write_trace_jsonl(trace, log_dir=str(tmp_path))
    log_file = tmp_path / "query_trace.jsonl"
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["rewritten_query"] == "商代青铜鼎"


def test_write_trace_appends(tmp_path):
    write_trace_jsonl(RetrievalTrace(trace_id="a", query="q1"), log_dir=str(tmp_path))
    write_trace_jsonl(RetrievalTrace(trace_id="b", query="q2"), log_dir=str(tmp_path))
    log_file = tmp_path / "query_trace.jsonl"
    assert len(log_file.read_text(encoding="utf-8").strip().splitlines()) == 2
