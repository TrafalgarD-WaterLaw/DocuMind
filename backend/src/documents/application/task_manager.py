"""上传任务管理器——内存任务表 + JSONL 持久化（U6a）

内存表服务运行期；每次状态变更 append 一行 JSON 到 data/tasks.jsonl，
重启时恢复最近任务（进行中任务标记「服务重启中断」）。
保留最近 N 条任务防内存膨胀。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PERSIST = Path(__file__).resolve().parent.parent / "data" / "tasks.jsonl"

# 重启恢复时：进行中状态视为中断
_ACTIVE_STATUSES = ("queued", "parsing", "chunking", "indexing", "questions")


class TaskStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    INDEXING = "indexing"
    QUESTIONS = "questions"
    DONE = "done"
    FAILED = "failed"


@dataclass
class UploadTask:
    """一次上传解析任务的完整状态"""

    task_id: str
    file_name: str
    status: TaskStatus = TaskStatus.QUEUED
    progress: int = 0
    stage_text: str = ""
    error: str = ""
    source: str = ""
    pages: int = 0
    blocks: dict[str, int] = field(default_factory=dict)
    chunks: int = 0
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)  # U6b 分阶段耗时


def _to_record(task: UploadTask) -> dict:
    """UploadTask → JSON 记录（status 用 value）"""
    rec = dataclasses.asdict(task)
    rec["status"] = task.status.value
    return rec


def _from_record(rec: dict) -> UploadTask:
    """JSON 记录 → UploadTask（未知字段忽略，兼容旧格式）"""
    valid = {k: v for k, v in rec.items() if k in UploadTask.__dataclass_fields__}
    task = UploadTask(**valid)
    try:
        task.status = TaskStatus(rec.get("status", "queued"))
    except ValueError:
        task.status = TaskStatus.FAILED
    return task


class TaskManager:
    """内存任务表 + JSONL 追加持久化（asyncio 单事件循环内操作原子）"""

    def __init__(self, max_tasks: int = 50, persist_path: Path | None = None):
        self._tasks: dict[str, UploadTask] = {}
        self._max_tasks = max_tasks
        self._persist = persist_path or DEFAULT_PERSIST
        self._load()

    # ── 持久化 ─────────────────────────────────────────────

    def _load(self) -> None:
        """启动恢复：逐行回放 JSONL（每 task_id 取最后一条），
        进行中任务标记「服务重启中断」"""
        if not self._persist.exists():
            return
        latest: dict[str, dict] = {}
        for line in self._persist.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("removed"):
                latest.pop(rec.get("task_id"), None)
                continue
            tid = rec.get("task_id")
            if tid:
                latest[tid] = rec
        for tid, rec in latest.items():
            try:
                task = _from_record(rec)
            except Exception as e:
                logger.warning(
                    f"任务日志坏记录跳过: {rec.get('task_id', '?')}: {e}"
                )
                continue
            if task.status.value in _ACTIVE_STATUSES:
                task.status = TaskStatus.FAILED
                task.error = "服务重启中断"
                task.stage_text = "服务重启中断，任务未完成"
                task.finished_at = time.time()
            self._tasks[tid] = task
        if self._tasks:
            logger.info(f"任务日志恢复: {len(self._tasks)} 条（进行中已标记中断）")

    def _persist_task(self, task: UploadTask) -> None:
        """追加任务日志——尽力而为：内存表是权威，日志仅服务重启后恢复用，
        写盘失败只 warning 不阻断任务流程（内存-磁盘短暂分叉可接受）"""
        try:
            self._persist.parent.mkdir(parents=True, exist_ok=True)
            with open(self._persist, "a", encoding="utf-8") as f:
                f.write(json.dumps(_to_record(task), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"任务日志写入失败（内存状态不受影响）: {e}")

    def _persist_removed(self, task_id: str) -> None:
        """追加删除墓碑记录——语义同上，尽力而为"""
        try:
            self._persist.parent.mkdir(parents=True, exist_ok=True)
            with open(self._persist, "a", encoding="utf-8") as f:
                f.write(json.dumps({"task_id": task_id, "removed": True}) + "\n")
        except Exception as e:
            logger.warning(f"任务日志写入失败（内存状态不受影响）: {e}")

    # ── 任务操作 ───────────────────────────────────────────

    def create_task(self, file_name: str, source: str = "") -> str:
        task = UploadTask(
            task_id=uuid.uuid4().hex[:12],
            file_name=file_name,
            source=source,
        )
        self._tasks[task.task_id] = task
        self._persist_task(task)
        # 超出上限时淘汰最旧任务
        while len(self._tasks) > self._max_tasks:
            oldest = min(self._tasks.values(), key=lambda t: t.created_at)
            self._tasks.pop(oldest.task_id, None)
        return task.task_id

    def get_task(self, task_id: str) -> UploadTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> list[UploadTask]:
        ordered = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
        return ordered[:limit]

    def update_task(self, task_id: str, **fields: Any) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)
        if fields.get("status") in (TaskStatus.DONE, TaskStatus.FAILED):
            task.finished_at = time.time()
        self._persist_task(task)

    def latest_by_source(self, source: str) -> UploadTask | None:
        matches = [t for t in self._tasks.values() if t.source == source]
        if not matches:
            return None
        return max(matches, key=lambda t: t.created_at)

    def remove_by_source(self, source: str) -> None:
        for tid in [t.task_id for t in self._tasks.values() if t.source == source]:
            self._tasks.pop(tid, None)
            self._persist_removed(tid)


# 模块级单例（与 core.di.container 同一生命周期）
task_manager = TaskManager()
