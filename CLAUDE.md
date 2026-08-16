# CLAUDE.md

本文件为 Claude Code（claude.ai/code）在此仓库中工作时提供指导。

## 项目概述

智慧文物探索——智能问答与解析系统。一个结合知识图谱（Neo4j）、RAG 问答（六路混合检索）、深度学习图像识别（CLIP 图找图零样本识别）和交互式可视化（ECharts、Mind Elixir）的全栈中国历史文物探索应用。

仓库包含两个独立的子项目，没有共享的 monorepo 工具：

- **`frontend/`** — Vue 3 + Vite + TypeScript 单页应用（开发端口 5173）
- **`backend/`** — Python FastAPI 服务端（端口 5172）

## 常用命令

### 前端

```bash
cd frontend
pnpm dev              # 启动 Vite 开发服务器（5173，/api 代理到 5172）
pnpm build            # 生产构建（vue-tsc 类型检查 + Vite）
```

### 后端

```bash
cd backend
uv run python src/main.py       # 启动 FastAPI 服务（0.0.0.0:5172，热重载；必须 uv run——裸 python 缺依赖）
uv run pytest                   # 全量测试（当前 185 passed，~2.5 分钟）
python scripts/migrate_chunk_type.py         # P1-A 契约迁移：文本块补 chunk_type
python scripts/migrate_image_path_prefix.py  # P1-B 契约迁移：image_path 补 /api/uploads/ 前缀
python scripts/cleanup_test_data.py          # P1-D 清理：旧测试文档残留
python -m ingestion --source X         # 统一 ingest 管道入口（--dry-run 试跑）
```

### 评测（在 backend 目录执行，检索/多模态不耗 LLM token，输出到 `backend/eval/reports/`）

```bash
cd backend
uv run python eval/run_eval.py --retrieval-only   # 检索评测（Recall/MRR，不耗 LLM token）
uv run python eval/run_eval.py                     # 全量：检索 + 引用一致性
uv run python eval/judge.py                        # LLM-as-judge GT 事实包含率
uv run python eval/vision_eval.py                  # 混合模态评测（图+文联合检索，不耗 LLM token）
uv run python eval/clip_image_eval.py              # 文找图评测（AnyHit@5，不耗 LLM token）
```

## 架构

### API 通信（前端 → 后端）

前端通过 axios 实例（`frontend/src/api/client.ts`）访问后端，`import.meta.env.VITE_API_BASE_URL || 'http://localhost:5172'`；开发环境由 Vite 代理 `/api` → `http://localhost:5172`。

主要接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/chat` | 快速问答（统一走 ResearchOrchestrator），NDJSON 流式 |
| POST | `/api/research` | Agent 深度研究（史官/工艺/关联三专家），流式 |
| POST | `/api/upload` | 上传 PDF → 异步任务入库（返回 task_id，前端轮询） |
| GET | `/api/upload/tasks` `/api/upload/tasks/{id}` | 上传任务状态 |
| GET | `/api/documents` / `DELETE /api/documents/{source}` | 文档源列表 / 删除 |
| GET | `/api/knowledge/init` `/api/knowledge/expand` `/api/knowledge/search` | 图谱初始化 / 展开 / 搜索 |
| POST | `/api/vision/chat` | 多模态问答（图片 → CLIP 零样本识别 → 混合检索 → 流式回答） |
| POST | `/api/image/recognize` | 零样本文物图像识别 |
| GET | `/api/stats` | 系统规模统计 |
| GET | `/api/health` | 健康检查 |
| GET | `/api/uploads/*` `/api/images/*` | 上传文档图片 / 数据集图片静态服务 |

### 流式响应格式

LLM 响应以 NDJSON（换行分隔 JSON）流式传输。每行是一个包含 `type`、`data`、`timestamp` 字段的 JSON 对象。事件类型：

| `type` | 用途 |
|---|---|
| `reasoning` | LLM 思维链文本 |
| `content` | 最终回答的文本片段 |
| `markdown_dict` | 用于渲染思维导图的结构化 Markdown |
| `sources` | 检索证据（含 paths 路径标注、images 图片、graph_anchor 图谱锚定） |
| `trace` | 检索轨迹事件（RetrievalTrace，前端诊断面板用） |

前端通过 Fetch API `ReadableStream` 读取，逐行解析 JSON。

### 后端目录结构（`backend/src/`）

- `api/` — 仅跨域系统路由（stats）
- `core/` — 全局横切：`config.py`（集中配置）、`di.py`（AppContainer 组合根）、`tracing.py`（查询轨迹 + TraceLogWriter）、`llm_retry.py`（LLM 重试横切工具）、`prewarm.py`（启动预热）、`json_utils.py`
- `interfaces/` — 6 个跨域抽象接口（doc_parser/embedder/graph_store/image_captioner/llm/vector_store）
- `models/` — Pydantic 请求/响应契约（跨域）
- `prompts/` — 15 个 Markdown 提示词模板 + PromptRenderer（容器装配）
- 业务域（2026-08-14 DDD 重排，按能力域分层）：
  - `retrieval/` — 检索域（单层算法包）：hybrid（六路召回+RRF）/ bm25 / tree（树剪枝）/ hypothesis（问题索引）/ context（噪声过滤）/ entity_anchor / rerank / embedder
  - `conversation/` — 问答域（四层）：domain（QueryPlan/Verdict/ResearchPlan）· application（quick_answer/deep_research/experts/synthesizer/orchestrator/query_understanding/memory）· infrastructure（deepseek_llm）· interfaces（chat/research/vision 路由）
  - `graph/` — 图谱域（四层）：domain（intent_router/templates）· application（graph_qa T1-T6）· infrastructure（neo4j_store/graph_models）· interfaces（knowledge 路由）
  - `multimodal/` — 多模态域：clip_retrieval 图文互检 / image_index 映射表 / image_caption VLM 图注 / evidence 图片证据链 / assets 资产门面
  - `ingestion/` — 入库域（四层）：domain（source_contract P1-C）· application（upload_pipeline/ingest_base/ingest_service）· infrastructure（docling/pypdf/chroma_store/chunker/indexer/ingestors）· interfaces（upload 路由）；CLI `python -m ingestion`
  - `documents/` — 文档管理域（两层）：application（task_manager/hash_index）· interfaces（document 路由）

### 检索流水线（RAG 核心）

位于 `backend/src/retrieval/`：

1. **查询理解**：查询改写（LLM 代词消解）→ 意图路由（文本/图谱）→ CRAG 质量评估 → 实体锚定
2. **六路混合召回**：
   - `semantic`：向量检索（树状层级剪枝：窑口→器物→鉴定维度粗筛→细搜）+ 图片块直检通道（`where={"chunk_type": "image"}` 补 3 条）
   - `question`：Q-to-Q 假设问题索引匹配（hypothesis.py 入库侧生成）
   - `bm25`：BM25 关键词（jieba 分词 + rank_bm25，惰性重建）
   - `graph`：Neo4j 图谱锚定（LLM 提实体 → 查关联 → 扩展词补检索）
   - `entity`：文本实体锚定（source 名精确匹配）
   - `clip`：CLIP 文找图（图-文同空间余弦召回，低权重参与 RRF——视觉相似≠语义相关）
3. **加权 RRF 融合**（k=60，graph/question 加权）→ 来源多样性 → 证据链输出
4. **生成**：证据锚定提示词 + 引用溯源 + 归因规则（agent_quick.md）

### 知识图谱

- `graph/infrastructure/neo4j_store.py`（Neo4jStore）；`graph/application/graph_qa.py`：意图路由 + **模板化 Cypher（T1-T6，不自由生成）**，失败自动降级文本检索；意图词表在 `graph/domain/intent_router.py`
- 节点：Artifact 2601 / Site 409 / Era 11 / Kiln 4；关系：EXCAVATED_AT 2803 / BELONGS_TO 2575 / BELONGS_TO_KILN 70
- Neo4j 连接信息在 `.env`，未配置时 graph=None 静默降级（文本检索不受影响）

### 多模态（图片链路）

- **上传文档图片**：Docling 导出图 → `{source}.images/` 目录 → VLM 描述（无 key 时 contextual 占位）→ 图片块入库（`chunk_type: "image"`，`image_path: "/api/uploads/..."`）
- **数据集图片**：`image_index.json` 映射表（source → images），检索命中文本块时随 sources 返回；图片本身不进向量库
- **河南图注级图片块**：`henan_images.json`（图注/栏目/语境）+ 3820 图片块入库，检索可精确到张
- 图片块内容以 `【图片·图N】图注` 或 `【文档图片·第N页】` 开头，用于区分来源

### 数据目录（`backend/src/data/`）

> 运行数据（chroma/images/uploads/logs）本地生成、不入库——克隆仓库后需重新爬取/入库;仅小 JSON 数据（爬取文本/图注清单/映射表）随仓库分发。

| 路径 | 内容 |
|---|---|
| `chroma/` | ChromaDB 持久化（documents + questions 两个 collection 共用） |
| `uploads/` | 上传文档（`{timestamp}_{文件名}.pdf` + `.images/` 解析图片目录） |
| `images/` | 数据集图片（bronze/henan/porcelain 三个子目录） |
| `henan_museum.json` | 河南博物院爬取文本（283 条） |
| `henan_images.json` | 河南图注级图片清单 |
| `image_index.json` | source → 图片 URL 映射表 |
| `logs/` | query_trace.jsonl 查询轨迹日志 |

### 数据契约（P1 治理后规范）

- `chunk_type`：文本块 `text`，图片块 `image`（`where={"chunk_type": "image"}` 精确直检图片块）
- `image_path`：一律带前缀完整路径（`/api/uploads/...` 或 `/api/images/...`）；映射表存相对路径、服务层拼前缀
- `source` 命名规范（`ingestion/domain/source_contract.py` 的 `validate_source()` 强制校验新接入数据源）：

```
{域}-{实体}         文本块    青铜-叩鼎 / 宣德-青花梅瓶 / 河南博物院-妇好墓玉龙
{域}-{实体}#图      图片块    河南博物院-妇好墓玉龙#图（#图 后缀 = 图片块）
{timestamp}_{file}  上传文档  （天然时间戳前缀）
```

### 入库脚本（`backend/scripts/`）

- **统一管道**：`ingestion/` 域（`application/ingest_base.py` BaseIngestor + `application/ingest_service.py` registry + CLI，新数据源用此接入）
- 历史脚本（标 deprecated 不重写，数据已验证入库）：`import_*_chroma.py`、`import_*_neo4j.py`、`generate_questions*.py`、`crawl_henan_*.py`、`import_dataset_images.py`
- 迁移/清理脚本：`migrate_chunk_type.py`、`migrate_image_path_prefix.py`、`cleanup_test_data.py`（均幂等，可复跑）

## 编码约定

- **前端**：Vue 3 `<script setup lang="ts">` + Pinia + Less（`.less` 或 `<style lang="less">`，不用纯 CSS/SCSS）；hash 路由（`/#/chat`、`/#/knowledge`、`/#/library`）
- **语言**——所有 UI 文本、注释、配置均为中文。
- **API 密钥安全**——DeepSeek、阿里云百炼（Qwen-VL 图注）、Neo4j 凭证经 `backend/.env` 环境变量注入（core/config.py 读取），`.env` 不入库。切勿将密钥复制或对外暴露。
- **无测试**——前端无测试；后端 pytest（185 passed）。检索改动后必须跑 `eval/run_eval.py --retrieval-only` 确认无召回回归。
- **面试叙事核心文档**：`docs/rag-optimization-blueprint.md`（全链路蓝图）、`docs/knowledge-graph-explainer.md`（图谱讲解）、`docs/roadmap.md`（待办索引）。
