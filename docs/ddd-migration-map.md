# DocuMind DDD 重构映射表

> 2026-08-14 产出。依据 `docs/ddd-refactor-blueprint.md`(通用规则)对 DocuMind
> 现状的实测盘点 + 逐模块迁移映射 + 步骤清单。
> 现状基准:8663 行后端代码 / 185 tests / 三面评测契约(检索 28/28、
> 混合模态 15/15、文找图 12/13)。

## 0. 架构师裁量(先读,决定做多少)

DocuMind 是**检索管道系统**(chunk → embed → retrieve → rerank → generate),
不是富领域交易系统。蓝图"每域四层"按域裁剪:

| 域 | 分层裁量 | 理由 |
|---|---|---|
| retrieval | **降级为单层包** | 纯算法(六路召回/RRF/树剪枝),无领域规则,套四层是仪式不是架构 |
| multimodal | **维持现状** | 2026-08-13 刚收拢为内聚包(clip_retrieval/image_index/image_caption/evidence/assets),已达目标形态 |
| conversation / graph / ingestion | **四层化** | 有真业务规则:引用归因与拒答(conversation)、意图路由与 T1-T6 模板(graph)、source 契约与 8 路删除联动(ingestion) |
| documents | **两层层**(application + interfaces) | 纯 CRUD 管理,蓝图"纯 CRUD 模块降级"条款适用 |

函数 ≤20 行:对 **async 生成器编排函数**放宽——把 `_answer_flow`(89 行,
全程 yield 流事件)机械拆成 `_step1/_step2` 会打碎事件流;按"阶段"拆分
(已有 `_retrieve_main`/`_crag_correction` 模式),目标 ≤40 行/函数。

## 1. 现状盘点(实测)

### 1.1 已做对的(蓝图规则对照)

- ✅ **接口抽象**:`interfaces/` 6 个 ABC(vector_store/llm/graph_store/embedder/image_captioner/doc_parser)——依赖倒置已成立
- ✅ **DI 容器**:`core/di.py` AppContainer 构造函数注入 + FastAPI app.state 组合根
- ✅ **api 薄层**:`api/upload.py` 业务已下沉 services(upload_pipeline),各路由只做 HTTP
- ✅ **异常不裸吞**:graph_query/vision/upload 的吞错修复(2026-08-10,任务 #142)
- ✅ **类型注解**:pyright 0 errors;命名规范本周已一轮统一
- ✅ **数据转换集中**:Entity↔ORM 在 mapper 一处;DTO 在 models/(pydantic)

### 1.2 三大差距(实测数据)

**① 上帝文件 6 个**(>500 行或接近,蓝图要求 300-500):

| 文件 | 行数 | 拆分方向 |
|---|---|---|
| `services/retrieval/hybrid.py` | 731 | 按召回路径拆包 |
| `services/agent/quick.py` | 599 | 按编排阶段拆 |
| `services/upload_pipeline.py` | 536 | 按管道阶段拆 |
| `services/graph_query.py` | 387 | 路由/模板/格式化三拆 |
| `services/agent/deep.py` | 295 | 编排/事件构造拆 |
| `services/chunker.py` | 292 | 结构解析/父子切分拆 |

**② 超 20 行函数 97 个**。头部清单(>40 行):

| 函数 | 行数 | 拆分方案 |
|---|---|---|
| `deep.deep_research` | 164 | `_run_experts` / `_emit_sources_merged` / `_stream_report` / `_build_mindmap` |
| `quick._answer_flow` | 89 | 已有阶段拆分的延续:`_prepare_stage` / `_retrieve_stage` / `_generate_stage`(生成器放宽,见裁量) |
| `quick._generate_answer` | 83 | `_build_context` / `_emit_sources` / `_stream_answer` / `_finalize_trace` |
| `upload_pipeline._run_pipeline` | 81 | `_parse_stage` / `_chunk_stage` / `_index_stage` / `_post_stage` |
| `hypothesis.build_question_documents` | 73 | 批次循环 + LLM 调用拆 `_build_batch` |
| `synthesizer.synthesize` | 73 | `_assemble_sections` / `_build_evidence_text` |
| `tree.retrieve` | 72 | `_coarse_recall` / `_refine_search` / `_fallback_flat` |
| `hybrid._path_entity_anchor` | 72 | `_extract_entities` / `_match_sources` |
| `hybrid._path_clip` | 64 | `_visual_hits_to_sources` / `_query_documents` |
| `ingest/base.load` | 70 | `_delete_old` / `_write_batches` / `_update_assets` |
| `chunker._make_children` | 71 | 递归子切分收敛为迭代 + `_split_by_sentences` |
| `hybrid._recall_paths` | 58 | 六路 gather 保持,各路径已是 `_path_*` 方法(已达标 50%) |
| `graph_query.query` | 57 | `_execute_structured` / `_fallback_text` |
| `docling_parser.parse` | 56 | `_to_blocks` / `_extract_figures` |
| `hybrid._path_graph` | 55 | `_route_intent` / `_expand_terms` |
| `hybrid.retrieve` | 53 | 入口只留编排,细节下沉 `_path_*` |
| `graph_query._format` | 51 | T1-T6 各一个 `_format_*`(字典分派) |
| `context.filter_noise_chunks` | 50 | 已含决策树注释,拆 `_is_single_weak_vote` |
| `neo4j.search_path` | 50 | `_paths_between` / `_neighbor_walk` |
| `knowledge.init_graph` | 50 | `_fetch_representatives` 已拆,继续拆 `_build_skeleton` |

(其余 77 个 20-40 行函数按同模式:提取私有 `_step` 或数据组装函数,注释保留决策依据。)

**③ 模块级单例/全局状态 8 处**:

| 位置 | 判定 |
|---|---|
| `core/config.settings` | ✅ 豁免(蓝图允许配置单例) |
| `core/di.container` | ✅ 豁免(组合根,app.state 已持有;蓝图"main.py 或容器统一装配"即此) |
| `clip_retrieval.clip_retriever` 模块单例 | ✅ **豁免并文档化**:ChineseCLIP 是 400MB 重量级模型资源,类级共享 + 双检锁 + 失败冷却已是正确形态;DI 化每请求 new 反而制造灾难。面试口径:"模型资源型单例,与配置同级豁免" |
| `clip_retrieval._shared_model/_shared_col` 类级共享 | ✅ 同上(防跨实例双加载,任务 #146 实测) |
| `conversation_memory._cache`(OrderedDict) | 🔧 **改造**:模块级 → 服务实例字段(构造函数注入缓存上限) |
| `document_hashes._cache/_path` | 🔧 **改造**:模块级 dict + 懒加载 → 小类 `DocumentHashIndex`,容器装配注入 |
| `image_index._cache/_last_mtime`(mtime 失效) | 🔧 **改造**:同上,包 `FileBackedIndex` 类;ingest 注入路径已支持,顺势收口 |
| `prompts/_cache` | 🔧 **改造**:`PromptRenderer` 类(容器装配),render_system/render_user 成为其实例方法 |
| `core/tracing._trace_handles`(2026-08-14 新增) | 🔧 **改造**:并入 `TraceLogWriter` 类,句柄为实例字段(今日小账修复的延续) |

## 2. 业务域识别与目录映射

目标形态(仅 Backend/src,前端不动):

```
src/
├── core/                  # 保持现状(已符合蓝图:config/exceptions/logging/database/tracing)
├── interfaces/            # 保持:6 个跨域端口抽象(相当于蓝图 core/ 的接口延伸——
│                          # 向量库/LLM/图谱被多域共用,拆进单域反而制造跨域 import)
├── models/                # 保持:pydantic 请求/响应契约(蓝图 interfaces/schemas 的角色)
├── prompts/               # 保持(改造为 PromptRenderer 类后仍在 core 侧)
│
├── retrieval/             # 检索域(降级:单层包,不套四层)
│   ├── hybrid.py          #   ← services/retrieval/hybrid.py(先拆路径再考虑拆包)
│   ├── bm25.py            #   ← services/retrieval/bm25.py
│   ├── tree.py            #   ← services/retrieval/tree.py
│   ├── hypothesis.py      #   ← services/retrieval/hypothesis.py(问题索引)
│   ├── context.py         #   ← services/context.py(噪声过滤)
│   ├── entity_anchor.py   #   ← services/document_entities.py
│   └── rerank.py          #   ← providers/rerank/reranker.py
│
├── conversation/          # 问答域(四层)
│   ├── domain/
│   │   ├── query_plan.py      # ← services/query_understanding.QueryPlan(值对象)
│   │   ├── verdict.py         # ← services/agent/types.RetrievalVerdict + 拒答规则
│   │   └── exceptions.py      # ← 新增:拒答/越界引用/空流业务异常
│   ├── application/
│   │   ├── quick_answer.py    # ← services/agent/quick.py(拆分后)
│   │   ├── deep_research.py   # ← services/agent/deep.py(拆分后)
│   │   ├── experts.py         # ← services/agent/experts.py
│   │   ├── synthesizer.py     # ← services/agent/synthesizer.py
│   │   ├── orchestrator.py    # ← services/agent/orchestrator.py
│   │   ├── query_understanding.py  # ← services/query_understanding.py(rewrite/decompose)
│   │   └── memory.py          # ← services/conversation_memory.py(注入化后)
│   ├── infrastructure/
│   │   ├── deepseek_llm.py    # ← providers/llm/(deepseek + retry)
│   │   └── rerank_client.py   # ← 精排接口实现(若检索域需要)
│   └── interfaces/
│       ├── chat_routes.py     # ← api/chat.py
│       ├── research_routes.py # ← api/research.py
│       ├── vision_routes.py   # ← api/vision.py(编排部分)
│       └── schemas.py         # ← models/ 中问答相关契约
│
├── graph/                 # 图谱域(四层)
│   ├── domain/
│   │   ├── intent_router.py    # ← graph_query.is_structured + REL_WORDS/FEATURE_WORDS
│   │   ├── templates.py        # ← TEMPLATES T1-T6(白名单模板)
│   │   └── exceptions.py       # ← 新增:实体缺失/关系缺失业务异常
│   ├── application/
│   │   └── graph_qa.py         # ← services/graph_query.py 的 query/模板选择/格式化
│   ├── infrastructure/
│   │   ├── neo4j_store.py      # ← providers/graph/neo4j.py
│   │   └── graph_models.py     # ← providers/graph/models.py(neomodel ODM)
│   └── interfaces/
│       └── knowledge_routes.py # ← api/knowledge.py
│
├── multimodal/            # 多模态域(维持现状,2026-08-13 已收拢)
│   └── (clip_retrieval / image_index / image_caption / evidence / assets)
│
├── ingestion/             # 入库域(四层)
│   ├── domain/
│   │   ├── source_contract.py  # ← ingest/registry.validate_source(P1-C 契约)
│   │   └── exceptions.py       # ← 新增:契约违反/解析失败异常
│   ├── application/
│   │   ├── upload_pipeline.py  # ← services/upload_pipeline.py(拆分后)
│   │   └── ingest_service.py   # ← services/ingest/registry.run + base.load
│   ├── infrastructure/
│   │   ├── docling_parser.py   # ← providers/parser/docling_parser.py
│   │   ├── chroma_store.py     # ← providers/vector/chroma.py
│   │   ├── chunker.py          # ← services/chunker.py(分块算法)
│   │   ├── indexer.py          # ← services/indexer.py
│   │   └── ingestors/          # ← services/ingest/examples/*(数据源实现)
│   └── interfaces/
│       └── upload_routes.py    # ← api/upload.py
│
└── documents/             # 文档管理域(两层:application + interfaces)
    ├── application/
    │   ├── task_manager.py     # ← services/task_manager.py
    │   └── hash_index.py       # ← services/document_hashes.py(注入化后)
    └── interfaces/
        └── document_routes.py  # ← api/upload.py 的列表/删除端点
```

## 3. 迁移步骤清单(保护三面契约)

**总原则:行为零变化迁移——每一步跑 185 tests + `run_eval.py --retrieval-only`
+ `vision_eval.py` + `clip_image_eval.py`,任一契约漂移即回滚该步。**

### 阶段 0:基线锁定(已存在,不新增工作)

185 passed / 检索 28/28 / 混合模态 15/15 / 文找图 12/13 / pyright 0 errors。

### 阶段 1:上帝文件拆分(纯函数提取,零行为变化,最高性价比)

1. `hybrid.py`(731):`retrieve` 瘦身为编排入口;`_path_entity_anchor`/`_path_clip`/
   `_path_graph` 按"提取 → 组装 → 查询"拆私有函数。**不动六路召回语义**——eval 契约是唯一验收。
2. `quick.py`(599):`_generate_answer` 拆 `_build_context`/`_emit_sources`/`_stream_answer`;
   `_answer_flow` 按阶段拆(生成器放宽 ≤40 行)。同文件 `test_clip_evidence.py`、
   `test_orchestrator_*.py` 覆盖,拆完必须全绿。
3. `upload_pipeline.py`(536):`_run_pipeline` 拆四阶段私有函数(见 1.2 表)。
4. `deep.py`(295):`deep_research` 拆四个私有函数;事件构造已收口 `_expert_event`,不动。
5. `graph_query.py`(387):`_format` 改字典分派 T1-T6 各一个 `_format_*`。
6. `chunker.py`(292):`_make_children` 递归改迭代(行为等价,`test_chunker.py` 验收)。

### 阶段 2:全局状态注入化(改造 5 处缓存,行为零变化)

- [x] **2026-08-14 已完成**:5 处模块级缓存全部类化,容器(组合根)统一装配:
  `PromptRenderer` / `TraceLogWriter` / `ConversationMemory` / `DocumentHashIndex`
  / `FileBackedImageIndex`——实例状态 + 注入路径;di.py 5 个 lazy property;
  模块函数保留为**无状态委托入口**(42 处 render 调用面零改动);
  ingest base 持注入实例;三个依赖模块级状态的测试改注入式重写
  (test_document_hashes / test_image_index_service / test_conversation_memory)。
  **豁免不改**:`settings` / `container` / `clip_retriever`(理由见 1.2 表,写入 ADR)。

### 阶段 3:业务域重排(可选,面试叙事收益 > 工程收益)

- [x] **2026-08-14 已完成**:五域全部搬迁 + 拆分:
  - `graph/` 四层(domain/intent_router + templates、application/graph_qa、
    infrastructure/neo4j_store + graph_models、interfaces/knowledge_routes)
  - `retrieval/` 单层包(hybrid/bm25/tree/hypothesis/context/entity_anchor/
    rerank/embedder/langchain_adapter——embed/rerank 两个 provider 并入)
  - `conversation/` 四层(domain/query_plan + verdict + research_plan、
    application/quick_answer + deep_research + experts + synthesizer +
    orchestrator + query_understanding + memory、
    infrastructure/deepseek_llm、interfaces/chat + research + vision 路由)
  - `ingestion/` 四层(domain/source_contract、application/upload_pipeline +
    ingest_base + ingest_service、infrastructure/docling + pypdf +
    chroma_store + chunker + indexer + ingestors/、interfaces/upload_routes);
    CLI 改 `python -m ingestion`
  - `documents/` 两层(application/task_manager + hash_index、
    interfaces/document_routes——upload.py 拆出列表/删除端点)
  - `multimodal/` 上移顶层;`providers/` 全拆并入域(空目录已删);
    `api/` 仅留跨域 stats;`services/` 已删
  - 横切调整:`providers/llm/retry.py` → `core/llm_retry.py`;
    `services/prewarm.py` → `core/prewarm.py`
  - 验证(机器负载下尽力而为):全树编译 OK;9 个新域包逐包 import OK;
    `import main` 卡在 langchain_community PyPDFLoader 惰性导入
    (faulthandler 实证——与搬迁无关的既有链路,训练占机期间环境性慢);
    全量 pytest + 三面契约押后(等训练结束)
13. `interfaces/` 与 `models/` 保持全局(跨域端口与契约),不拆入单域 ✓

### 阶段 4:收尾

- [x] **2026-08-14 已完成**:第二批拆分 17 个超长函数
  (tree.retrieve 三助手 / context._keep_decision / neo4j_store._pack_node
  三处共用 / quick._stream_with_trace / deep._synthesize_report /
  synthesizer._assemble_sections + _build_evidence_text /
  hypothesis._collect_existing_chunks + _build_question_docs);
  生成器豁免 6 个 + 20-40 行段 67 个批量豁免——均入 ADR
  (`docs/adr-2026-08-14-ddd-refactor-decisions.md`:四层裁剪/单例豁免/
  函数长度豁免三裁决);CLAUDE.md 目录结构已随阶段 3 更新。

## 4. 与蓝图规则的对应速查

| 蓝图规则 | DocuMind 落地 |
|---|---|
| 业务域分层 | 五域:retrieval/conversation/graph/multimodal/ingestion + documents(CRUD 降级) |
| 四层结构 | conversation/graph/ingestion 三域套;retrieval 降级;multimodal 已达标 |
| 实体/服务/仓储/控制器职责 | conversation 域:QueryPlan/Verdict=domain,Service 只编排(已合规),Repository 在 infra,路由薄层(已合规) |
| 函数 ≤20 行 | 97 个超长函数分两批清;生成器编排函数放宽 ≤40(ADR 记录) |
| 三次原则 | 阶段 1 拆分沿用(不主动提取跨域相似逻辑) |
| 数据转换集中 | mapper/工厂已在位;新域迁移时保持"转换只在一处" |
| 异常基类体系 | 新增 `core/exceptions.py` 三级体系,现有 try/except 日志化对齐 |
| 构造函数注入 | 已达标;5 处缓存注入化后收口;模型资源单例豁免入 ADR |
| 外部调用只在 infra | LLM/Neo4j/Chroma/CLIP 全部已封装,迁移后保持该边界 |
