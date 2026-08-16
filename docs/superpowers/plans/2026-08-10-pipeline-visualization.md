# 检索流水线可视化（Pipeline Panel）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 聊天页右侧上部由思维导图改为「检索流水线」——回答生成时五路检索/RRF 融合/生成节点按后端真实时序动态点亮（方案 B 真流式），每路可展开详情。

**Architecture:** 后端 `RetrievalTrace` 挂发射器（`record_path` 处回调）→ orchestrator 用 `asyncio.Queue` 桥接把检索过程事件实时 yield 为 `pipeline` NDJSON 事件（async generator 在 await 期间不能 yield，故检索放后台任务 + 主循环轮询队列）；前端 store 按轮次累加 `pipeline` 数组，新组件 PipelinePanel 状态机渲染。trace 全量事件保留（展开详情数据源）。

**Tech Stack:** Python FastAPI / asyncio / Vue 3 `<script setup lang="ts">` + Less / NDJSON 流式（现有通道零新基建）。

## Global Constraints

- 所有 UI 文本、注释、配置均为中文
- 前端 Less（不用纯 CSS/SCSS）
- 后端测试：pytest + `uv run pytest`（全量 159 passed 基线）
- 前端验证：`pnpm build`（vue-tsc 类型检查）
- 项目非 git 仓库——任务末用「检查点」记录（写 .superpowers/sdd/progress.md）代替 commit
- 不破坏现有事件流：trace/sources/content/error 行为不变

---

### Task 1: RetrievalTrace 发射器（core/tracing.py）

**Files:**
- Modify: `Backend/src/core/tracing.py`（RetrievalTrace 类）
- Test: `Backend/tests/test_trace_emitter.py`（新建）

**Interfaces:**
- Produces: `RetrievalTrace._emitter: Callable[[str, dict], None] | None` 字段；
  `record_path(name, hits, took_ms)` 在记录后调用 `self._emitter("path_done", {"name", "hits", "took_ms"})`（异常吞掉）

- [ ] **Step 1: 写失败测试** `Backend/tests/test_trace_emitter.py`

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd Backend && uv run pytest tests/test_trace_emitter.py -v`
Expected: FAIL（`RetrievalTrace` 无 `_emitter` 属性——AttributeError）

- [ ] **Step 3: 实现**（tracing.py 修改——`from typing import Callable` 加入 import 行）

```python
from typing import Any, Callable

@dataclass
class RetrievalTrace:
    ...
    # pipeline 事件发射器（方案 B）——record_path 时回调，orchestrator 经
    # asyncio.Queue 桥接转成实时 NDJSON 事件；异常吞掉不影响主链路
    _emitter: Callable[[str, dict], None] | None = None

    def record_path(self, name: str, hits: int, took_ms: float) -> None:
        self.paths[name] = PathTrace(hits=hits, took_ms=round(took_ms, 1))
        if self._emitter is not None:
            try:
                self._emitter("path_done", {
                    "name": name,
                    "hits": hits,
                    "took_ms": round(took_ms, 1),
                })
            except Exception:
                pass
```

- [ ] **Step 4: 运行确认通过**

Run: `cd Backend && uv run pytest tests/test_trace_emitter.py -v`
Expected: PASS 3 passed

- [ ] **Step 5: 检查点**——progress.md 追加一行

---

### Task 2: models/response.py 加 PIPELINE 枚举

**Files:**
- Modify: `Backend/src/models/response.py`

- [ ] **Step 1: 加枚举值**

```python
    TRACE = "trace"
    PIPELINE = "pipeline"   # 检索流水线实时事件（方案 B）
    ERROR = "error"
```

- [ ] **Step 2: 验证**

Run: `cd Backend && uv run python -c "from models.response import StreamEventType; print(StreamEventType.PIPELINE.value)"`
Expected: `pipeline`

---

### Task 3: orchestrator Queue 桥接 + pipeline 事件

**Files:**
- Modify: `Backend/src/services/agent/orchestrator.py`（quick_answer 检索段 ~line 478-540；`_chat_stream_with_fallback` 生成段）
- Test: `Backend/tests/test_orchestrator_pipeline.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `RetrievalTrace._emitter`；Task 2 的 `StreamEventType.PIPELINE`
- Produces: `_pipeline_event(stage, data) -> StreamEvent`（orchestrator 方法）；
  `_retrieve_with_pipeline(retriever, query, trace, pipe_queue) -> list[dict]`（async generator，yield pipeline 事件并返回检索结果）

- [ ] **Step 1: 写失败测试** `Backend/tests/test_orchestrator_pipeline.py`

```python
"""quick_answer pipeline 事件流测试（方案 B：检索过程实时事件）"""
from core.tracing import RetrievalTrace
from models.response import StreamEventType
from services.agent.orchestrator import ResearchOrchestrator


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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd Backend && uv run pytest tests/test_orchestrator_pipeline.py -v`
Expected: FAIL（无 pipeline 事件——assert pipe 为空）

- [ ] **Step 3: 实现**（orchestrator.py）

3a. 加方法（`_pipeline_event` + `_retrieve_with_pipeline`，放在 `_with_history` 附近）：

```python
    def _pipeline_event(self, stage: str, data: dict) -> StreamEvent:
        """pipeline 事件（检索流水线实时状态）"""
        return StreamEvent(
            type=StreamEventType.PIPELINE,
            data={"stage": stage, **data},
            timestamp=time.time(),
        )

    async def _retrieve_with_pipeline(
        self, retriever, query: str, trace: RetrievalTrace,
        pipe_queue: asyncio.Queue,
    ) -> list[dict[str, Any]]:
        """后台任务检索 + 主循环转发 pipeline 事件（Queue 桥接）

        async generator 在 await 期间不能 yield——检索放后台任务，
        主循环轮询队列，record_path 的实时事件逐条转发。
        """
        ret_task = asyncio.create_task(retriever.retrieve(query, trace=trace))
        while not ret_task.done() or not pipe_queue.empty():
            try:
                ev = await asyncio.wait_for(pipe_queue.get(), timeout=0.1)
                yield self._pipeline_event(ev["stage"], ev["data"])
            except asyncio.TimeoutError:
                continue
        return ret_task.result()
```

3b. quick_answer 检索段改造（原 ~478-505 行，替换为）：

```python
        retrieval_query = await self._rewrite_query(query, history)
        trace.rewritten_query = retrieval_query

        # Pipeline 事件桥（方案 B）：trace._emitter → 队列 → 实时 yield
        pipe_queue: asyncio.Queue = asyncio.Queue()

        def _emit_pipeline(stage: str, data: dict) -> None:
            try:
                pipe_queue.put_nowait({"stage": stage, "data": data})
            except Exception:
                pass

        trace._emitter = _emit_pipeline
        yield self._pipeline_event("rewrite", {"rewritten_query": retrieval_query})

        retrieved_docs: list[dict[str, Any]] = []
        if self.retriever:
            try:
                if hasattr(self.retriever, "retrieve") and not asyncio.iscoroutinefunction(
                    self.retriever.retrieve
                ):
                    # 同步 VectorStore 兼容（无实时事件，直接取结果）
                    raw_docs = self.retriever.retrieve(retrieval_query, top_k=5)
                    retrieved_docs = [
                        {
                            "content": d.get("content", ""),
                            "source": d.get("source", ""),
                            "paths": [],
                            "metadata": d.get("metadata", {}) or {},
                        }
                        for d in raw_docs
                    ]
                else:
                    async for ev in self._retrieve_with_pipeline(
                        self.retriever, retrieval_query, trace, pipe_queue
                    ):
                        yield ev
                    retrieved_docs = ...
```

> 注意：`_retrieve_with_pipeline` 是 async generator，不能直接取返回值——最后一段改为
> `retrieved_docs = [d async for d in ...]` 不可行（yield 转发与返回值冲突）。
> 正确做法：拆两个方法——`_retrieve_with_pipeline` 只 yield 事件，检索结果由
> 任务对象带回。实现：

```python
        # （替换 else 分支）——
        ret_task = asyncio.create_task(
            self.retriever.retrieve(retrieval_query, trace=trace)
        )
        while not ret_task.done() or not pipe_queue.empty():
            try:
                ev = await asyncio.wait_for(pipe_queue.get(), timeout=0.1)
                yield self._pipeline_event(ev["stage"], ev["data"])
            except asyncio.TimeoutError:
                continue
        retrieved_docs = ret_task.result()
```

> 即：不抽取 helper（避免 async generator 返回值陷阱），主检索与 CRAG 重检索
> 两处内联同一模式。`_pipeline_event` helper 保留。

3c. RRF 融合完成事件（retrieve 返回后、CRAG 评估前）：

```python
        # 融合完成事件（merged=候选数，sources=去重来源数）
        yield self._pipeline_event("fuse", {
            "merged": len(retrieved_docs),
            "sources": len({d.get("source", "") for d in retrieved_docs}),
        })
```

3d. CRAG 评估事件（在现有 CRAG 块 `if settings.crag_enabled and retrieved_docs:` 内最前）：

```python
            yield self._pipeline_event("crag", {"status": "evaluating"})
```

重检索（`retrieved_docs = await self.retriever.retrieve(rewritten, trace=trace)`）
改为 Queue 循环模式，并在重检索完成后：

```python
                yield self._pipeline_event("crag", {"status": "retried"})
```

3e. 生成开始事件（LLM 流式调用前，messages 构建后）：

```python
        yield self._pipeline_event("generate", {"status": "start"})
```

（quick_answer 与 research 主流程各一处；mock 模式不涉及）

- [ ] **Step 4: 运行确认通过**

Run: `cd Backend && uv run pytest tests/test_orchestrator_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: 回归**

Run: `cd Backend && uv run pytest tests/test_orchestrator_trace.py tests/test_orchestrator_fallback.py -q`
Expected: 全 PASS（trace/拒答行为不变）

- [ ] **Step 6: 检查点**

---

### Task 4: 前端类型 + store 事件处理

**Files:**
- Modify: `Frontend/src/types/api.ts`（StreamEventType 枚举 + PipelineStage 类型）
- Modify: `Frontend/src/types/domain.ts`（ChatMessage 加 pipeline 字段）
- Modify: `Frontend/src/stores/chat.ts`（case PIPELINE）

- [ ] **Step 1: types/api.ts**——枚举加值：

```ts
  TRACE = 'trace',
  PIPELINE = 'pipeline',
```

并加类型（文件末尾）：

```ts
/** 检索流水线阶段（方案 B：后端实时事件累加） */
export interface PipelineStage {
  stage: string
  status?: string
  name?: string
  hits?: number
  tookMs?: number
  rewrittenQuery?: string
  merged?: number
  sources?: number
}
```

- [ ] **Step 2: types/domain.ts**——ChatMessage 加字段（在 sources 附近）：

```ts
  /** 检索流水线实时阶段（方案 B） */
  pipeline?: PipelineStage[]
```

- [ ] **Step 3: stores/chat.ts**——在事件处理 switch 加分支（参照现有 `case StreamEventType.SOURCES` 的模式——取当前流式消息并修改）：

```ts
          case StreamEventType.PIPELINE: {
            // 方案 B: 检索流水线实时事件 → 按轮次消息累加
            const data = ev.data as Record<string, unknown>
            const stage: PipelineStage = {
              stage: String(data.stage || ''),
              status: data.status ? String(data.status) : undefined,
              name: data.name ? String(data.name) : undefined,
              hits: typeof data.hits === 'number' ? data.hits : undefined,
              tookMs: typeof data.took_ms === 'number' ? data.took_ms : undefined,
              rewrittenQuery: data.rewritten_query
                ? String(data.rewritten_query)
                : undefined,
              merged: typeof data.merged === 'number' ? data.merged : undefined,
              sources: typeof data.sources === 'number' ? data.sources : undefined,
            }
            // （按现有 SOURCES 分支的 message 获取方式——streamingMessage / 最后一条）
            break
          }
```

> 注：`took_ms` 后端下划线命名 → 前端 camelCase `tookMs`。
> store 中获取当前消息的代码复用 SOURCES 分支的现有变量（实现时对齐该分支写法）。

- [ ] **Step 4: 验证**

Run: `cd Frontend && pnpm build`
Expected: 构建通过（类型检查）

- [ ] **Step 5: 检查点**

---

### Task 5: PipelinePanel.vue 组件

**Files:**
- Create: `Frontend/src/components/PipelinePanel.vue`

**Interfaces:**
- Consumes: `pipeline: PipelineStage[]`（props，来自 store 轮次消息）、`query: string`
- Produces: 竖向流水线渲染（节点状态机 + 手风琴展开）

- [ ] **Step 1: 写组件**（完整代码，模板 + script + style）

```vue
<template>
  <div class="pipeline-panel">
    <!-- 查询与改写 -->
    <div class="pp-row pp-query">
      <span class="pp-label">问题</span>
      <span class="pp-text">{{ query || '（等待输入）' }}</span>
    </div>
    <div v-if="rewriteText" class="pp-row pp-rewrite">
      <span class="pp-label">改写</span>
      <span class="pp-text">{{ rewriteText }}</span>
    </div>

    <!-- 五路检索 -->
    <div
      v-for="p in PATH_ORDER"
      :key="p"
      class="pp-row pp-path"
      :class="pathClass(p)"
      @click="togglePath(p)"
    >
      <span class="pp-icon">{{ PATH_ICONS[p] }}</span>
      <span class="pp-name">{{ PATH_NAMES[p] }}</span>
      <span class="pp-dot" />
      <span class="pp-nums">
        {{ pathStage(p)?.hits ?? '–' }} 条
        <template v-if="pathStage(p)?.tookMs != null">· {{ fmtMs(pathStage(p)!.tookMs!) }}</template>
      </span>
      <span class="pp-chevron">{{ expanded === p ? '▾' : '▸' }}</span>
      <!-- 展开详情 -->
      <div v-if="expanded === p" class="pp-detail" @click.stop>
        <div v-if="pathStage(p)?.hits" class="pp-detail-line">
          命中 {{ pathStage(p)!.hits }} 条 · 耗时 {{ fmtMs(pathStage(p)!.tookMs || 0) }}
        </div>
        <div v-else class="pp-detail-line">未命中</div>
        <div v-if="p === 'graph'" class="pp-detail-line">图谱锚定：见下方证据链 graph_anchor</div>
      </div>
    </div>

    <!-- 融合与生成 -->
    <div class="pp-row pp-fuse" :class="{ done: fuseStage?.status }">
      <span class="pp-icon">⚖</span>
      <span class="pp-name">RRF 融合</span>
      <span class="pp-nums">
        <template v-if="fuseStage?.merged != null">
          {{ fuseStage!.merged }} 候选 · {{ fuseStage!.sources }} 来源
        </template>
        <template v-else>等待…</template>
      </span>
    </div>
    <div class="pp-row pp-gen" :class="genClass">
      <span class="pp-icon">✒</span>
      <span class="pp-name">LLM 生成</span>
      <span class="pp-nums">
        <span v-if="genStarted" class="pp-bar" />
        <template v-else>等待…</template>
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PipelineStage } from '@/types/api'

const props = defineProps<{
  pipeline: PipelineStage[]
  query: string
}>()

// 五路固定顺序与元数据（金棕单色 SVG 线稿图标用 emoji/字符占位，
// 如需精致图标后续替换为内联 SVG）
const PATH_ORDER = ['semantic', 'question', 'bm25', 'graph', 'entity'] as const
const PATH_NAMES: Record<string, string> = {
  semantic: '语义检索', question: '假设问题', bm25: '关键词',
  graph: '图谱锚定', entity: '实体锚定',
}
const PATH_ICONS: Record<string, string> = {
  semantic: '印', question: '问', bm25: '简',
  graph: '网', entity: '铭',
}

const expanded = ref<string | null>(null)
function togglePath(p: string) {
  expanded.value = expanded.value === p ? null : p
}

const rewriteText = computed(() => props.pipeline.find(s => s.stage === 'rewrite')?.rewrittenQuery || '')
const fuseStage = computed(() => props.pipeline.find(s => s.stage === 'fuse'))
const genStarted = computed(() => props.pipeline.some(s => s.stage === 'generate'))

function pathStage(p: string): PipelineStage | undefined {
  return props.pipeline.find(s => s.stage === 'path_done' && s.name === p)
}
function pathClass(p: string): Record<string, boolean> {
  const st = pathStage(p)
  return { running: false, done: !!st, empty: st?.hits === 0 }
}
const genClass = computed<Record<string, boolean>>(() => ({ done: genStarted.value }))

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}
</script>

<style scoped lang="less">
.pipeline-panel {
  padding: 8px 10px;
  font-size: 12px;
  color: var(--color-primary);
}

.pp-row {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 8px; margin-bottom: 4px;
  border: 1px solid rgba(201, 169, 110, 0.25);
  border-radius: 6px;
  background: rgba(253, 250, 243, 0.6);
  transition: border-color 0.2s;
}

.pp-query, .pp-rewrite {
  flex-wrap: wrap;
  .pp-label { font-weight: 600; color: var(--color-gold); }
  .pp-text { flex: 1; min-width: 0; word-break: break-all; color: #6b5a3f; }
}

.pp-path { cursor: pointer; position: relative; }
.pp-icon { width: 18px; text-align: center; color: var(--color-gold); }
.pp-name { flex: 1; font-weight: 500; }
.pp-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: #c8b89a;
  &.running { background: var(--color-gold); animation: pp-pulse 0.8s infinite; }
  &.done { background: #6a9a5c; }
}
.pp-nums { font-size: 11px; color: #8a7a5f; white-space: nowrap; }
.pp-chevron { color: #b8a68c; font-size: 10px; }

.pp-row.done { border-color: rgba(106, 154, 92, 0.4); }
.pp-row.empty { opacity: 0.55; }

.pp-detail {
  position: absolute; top: 100%; left: 0; right: 0; z-index: 10;
  background: #fffdf7; border: 1px solid rgba(201, 169, 110, 0.3);
  border-radius: 6px; padding: 6px 8px; margin-top: 2px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}
.pp-detail-line { font-size: 11px; color: #6b5a3f; padding: 2px 0; }

.pp-bar {
  display: inline-block; width: 60px; height: 6px;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--color-gold), #e8d9b8);
  animation: pp-grow 1.2s ease-in-out infinite;
}

@keyframes pp-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
@keyframes pp-grow {
  0% { width: 20%; }
  100% { width: 90%; }
}
</style>
```

- [ ] **Step 2: 验证**

Run: `cd Frontend && pnpm build`
Expected: 构建通过

---

### Task 6: SidePanel 替换 + 清理 + 全量验证

**Files:**
- Modify: `Frontend/src/components/SidePanel.vue`（上部 MindMapCard → PipelinePanel）
- Delete: `Frontend/src/components/MindMapCard.vue`
- Modify: `docs/manual-test-plan.md`（A10 用例更新）

- [ ] **Step 1: SidePanel.vue 上部替换**

template（保留轮次 tab 逻辑，替换导图区域）：

```vue
    <!-- 上部：检索流水线（当前轮次，常驻可见——方案 B 动态点亮） -->
    <div class="panel-title">
      <span class="title-dot"></span>
      检索流水线
      <div v-if="rounds.length > 1" class="round-tabs">
        <button
          v-for="(r, i) in rounds"
          :key="r.id"
          class="round-tab"
          :class="{ active: i === activeRoundIdx }"
          @click="selectRound(i)"
        >第 {{ i + 1 }} 轮</button>
      </div>
    </div>
    <div class="pipeline-area">
      <PipelinePanel
        v-if="activeRound"
        :pipeline="activeRound.pipeline || []"
        :query="activeRound.query || ''"
      />
      <div v-else class="pipeline-empty">发送问题后，检索流水线将在此实时点亮</div>
    </div>
```

script：`import MindMapCard from './MindMapCard.vue'` → `import PipelinePanel from './PipelinePanel.vue'`
style：`.mindmap-area` 改 `.pipeline-area`（高度 260px 不变，`overflow-y: auto` 支持展开溢出）。

- [ ] **Step 2: 删除 MindMapCard.vue**

Run: `rm Frontend/src/components/MindMapCard.vue`，并确认无其他引用：`grep -rn "MindMapCard" Frontend/src/`
Expected: 无引用

- [ ] **Step 3: 更新 manual-test-plan.md A10 用例**

A10 改为「检索流水线」用例：提问后右侧上部五路节点按真实时序点亮（语义→假设问题→关键词→图谱→实体）、命中数与耗时显示、RRF 融合与 LLM 生成节点出现、点击节点展开详情。

- [ ] **Step 4: 后端全量回归**

Run: `cd Backend && uv run pytest -q`
Expected: 159+ passed（原 159 + 新增 ~5）

- [ ] **Step 5: 前端构建**

Run: `cd Frontend && pnpm build`
Expected: 构建通过

- [ ] **Step 6: 端到端冒烟**（后端运行中）

真实提问「商代青铜鼎的铸造工艺有什么特点」，确认：
1. 流式响应含 `pipeline` 事件行（rewrite → path_done×5 → fuse → generate 顺序）
2. 前端右侧流水线节点依次点亮，命中数与 trace 诊断一致
3. 点击节点可展开详情

- [ ] **Step 7: 检查点**——progress.md 追加完成记录（含事件协议、验证结果）
