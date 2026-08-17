# DocuMind · 智慧文物探索

> 面向中国文物的**多模态 RAG 智能问答系统**:六路混合检索 + 知识图谱 + CLIP 零样本图像识别,对私有文物知识库进行带引用溯源的问答、拍照识物、深度研究与图谱探索。

## 核心能力

| 能力 | 描述 |
|---|---|
| 💬 快速问答 | 六路混合召回 + 加权 RRF 融合,回答自带 `[1][2]` 引用,点击直达证据原文,图文并排对照 |
| 🔍 深度研究 | 史官 / 工艺 / 关联三专家 Agent 并行分析,著述 Agent 融合成带思维导图的综合报告 |
| 📷 拍照识物 | 上传文物照片,CLIP 图-图余弦在 9468 张图索引上零样本识别(无需训练),置信度门控防幻觉 |
| 🕸 图谱探索 | 2601 件文物 / 409 遗址 / 11 朝代 / 4 窑口的 Neo4j 知识图谱,ECharts 交互式展开 |
| 📄 文档入库 | 上传 PDF 异步解析入库(结构感知切分 + 图片抽取 + VLM 图注),秒级可检索 |
| 📊 全链路可观测 | 每次检索六路命中数与耗时实时流式推送诊断面板,jsonl 落盘 |

## 📸 演示

> 以下示例均使用知识库内**真实文物数据与图片**(文本 / 图谱 / 图片三态齐全)。

### 快速问答 —— 六路检索 + 引用溯源 + 可观测

> 提问:**「妇好墓玉龙是什么时期的玉器?它有什么特点?」**

![快速问答](demo/quick-answer.png)

- 六路混合检索,实体锚定精确命中 + 图谱锚定查年代关系
- 回答携带 `[1][2]` 引用徽章,点击直达证据原文,图文并排对照
- 诊断面板实时展示六路命中数与耗时

### 深度研究 —— 三专家 Agent + 思维导图

> 提问:**「妇好墓玉龙的器型特征与文化意义」**

![深度研究](demo/deep-research.png)

- 史官 / 工艺 / 关联三专家并行分析,流水线面板实时进度
- 著述 Agent 融合出带思维导图的综合研究报告

### 拍照识物 —— CLIP 零样本识别

> 上传妇好墓玉龙照片 + 提问:**「这是什么文物?」**

![拍照识物](demo/vision-recognition.png)

- 无需训练,CLIP 图-图余弦在 9468 张图索引上零样本识别
- 识别徽章 + 图文证据链,置信度门控防幻觉

## 检索流水线(项目核心)

```
用户问题
   │
   ▼
查询理解 ── 改写(代词消解) → 意图路由 → CRAG 质量评估 → 实体锚定
   │
   ▼
六路混合召回 ────────────────────────────────────────────────┐
  semantic  向量检索(树状层级剪枝:窑口→器物→鉴定维度)        │
  question  假设性问题索引 Q-to-Q(16152 条,入库侧预生成)     │
  bm25      jieba 分词关键词匹配                              │
  graph     Neo4j 图谱锚定(LLM 提实体 → 扩展词补检索)         │
  entity    文本实体名精确匹配(短条目 embedding 劣势补偿)     │
  clip      CLIP 文找图(图-文同空间余弦,低权重防挤占)         │
   │
   ▼
加权 RRF 融合(k=60,路径权重网格实验定标)→ 块级噪声过滤 → 证据链
   │
   ▼
证据锚定生成 ── 全局编号引用溯源 → 流式回答(NDJSON:思维链/内容/来源/轨迹)
```

**关键设计决策**:

- **路径权重来自实验而非拍脑袋**:graph 路与 semantic 重叠降权 0.5;CLIP"视觉相似≠语义相关"(汝窑/钧窑釉色混淆)降至 0.1;图注块 0.3 不与文本同权竞争
- **入库侧假设性问题索引**:为每个 chunk 预生成 3 条用户可能问的问题,查询侧零额外延迟地弥合"问法 vs 陈述句"语义鸿沟(与 HyDE 的区别:不增加查询延迟)
- **模板化 Cypher(T1-T6)**:图谱查询不自由生成,防注入;图谱宕机 60s 冷却 + 静默降级文本检索
- **结构化失败降级**:Neo4j / CLIP / 百炼任一不可用只降级对应能力,不阻塞主链路;重模型启动后台预热,首问不被 10-30s 加载阻塞

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 · Vite · TypeScript · Pinia · Less · ECharts · Mind Elixir |
| 后端 | Python · FastAPI · Pydantic · uvicorn · DDD 四层 + DI 组合根 |
| 向量检索 | ChromaDB · BGE-small-zh-v1.5 · BM25(jieba + rank_bm25) |
| 知识图谱 | Neo4j · neomodel ODM |
| 多模态 | Chinese-CLIP(图文互检)· Qwen-VL(图注)· Docling(PDF 解析) |
| LLM | DeepSeek(NDJSON 流式)|

## 快速开始

### 1. 启动后端(端口 5172)

```bash
cd backend
cp .env.example .env          # 必填 DEEPSEEK_API_KEY;Neo4j/百炼可选,缺省自动降级
uv sync
uv run python src/main.py
```

本地模型(BGE / CLIP / reranker)从 ModelScope 缓存加载,默认路径见 `backend/src/core/config.py`,可用环境变量覆盖。

### 2. 启动前端(端口 5173)

```bash
cd frontend
pnpm install
pnpm dev
```

浏览器访问 `http://localhost:5173`。

### 3. 可选:Neo4j 知识图谱

未启动时图谱问答与图谱探索不可用,其余功能不受影响。启动后执行图谱入库脚本(见下)。

### 4. 数据重建(仓库不含运行数据)

运行数据(chroma 向量库 / 数据集图片 / 爬取文本 / 上传文档 / 日志)**不入库**,克隆后按依赖顺序重建:

```bash
cd backend
# ① 爬取河南博物院文本与图片(含图注)
uv run python scripts/crawl_henan_museum.py
uv run python scripts/crawl_henan_images.py

# ② 数据集图片落盘 + 映射表(数据集目录经环境变量配置)
#    BRONZE_DATASET_DIR / PORCELAIN_DATASET_DIR,默认 datasets/bronze、datasets/porcelain
uv run python scripts/import_dataset_images.py --source bronze
uv run python scripts/import_dataset_images.py --source porcelain
uv run python scripts/import_dataset_images.py --source henan

# ③ 文本/图片块入 Chroma
uv run python scripts/import_bronze_chroma.py
uv run python scripts/import_porcelain_chroma.py
uv run python scripts/import_henan_chroma.py
uv run python scripts/import_henan_image_chunks.py

# ④ 索引:CLIP 图片索引 + 假设性问题索引
uv run python scripts/import_clip_images.py
uv run python scripts/generate_questions.py

# ⑤ 知识图谱(需 Neo4j 运行中)
uv run python scripts/import_bronze_neo4j.py
uv run python scripts/import_porcelain_neo4j.py
uv run python scripts/import_henan_neo4j.py
```

上传 PDF 走统一入库管道 `python -m ingestion --source X`(含结构感知切分与问题生成)。

## 项目结构

```
frontend/                          Vue 3 单页应用
  src/
    views/                         问答 · 知识图谱 · 文库
    components/                    证据面板 · 思维导图 · 流水线诊断
    stores/                        Pinia 状态(chat / knowledge / app)
backend/
  src/
    core/                          配置 · DI 组合根 · 查询轨迹 · 预热
    interfaces/                    6 个跨域抽象接口(可插拔)
    retrieval/                     六路召回 · RRF · 树剪枝 · 噪声过滤
    conversation/                 问答域(四层):编排 · 深度研究 · 多轮记忆
    graph/                         图谱域(四层):模板化 Cypher T1-T6
    multimodal/                    CLIP 图文互检 · 图片证据链 · 资产门面
    ingestion/                     入库域(四层):PDF 管道 · 统一 ingest CLI
    documents/                     文档管理:上传任务 · 指纹索引
  scripts/                         数据重建脚本(爬取 / 入库 / 索引)
```

## 安全

API 密钥(DeepSeek / 阿里云百炼 / Neo4j)经 `backend/.env` 注入,**`.env` 不入库**;仓库仅提供 `.env.example` 模板,数据与文档目录均不入库。
