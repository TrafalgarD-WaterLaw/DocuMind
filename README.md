# 智慧文物探索 · DocuMind

面向中国文物的多模态 RAG 智能问答系统。结合**知识图谱(Neo4j)**、**六路混合检索**、**CLIP 零样本图像识别**与**交互式可视化**,对私有文物知识库进行带引用溯源的问答、拍照识物、深度研究与图谱探索。

## ✨ 核心特性

- **六路混合检索 + 加权 RRF 融合**——语义(树状层级剪枝)/ 假设性问题索引 / BM25 / 图谱锚定 / 实体锚定 / CLIP 文找图,各路权重由网格实验定标;RRF 分数分布做块级噪声过滤
- **假设性问题索引(Q-to-Q)**——入库侧 LLM 预生成 16152 条问题索引,弥补"用户问法 vs 库内陈述句"的语义鸿沟,查询侧零额外延迟
- **证据链与引用溯源**——证据块全局编号锚定原文,回答中引用可点击跳转证据面板,图文并排对照;引用一致性评测兜底
- **CLIP 图找图零样本识别**——拍照识物,9468 张图索引上图-图余弦检索,置信度门控防幻觉,识别-检索一体
- **知识图谱增强**——2601 件文物 / 409 遗址 / 11 朝代,模板化 Cypher(T1-T6,不自由生成),图谱宕机自动降级文本检索
- **三专家深度研究 Agent**——史官/工艺/关联并行分析,著述 Agent 融合成带思维导图的综合报告,NDJSON 流式输出
- **全链路可观测**——每次检索六路命中数/耗时实时流式推送前端诊断面板,jsonl 落盘

## 🏗 系统架构

```
┌────────────── 前端 frontend/ (Vue3 + Vite + TS + Pinia) ──────────────┐
│  问答页(引用联动/证据面板) · 知识图谱页(ECharts) · 诊断面板 · 文库      │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ NDJSON 流式 (ReadableStream)
┌──────────────────────────────▼─────────── 后端 backend/ (FastAPI) ────┐
│  查询理解 ─→ 六路混合召回 ─→ 加权 RRF 融合 ─→ 证据锚定生成(引用溯源)   │
│  semantic·question·bm25·graph·entity·clip                              │
│                                                                        │
│  ChromaDB(文本/图片块)   Neo4j(知识图谱)   BGE/CLIP(本地模型)   DeepSeek│
└────────────────────────────────────────────────────────────────────────┘
```

DDD 四层分层(domain / application / infrastructure / interfaces)+ DI 组合根;检索域独立算法包,六个跨域抽象接口可插拔。

## 🧩 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 · Vite · TypeScript · Pinia · Less · ECharts · Mind Elixir |
| 后端 | Python · FastAPI · Pydantic · uvicorn |
| 检索 | ChromaDB · BGE-small-zh-v1.5 · BM25(jieba + rank_bm25) · bge-reranker-v2-m3(实验后禁用) |
| 图谱 | Neo4j · neomodel ODM |
| 多模态 | Chinese-CLIP · Qwen-VL(图注) · Docling(PDF 解析) |
| LLM | DeepSeek(流式 NDJSON,思维链/引用/轨迹事件) |

## 🚀 快速开始

```bash
# ── 后端(端口 5172)──
cd backend
cp .env.example .env        # 填入 DEEPSEEK_API_KEY 等(必填);Neo4j/百炼可选,缺省自动降级
uv sync
uv run python src/main.py

# ── 前端(端口 5173)──
cd frontend
pnpm install
pnpm dev
```

**本地模型**:BGE / CLIP / reranker 从 ModelScope 缓存加载,默认路径见 `backend/src/core/config.py`(可用环境变量覆盖)。

## 📦 数据说明

运行数据(chroma 向量库 / 数据集图片 / 上传文档 / 爬取文本 / 轨迹日志)均**不入库**,克隆后需自行重建:

- 爬取与入库脚本在 `backend/scripts/`(历史脚本,标 deprecated 仍可用);统一入口 `python -m ingestion --source X`
- 数据集图片(bronze / henan / porcelain 三个子目录)来自公开文物图片数据集,需自行获取放入 `backend/src/data/images/`
- Neo4j 图谱由 `import_*_neo4j.py` 系列脚本构建

## 📁 项目结构

```
frontend/    Vue 3 单页应用
backend/
  src/
    core/          配置 · DI 组合根 · 轨迹 · 预热
    interfaces/    6 个跨域抽象接口
    retrieval/     六路召回 · RRF · 树剪枝 · 噪声过滤
    conversation/  问答域(四层):问答编排 · 深度研究 · 多轮记忆
    graph/         图谱域(四层):模板化 Cypher T1-T6
    multimodal/    CLIP 图文互检 · 图片证据链 · 资产门面
    ingestion/     入库域(四层):PDF 管道 · 统一 ingest CLI
    documents/     文档管理域:上传任务 · 指纹索引
  scripts/         爬取/入库/迁移脚本
```

## ⚠️ 安全

API 密钥(DeepSeek / 百炼 / Neo4j)经 `backend/.env` 注入,**`.env` 不入库**;仓库仅提供 `.env.example` 模板。
