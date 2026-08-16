"""查询轨迹（RetrievalTrace）——一次问答的检索过程全记录

供前端 trace 事件、query_trace.jsonl 结构化日志使用；
对齐业界 RAG 项目的查询可观测性（Dify 问答日志）。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.config import settings


@dataclass
class PathTrace:
    """单条检索路径的命中数与耗时"""

    hits: int = 0
    took_ms: float = 0.0


@dataclass
class RetrievalTrace:
    """一次检索的完整诊断信息（跨请求独立实例，无共享状态）"""

    trace_id: str
    query: str
    # 改写词——拆解场景无单一改写词 → None（前端诊断显示"无"）
    rewritten_query: str | None = None
    crag_triggered: bool = False
    paths: dict[str, PathTrace] = field(default_factory=dict)
    path_stats: dict[str, int] = field(default_factory=dict)
    total_ms: float = 0.0
    llm_usage: dict[str, int] = field(default_factory=dict)
    # pipeline 事件发射器——record_path 时回调，orchestrator 经
    # asyncio.Queue 桥接转成实时 NDJSON 事件；异常吞掉不影响主链路
    _emitter: Callable[[str, dict], None] | None = None

    def record_path(self, name: str, hits: int, took_ms: float) -> None:
        self.paths[name] = PathTrace(hits=hits, took_ms=round(took_ms, 1))
        if self._emitter is not None:
            try:
                self._emitter("path_done", {
                    "name": name,
                    "hits": hits,
                    "took_ms": took_ms,
                })
            except Exception:
                # 可观测性失败不影响主链路
                pass

    def set_path_stats(self, stats: dict[str, int]) -> None:
        self.path_stats = stats

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "query": self.query,
            "rewritten_query": self.rewritten_query,
            "crag_triggered": self.crag_triggered,
            "paths": {
                name: {"hits": p.hits, "took_ms": p.took_ms}
                for name, p in self.paths.items()
            },
            "path_stats": self.path_stats,
            "total_ms": round(self.total_ms, 1),
            "llm_usage": self.llm_usage,
            "ts": time.time(),
        }


def new_trace_id() -> str:
    """生成短 trace id（uuid4 前 8 位）"""
    return uuid.uuid4().hex[:8]


class TraceLogWriter:
    """trace jsonl 写入器——追加句柄缓存为实例状态（容器装配唯一实例）

    句柄复用、失活（f.closed）时重开;测试可注入临时 writer。
    """

    def __init__(self) -> None:
        self._handles: dict[str, Any] = {}

    def write(self, trace: RetrievalTrace, log_dir: str | None = None) -> None:
        """追加写入结构化日志（一条查询一行 JSON;文件句柄缓存复用）"""
        try:
            d = Path(log_dir or settings.trace_log_dir)
            d.mkdir(parents=True, exist_ok=True)
            f = self._handles.get(str(d))
            if f is None or f.closed:
                f = open(d / "query_trace.jsonl", "a", encoding="utf-8")
                self._handles[str(d)] = f
            f.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")
            f.flush()
        except Exception:
            # 可观测性失败不影响主链路（含句柄写入失败——不重开,
            # 避免可观测性路径上的重试复杂度）
            pass


def write_trace_jsonl(
    trace: RetrievalTrace, log_dir: str | None = None
) -> None:
    """模块级委托入口——写入器实例由容器装配（组合根），函数无全局状态"""
    from core.di import container

    container.trace_writer.write(trace, log_dir)
