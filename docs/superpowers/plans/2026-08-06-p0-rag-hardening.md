# P0 全链路 RAG 加固实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 P0 三包：入库闭环（异步任务 + 索引一致性）、问答可观测性（trace 事件 + 诊断）、LLM 统一容错（timeout + 重试 + 兜底）。

**Architecture:** 后端新增 `task_manager.py`（内存任务表）与 `core/tracing.py`（查询轨迹）；`hybrid.py` 的 `retrieve()` 增加可注入的 `trace` 参数与 BM25 惰性重建；`deepseek.py` 加 timeout/usage/重试；orchestrator 组装 trace 事件与流式兜底。前端重设计 LibraryView（任务队列+轮询+文档管理）与 ChatMessageItem（诊断折叠面板）。

**Tech Stack:** Python 3.10+ / FastAPI / pytest (dev, 已配置 pyproject.toml 的 `[tool.pytest.ini_options]`: `pythonpath=["src"]`, `asyncio_mode="auto"`, `testpaths=["tests"]`)；Vue 3 `<script setup lang="ts">` + Pinia + Less + Element Plus。

**Spec:** `docs/superpowers/specs/2026-08-06-full-chain-rag-hardening-design.md`

## Global Constraints

- 所有 UI 文本、注释、代码内文案必须为中文（后端日志/注释同理）。
- 前端仅用 `<script setup lang="ts">`（项目已迁移，勿回退 Options API）；样式用 Less。
- API 密钥/Neo4j 密码硬编码在 config.ini / rag.py 等既有文件中——**绝不复制、绝不输出到日志**。
- 后端运行方式：`cd Backend && uv run python src\main.py`（热重载）。前端：`cd Frontend && npm run dev` / `npm run build`（build 含 vue-tsc 类型检查）。
- **项目无 git 仓库**：所有计划中的 "Commit" 步骤替换为「运行验证命令并确认通过」。
- 后端测试命令：`cd Backend && uv run pytest tests/<path> -v`（先 `uv pip install pytest pytest-asyncio` 安装一次）。
- 检索链路参数（chunk_size=500、chunk_overlap=50、rrf_k=60 等）以 `src/core/config.py` settings 为准，前端高级选项传参覆盖默认值。

---

### Task A1: pytest 基础设施就绪

**Files:**
- Modify: `Backend/pyproject.toml`（无需改，dev 依赖已含 pytest/pytest-asyncio）
- Test: `Backend/tests/test_smoke.py`

**Interfaces:**
- Produces: 可运行的 pytest 命令 `uv run pytest`（能收集 `tests/` 下用例并通过）。

- [ ] **Step 1: 安装 dev 依赖**

Run: `cd E:/projects/DocuMind/Backend && uv pip install pytest pytest-asyncio`
Expected: 输出成功安装版本号（pytest>=8.3.0, pytest-asyncio>=0.24.0）。

- [ ] **Step 2: 写冒烟测试**

Create `Backend/tests/test_smoke.py`:

```python
"""冒烟测试——验证 pytest 基础设施与 src 导入路径"""
from core.config import settings


def test_settings_loads():
    assert settings.chunk_size == 500
    assert settings.chunk_overlap == 50


def test_import_providers():
    from providers.llm import DeepSeekProvider  # noqa: F401
    from services.retrieval.bm25 import BM25Index  # noqa: F401
```

- [ ] **Step 3: 运行验证**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_smoke.py -v`
Expected: 2 passed（`pythonpath=["src"]` 生效，可导入 `core.config`）。

---

### Task A2: 任务管理器 TaskManager

**Files:**
- Create: `Backend/src/services/task_manager.py`
- Test: `Backend/tests/test_task_manager.py`

**Interfaces:**
- Consumes: 无（纯逻辑模块）。
- Produces:
  ```python
  class TaskStatus(StrEnum): QUEUED/PARSING/CHUNKING/INDEXING/QUESTIONS/DONE/FAILED
  class UploadTask: task_id, file_name, source, status, progress, stage_text, error, pages, blocks, chunks, created_at, finished_at
  class TaskManager:
      def create_task(file_name: str, source: str = "") -> str
      def get_task(task_id: str) -> UploadTask | None
      def list_tasks(limit: int = 50) -> list[UploadTask]   # 按创建时间倒序
      def update_task(task_id: str, **fields) -> None
      def latest_by_source(source: str) -> UploadTask | None
      def remove_by_source(source: str) -> None
  task_manager = TaskManager()   # 模块级单例
  ```

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_task_manager.py`:

```python
"""任务管理器测试——状态机、保留上限、按来源查询"""
import pytest

from services.task_manager import TaskManager, TaskStatus


def test_create_and_update():
    tm = TaskManager()
    tid = tm.create_task(file_name="a.pdf")
    t = tm.get_task(tid)
    assert t.status == TaskStatus.QUEUED
    assert t.progress == 0
    assert t.file_name == "a.pdf"
    tm.update_task(tid, status=TaskStatus.PARSING, progress=20, stage_text="解析中")
    assert tm.get_task(tid).status == TaskStatus.PARSING
    assert tm.get_task(tid).progress == 20


def test_failed_holds_error():
    tm = TaskManager()
    tid = tm.create_task(file_name="b.pdf")
    tm.update_task(tid, status=TaskStatus.FAILED, error="boom")
    assert tm.get_task(tid).error == "boom"


def test_list_orders_newest_first():
    tm = TaskManager()
    ids = [tm.create_task(file_name=f"f{i}.pdf") for i in range(3)]
    listed = tm.list_tasks()
    assert [t.task_id for t in listed] == list(reversed(ids))


def test_cap_keeps_recent():
    tm = TaskManager(max_tasks=3)
    for i in range(5):
        tm.create_task(file_name=f"f{i}.pdf")
    assert len(tm.list_tasks()) == 3
    assert tm.list_tasks()[0].file_name == "f4.pdf"


def test_latest_and_remove_by_source():
    tm = TaskManager()
    tm.create_task(file_name="c.pdf", source="123_c.pdf")
    tm.update_task(tm.list_tasks()[0].task_id, status=TaskStatus.DONE, progress=100)
    latest = tm.latest_by_source("123_c.pdf")
    assert latest is not None and latest.status == TaskStatus.DONE
    tm.remove_by_source("123_c.pdf")
    assert tm.latest_by_source("123_c.pdf") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_task_manager.py -v`
Expected: FAIL（ModuleNotFoundError: services.task_manager）。

- [ ] **Step 3: 实现**

Create `Backend/src/services/task_manager.py`:

```python
"""上传任务管理器——进程内任务表（内存存储）

演示场景足够：重启/热重载后丢失进行中任务可接受（前端挂载时
重新拉取任务列表恢复轮询）。保留最近 N 条任务防内存膨胀。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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


class TaskManager:
    """内存任务表——线程安全（asyncio 单事件循环内 dict 操作原子）"""

    def __init__(self, max_tasks: int = 50):
        self._tasks: dict[str, UploadTask] = {}
        self._max_tasks = max_tasks

    def create_task(self, file_name: str, source: str = "") -> str:
        task = UploadTask(
            task_id=uuid.uuid4().hex[:12],
            file_name=file_name,
            source=source,
        )
        self._tasks[task.task_id] = task
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

    def latest_by_source(self, source: str) -> UploadTask | None:
        matches = [t for t in self._tasks.values() if t.source == source]
        if not matches:
            return None
        return max(matches, key=lambda t: t.created_at)

    def remove_by_source(self, source: str) -> None:
        for tid in [t.task_id for t in self._tasks.values() if t.source == source]:
            self._tasks.pop(tid, None)


# 模块级单例（与 core.di.container 同一生命周期）
task_manager = TaskManager()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_task_manager.py -v`
Expected: 5 passed。

---

### Task A3: 问题生成进度回调

**Files:**
- Modify: `Backend/src/services/retrieval/hypothesis.py`（`build_question_documents` 加 `on_progress` 参数）
- Test: `Backend/tests/test_hypothesis_progress.py`

**Interfaces:**
- Consumes: `LLMProvider`（interfaces.llm）、`VectorStore`（interfaces.vector_store）。
- Produces: `build_question_documents(llm, documents, questions_store, *, batch_size=None, skip_existing=True, on_progress: Callable[[int, int], None] | None = None) -> int`——每处理完一批调用 `on_progress(done_batches, total_batches)`。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_hypothesis_progress.py`:

```python
"""问题生成进度回调测试——on_progress 单调递增且总量正确"""
import json

import pytest

from services.retrieval.hypothesis import build_question_documents


class FakeLLM:
    """返回固定 JSON 的假 LLM"""

    def build_messages(self, *args, **kwargs):
        return []

    async def chat(self, messages, **kwargs):
        # 3 个 chunk → 3 条带 questions 的结果
        payload = {
            "results": [
                {"chunk_id": f"c{i}", "questions": [f"问题{i}-1", f"问题{i}-2"]}
                for i in range(3)
            ]
        }
        return json.dumps(payload, ensure_ascii=False)


class FakeStore:
    """内存问题库：get_all_documents + add_documents"""

    def __init__(self):
        self.docs = []

    def get_all_documents(self):
        return list(self.docs)

    def add_documents(self, documents):
        self.docs.extend(documents)


async def test_on_progress_reports_batches():
    llm = FakeLLM()
    store = FakeStore()
    docs = [
        {"id": f"c{i}", "content": f"内容{i}", "metadata": {"source": "s"}}
        for i in range(3)
    ]
    calls: list[tuple[int, int]] = []
    total = await build_question_documents(
        llm, docs, store, batch_size=1, on_progress=lambda d, t: calls.append((d, t))
    )
    assert total == 6  # 3 chunk × 2 问
    assert calls == [(1, 3), (2, 3), (3, 3)]  # 每批完成回调一次，总量正确


async def test_on_progress_optional():
    llm = FakeLLM()
    store = FakeStore()
    docs = [{"id": "c0", "content": "x", "metadata": {}}]
    total = await build_question_documents(llm, docs, store, batch_size=1)
    assert total == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_hypothesis_progress.py -v`
Expected: FAIL（TypeError: build_question_documents() got an unexpected keyword argument 'on_progress'）。

- [ ] **Step 3: 实现**

Modify `Backend/src/services/retrieval/hypothesis.py`：

1) 在 `build_question_documents` 签名处加入 `on_progress` 参数：

```python
async def build_question_documents(
    llm: LLMProvider,
    documents: list[dict[str, Any]],
    questions_store: VectorStore,
    *,
    batch_size: int | None = None,
    skip_existing: bool = True,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
```

2) 在 `total = 0` 之后、`for start in range(...)` 之前加入批数统计：

```python
    total = 0
    total_batches = max(
        1, (len(documents) + batch_size - 1) // batch_size
    )
    done_batches = 0
```

3) 在 `for` 循环体内 `batch = [d for d in batch if d["id"] not in existing]` 之后、`if not batch: continue` 改为：

```python
        batch = [d for d in batch if d["id"] not in existing]
        done_batches += 1
        if on_progress:
            on_progress(done_batches, total_batches)
        if not batch:
            continue
```

4) 顶部 import 加 `from typing import Any, Callable`（当前为 `from typing import Any`）。

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_hypothesis_progress.py -v`
Expected: 2 passed。

---

### Task A4: BM25 惰性重建（dirty 标记）

**Files:**
- Modify: `Backend/src/services/retrieval/bm25.py`
- Test: `Backend/tests/test_bm25_dirty.py`

**Interfaces:**
- Consumes: `VectorStore.get_all_documents()`。
- Produces: `BM25Index.mark_dirty() -> None`、`BM25Index.rebuild_if_dirty(doc_store) -> bool`（返回是否实际重建）。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_bm25_dirty.py`:

```python
"""BM25 惰性重建测试——上传/删除后脏标记，下次检索前重建"""
from services.retrieval.bm25 import BM25Index


class FakeStore:
    def __init__(self, docs):
        self._docs = list(docs)

    def get_all_documents(self):
        return list(self._docs)


def _doc(did: str, text: str) -> dict:
    return {"id": did, "content": text, "metadata": {"source": f"s-{did}"}}


def test_build_and_retrieve():
    idx = BM25Index()
    idx.build([_doc("1", "商代青铜鼎 叩鼎")])
    assert idx.count == 1
    assert idx.retrieve("叩鼎", top_k=5)


def test_mark_dirty_then_rebuild():
    idx = BM25Index()
    idx.build([_doc("1", "商代青铜鼎")])
    assert not idx.dirty
    idx.mark_dirty()
    assert idx.dirty
    store = FakeStore([_doc("1", "商代青铜鼎"), _doc("2", "宣德青花釉里红")])
    rebuilt = idx.rebuild_if_dirty(store)
    assert rebuilt is True
    assert idx.dirty is False
    assert idx.count == 2
    # 新文档可被关键词召回
    assert any(r["id"] == "2" for r in idx.retrieve("宣德青花", top_k=5))


def test_rebuild_if_clean_does_nothing():
    idx = BM25Index()
    idx.build([_doc("1", "商代青铜鼎")])
    assert idx.rebuild_if_dirty(FakeStore([_doc("1", "商代青铜鼎")])) is False
    assert idx.count == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_bm25_dirty.py -v`
Expected: FAIL（AttributeError: 'BM25Index' object has no attribute 'dirty'）。

- [ ] **Step 3: 实现**

Modify `Backend/src/services/retrieval/bm25.py`：

1) `__init__` 增加脏标记：

```python
    def __init__(self):
        self._corpus: list[dict[str, Any]] = []
        self._bm25: BM25Okapi | None = None
        # 脏标记：上传/删除文档后置位，检索前惰性重建
        # （重建为全量同步构建，毫秒级；幂等无副作用，不设锁）
        self.dirty = False
```

2) `build()` 末尾清脏标记（加在 `logger.info(...)` 后）：

```python
        self.dirty = False
```

3) 新增两个方法（放在 `add` 之后）：

```python
    def mark_dirty(self) -> None:
        """标记索引过期（上传/删除文档后调用）"""
        self.dirty = True

    def rebuild_if_dirty(self, doc_store) -> bool:
        """脏时从向量库全量重建；返回是否实际重建"""
        if not self.dirty:
            return False
        self.build(doc_store.get_all_documents())
        return True
```

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_bm25_dirty.py -v`
Expected: 3 passed。

---

### Task A5: HybridRetriever 检索前重建 + 检索轨迹采集

**Files:**
- Modify: `Backend/src/services/retrieval/hybrid.py`
- Test: `Backend/tests/test_hybrid_trace.py`

**Interfaces:**
- Consumes: `core.tracing.RetrievalTrace`（Task B1 实现，本任务先按下列签名内联使用——**先做 Task B1 再执行本任务**）。
- Produces: `HybridRetriever.retrieve(query, *, top_k=None, use_graph=True, max_per_source=2, trace: RetrievalTrace | None = None)`——入口先 `self.bm25.rebuild_if_dirty(self.doc_store)`；`trace` 非空时逐路记录 `hits`/`took_ms`。

> 执行顺序说明：Task A5 依赖 Task B1（tracing.py）。计划允许先做 B1 再回做 A5；B1 为纯新模块，无前置依赖。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_hybrid_trace.py`:

```python
"""混合检索轨迹测试——各路径命中数与耗时记录"""
import pytest

from core.tracing import RetrievalTrace
from services.retrieval.bm25 import BM25Index
from services.retrieval.hybrid import HybridRetriever


class FakeStore:
    """最小文档库：semantic(树)/question/bm25/实体 各路径所需方法"""

    def __init__(self, docs, questions=None):
        self._docs = list(docs)
        self._questions = list(questions or [])

    def retrieve(self, query, *, top_k=5, where=None):
        return [
            {"id": d["id"], "content": d["content"],
             "source": d["metadata"].get("source", ""), "score": 0.5,
             "metadata": d["metadata"]}
            for d in self._docs[:top_k]
        ]

    def get_by_ids(self, ids):
        by_id = {d["id"]: d for d in self._docs}
        return [by_id[i] for i in ids if i in by_id]

    def get_by_source_like(self, keyword, limit=50):
        return []

    def get_all_documents(self):
        return [dict(d) for d in self._docs]

    def list_sources(self):
        return [d["metadata"].get("source", "") for d in self._docs]

    def count(self):
        return len(self._docs)


async def test_retrieve_records_trace_paths(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "entity_anchor_enabled", False)
    docs = [
        {"id": "c1", "content": "叩鼎是商代青铜器。", "metadata": {"source": "s1"}},
        {"id": "c2", "content": "宣德青花瓷釉层。", "metadata": {"source": "s2"}},
    ]
    doc_store = FakeStore(docs)
    bm25 = BM25Index()
    bm25.build(doc_store.get_all_documents())

    retriever = HybridRetriever(
        doc_store=doc_store,
        question_store=FakeStore([], questions=[]),
        bm25=bm25,
        graph=None,          # 图谱路跳过
        llm=None,            # 实体提取跳过
        top_k=4,
    )
    trace = RetrievalTrace(trace_id="t1", query="叩鼎")
    results = await retriever.retrieve("叩鼎", trace=trace)
    assert results  # 至少 semantic 路命中
    assert "semantic" in trace.paths
    assert "bm25" in trace.paths
    assert trace.paths["semantic"].hits >= 1
    assert trace.paths["semantic"].took_ms >= 0


async def test_retrieve_without_trace_backward_compat(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "entity_anchor_enabled", False)
    doc_store = FakeStore([
        {"id": "c1", "content": "叩鼎", "metadata": {"source": "s1"}},
    ])
    bm25 = BM25Index()
    bm25.build(doc_store.get_all_documents())
    retriever = HybridRetriever(
        doc_store=doc_store, question_store=FakeStore([]),
        bm25=bm25, graph=None, llm=None, top_k=4,
    )
    results = await retriever.retrieve("叩鼎")
    assert isinstance(results, list)


async def test_dirty_rebuild_on_retrieve(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "entity_anchor_enabled", False)
    doc_store = FakeStore([
        {"id": "c1", "content": "新文档内容", "metadata": {"source": "new.pdf"}},
    ])
    bm25 = BM25Index()
    bm25.build([])  # 空索引 + 脏标记 → retrieve 时自动重建
    bm25.mark_dirty()
    retriever = HybridRetriever(
        doc_store=doc_store, question_store=FakeStore([]),
        bm25=bm25, graph=None, llm=None, top_k=4,
    )
    results = await retriever.retrieve("新文档")
    assert not bm25.dirty
    assert any(r["source"] == "new.pdf" for r in results)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_hybrid_trace.py -v`
Expected: FAIL（ModuleNotFoundError: core.tracing）。

- [ ] **Step 3: 实现**

Modify `Backend/src/services/retrieval/hybrid.py`：

1) 顶部 import 增加：

```python
from core.tracing import RetrievalTrace
```

2) `retrieve` 签名改为：

```python
    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        use_graph: bool = True,
        max_per_source: int = 2,
        trace: RetrievalTrace | None = None,
    ) -> list[dict[str, Any]]:
```

3) 在 `top_k = top_k or self.top_k` 之后插入 BM25 惰性重建：

```python
        # 上传/删除文档后 BM25 置脏，检索前惰性重建（全量毫秒级）
        try:
            self.bm25.rebuild_if_dirty(self.doc_store)
        except Exception as e:
            logger.warning(f"BM25 惰性重建失败: {e}")
```

4) 在 `ranked_paths` 组装处包一层计时（替换原 `ranked_paths: list[...] = [...]` 块）：

```python
        import time as _time

        ranked_paths: list[tuple[str, list[dict[str, Any]]]] = []
        for path_name, call in (
            ("semantic", lambda: self._path_semantic(query, top_k=path_k)),
            ("question", lambda: self._path_question(query, top_k=path_k)),
            ("bm25", lambda: self._path_bm25(query, top_k=path_k)),
        ):
            t0 = _time.perf_counter()
            docs = call()
            ranked_paths.append((path_name, docs))
            if trace is not None:
                trace.record_path(path_name, len(docs), (_time.perf_counter() - t0) * 1000)
        if use_graph:
            t0 = _time.perf_counter()
            gdocs = await self._path_graph(query)
            ranked_paths.append(("graph", gdocs))
            if trace is not None:
                trace.record_path("graph", len(gdocs), (_time.perf_counter() - t0) * 1000)
        if settings.entity_anchor_enabled:
            t0 = _time.perf_counter()
            edocs = await self._path_entity_anchor(query)
            ranked_paths.append(("entity", edocs))
            if trace is not None:
                trace.record_path("entity", len(edocs), (_time.perf_counter() - t0) * 1000)
```

5) 在 `results = candidates[:top_k]` 之后、`if results:` 日志块之后（返回前）加：

```python
        if trace is not None:
            trace.set_path_stats(self._path_stats(results))
```

（`set_path_stats` 在 Task B1 实现，记录最终 top-8 的路分布。）

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_hybrid_trace.py -v`
Expected: 3 passed。

---

### Task B1: 查询轨迹模块 core/tracing.py

**Files:**
- Create: `Backend/src/core/tracing.py`
- Modify: `Backend/src/core/config.py`（加 `trace_log_dir`）
- Test: `Backend/tests/test_tracing.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class PathTrace: hits: int = 0; took_ms: float = 0.0
  @dataclass
  class RetrievalTrace:
      trace_id: str; query: str
      rewritten_query: str = ""
      crag_triggered: bool = False
      paths: dict[str, PathTrace] = field(default_factory=dict)
      path_stats: dict[str, int] = field(default_factory=dict)
      total_ms: float = 0.0
      llm_usage: dict[str, int] = field(default_factory=dict)   # {prompt_tokens, completion_tokens}
      def record_path(name, hits, took_ms) -> None
      def set_path_stats(stats: dict[str, int]) -> None
      def to_dict() -> dict
  def new_trace_id() -> str
  def write_trace_jsonl(trace: RetrievalTrace, log_dir: str | None = None) -> None
  ```
  日志目录：`settings.trace_log_dir`（默认 `src/data/logs`），文件 `query_trace.jsonl`，UTF-8 追加。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_tracing.py`:

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_tracing.py -v`
Expected: FAIL（ModuleNotFoundError: core.tracing）。

- [ ] **Step 3: 实现**

1) Modify `Backend/src/core/config.py`，在「── 路径 ──」区块末尾加：

```python
    trace_log_dir: str = "src/data/logs"   # 查询轨迹 JSONL 日志目录
```

2) Create `Backend/src/core/tracing.py`:

```python
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
from typing import Any

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
    rewritten_query: str = ""
    crag_triggered: bool = False
    paths: dict[str, PathTrace] = field(default_factory=dict)
    path_stats: dict[str, int] = field(default_factory=dict)
    total_ms: float = 0.0
    llm_usage: dict[str, int] = field(default_factory=dict)

    def record_path(self, name: str, hits: int, took_ms: float) -> None:
        self.paths[name] = PathTrace(hits=hits, took_ms=round(took_ms, 1))

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


def write_trace_jsonl(
    trace: RetrievalTrace, log_dir: str | None = None
) -> None:
    """追加写入结构化日志（一条查询一行 JSON）"""
    try:
        d = Path(log_dir or settings.trace_log_dir)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "query_trace.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")
    except Exception:
        # 可观测性失败不影响主链路
        pass
```

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_tracing.py -v`
Expected: 4 passed。

---

### Task A6: upload.py 异步任务化

**Files:**
- Modify: `Backend/src/api/upload.py`
- Modify: `Backend/src/interfaces/vector_store.py`（加 `count_by_source`）
- Modify: `Backend/src/providers/vector/chroma.py`（实现 `count_by_source`）
- Modify: `Backend/src/core/di.py`（加 `mark_bm25_dirty()`）
- Verify: 手动 curl 冒烟（需后端运行 + 一个测试 PDF）

**Interfaces:**
- Consumes: `services.task_manager.task_manager`（Task A2）、`BM25Index.mark_dirty`（Task A4）、`build_question_documents(on_progress=...)`（Task A3）。
- Produces:
  - `POST /api/upload`（multipart：`file`、可选 `replace: bool = Form(False)`、`chunk_size: int = Form(0)`、`chunk_overlap: int = Form(0)`）→ `{"task_id": str, "file_name": str}`
  - `GET /api/upload/tasks` → `{"tasks": [UploadTask dicts]}`
  - `GET /api/upload/tasks/{task_id}` → UploadTask dict
  - `GET /api/documents` 增强 → `{"documents": [{source, chunks, questions, pages, status, created_at}]}`
  - `VectorStore.count_by_source(source) -> int`（接口默认 `NotImplementedError`，ChromaStore 实现）

- [ ] **Step 1: 写失败测试（count_by_source）**

Create `Backend/tests/test_chroma_count.py`:

```python
"""ChromaStore.count_by_source 测试（真实 Chroma + 临时目录 + 固定向量）"""
import numpy as np

from providers.vector import ChromaStore


def _ef(input: list[str]) -> list[list[float]]:
    """固定维度 embedding（不依赖模型）"""
    return [[0.1] * 64 for _ in input]


def test_count_by_source(tmp_path):
    store = ChromaStore(persist_dir=str(tmp_path), embedding_function=_ef)
    store.add_documents([
        {"chunk_id": "1", "content": "a", "metadata": {"source": "s1"}},
        {"chunk_id": "2", "content": "b", "metadata": {"source": "s1"}},
        {"chunk_id": "3", "content": "c", "metadata": {"source": "s2"}},
    ])
    assert store.count_by_source("s1") == 2
    assert store.count_by_source("s2") == 1
    assert store.count_by_source("nope") == 0


def test_count_by_source_empty(tmp_path):
    store = ChromaStore(persist_dir=str(tmp_path), embedding_function=_ef)
    assert store.count_by_source("s1") == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_chroma_count.py -v`
Expected: FAIL（TypeError: object has no attribute 'count_by_source'）。

- [ ] **Step 3: 实现 count_by_source**

1) Modify `Backend/src/interfaces/vector_store.py`，在 `count` 之后加：

```python
    def count_by_source(self, source: str) -> int:
        """按来源统计文档块数（未实现的存储返回 0）"""
        return 0
```

（接口给默认实现，避免破坏其它实现；ChromaStore 覆写为精确计数。）

2) Modify `Backend/src/providers/vector/chroma.py`，在 `list_sources` 之后加：

```python
    def count_by_source(self, source: str) -> int:
        if not source:
            return 0
        try:
            results = self.collection.get(where={"source": source})
            return len(results.get("ids", []))
        except Exception as e:
            logger.warning(f"count_by_source 失败 ({source}): {e}")
            return 0
```

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_chroma_count.py -v`
Expected: 2 passed。

- [ ] **Step 5: 实现 upload.py 异步任务化（全量重写文件）**

Modify `Backend/src/api/upload.py`（重写为以下完整内容，保留 `_get_parser` 与删除逻辑）：

```python
"""文档上传与解析 API——异步任务化（提交即返回 task_id，前端轮询进度）"""
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from core.config import settings
from core.di import container
from interfaces.doc_parser import DocParser, ParsedDocument
from providers.parser import PyPDFParser
from services.indexer import IndexerService
from services.task_manager import TaskStatus, task_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["upload"])

UPLOAD_DIR = Path(settings.upload_dir)


def _get_parser() -> DocParser:
    """选择解析器 — 优先 Docling，回退 PyPDF"""
    try:
        from providers.parser import DoclingParser

        parser = DoclingParser()
        if parser.available:
            return parser
        logger.info("Docling 未安装，使用 PyPDF 回退")
    except ImportError:
        logger.info("Docling 未安装，使用 PyPDF 回退")
    return PyPDFParser()


def _count_blocks(parsed: ParsedDocument) -> dict[str, int]:
    counts: dict[str, int] = {}
    for b in parsed.blocks:
        key = b.type.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _sources_matching_filename(file_name: str) -> list[str]:
    """找出与原始文件名对应的已入库 source（strip 时间戳前缀）"""
    sources = container.vector.list_sources()
    return [s for s in sources if s.endswith(f"_{file_name}")]


def _delete_source(source: str) -> int:
    """删除主索引 + 同步清理问题索引，返回删除数"""
    removed = container.vector.delete(source)
    try:
        q_docs = container.questions.get_all_documents()
        stale = [
            d["id"] for d in q_docs
            if d.get("metadata", {}).get("source", "") == source
        ]
        if stale:
            container.questions.collection.delete(ids=stale)
    except Exception as e:
        logger.warning(f"问题索引清理失败: {e}")
    container.mark_bm25_dirty()
    task_manager.remove_by_source(source)
    return removed


async def _run_pipeline(
    task_id: str,
    file_path: Path,
    file_name: str,
    source: str,
    replace: bool,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    """上传解析管线：解析 → 分块 → 入库 → 假设问题生成（后台任务执行）"""
    tm = task_manager
    try:
        # ── 1. 解析（Docling 优先）──
        tm.update_task(task_id, status=TaskStatus.PARSING, progress=10,
                       stage_text="版面解析中（Docling / PyPDF）…")
        parser = _get_parser()
        parsed: ParsedDocument = parser.parse(str(file_path))
        tm.update_task(task_id, progress=40,
                       pages=parsed.metadata.get("pages", 0),
                       blocks=_count_blocks(parsed))

        # ── 2. 分块 ──
        tm.update_task(task_id, status=TaskStatus.CHUNKING, progress=45,
                       stage_text="智能分块中…")
        indexer = IndexerService()
        chunks = indexer.load_chunks_from_text(
            parsed.markdown,
            source=source,
            chunk_size=chunk_size or settings.chunk_size,
            chunk_overlap=chunk_overlap or settings.chunk_overlap,
        )
        doc_dicts = [
            {
                "chunk_id": str(uuid.uuid4()),
                "content": c.page_content,
                "metadata": c.metadata,
            }
            for c in chunks
        ]

        # ── 3. 同名替换：先删旧 source ──
        if replace:
            for old in _sources_matching_filename(file_name):
                _delete_source(old)
                logger.info(f"替换模式：已删除旧来源 {old}")

        # ── 4. 入库 ──
        tm.update_task(task_id, status=TaskStatus.INDEXING, progress=55,
                       stage_text="向量化入库中…")
        container.vector.add_documents(doc_dicts)
        container.mark_bm25_dirty()  # BM25 下次检索前惰性重建
        total_chunks = len(doc_dicts)
        tm.update_task(task_id, progress=60, chunks=total_chunks)

        # ── 5. 假设问题生成（真实 LLM 模式；失败不阻断，文档已可检索）──
        if not settings.mock_llm:
            tm.update_task(task_id, status=TaskStatus.QUESTIONS, progress=62,
                           stage_text="生成假设问题中…")

            async def on_progress(done: int, total: int) -> None:
                pct = 62 + int(done / max(total, 1) * 33)
                tm.update_task(task_id, progress=min(pct, 95),
                               stage_text=f"生成假设问题 {done}/{total} 批…")

            try:
                from services.retrieval.hypothesis import build_question_documents
                await build_question_documents(
                    container.llm, doc_dicts, container.questions,
                    skip_existing=True, on_progress=on_progress,
                )
            except Exception as e:
                logger.exception("问题生成失败（文档已可检索）")
                tm.update_task(task_id, progress=95,
                               stage_text="问题生成失败，文档已可检索")

        tm.update_task(task_id, status=TaskStatus.DONE, progress=100,
                       stage_text="入库完成")
        logger.info(f"上传管线完成: {file_name} ({total_chunks} chunks)")
    except Exception as e:
        logger.exception("上传管线失败")
        tm.update_task(task_id, status=TaskStatus.FAILED, error=str(e),
                       stage_text="解析失败")


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
    replace: bool = Form(False),
    chunk_size: int = Form(0),
    chunk_overlap: int = Form(0),
):
    """上传文档 → 创建任务 → 后台解析入库（立即返回 task_id）"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")

    ext = Path(file.filename).suffix.lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail=f"暂不支持 {ext} 格式，仅支持 PDF")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{int(time.time())}_{file.filename}"
    file_path = UPLOAD_DIR / safe_name

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="上传文件为空")

    with open(file_path, "wb") as f:
        f.write(contents)

    task_id = task_manager.create_task(file_name=file.filename, source=safe_name)
    background_tasks.add_task(
        _run_pipeline, task_id, file_path, file.filename,
        safe_name, replace, chunk_size, chunk_overlap,
    )
    return JSONResponse(content={"task_id": task_id, "file_name": file.filename})


@router.get("/upload/tasks")
async def list_upload_tasks():
    """最近上传任务列表（前端挂载时恢复轮询）"""
    return {"tasks": [_task_dict(t) for t in task_manager.list_tasks()]}


@router.get("/upload/tasks/{task_id}")
async def get_upload_task(task_id: str):
    """单个任务状态"""
    task = task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _task_dict(task)


def _task_dict(t) -> dict:
    """UploadTask → dict（供 JSON 序列化）"""
    return {
        "task_id": t.task_id,
        "file_name": t.file_name,
        "source": t.source,
        "status": t.status.value,
        "progress": t.progress,
        "stage_text": t.stage_text,
        "error": t.error,
        "pages": t.pages,
        "blocks": t.blocks,
        "chunks": t.chunks,
        "created_at": t.created_at,
        "finished_at": t.finished_at,
    }


@router.get("/documents")
async def list_documents():
    """列出已入库文档源（含切片/问题数/最近任务状态）"""
    docs = []
    try:
        sources = set(container.vector.list_sources())
    except NotImplementedError:
        sources = set()
    for source in sorted(sources):
        latest = task_manager.latest_by_source(source)
        docs.append({
            "source": source,
            "chunks": container.vector.count_by_source(source),
            "questions": container.questions.count_by_source(source),
            "pages": latest.pages if latest else 0,
            "status": latest.status.value if latest else "done",
            "created_at": latest.created_at if latest else 0,
        })
    return {"documents": docs, "count": len(docs)}


@router.delete("/documents/{source:path}")
async def delete_document(source: str):
    """按来源删除文档及其所有 chunk（含对应假设性问题与任务记录）"""
    if not source:
        raise HTTPException(status_code=400, detail="来源为空")
    removed = _delete_source(source)
    return {"removed": removed, "source": source}
```

- [ ] **Step 6: 实现 di.py 的 mark_bm25_dirty**

Modify `Backend/src/core/di.py`，在 `health_report` 之前加：

```python
    def mark_bm25_dirty(self) -> None:
        """上传/删除文档后标记 BM25 过期（下次检索前重建）"""
        try:
            if self._retriever is not None:
                self._retriever.bm25.mark_dirty()
        except Exception as e:
            logger.warning(f"mark_bm25_dirty 失败: {e}")
```

- [ ] **Step 7: 后端冒烟验证（手动，需后端运行中）**

1) 确保后端运行：`cd E:/projects/DocuMind/Backend && uv run python src\main.py`（如已在运行则跳过）。
2) 用一个测试 PDF（如 `E:/桌面/软创赛/datasets/青铜器/complete_DATASET` 下任一 PDF，或手动构造一个 1 页 PDF）执行：

```bash
curl -s -X POST http://127.0.0.1:5172/api/upload -F "file=@<某.pdf>" -F "chunk_size=300"
# 预期：{"task_id":"<12位hex>","file_name":"<名>.pdf"}，2 秒内返回（不等解析）
```

3) 轮询任务（把 `<task_id>` 换成上一步返回值）：

```bash
curl -s http://127.0.0.1:5172/api/upload/tasks/<task_id>
# 预期：status 从 parsing/chunking/indexing/questions 流转到 done，progress 递增到 100
```

4) 验证文档列表带统计：

```bash
curl -s http://127.0.0.1:5172/api/upload/tasks | head -c 600
curl -s http://127.0.0.1:5172/api/documents | head -c 600
# 预期：documents 含 source/chunks/questions/pages/status 字段
```

5) 删除验证（用上一步返回的 source，注意 URL 编码中文/空格）：

```bash
curl -s -X DELETE "http://127.0.0.1:5172/api/documents/<source>"
# 预期：{"removed": N}
```

6) 清理：验证完删除测试文档，避免污染知识库。

---

### Task A7: 前端 upload API 与 LibraryView 重设计

**Files:**
- Modify: `Frontend/src/api/upload.ts`
- Rewrite: `Frontend/src/views/LibraryView.vue`

**Interfaces:**
- Consumes: 后端 `POST /api/upload`（返回 task_id）、`GET /api/upload/tasks[/{id}]`、`GET /api/documents`（增强字段）。
- Produces:
  ```ts
  export interface UploadTask { task_id, file_name, source, status, progress, stage_text, error, pages, blocks, chunks, created_at, finished_at }
  export interface DocInfo { source, chunks, questions, pages, status, created_at }
  export function uploadDocument(file: File, opts?: { replace?: boolean; chunkSize?: number; overlap?: number }): Promise<{ task_id: string; file_name: string }>
  export function listUploadTasks(): Promise<{ tasks: UploadTask[] }>
  ```

- [ ] **Step 1: 修改 upload.ts（完整重写）**

Modify `Frontend/src/api/upload.ts`：

```ts
import apiClient from './client'

export interface UploadTask {
  task_id: string
  file_name: string
  source: string
  status: 'queued' | 'parsing' | 'chunking' | 'indexing' | 'questions' | 'done' | 'failed'
  progress: number
  stage_text: string
  error: string
  pages: number
  blocks: Record<string, number>
  chunks: number
  created_at: number
  finished_at: number
}

export interface DocInfo {
  source: string
  chunks: number
  questions: number
  pages: number
  status: string
  created_at: number
}

/** 上传 PDF → 创建解析任务，立即返回 task_id（进度走任务轮询） */
export async function uploadDocument(
  file: File,
  opts?: { replace?: boolean; chunkSize?: number; overlap?: number },
): Promise<{ task_id: string; file_name: string }> {
  const formData = new FormData()
  formData.append('file', file)
  if (opts?.replace) formData.append('replace', 'true')
  if (opts?.chunkSize) formData.append('chunk_size', String(opts.chunkSize))
  if (opts?.overlap) formData.append('chunk_overlap', String(opts.overlap))
  const response = await apiClient.post<{ task_id: string; file_name: string }>(
    '/api/upload', formData,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60000 },
  )
  return response.data
}

/** 最近上传任务列表（挂载时恢复轮询） */
export async function listUploadTasks(): Promise<{ tasks: UploadTask[] }> {
  const response = await apiClient.get<{ tasks: UploadTask[] }>('/api/upload/tasks')
  return response.data
}

/** 单个任务状态 */
export async function getUploadTask(taskId: string): Promise<UploadTask> {
  const response = await apiClient.get<UploadTask>(`/api/upload/tasks/${taskId}`)
  return response.data
}

/** 列出已入库文档（含切片/问题数） */
export async function listDocuments(): Promise<{ documents: DocInfo[]; count: number }> {
  const response = await apiClient.get<{ documents: DocInfo[]; count: number }>('/api/documents')
  return response.data
}

/** 删除文档来源（chunks + 问题 + 任务记录） */
export async function deleteDocument(source: string): Promise<{ removed: number }> {
  const response = await apiClient.delete(`/api/documents/${encodeURIComponent(source)}`)
  return response.data
}
```

- [ ] **Step 2: 重写 LibraryView.vue（完整文件）**

Rewrite `Frontend/src/views/LibraryView.vue`（下述为完整文件）：

```vue
<template>
  <div class="library-view">
    <!-- 页头：入库流水线叙事 -->
    <header class="lib-header">
      <div class="lib-title-wrap">
        <h2>知识库 · 入库流水线</h2>
        <p>上传 → 版面解析 → 智能分块 → 向量化 → 假设问题生成 → 可检索</p>
      </div>
      <div class="lib-meta">
        <span class="meta-dot"></span>
        已入库 {{ documents.length }} 个来源 · {{ statsText }}
      </div>
    </header>

    <!-- 上传区 -->
    <section class="lib-upload">
      <el-upload
        ref="uploadRef"
        class="upload-area"
        drag
        multiple
        :auto-upload="false"
        :show-file-list="false"
        :on-change="handleFilesChange"
        accept=".pdf"
      >
        <div class="upload-content">
          <el-icon :size="44" color="#c9a96e"><UploadFilled /></el-icon>
          <p>拖拽 PDF 文档到此处（可多选）</p>
          <span>Docling 版面分析 · 表格识别 · 公式 OCR，入库后立即可问答</span>
        </div>
      </el-upload>

      <!-- 分段策略（高级选项） -->
      <div class="chunk-options">
        <el-collapse>
          <el-collapse-item title="分段策略（高级）">
            <div class="chunk-op-row">
              <span>分段大小</span>
              <el-slider v-model="chunkSize" :min="200" :max="1000" :step="100" show-input />
            </div>
            <div class="chunk-op-row">
              <span>分段重叠</span>
              <el-slider v-model="chunkOverlap" :min="0" :max="200" :step="10" show-input />
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>

      <!-- 任务队列：每文件一卡，实时状态 -->
      <div v-if="queue.length" class="task-queue">
        <div v-for="t in queue" :key="t.task_id" class="task-card" :class="'task-' + t.status">
          <div class="task-head">
            <span class="task-name" :title="t.file_name">{{ t.file_name }}</span>
            <span class="task-badge" :class="'badge-' + t.status">{{ statusLabel(t.status) }}</span>
          </div>
          <div v-if="t.status !== 'done' && t.status !== 'failed'" class="task-progress">
            <el-progress :percentage="t.progress" :stroke-width="6" :color="'#c9a96e'" />
            <p class="task-stage">{{ t.stage_text }}</p>
          </div>
          <p v-if="t.status === 'failed'" class="task-error">{{ t.error || '解析失败' }}</p>
          <div v-if="t.status === 'done'" class="task-done-line">
            {{ t.chunks }} 个切片 · {{ t.pages }} 页
            <router-link to="/DeepQAView" class="task-ask-link">去提问 →</router-link>
          </div>
        </div>
      </div>
    </section>

    <!-- 文档列表 -->
    <section class="lib-docs">
      <div class="docs-head">
        <h3>已入库文档</h3>
        <el-button size="small" text type="primary" @click="refreshAll">刷新</el-button>
      </div>

      <div v-if="documents.length === 0" class="docs-empty">
        <p>知识库为空，上传第一份文档开始建库</p>
      </div>

      <div v-else class="docs-table">
        <div class="doc-row doc-row--head">
          <span class="col-name">来源</span>
          <span class="col-chunks">切片</span>
          <span class="col-questions">问题</span>
          <span class="col-status">状态</span>
          <span class="col-action">操作</span>
        </div>
        <div v-for="doc in documents" :key="doc.source" class="doc-row">
          <span class="col-name" :title="doc.source">{{ displayName(doc.source) }}</span>
          <span class="col-chunks">{{ doc.chunks }}</span>
          <span class="col-questions">{{ doc.questions }}</span>
          <span class="col-status"><span class="status-dot" :class="'status-' + doc.status" />{{ docStatusLabel(doc.status) }}</span>
          <span class="col-action">
            <el-button size="small" text type="danger" :loading="deleting === doc.source" @click="handleDelete(doc)">删除</el-button>
          </span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { uploadDocument, listUploadTasks, listDocuments, deleteDocument } from '@/api/upload'
import type { UploadTask, DocInfo } from '@/api/upload'
import { fetchStats } from '@/api/stats'

const queue = ref<UploadTask[]>([])
const documents = ref<DocInfo[]>([])
const deleting = ref('')
const statsText = ref('')
const chunkSize = ref(500)
const chunkOverlap = ref(50)

const ACTIVE_STATUSES = new Set(['queued', 'parsing', 'chunking', 'indexing', 'questions'])
const hasActive = computed(() => queue.value.some(t => ACTIVE_STATUSES.has(t.status)))

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中', parsing: '版面解析', chunking: '智能分块',
  indexing: '向量入库', questions: '生成问题', done: '完成', failed: '失败',
}
function statusLabel(s: string): string { return STATUS_LABELS[s] || s }
function docStatusLabel(s: string): string {
  if (s === 'done') return '已就绪'
  if (s === 'failed') return '失败'
  return STATUS_LABELS[s] || s
}
function displayName(source: string): string {
  // 剥掉时间戳前缀：1234567890_xxx.pdf → xxx.pdf
  return source.replace(/^\d+_/, '')
}

// ── 轮询：有活跃任务时每 2s 拉一次；挂载时恢复 ──
let pollTimer: ReturnType<typeof setInterval> | null = null

async function refreshTasks() {
  try {
    const res = await listUploadTasks()
    queue.value = res.tasks
    if (!hasActive.value && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  } catch { /* 后端不可用时静默，手动刷新兜底 */ }
}

async function refreshDocs() {
  try {
    const res = await listDocuments()
    documents.value = res.documents
    const stats = await fetchStats()
    statsText.value = `共 ${stats.chunks} 切片 · ${stats.questions} 假设问题`
  } catch {
    statsText.value = ''
  }
}

async function refreshAll() {
  await Promise.all([refreshTasks(), refreshDocs()])
  if (hasActive.value && !pollTimer) {
    pollTimer = setInterval(refreshTasks, 2000)
  }
}

onMounted(refreshAll)
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })

// ── 上传：多文件逐个提交（同名提示替换）──
async function handleFilesChange(file: any) {
  const raw = file.raw as File
  if (!raw) return

  const existing = documents.value.find(d => displayName(d.source) === raw.name)
  let replace = false
  if (existing) {
    try {
      await ElMessageBox.confirm(
        `「${raw.name}」已入库（${existing.chunks} 切片），重新上传将替换旧版本？`,
        '同名文档',
        { confirmButtonText: '替换', cancelButtonText: '取消', type: 'warning' },
      )
      replace = true
    } catch {
      return
    }
  }

  try {
    const res = await uploadDocument(raw, {
      replace,
      chunkSize: chunkSize.value,
      overlap: chunkOverlap.value,
    })
    // 立即入队显示状态卡
    queue.value.unshift({
      task_id: res.task_id, file_name: res.file_name, source: '',
      status: 'queued', progress: 0, stage_text: '排队中…', error: '',
      pages: 0, blocks: {}, chunks: 0, created_at: Date.now() / 1000, finished_at: 0,
    })
    if (!pollTimer) pollTimer = setInterval(refreshTasks, 2000)
    ElMessage.success(`已提交「${raw.name}」解析任务`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message || '提交失败')
  }
}

// ── 删除 ──
async function handleDelete(doc: DocInfo) {
  try {
    await ElMessageBox.confirm(
      `删除「${displayName(doc.source)}」的全部切片与假设问题？此操作不可恢复。`,
      '删除文档',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  deleting.value = doc.source
  try {
    const res = await deleteDocument(doc.source)
    documents.value = documents.value.filter(d => d.source !== doc.source)
    ElMessage.success(`已删除 ${res.removed} 个切片`)
    await refreshAll()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  } finally {
    deleting.value = ''
  }
}
</script>

<style scoped lang="less">
.library-view { height: 100%; overflow-y: auto; padding: 28px 32px 48px; }

.lib-header {
  display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 24px;

  .lib-title-wrap {
    h2 {
      font-family: 'STSong', 'SimSun', serif; font-size: 26px; font-weight: 900;
      color: var(--color-primary); letter-spacing: 4px; margin-bottom: 6px;
    }
    p { font-size: 13px; color: var(--color-ink); opacity: 0.5; letter-spacing: 2px; }
  }

  .lib-meta {
    font-size: 12px; color: var(--color-primary); display: flex; align-items: center; gap: 6px;

    .meta-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-gold); }
  }
}

.lib-upload { max-width: 760px; margin-bottom: 32px; }

.upload-content {
  padding: 34px 20px; text-align: center;
  p { margin-top: 12px; font-size: 15px; color: var(--color-ink); font-weight: 600; }
  span { font-size: 12px; color: #999; line-height: 1.8; }
}

:deep(.el-upload-dragger) {
  background: var(--color-card);
  border: 2px dashed rgba(201, 169, 110, 0.45);
  border-radius: 10px;
  transition: border-color 0.3s;
  &:hover { border-color: var(--color-gold); }
}

.chunk-options {
  margin-top: 12px;
  :deep(.el-collapse-item__header) { font-size: 12px; color: var(--color-primary); }
  .chunk-op-row {
    display: flex; align-items: center; gap: 16px; padding: 4px 0;
    span { font-size: 12px; color: #8b7355; width: 70px; flex-shrink: 0; }
    :deep(.el-slider) { flex: 1; }
  }
}

// ── 任务队列卡片 ──
.task-queue { margin-top: 14px; display: flex; flex-direction: column; gap: 10px; }

.task-card {
  padding: 12px 16px;
  background: var(--color-card);
  border: 1px solid rgba(201, 169, 110, 0.4);
  border-radius: 10px;
  border-left-width: 3px;

  &.task-done { border-left-color: #67c23a; }
  &.task-failed { border-left-color: #f56c6c; }
  &.task-parsing, &.task-chunking, &.task-indexing, &.task-questions, &.task-queued {
    border-left-color: var(--color-gold);
  }
}

.task-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }

.task-name {
  font-size: 13px; font-weight: 600; color: var(--color-ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.task-badge {
  font-size: 11px; font-weight: 600; padding: 2px 10px; border-radius: 10px; flex-shrink: 0;

  &.badge-done { color: #529b2e; background: rgba(103, 194, 58, 0.12); }
  &.badge-failed { color: #f56c6c; background: rgba(245, 108, 108, 0.12); }
  &.badge-queued, &.badge-parsing, &.badge-chunking, &.badge-indexing, &.badge-questions {
    color: var(--color-primary); background: rgba(201, 169, 110, 0.15);
  }
}

.task-stage { margin: 4px 0 0; font-size: 12px; color: #999; }
.task-error { margin: 4px 0 0; font-size: 12px; color: #f56c6c; }
.task-done-line {
  margin-top: 4px; font-size: 12px; color: #8b7355;
  display: flex; align-items: center; gap: 10px;
}
.task-ask-link { color: var(--color-accent); font-weight: 600; text-decoration: none; }

// ── 文档列表 ──
.lib-docs { max-width: 760px; }

.docs-head {
  display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px;
  h3 {
    font-family: 'STSong', 'SimSun', serif; font-size: 16px; font-weight: 700;
    color: var(--color-primary); letter-spacing: 2px;
  }
}

.docs-empty {
  padding: 36px 0; text-align: center;
  border: 1px dashed rgba(201, 169, 110, 0.35); border-radius: 8px;
  color: #b0a08a; font-size: 13px; letter-spacing: 1px;
}

.docs-table {
  border: 1px solid rgba(201, 169, 110, 0.3); border-radius: 8px;
  overflow: hidden; background: var(--color-card);
}

.doc-row {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(201, 169, 110, 0.12);
  font-size: 13px;

  &:last-child { border-bottom: none; }
  &--head {
    background: rgba(201, 169, 110, 0.1); font-size: 12px;
    font-weight: 600; color: var(--color-primary);
  }
  &:hover:not(.doc-row--head) { background: rgba(201, 169, 110, 0.04); }
}

.col-name {
  flex: 1; min-width: 0; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; color: var(--color-ink);
}
.col-chunks, .col-questions { width: 56px; text-align: center; color: #999; font-size: 12px; }
.col-status { width: 80px; text-align: center; font-size: 12px; color: var(--color-ink); }
.col-action { width: 60px; text-align: right; }

.status-dot {
  display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 5px;

  &.status-done { background: #67c23a; }
  &.status-failed { background: #f56c6c; }
  &.status-parsing, &.status-chunking, &.status-indexing, &.status-questions, &.status-queued {
    background: var(--color-gold);
  }
}
</style>
```

- [ ] **Step 3: 构建验证**

Run: `cd E:/projects/DocuMind/Frontend && npm run build`
Expected: `vue-tsc --noEmit` 通过 + `✓ built in <30s`。

- [ ] **Step 4: 手动冒烟（浏览器）**

1) `npm run dev` 打开 `http://localhost:5173/#/Library`（路由名以实际侧边栏为准）。
2) 上传一个 PDF → 立即出现任务卡（状态从 排队中→版面解析→智能分块→向量入库→生成问题→完成），进度条真实增长。
3) 完成后任务卡显示"X 个切片 · Y 页 · 去提问 →"；点"去提问"进入问答页。
4) 文档列表显示切片数/问题数/状态徽章；删除有确认框。
5) 刷新页面 → 已完成任务卡仍在（从 /api/upload/tasks 恢复）；进行中任务继续轮询。
6) 再次上传同名文件 → 弹"将替换旧版本"确认框。
7) 修改分段大小后再上传 → 后端按新 chunk_size 分块（文档列表切片数变化）。

---

### Task B2: DeepSeek provider usage 采集 + timeout

**Files:**
- Modify: `Backend/src/providers/llm/deepseek.py`
- Test: `Backend/tests/test_deepseek_usage.py`

**Interfaces:**
- Produces:
  ```python
  chat(messages, *, temperature=0.7, max_tokens=4096, usage_tracker: dict[str, int] | None = None) -> str
  chat_stream(messages, *, temperature=0.7, max_tokens=4096, usage_tracker: dict[str, int] | None = None) -> AsyncGenerator[str, None]
  ```
  `usage_tracker` 非空时填充 `{"prompt_tokens": int, "completion_tokens": int}`；客户端 timeout 为 `httpx.Timeout(connect=10, read=300, write=60, pool=30)`。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_deepseek_usage.py`:

```python
"""DeepSeek provider usage 采集与 timeout 测试（mock OpenAI 客户端）"""
from types import SimpleNamespace

import pytest

from providers.llm.deepseek import DeepSeekProvider


class _FakeStream:
    """模拟 OpenAI 流式 chunk：末尾 chunk 携带 usage"""

    def __init__(self):
        self._chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你"))], usage=None),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好"))], usage=None),
            SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)),
        ]

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk


async def test_chat_stream_usage_collected(monkeypatch):
    provider = DeepSeekProvider(api_key="k", base_url="http://x")

    class FakeCreate:
        async def __call__(self, **kwargs):
            return _FakeStream()

    monkeypatch.setattr(provider.client.chat.completions, "create", FakeCreate())

    usage: dict = {}
    text = ""
    async for chunk in provider.chat_stream([{"role": "user", "content": "hi"}], usage_tracker=usage):
        text += chunk
    assert text == "你好"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5}


async def test_chat_usage_collected(monkeypatch):
    provider = DeepSeekProvider(api_key="k", base_url="http://x")

    class FakeCreate:
        async def __call__(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="答"))],
                usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
            )

    monkeypatch.setattr(provider.client.chat.completions, "create", FakeCreate())

    usage: dict = {}
    text = await provider.chat([{"role": "user", "content": "hi"}], usage_tracker=usage)
    assert text == "答"
    assert usage == {"prompt_tokens": 7, "completion_tokens": 3}


def test_client_has_timeout():
    provider = DeepSeekProvider(api_key="k", base_url="http://x")
    assert provider.client._client._timeout.connect == 10.0
    assert provider.client._client._timeout.read == 300.0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_deepseek_usage.py -v`
Expected: FAIL（usage_tracker 参数不存在 / timeout 断言失败）。

- [ ] **Step 3: 实现**

Rewrite `Backend/src/providers/llm/deepseek.py`：

```python
"""LLM Provider — DeepSeek V4 Flash (OpenAI 兼容接口)

容错与可观测性：
  - 显式超时（connect 10s / read 300s / write 60s / pool 30s）
  - usage_tracker 采集 token 用量（流式末尾 chunk 携带 usage）
"""
from collections.abc import AsyncGenerator

import httpx
from openai import AsyncOpenAI

from interfaces.llm import LLMProvider


class DeepSeekProvider(LLMProvider):
    """DeepSeek V4 Flash 客户端"""

    def __init__(self, api_key: str, base_url: str, model: str = "deepseek-v4-flash"):
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=30.0),
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        usage_tracker: dict[str, int] | None = None,
    ) -> str:
        completion = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        if usage_tracker is not None and completion.usage is not None:
            usage_tracker["prompt_tokens"] = completion.usage.prompt_tokens
            usage_tracker["completion_tokens"] = completion.usage.completion_tokens
        return completion.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        usage_tracker: dict[str, int] | None = None,
    ) -> AsyncGenerator[str, None]:
        completion = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in completion:
            # DeepSeek 流式 usage 在最后一个 chunk（choices 为空）
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if usage_tracker is not None:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    usage_tracker["prompt_tokens"] = usage.prompt_tokens
                    usage_tracker["completion_tokens"] = usage.completion_tokens

    def build_messages(
        self, system_prompt: str, user_prompt: str
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_deepseek_usage.py -v`
Expected: 3 passed。

---

### Task B3: orchestrator 组装 trace + trace 事件 + jsonl 日志

**Files:**
- Modify: `Backend/src/models/response.py`（StreamEventType 加 TRACE）
- Modify: `Backend/src/services/agent/orchestrator.py`（quick_answer 组装 trace）
- Test: `Backend/tests/test_orchestrator_trace.py`

**Interfaces:**
- Consumes: `core.tracing`（Task B1）、`RetrievalTrace` 注入 `retrieve(trace=...)`（Task A5）、`usage_tracker`（Task B2）。
- Produces: 流式事件末尾新增 `StreamEventType.TRACE`（data = trace dict）；`query_trace.jsonl` 追加记录。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_orchestrator_trace.py`:

```python
"""quick_answer trace 事件与 jsonl 日志测试（fake LLM + 无检索器）"""
import json
from pathlib import Path

import pytest

from core.tracing import RetrievalTrace
from models.response import StreamEventType
from services.agent.orchestrator import ResearchOrchestrator


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

    # 事件顺序：content 之后、coordinator done 之前
    kinds = [ev.type for ev in events]
    assert kinds.index(StreamEventType.TRACE) < kinds.index(StreamEventType.AGENT_STEP)

    # jsonl 落盘
    log_file = Path(tmp_path) / "query_trace.jsonl"
    assert log_file.exists()
    payload = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[0])
    assert payload["trace_id"] == data["trace_id"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_orchestrator_trace.py -v`
Expected: FAIL（StreamEventType.TRACE 不存在 / 无 trace 事件）。

- [ ] **Step 3: 实现**

1) Modify `Backend/src/models/response.py`，在 StreamEventType 枚举中 `ERROR` 之前加：

```python
    TRACE = "trace"
```

2) Modify `Backend/src/services/agent/orchestrator.py`：

a) 顶部 import 增加：

```python
from core.tracing import RetrievalTrace, new_trace_id, write_trace_jsonl
```

b) `quick_answer` 中「3. 混合检索」块改为（保留同步 VectorStore 兼容分支，仅异步分支注入 trace；同步分支无 trace）：

```python
        # 3. 混合检索（三路召回 + RRF 融合 + 图谱锚定）
        # 多轮对话时先做查询改写（代词消解为独立检索词，LLM 失败回退拼接）
        trace = RetrievalTrace(trace_id=new_trace_id(), query=query)
        t_start = time.time()
        retrieval_query = await self._rewrite_query(query, history)
        trace.rewritten_query = retrieval_query

        retrieved_docs: list[dict[str, Any]] = []
        if self.retriever:
            try:
                if hasattr(self.retriever, "retrieve") and not asyncio.iscoroutinefunction(
                    self.retriever.retrieve
                ):
                    # 同步 VectorStore 兼容
                    raw_docs = self.retriever.retrieve(retrieval_query, top_k=5)
                    retrieved_docs = [
                        {
                            "content": d.get("content", ""),
                            "source": d.get("source", ""),
                            "paths": [],
                        }
                        for d in raw_docs
                    ]
                else:
                    retrieved_docs = await self.retriever.retrieve(
                        retrieval_query, trace=trace
                    )
            except Exception as e:
                logger.warning(f"快速问答混合检索失败: {e}")
```

c) CRAG 分支（`settings.crag_enabled ... == "poor"` 为真时）在 `rewritten = await self._rewrite_query(...)` 之后加：

```python
            trace.crag_triggered = True
```

d) 流式生成块（「5. 流式输出」的 try）改为带 usage_tracker 与重试一次：

```python
        usage: dict[str, int] = {}
        try:
            async for chunk in self.llm.chat_stream(messages, usage_tracker=usage):
                yield StreamEvent(
                    type=StreamEventType.CONTENT,
                    data=chunk,
                    timestamp=time.time(),
                )
                await asyncio.sleep(0)
        except Exception as e:
            logger.warning(f"LLM 流式首次失败，重试一次: {e}")
            try:
                async for chunk in self.llm.chat_stream(messages, usage_tracker=usage):
                    yield StreamEvent(
                        type=StreamEventType.CONTENT,
                        data=chunk,
                        timestamp=time.time(),
                    )
                    await asyncio.sleep(0)
            except Exception as e2:
                logger.exception("LLM 流式调用异常（重试后仍失败）")
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    data=f"LLM 调用失败: {str(e2)}",
                    timestamp=time.time(),
                )
                yield StreamEvent(
                    type=StreamEventType.CONTENT,
                    data="（知识库检索已完成，但模型服务暂时不可用，请稍后重试）",
                    timestamp=time.time(),
                )
```

e) 在「coordinator 完成」事件（`yield self._make_agent_step(AgentRole.COORDINATOR, "done", "快速问答完成")`）之前插入：

```python
        # 检索诊断事件（前端"本轮检索诊断"面板数据源）
        trace.total_ms = (time.time() - t_start) * 1000
        trace.llm_usage = usage
        yield StreamEvent(
            type=StreamEventType.TRACE,
            data=trace.to_dict(),
            timestamp=time.time(),
        )
        await asyncio.sleep(0)
        write_trace_jsonl(trace)
```

3) 注意：`import time` 在文件顶部已存在（第 4 行），`_structured_answer` 的流式块也按 d) 同样处理（重试一次 + 兜底），其内部无 trace（图谱分支）。

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_orchestrator_trace.py -v`
Expected: 1 passed。

- [ ] **Step 5: 全量回归**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/ -v --ignore=tests/test_api.py  # test_api.py 为预存真实API集成测试（需真实key），全量回归排除`
Expected: 全部通过（冒烟 2 + task_manager 5 + hypothesis 2 + bm25 3 + hybrid 3 + tracing 4 + chroma 2 + deepseek 3 + orchestrator 1 = 25 用例）。

---

### Task B4: 前端 trace 事件接线 + 诊断面板

**Files:**
- Modify: `Frontend/src/types/api.ts`
- Modify: `Frontend/src/types/domain.ts`
- Modify: `Frontend/src/stores/chat.ts`
- Modify: `Frontend/src/components/ChatMessageItem.vue`

**Interfaces:**
- Consumes: 后端 `trace` 事件（Task B3）。
- Produces: `ChatMessage.trace?: RetrievalTrace`；消息下方"本轮检索诊断"折叠面板。

- [ ] **Step 1: 类型 + store 接线**

1) Modify `Frontend/src/types/api.ts`，StreamEventType 枚举加：

```ts
  TRACE = 'trace',
```

并在文件末尾（KnowledgeSearchRequest 之后）加：

```ts
/** 检索诊断（trace 流事件）——一次问答的检索过程 */
export interface RetrievalTrace {
  trace_id: string
  query: string
  rewritten_query: string
  crag_triggered: boolean
  paths: Record<string, { hits: number; took_ms: number }>
  path_stats: Record<string, number>
  total_ms: number
  llm_usage: { prompt_tokens?: number; completion_tokens?: number }
}
```

2) Modify `Frontend/src/types/domain.ts`，ChatMessage 接口加：

```ts
  /** 检索诊断（trace 流事件） */
  trace?: import('./api').RetrievalTrace
```

3) Modify `Frontend/src/stores/chat.ts`，switch 内 `case StreamEventType.ZERO_RESULT: break` 前加：

```ts
          case StreamEventType.TRACE: {
            const trace = event.data as import('@/types/api').RetrievalTrace
            const last = session!.messages[session!.messages.length - 1]
            if (last && trace) last.trace = trace
            break
          }
```

- [ ] **Step 2: 诊断面板（ChatMessageItem.vue）**

Modify `Frontend/src/components/ChatMessageItem.vue`：

1) Template：在 `<!-- Agent steps -->` 折叠块之前插入：

```vue
      <!-- 检索诊断（trace 事件） -->
      <el-collapse v-if="message.trace" class="trace-collapse">
        <el-collapse-item>
          <template #title>
            <span class="steps-title">🔬 本轮检索诊断</span>
          </template>
          <div class="trace-body">
            <div v-for="(p, name) in message.trace.paths" :key="name" class="trace-row">
              <span class="trace-name">{{ PATH_LABELS[name] || name }}</span>
              <div class="trace-bar">
                <div class="trace-fill" :style="{ width: barWidth(name) }" />
              </div>
              <span class="trace-nums">{{ p.hits }} 条 · {{ p.took_ms }}ms</span>
            </div>
            <div class="trace-meta">
              <span>改写：{{ message.trace.rewritten_query || '无' }}</span>
              <span>CRAG：{{ message.trace.crag_triggered ? '触发' : '未触发' }}</span>
              <span>Token：{{ tokenText }}</span>
              <span>总耗时：{{ message.trace.total_ms }}ms</span>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
```

2) Script 增加（`const props = defineProps...` 之后）：

```ts
/** 检索路径中文名 */
const PATH_LABELS: Record<string, string> = {
  semantic: '语义',
  question: '假设问题',
  bm25: '关键词',
  graph: '图谱锚定',
  entity: '实体锚定',
}

const maxHits = computed(() =>
  Math.max(1, ...Object.values(props.message.trace?.paths || {}).map(p => p.hits)),
)

function barWidth(name: string): string {
  const p = props.message.trace?.paths[name]
  if (!p) return '0%'
  return `${Math.max(4, (p.hits / maxHits.value) * 100)}%`
}

const tokenText = computed(() => {
  const u = props.message.trace?.llm_usage
  if (!u) return '—'
  return `${u.prompt_tokens ?? '?'} / ${u.completion_tokens ?? '?'}`
})
```

（`computed` 已从 `vue` 导入？当前文件 `import { ref } from 'vue'`——改为 `import { computed, ref } from 'vue'`。）

3) Style 增加（`</style>` 前）：

```less
.trace-collapse { margin-bottom: 8px; }

.trace-body { padding: 4px 2px; }

.trace-row {
  display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px;
}

.trace-name { width: 56px; color: var(--color-primary); font-weight: 600; flex-shrink: 0; }

.trace-bar {
  flex: 1; height: 8px; background: rgba(201, 169, 110, 0.15);
  border-radius: 4px; overflow: hidden;
}

.trace-fill {
  height: 100%; border-radius: 4px;
  background: linear-gradient(90deg, var(--color-gold), var(--color-accent));
  transition: width 0.3s;
}

.trace-nums { width: 90px; text-align: right; color: #999; flex-shrink: 0; }

.trace-meta {
  display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 8px;
  padding-top: 6px; border-top: 1px dashed rgba(201, 169, 110, 0.4);
  font-size: 11px; color: #8b7355;
}
```

- [ ] **Step 3: 构建验证**

Run: `cd E:/projects/DocuMind/Frontend && npm run build`
Expected: vue-tsc 通过 + `✓ built`。

- [ ] **Step 4: 手动冒烟（浏览器）**

1) 问答页问"叩鼎是什么朝代"（快速模式）。
2) 回答完成后，消息下方出现"🔬 本轮检索诊断"折叠面板；展开显示五路条形（语义/假设问题/关键词/图谱锚定/实体锚定）、各条命中数与耗时、改写前后查询、CRAG 状态、Token 用量、总耗时。
3) 打开 `Backend/src/data/logs/query_trace.jsonl` 确认有对应记录。

---

### Task C1: LLM 非流式调用重试装饰器

**Files:**
- Create: `Backend/src/providers/llm/retry.py`
- Modify: `Backend/src/services/agent/orchestrator.py`（`_rewrite_query`、`_evaluate_retrieval` 应用重试）
- Modify: `Backend/src/services/retrieval/hybrid.py`（`_extract_entity` 应用重试）
- Test: `Backend/tests/test_llm_retry.py`

**Interfaces:**
- Produces:
  ```python
  async def with_llm_retry(fn, *, attempts: int = 3, base_delay: float = 1.0) -> Any
  ```
  对 `fn`（异步可调用）失败时指数退避重试 `attempts` 次，最后一次异常上抛。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_llm_retry.py`:

```python
"""LLM 非流式调用重试测试"""
import asyncio

import pytest

from providers.llm.retry import with_llm_retry


async def test_retry_succeeds_after_failures():
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("boom")
        return "ok"

    result = await with_llm_retry(flaky, attempts=3, base_delay=0.01)
    assert result == "ok"
    assert calls == 3


async def test_retry_exhausted_raises():
    calls = 0

    async def always_fail():
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await with_llm_retry(always_fail, attempts=3, base_delay=0.01)
    assert calls == 3


async def test_first_try_success_no_delay():
    async def ok():
        return "fine"

    assert await with_llm_retry(ok, attempts=3) == "fine"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_llm_retry.py -v`
Expected: FAIL（ModuleNotFoundError: providers.llm.retry）。

- [ ] **Step 3: 实现**

Create `Backend/src/providers/llm/retry.py`:

```python
"""LLM 非流式调用重试——指数退避（幂等调用：改写/评估/实体提取）

流式回答不重试（已消费的 token 无法回退），由 orchestrator 层做
"重试一次 + 兜底回答"。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_llm_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    """执行 fn，失败时指数退避重试（1s, 2s, ...），最后一次异常上抛"""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < attempts - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"LLM 调用失败，{delay}s 后重试 ({attempt + 1}/{attempts}): {e}")
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]
```

- [ ] **Step 4: 应用重试到调用点**

1) Modify `Backend/src/services/agent/orchestrator.py`：

   a) 顶部 import：`from providers.llm.retry import with_llm_retry`
   b) `_rewrite_query` 的 `raw = await self.llm.chat(messages, temperature=0.0, max_tokens=128)` 改为：

```python
            raw = await with_llm_retry(
                lambda: self.llm.chat(
                    messages, temperature=0.0, max_tokens=128
                ),
                attempts=3,
            )
```

   c) `_evaluate_retrieval` 的 `raw = await self.llm.chat(messages, temperature=0.0, max_tokens=128)` 改为：

```python
            raw = await with_llm_retry(
                lambda: self.llm.chat(
                    messages, temperature=0.0, max_tokens=128
                ),
                attempts=3,
            )
```

2) Modify `Backend/src/services/retrieval/hybrid.py`：

   a) 顶部 import：`from providers.llm.retry import with_llm_retry`
   b) `_extract_entity` 的 `raw = await self.llm.chat(messages, temperature=0.0, max_tokens=256)` 改为：

```python
                raw = await with_llm_retry(
                    lambda: self.llm.chat(
                        messages, temperature=0.0, max_tokens=256
                    ),
                    attempts=3,
                )
```

（lambda 捕获 `messages` 变量，作用域正确；`with_llm_retry` 每次重试重新调用 `fn()`。）

- [ ] **Step 5: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_llm_retry.py -v`
Expected: 3 passed。

- [ ] **Step 6: 全量回归**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/ -v --ignore=tests/test_api.py  # test_api.py 为预存真实API集成测试（需真实key），全量回归排除`
Expected: 28 用例全部通过。

---

### Task C2: 流式失败兜底（quick + 结构化）

**Files:**
- Modify: `Backend/src/services/agent/orchestrator.py`
- Test: `Backend/tests/test_orchestrator_fallback.py`

**Interfaces:**
- Consumes: Task B3 已改的流式块（本任务将同一逻辑抽象为私有 helper）。
- Produces: `_chat_stream_with_fallback(messages, usage) -> AsyncGenerator[StreamEvent, None]`——首次失败重试一次，仍失败发 ERROR 事件 + 兜底 CONTENT 事件。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_orchestrator_fallback.py`:

```python
"""流式失败兜底测试——重试一次，仍失败给兜底回答"""
import pytest

from models.response import StreamEventType
from services.agent.orchestrator import ResearchOrchestrator


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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_orchestrator_fallback.py -v`
Expected: 失败（首个断言 stream_calls 计数不符/无兜底内容——取决于 B3 实现细节，若 B3 已实现重试一次则第一个测试可能已过，至少 `test_fallback_content_on_persistent_failure` 应 FAIL）。

- [ ] **Step 3: 实现（抽象 helper）**

Modify `Backend/src/services/agent/orchestrator.py`：

1) 在 `_make_agent_step` 静态方法之后新增：

```python
    async def _chat_stream_with_fallback(
        self,
        messages: list[dict[str, str]],
        usage: dict[str, int],
    ):
        """流式生成：首次失败重试一次，仍失败发 ERROR + 兜底回答

        兜底回答保证前端不白屏（检索来源已在此之前发送，用户仍能看到证据）。
        """
        for attempt in (1, 2):
            try:
                async for chunk in self.llm.chat_stream(messages, usage_tracker=usage):
                    yield StreamEvent(
                        type=StreamEventType.CONTENT,
                        data=chunk,
                        timestamp=time.time(),
                    )
                    await asyncio.sleep(0)
                return
            except Exception as e:
                logger.warning(f"LLM 流式失败（第 {attempt} 次）: {e}")
                if attempt == 1:
                    continue
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data="LLM 调用失败，请稍后重试",
            timestamp=time.time(),
        )
        yield StreamEvent(
            type=StreamEventType.CONTENT,
            data="（知识库检索已完成，但模型服务暂时不可用，请稍后重试）",
            timestamp=time.time(),
        )
```

2) `quick_answer` 的「5. 流式输出」块整体替换为：

```python
        # 5. 流式输出（证据锚定：要求按编号引用知识库；失败自动重试一次 + 兜底）
        usage: dict[str, int] = {}
        async for ev in self._chat_stream_with_fallback(messages, usage):
            yield ev
```

3) `_structured_answer` 的流式块（`messages = self.llm.build_messages(...)` 之后的 try/except）替换为：

```python
        usage: dict[str, int] = {}
        async for ev in self._chat_stream_with_fallback(messages, usage):
            yield ev
```

（`_structured_answer` 内原 `except` 分支删除。）

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_orchestrator_fallback.py -v`
Expected: 2 passed。

- [ ] **Step 5: 全量回归 + 冒烟**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/ -v --ignore=tests/test_api.py  # test_api.py 为预存真实API集成测试（需真实key），全量回归排除`
Expected: 30 用例全部通过。

手动冒烟：后端运行中，问答页问一个问题（正常路径），确认回答正常流出、无 ERROR；临时把 `deepseek_api_key` 改错后重启，再问 → 前端出现"知识库检索已完成，但模型服务暂时不可用"兜底文案，且 sources 证据仍在。验证完恢复 key。

---

## 计划外回归清单（每个任务后执行对应项）

| 任务 | 回归命令 |
|------|---------|
| 所有后端任务 | `cd Backend && uv run pytest tests/ -v --ignore=tests/test_api.py  # test_api.py 为预存真实API集成测试（需真实key），全量回归排除` |
| A7 / B4 | `cd Frontend && npm run build` |
| B3 后 | 问答页真实问一句，确认 NDJSON 流正常（新增 trace 事件不破坏前端解析——前端 store 已有 TRACE 分支） |

## 交付顺序与依赖

```
A1 → A2 → A3 → A4 → B1 → A5 → A6 → A7   (P0-A + B1)
      └───────── B2 → B3 → B4 ─────────┘   (P0-B，B2 独立可先行)
                        └→ C1 → C2          (P0-C)
```

（B1 是 A5 的依赖；B2 无依赖可随时做；C1/C2 依赖 B3 的流式块改造位置，但代码上独立，顺序执行即可。）
