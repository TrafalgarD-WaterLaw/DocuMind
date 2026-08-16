# DocuMind 全链路 RAG 加固设计（方案一）

日期：2026-08-06
状态：已获用户批准（"记录下来方案一"）
定位：简历演示项目——全链路 RAG 系统（Ingest → Index → Retrieve → Generate → 可观测）

## 一、业界参考架构

对照 RAGFlow / Dify / FastGPT / AnythingLLM 的共识分层：

```
Ingest 层    上传 → 版面解析 → 智能分块 → 嵌入 → 索引
             （辅助索引同步更新：BM25 / 问题索引 / 图谱）
Retrieve 层  查询理解(改写·路由) → 多路召回 → RRF融合 → 重排 → 质量评估(CRAG)
Generate 层  提示词组装(证据锚定) → 流式生成 → 引用溯源 → 多轮记忆
横切能力     索引一致性(增量) · 可观测性(trace·token) · 容错(重试·降级) · 评测基线
```

## 二、现状对照结论

**已达业界水准（不动）**：五路混合召回 + 加权 RRF（k=60）+ 来源多样性；
意图路由 + 图谱结构化问答（T1-T6 白名单模板 + 规则回退）；CRAG 检索质量评估；
查询改写；Q-to-Q 假设问题索引；文本实体锚定；NDJSON 流式协议 + 证据溯源。

**关键缺口（按演示价值排序）**：

| # | 缺口 | 业界做法 | 影响 |
|---|------|---------|------|
| 1 | BM25 索引为启动时一次性快照，上传/删除后不重建 | RAGFlow 增量索引管道 | 刚上传的文档 BM25 路召回不到，上传→问答闭环断裂 |
| 2 | 可观测性空白：无 trace id、无各路径命中/耗时、token 丢弃 | Dify 问答日志（改写/召回/生成各阶段可见） | "为什么这么答"说不清 |
| 3 | LLM 无统一容错：无 timeout、无重试、失败即断流 | LangChain retry/fallback | 演示偶发断流 |
| 4 | 死代码：/api/research/quick 与 /api/chat 重复；reasoning 事件未使用 | — | 讲项目时被动 |
| 5 | 多轮记忆硬截断（6 轮×200 字） | Dify message history + summarization | 长对话"忘事"（P2） |
| 6 | CORS `*` + credentials 矛盾 | 固定 origin | 顺手修 |

## 三、工作包

### P0-A 入库闭环（上传异步任务化 + 索引一致性）

**后端**

1. 新增 `src/services/task_manager.py`
   - 进程内任务表（dict + asyncio.Lock），字段：
     `task_id, file_name, source, status, progress(0-100), stage_text, error, pages, blocks, chunks, created_at, finished_at`
   - 状态机：`queued → parsing → chunking → indexing → questions → done`，任一步失败 `failed`（携带 error）
   - 保留最近 50 条任务（防止内存膨胀）；提供 `list_tasks()` / `get_task(id)` / `update_task(id, **fields)`
   - 注释说明：进程内存存储，热重载/重启后任务丢失可接受（演示场景；任务表挂载时由前端重新拉取未完成项）

2. 改造 `src/api/upload.py`
   - `POST /api/upload`：保存文件 → 建任务（queued）→ BackgroundTasks 执行管线 → **立即返回 `{task_id, file_name}`**
   - 管线阶段：
     - parsing：Docling 优先 / PyPDF 回退（复用 `_get_parser()`），progress 10→40
     - chunking：现有 `IndexerService.load_chunks_from_text`（chunk_size/overlap 从请求参数读，默认 settings），progress 45
     - indexing：`vector.add_documents`，progress 50→60，**标记 BM25 dirty**；`questions` 阶段并入同一任务
     - questions：`build_question_documents` 增加 `on_progress(done_batch, total_batch)` 回调 → progress 60→95，完成后 100/done
   - 问题生成失败不使任务 failed（文档已可检索）：记录 stage_text="问题生成失败（可重试）" 并完成
   - `GET /api/upload/tasks`：最近任务列表（前端挂载恢复轮询）
   - `GET /api/upload/tasks/{task_id}`：单任务详情
   - 同名替换：请求带 `replace=1` 时，入库前先 `vector.delete(旧 source)` + 清理问题索引（复用现有删除逻辑）

3. BM25 惰性重建（`src/services/retrieval/bm25.py` + `src/core/di.py`）
   - BM25 索引增加 `dirty` 标记与重建锁；upload/delete 后置 dirty
   - `retriever.retrieve()` 入口处检查：dirty 且无重建进行中 → 从 `vector.get_all_documents()` 重建（5533 chunks 量级，毫秒级）→ 清 dirty
   - 并发安全：重建锁防重入；重建期间检索用旧索引（不阻塞）

4. 增强 `GET /api/documents`：每文档返回
   `{source, chunks(主库 where source 计数), questions(问题索引 where source 计数), pages(任务记录), status(最近任务状态), created_at}`

5. `src/services/retrieval/hypothesis.py`：`build_question_documents` 增加可选 `on_progress: Callable[[int, int], None] | None = None`（完成批数/总批数）

**前端（LibraryView 重设计）**

- 页头改为"入库流水线"叙事：上传 → 版面解析 → 智能分块 → 向量化 → 假设问题生成 → 可检索
- 上传区：支持多文件选择/拖拽；提交后进入**任务队列卡片流**（每文件一卡：文件名、状态徽章、阶段文案、真实进度条、失败显示错误并可重试）
- 轮询：有活跃任务时每 2s 拉 `GET /api/upload/tasks`；组件挂载时恢复未完成任务
- 高级选项（折叠面板）：分段大小（chunk_size）、重叠（chunk_overlap）
- 同名文件再次选择 → 确认"将替换已入库的《x》"→ 带 `replace=1` 提交
- 文档列表：状态徽章 + 切片数/问题数/页数 + 行展开详情（块类型统计、预览 200 字）+ 删除确认 + 失败文档"重新解析"
- 上传完成后提供"去提问"入口（跳转 DeepQAView）

### P0-B 问答可观测性

**后端**

1. 新增 `src/core/tracing.py`：`trace_id`（uuid4 短码）+ contextvars 贯穿一次请求；`RetrievalTrace` 数据结构：
   `{trace_id, query, rewritten_query, crag_triggered, paths: {semantic|question|bm25|graph|entity: {hits, took_ms}}, total_ms, llm: {model, prompt_tokens, completion_tokens}}`

2. `src/services/retrieval/hybrid.py`：`retrieve()` 增加 `return_trace`（或返回 `(docs, trace)` 的兼容方式），逐路记录命中数/耗时

3. `src/providers/llm/deepseek.py`：流式响应累加提取 `usage`（DeepSeek 流式 usage 在末 chunk）；非流式直接取

4. `src/services/agent/orchestrator.py`：组装 `RetrievalTrace`，在流式末尾追加 `trace` 事件（新事件类型）；同时追加 `sources` 事件携带各路径命中数（或复用 trace 事件一次带全）

5. 结构化日志：新增 JSON-lines handler 写 `src/data/logs/query_trace.jsonl`（一条请求一行，含全部诊断字段），不依赖日志框架改造

**前端**

- DeepQAView 消息区：每条助手消息下方"本轮检索诊断"折叠面板（数据来自 `trace` 事件）：
  五路命中数/耗时横向条形、改写前后查询、CRAG 是否触发、token 用量、总耗时
- 与现有"证据链"面板（sources/graph_anchor）互补：证据链展示"检索到什么"，诊断展示"检索过程"

### P0-C LLM 统一容错

1. `src/providers/llm/deepseek.py`
   - 显式超时：连接 60s、流式读 300s
   - 幂等调用自动重试：新增重试装饰器/工具函数（2 次指数退避 1s/2s），仅用于非流式调用（查询改写、检索评估、实体提取、模板选择）
   - 流式回答：失败重试 1 次；仍失败 → `error` 事件 + 兜底回答模板（"当前服务繁忙，请稍后再试"，仍展示已检索到的 sources）
2. 确认图谱结构化分支失败降级链路完整（已有 ok=False → 文本检索，补测试说明）

### P1 清理

1. 删除 `POST /api/research/quick` 路由（前端 DeepQAView 改指 `/api/chat`；先确认前端当前调用路径）
2. `reasoning` 事件接入：深度模式专家思考摘要（historian/craftsman 的思考过程摘要）以 `reasoning` 事件流式发出；前端侧栏"推理过程"折叠区展示。若前端改动过大则仅保留后端事件（前端 P2 接）
3. CORS：`allow_origins=["http://localhost:5173"]`（本地演示固定 origin），移除 `allow_credentials=True`

### P2 可选（记录不排期）

- 多轮记忆摘要压缩（LLM 摘要旧轮次，替代硬截断）
- 一键评测挂接：`scripts/run_eval_all.py`（核心集/扩展集/judge 一键跑 + 结果打印）
- 独立"问答日志"页（消费 query_trace.jsonl）

## 四、明确不做

- 鉴权/用户体系、多知识库隔离（source 过滤已预留，将来可加）
- 语义缓存（相同问题重复消耗 token 可接受）
- MinerU 接入（解析器接口已可插拔，留 `MinerUParser` 扩展位）
- 文档内搜索分页（文档量几十，全量渲染足够）

## 五、验收标准

- **P0-A**：上传 1 个含新内容的中文 PDF → 任务状态实时流转（解析中→分块→入库→问题生成→完成）→ 完成后立即提问文档内容 → 回答命中且诊断面板显示 bm25 路有命中 → 删除文档后切片/问题数归零
- **P0-B**：一次问答流式末尾收到 `trace` 事件；前端折叠面板显示五路命中/耗时/token；query_trace.jsonl 有对应记录
- **P0-C**：断网/非法 key 模拟 → 非流式调用自动重试可见（日志）；流式失败最终出现兜底回答而非空白
- **P1**：`/api/research/quick` 无代码引用；深度模式流中出现 `reasoning` 事件；CORS 只放行 localhost:5173

## 六、实施顺序

P0-A → P0-B → P0-C → P1（P2 视时间）
