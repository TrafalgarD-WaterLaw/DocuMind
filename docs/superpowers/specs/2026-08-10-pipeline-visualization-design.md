# 检索流水线可视化设计（Pipeline Panel）

日期: 2026-08-10
状态: 已批准（用户确认 3 节设计）

## 背景

聊天页右侧 360px 窄栏上部为「回答结构」（MindMapCard 思维导图，固定 260px）。
用户评估: 导图简陋、内容形式单一、缺特色。选择方案 B——**后端真流式阶段事件**，
右侧改为「检索流水线（过程）+ 证据链（结果）」结构，回答生成时流水线节点按
真实时序依次点亮（动态直播），每路可点击展开详情。

## 需求

1. 替换右侧上部思维导图 → 竖向检索流水线
2. 流水线节点按后端真实检索时序动态点亮（37s 检索过程实时可见）
3. 每路节点可展开详情（命中来源样例）
4. 视觉延续文物主题（金棕/米色系），不引入新设计体系
5. 零侵入：不影响检索/生成主链路，trace 全量事件保留

## 事件协议（StreamEvent 新 type = `pipeline`）

NDJSON 每行: `{"type": "pipeline", "data": {...}, "timestamp": ...}`

| stage | status | data 载荷 | 触发点 |
|---|---|---|---|
| `rewrite` | done | `{rewritten_query}` | 查询改写完成（CRAG 前） |
| `crag` | triggered / not_triggered | `{re_retrieved: bool}` | CRAG 评估结果 |
| `semantic` `question` `bm25` `graph` `entity` | done | `{hits, took_ms}` | 各路完成——挂在 `trace.record_path` 内 |
| `fuse` | done | `{merged: N, sources: M}` | RRF 融合 + 来源多样性后 |
| `generate` | start | `{}` | LLM 生成开始 |

## 后端架构

### 1. core/tracing.py — RetrievalTrace 加发射器

```python
@dataclass
class RetrievalTrace:
    ...
    _emitter: Callable[[str, dict], None] | None = None

    def record_path(self, name, hits, took_ms):
        self.paths[name] = PathTrace(hits=hits, took_ms=round(took_ms, 1))
        if self._emitter:
            self._emitter("path_done", name=name, hits=hits, took_ms=round(took_ms, 1))
```

hybrid.py 五路串行执行 + record_path 即每路完成点 → 零侵入获得实时事件。

### 2. orchestrator.py — asyncio.Queue 桥接

async generator 在 await 期间不能 yield → 队列轮询:

```python
pipe_queue: asyncio.Queue = asyncio.Queue()
def _emit(stage, **kw): pipe_queue.put_nowait({"stage": stage, **kw})
trace._emitter = lambda stage, **kw: _emit(stage, **kw)

yield pipeline(rewrite done, rewritten_query)
task = asyncio.create_task(self.retriever.retrieve(query, trace=trace))
while not task.done() or not pipe_queue.empty():
    try:
        ev = await asyncio.wait_for(pipe_queue.get(), timeout=0.1)
        yield serialize(ev)
    except asyncio.TimeoutError:
        pass
docs = task.result()
yield pipeline(fuse done, merged=len(docs), sources=...)
```

CRAG 重检索、生成开始同理发事件。emitter 异常被 Queue 吞掉不影响主链路。
trace 最后仍发全量（展开详情的数据源）。

### 3. models/response.py — StreamEventType 加 PIPELINE

## 前端架构

### 组件

- `PipelinePanel.vue`（新）替换 SidePanel 上部 MindMapCard
- `SidePanel.vue`: 上部引用替换（MindMapCard 删除）
- `stores/chat.ts`: 处理 pipeline 事件 → message.pipeline: PipelineStage[] 累加
- `types/api.ts`: PipelineStage 类型 + pipeline 事件分支

### PipelineStage 类型

```ts
interface PipelineStage {
  stage: 'rewrite' | 'crag' | 'semantic' | 'question' | 'bm25' | 'graph'
        | 'entity' | 'fuse' | 'generate'
  status: 'pending' | 'running' | 'done' | 'error'
  hits?: number
  tookMs?: number
  rewrittenQuery?: string
  merged?: number
  sources?: number
}
```

### 布局（360px 窄栏竖向）

```
问题: {原始 query}
✎ 改写: {rewritten_query}          ← rewrite done 后出现
① 语义      11 条 · 5.6s           ← 五路卡片
② 假设问题   8 条 · 3.5s             状态灯: 灰=等待 金脉冲=运行中 绿=完成
③ 关键词     8 条 · 24ms
④ 图谱       9 条 · 7.6s            ← 展开显示锚定实体+关系
⑤ 实体       0 条 · 0.6s
▷ RRF 融合 → 候选 6 · 来源 5
▷ LLM 生成  ██████░░                ← generate start 后进度条
```

每路卡片可点击展开（手风琴，一次一个）: 命中来源列表（source 名 + 内容前 60 字，
最多 5 条，取自 trace.paths + sources 条目），图谱路额外显示 graph_anchor 关系。

### 交互

| 交互 | 行为 |
|---|---|
| 生成中 | 节点真实时序点亮；generate 后按 content 流长度估算进度条 |
| 完成 | 全定格；生成节点显示耗时 + token（llm_usage） |
| 点击节点 | 手风琴展开/收起详情 |
| 多轮切换 | 第 N 轮 tab 保留，pipeline 随轮次切换 |
| 拒答/空检索 | 流水线空态 + 拒答红标 |

### 视觉

- 沿用 --color-card 米色底、--color-primary 深棕金
- 状态灯: #c8b89a（等待）/ --color-gold 脉冲（运行中）/ #6a9a5c（完成）
- 五路图标 SVG 线稿（单色金棕）: 语义=印章、假设问题=问答气泡、关键词=竹简、
  图谱=关系网、实体=铭文块
- 展开动画 max-height 0.2s 过渡

## 错误处理

| 场景 | 行为 |
|---|---|
| emitter 异常 | Queue 吞掉，检索主链路不受影响 |
| 无 pipeline 事件（旧后端/mock） | 全部 pending 灰态 + 提示，不崩 |
| 检索失败 | 节点标红 + ERROR 事件原样走 |
| 流式中断 | 已点亮节点定格，下次提问重置 |

## 测试

- 后端 pytest（+2~3）:
  1. test_trace_emitter_called_on_record_path — emitter 回调触发且带参
  2. test_orchestrator_pipeline_events — mock retriever 分阶段返回，断言
     NDJSON 事件序列 rewrite → path_done(semantic…) → fuse 有序
  3. 无 emitter 的旧 trace 行为不变
- 前端: pnpm build 类型检查 + 手动冒烟（真实问答看点亮时序）

## 文件清单

| 文件 | 改动 |
|---|---|
| Backend/src/core/tracing.py | RetrievalTrace._emitter + record_path 回调 |
| Backend/src/services/agent/orchestrator.py | Queue 桥接 + rewrite/fuse/generate 事件 |
| Backend/src/models/response.py | StreamEventType.PIPELINE |
| Backend/tests/test_trace_emitter.py（新） | 后端测试 |
| Frontend/src/types/api.ts | PipelineStage + 事件分支 |
| Frontend/src/stores/chat.ts | pipeline 事件处理 |
| Frontend/src/components/PipelinePanel.vue（新） | 流水线组件 |
| Frontend/src/components/SidePanel.vue | 上部引用替换 |
| Frontend/src/components/MindMapCard.vue | 删除 |
