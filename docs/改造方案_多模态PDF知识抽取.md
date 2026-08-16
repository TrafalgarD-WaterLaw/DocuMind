# 多模态 PDF 智能解析与知识抽取系统 — 改造方案

## 零、改造起点

当前项目：**智慧文物探索**（Smart Artifact Explorer）

目标项目：**多模态 PDF 智能解析与知识抽取系统**

核心思路：**80% 架构复用，20% 新能力注入**。当前项目的基础设施（FastAPI + Vue3 前端 + RAG 管道 + Neo4j + DeepSeek 调用）全部保留，改造集中在三块：PDF 解析增强、Agent 角色重定义、前端主题切换。

---

## 一、总体架构

```
┌─────────────────────────────────────────────────────────┐
│                      前端 (Vue 3 + Vite)                  │
│  ┌──────────┐ ┌──────────────┐ ┌────────┐ ┌─────────┐  │
│  │ PDF 上传  │ │ 流式问答聊天  │ │ 思维导图│ │知识图谱 │  │
│  │ 解析进度  │ │ (会话管理)    │ │ (Mind   │ │(ECharts)│  │
│  │          │ │              │ │  Elixir)│ │         │  │
│  └──────────┘ └──────────────┘ └────────┘ └─────────┘  │
├─────────────────────────────────────────────────────────┤
│                     API 层 (FastAPI)                       │
│  POST /api/upload     PDF 上传解析                         │
│  POST /api/chat       流式问答 (NDJSON)                    │
│  POST /api/knowledge  知识图谱查询                          │
├─────────────────────────────────────────────────────────┤
│                    服务层 (Services)                       │
│  ┌──────────────┐  ┌────────────┐  ┌──────────────────┐ │
│  │ PDF 解析引擎  │  │ RAG 管道   │  │ 多 Agent 协作    │ │
│  │ · 版面分析    │  │ · 混合检索  │  │ · Orchestrator   │ │
│  │ · 表格识别    │  │ · 向量索引  │  │ · 解析 Expert    │ │
│  │ · 公式OCR    │  │ · LLM 生成  │  │ · 实体 Expert    │ │
│  │ · 图片描述    │  │            │  │ · 摘要 Expert    │ │
│  └──────────────┘  └────────────┘  └──────────────────┘ │
├─────────────────────────────────────────────────────────┤
│                    存储层                                  │
│     FAISS 向量库    │    Neo4j 知识图谱    │   JSON 文档   │
└─────────────────────────────────────────────────────────┘
```

---

## 二、分阶段改造计划

### 第一阶段：基础设施复用验证（1-2 天）

#### 1.1 确认可复用模块

| 模块 | 文件 | 复用方式 |
|---|---|---|
| DeepSeek 流式调用 | `services/llm.py` | **零改动**，已有 `MODEL = "deepseek-v4-flash"` |
| FAISS 索引构建 | `services/indexer.py` | 保留，增强 `load_pdf()` 方法 |
| BM25+FAISS 混合检索 | `services/retriever.py` | **零改动**，架构完全通用 |
| NDJSON 流式问答 | `api/chat.py` | 改 system_prompt 和 reasoning 逻辑 |
| Pydantic 数据模型 | `models/*.py` | 新增请求/响应字段，保留基础结构 |
| Neo4j 知识图谱 | `services/knowledge.py` | 改本体模型（文物→文档概念） |
| 配置管理 | `core/config.py` | 新增 PDF 解析相关配置项 |

#### 1.2 确认可复用前端组件

| 组件 | 文件 | 复用方式 |
|---|---|---|
| 聊天面板 | `ChatPanel.vue` | **直接复用**，改标题和主题色 |
| 消息列表 | `ChatMessageList.vue` | **零改动** |
| 输入框 | `ChatInput.vue` | **零改动** |
| 思维导图 | `MindMapPanel.vue` | 保留，展示 PDF 知识结构 |
| 知识图谱 | `KnowledgeGraphPanel.vue` | 保留，展示文档实体关系 |
| Pinia Store | `stores/chat.ts` | 保留架构，扩展 upload 相关 state |

#### 1.3 快速验证

换一个 prompt 就跑通：
```
1. 把 api/chat.py 的 system_prompt 改为"文档知识分析专家"
2. 准备 3-5 本技术 PDF 放入 data/ 目录
3. 运行 indexer 构建索引
4. 启动服务，问"这本 PDF 讲了什么？"
```

---

### 第二阶段：PDF 多模态解析引擎（3-5 天）

这是**整个项目的技术亮点**，也是区别于普通 RAG 的核心。

#### 2.1 新增文件

```
backend/src/services/pdf/
├── __init__.py
├── parser.py      ← 多模态解析主流程
├── layout.py      ← 版面分析（集成 MinerU/Surya/Docling）
├── table.py       ← 表格识别（TableFormer 集成）
├── formula.py     ← 公式 OCR（LaTeX 还原）
└── image.py       ← 图片语义描述（VLM）
```

#### 2.2 核心实现：`parser.py`

```python
from dataclasses import dataclass, field
from enum import Enum


class BlockType(Enum):
    TEXT = "text"
    TABLE = "table"
    FORMULA = "formula"
    IMAGE = "image"
    HEADING = "heading"
    LIST = "list"
    CODE = "code"


@dataclass
class PageBlock:
    """PDF 页面中的一个语义块"""
    type: BlockType
    content: str                          # 文本/表格Markdown/公式LaTeX/图片描述
    bbox: tuple[int, int, int, int]       # 边界框 (x1, y1, x2, y2)
    page_num: int
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)


class MultiModalPDFParser:
    """多模态 PDF 解析器

    Pipeline:
      1. 版面分析 → 识别文本/表格/公式/图片区域
      2. 按区域类型分别处理
      3. 按阅读顺序拼接为结构化 Markdown
    """

    def __init__(self, layout_model: str = "docling"):
        self.layout_model = layout_model
        self._init_engines()

    def _init_engines(self):
        """初始化各子引擎"""
        self.table_engine = None    # TableFormer / PaddleTable
        self.formula_engine = None  # LaTeX-OCR
        self.image_engine = None    # VLM（可选）

    def parse(self, file_path: str) -> list[PageBlock]:
        """完整解析流程

        Args:
            file_path: PDF 文件路径

        Returns:
            按阅读顺序排列的语义块列表
        """
        # 1. 版面分析
        layout_blocks = self._detect_layout(file_path)
        # 2. 分类处理
        result = []
        for block in layout_blocks:
            parsed = self._process_block(block)
            result.append(parsed)
        # 3. 按阅读顺序排序
        result.sort(key=lambda b: (b.page_num, b.bbox[1], b.bbox[0]))
        return result

    def _detect_layout(self, file_path: str) -> list:
        """版面检测——支持三种引擎切换"""
        if self.layout_model == "mineru":
            return self._detect_mineru(file_path)
        elif self.layout_model == "docling":
            return self._detect_docling(file_path)
        elif self.layout_model == "surya":
            return self._detect_surya(file_path)
        raise ValueError(f"Unknown layout model: {self.layout_model}")

    def _detect_docling(self, file_path: str) -> list:
        """IBM Docling 版面分析"""
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(file_path)
        # 转换为统一的 PageBlock 格式
        return self._docling_to_blocks(result)

    def _detect_surya(self, file_path: str) -> list:
        """Surya 版面分析（轻量级备选）"""
        from surya.detection import batch_text_detection
        from surya.layout import batch_layout_detection
        from surya.ordering import batch_ordering
        # ... 调用 surya 模型
        pass

    def _process_block(self, block) -> PageBlock:
        """根据块类型调用对应处理器"""
        if block.type == BlockType.TABLE:
            return self._process_table(block)
        elif block.type == BlockType.FORMULA:
            return self._process_formula(block)
        elif block.type == BlockType.IMAGE:
            return self._process_image(block)
        else:
            return block  # 文本块直接返回

    def _process_table(self, block) -> PageBlock:
        """表格识别 → Markdown 表格"""
        # 使用 TableFormer 或 PaddleTable
        # 输入：表格区域图像
        # 输出：Markdown 格式表格
        md_table = self.table_engine.recognize(block.image)
        block.content = md_table
        return block

    def _process_formula(self, block) -> PageBlock:
        """公式识别 → LaTeX"""
        # 使用 LaTeX-OCR
        latex = self.formula_engine.recognize(block.image)
        block.content = f"$${latex}$$"
        return block

    def _process_image(self, block) -> PageBlock:
        """图片语义描述（可选 VLM 增强）"""
        if self.image_engine:
            desc = self.image_engine.describe(block.image)
            block.content = f"[图片描述: {desc}]"
        else:
            block.content = "[图片]"
        return block

    def assemble_markdown(self, blocks: list[PageBlock]) -> str:
        """将语义块拼接为结构化 Markdown"""
        md = []
        for block in blocks:
            if block.type == BlockType.HEADING:
                md.append(f"\n## {block.content}\n")
            elif block.type == BlockType.TABLE:
                md.append(f"\n{block.content}\n")
            elif block.type == BlockType.FORMULA:
                md.append(f"\n{block.content}\n")
            elif block.type == BlockType.IMAGE:
                md.append(f"\n> {block.content}\n")
            elif block.type == BlockType.LIST:
                md.append(block.content)
            else:
                md.append(block.content)
        return "\n\n".join(md)
```

#### 2.3 改造 `indexer.py` — 集成多模态解析

```python
# 改造后的 load_pdf 方法
def load_pdf(
    self,
    file_path: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    use_multimodal: bool = True,
) -> list[Document]:
    """多模态 PDF 加载与分块"""
    if use_multimodal:
        # 新：多模态解析 pipeline
        from services.pdf.parser import MultiModalPDFParser
        parser = MultiModalPDFParser(layout_model="docling")
        blocks = parser.parse(file_path)
        markdown_content = parser.assemble_markdown(blocks)

        # 按 Markdown 结构分块（保留表格/公式完整性）
        splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"), ("##", "h2"), ("###", "h3"),
            ]
        )
        chunks = splitter.split_text(markdown_content)
    else:
        # 旧：基础 PyPDFLoader（回退方案）
        loader = PyPDFLoader(file_path, extract_images=True)
        documents = loader.load()
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        chunks = splitter.split_documents(documents)

    for chunk in chunks:
        chunk.metadata["chunk_id"] = str(uuid.uuid4())
        chunk.metadata["parser"] = "multimodal" if use_multimodal else "basic"

    return chunks
```

#### 2.4 新增 API 接口

```python
# api/upload.py
@router.post("/upload")
async def upload_pdf(file: UploadFile, request: Request):
    """上传 PDF 并触发解析 → 索引构建"""
    # 1. 保存文件
    file_path = save_uploaded_file(file)
    # 2. 多模态解析
    parser = MultiModalPDFParser()
    blocks = parser.parse(file_path)
    markdown = parser.assemble_markdown(blocks)
    # 3. 构建索引
    indexer = IndexerService()
    chunks = indexer.load_pdf(file_path, use_multimodal=True)
    indexer.save_chunks(chunks, ...)
    indexer.build_faiss(chunks, ...)
    # 4. 返回解析结果概览
    return {
        "file_name": file.filename,
        "pages": len(set(b.page_num for b in blocks)),
        "blocks": {
            "text": sum(1 for b in blocks if b.type == BlockType.TEXT),
            "table": sum(1 for b in blocks if b.type == BlockType.TABLE),
            "formula": sum(1 for b in blocks if b.type == BlockType.FORMULA),
            "image": sum(1 for b in blocks if b.type == BlockType.IMAGE),
        },
        "markdown_preview": markdown[:500],
    }
```

---

### 第三阶段：多 Agent 协作改造（3-5 天）

#### 3.1 当前 Agent 架构

```
backend/src/services/agent/
├── orchestrator.py   ← 任务分发
├── experts.py        ← 专家 Agent
├── synthesizer.py    ← 结果整合
└── types.py          ← 类型定义
```

#### 3.2 Agent 角色重定义

| 原角色 | 新角色 | 职责 |
|---|---|---|
| 协调 Agent | **编排 Agent** | 分析 PDF 结构，决定调用哪些 Expert |
| 博闻 Agent | **解析 Agent** | 执行多模态解析（版面/表格/公式） |
| 著述 Agent | **实体 Agent** | 抽取关键实体（术语/概念/人名/日期） |
| — | **关系 Agent** (新增) | 构建实体间关系（引用/依赖/层级） |
| — | **摘要 Agent** (新增) | 生成分层摘要（全文/章节/段落） |
| (synthesizer) | **整合 Agent** | 汇总各 Agent 输出，生成最终报告 |

#### 3.3 对话流程改造

```python
# 改造后的 chat.py 核心流程

async def _generate_ndjson(query, request, history):
    # 阶段 1: 编排 Agent — 分析问题类型
    #   判断用户是在问"总结全文" / "查找某个概念" / "对比两篇文章"
    yield _serialize_event(StreamEvent(type="agent_step", data={
        "agent": "编排", "status": "running",
        "detail": "正在分析问题类型..."
    }))

    # 阶段 2: 检索 — 混合检索相关文档块
    docs = retriever.retrieve(query, k=5)
    yield _serialize_event(StreamEvent(type="agent_step", data={
        "agent": "检索", "status": "done",
        "detail": f"从 {len(docs)} 篇文档中找到相关内容"
    }))

    # 阶段 3: 实体 Agent — 从检索结果中提取关键实体
    yield _serialize_event(StreamEvent(type="agent_step", data={
        "agent": "实体", "status": "running",
        "detail": "正在抽取关键实体和术语..."
    }))
    entities = await extract_entities(docs)
    yield _serialize_event(StreamEvent(type="agent_step", data={
        "agent": "实体", "status": "done",
        "detail": f"识别到 {len(entities)} 个关键实体"
    }))

    # 阶段 4: 摘要 Agent — 生成分层摘要
    yield _serialize_event(StreamEvent(type="agent_step", data={
        "agent": "摘要", "status": "running",
        "detail": "正在生成内容摘要..."
    }))
    summary = await generate_summary(docs, query)
    yield _serialize_event(StreamEvent(type="agent_step", data={
        "agent": "摘要", "status": "done",
        "detail": "摘要生成完成"
    }))

    # 阶段 5: 整合 Agent — 流式生成最终回答
    system_prompt = _build_system_prompt(docs, entities, summary)
    async for chunk in llm_service.chat_stream(messages):
        yield _serialize_event(StreamEvent(type="content", data=chunk))

    # 阶段 6: 输出结构化 Markdown（思维导图渲染）
    yield _serialize_event(StreamEvent(type="markdown_dict", data={
        "sections": [
            {"title": "核心概念", "items": entities},
            {"title": "内容摘要", "items": summary},
            {"title": "相关文档", "items": [d["source"] for d in docs]},
        ]
    }))
```

#### 3.4 与 ChatDev 的呼应（简历串联点）

| ChatDev 概念 | PDF 系统对应 | 可讲的故事 |
|---|---|---|
| ChatChain 流水线 | Agent 编排流程 | "借鉴 ChatDev 的链式编排思想" |
| Phase 阶段执行 | Expert 分工协作 | "从串行 Phase 演进到并行 Expert" |
| Self-Reflection | 检索结果核查 | "引入反思机制校验实体抽取准确率" |
| ChatEnv 全局状态 | llm_service + retriever | "全局上下文在 Agent 间流转" |

---

### 第四阶段：前端改造（2-3 天）

#### 4.1 改造清单

| 组件 | 改什么 | 工作量 |
|---|---|---|
| 全局主题 | Less 变量：`--color-gold` → 蓝/青色系 | 30 min |
| `AppSidebar.vue` | Logo、导航文案：文物→文档分析 | 30 min |
| `HomeView.vue` | 首页文案、功能卡片描述 | 1 hour |
| `ChatPanel.vue` | 标题、Agent 步骤名称 | 30 min |
| `DeepQAView.vue` | 布局保留 | 0 |
| `MindMapPanel.vue` | 保留（展示知识结构） | 0 |
| `KnowledgeGraphPanel.vue` | 保留（展示实体关系） | 0 |
| **新增：`UploadPanel.vue`** | PDF 拖拽上传 + 解析进度条 | 1 day |
| **新增：`ParseResultCard.vue`** | 解析结果预览卡片 | 0.5 day |

#### 4.2 新增组件：文件上传面板

```vue
<!-- components/UploadPanel.vue -->
<template>
  <div class="upload-panel">
    <el-upload
      class="upload-area"
      drag
      :action="uploadUrl"
      :on-success="onParseSuccess"
      :on-progress="onProgress"
      accept=".pdf"
      multiple
    >
      <div class="upload-content">
        <svg><!-- 上传图标 --></svg>
        <p>拖拽 PDF 文件到此处，或点击上传</p>
        <span>支持多文件批量上传，单文件最大 50MB</span>
      </div>
    </el-upload>

    <!-- 解析进度 -->
    <div v-if="parsing" class="parse-progress">
      <div class="progress-header">
        <span>{{ currentFile }}</span>
        <span>{{ progress }}%</span>
      </div>
      <el-progress
        :percentage="progress"
        :stroke-width="6"
        :color="progressColors"
      />
      <div class="progress-detail">
        版面分析 {{ layoutDone ? '✓' : '...' }} →
        表格识别 {{ tableDone ? '✓' : '...' }} →
        实体抽取 {{ entityDone ? '✓' : '...' }} →
        索引构建 {{ indexDone ? '✓' : '...' }}
      </div>
    </div>

    <!-- 解析结果概览 -->
    <div v-if="parseResult" class="parse-result">
      <el-card v-for="r in parseResult" :key="r.file_name">
        <template #header>{{ r.file_name }}</template>
        <div class="result-stats">
          <el-tag>文本块 {{ r.blocks.text }}</el-tag>
          <el-tag type="success">表格 {{ r.blocks.table }}</el-tag>
          <el-tag type="warning">公式 {{ r.blocks.formula }}</el-tag>
          <el-tag type="info">图片 {{ r.blocks.image }}</el-tag>
        </div>
        <div class="result-preview">{{ r.markdown_preview }}</div>
      </el-card>
    </div>
  </div>
</template>
```

---

### 第五阶段：系统整合与测试（1-2 天）

#### 5.1 端到端测试用例

```
1. 上传单页技术报告 → 验证文本解析 + 向量检索
2. 上传含表格的文档 → 验证表格识别 + Markdown 输出
3. 上传含数学公式的论文 → 验证公式 OCR + LaTeX 还原
4. 上传扫描版 PDF → 验证 OCR + 版面分析
5. 连续提问"这篇文章的核心结论是什么？" → 验证 RAG 链路
6. 提问"对比第一段和第三段的观点" → 验证多 Agent 协作
```

#### 5.2 配置文件最终版

```ini
# .env
# === DeepSeek ===
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com

# === 嵌入模型 ===
EMBEDDING_MODEL_ID=iic/nlp_corom_sentence-embedding_chinese-base

# === PDF 解析 ===
PDF_LAYOUT_ENGINE=docling   # docling | mineru | surya
PDF_TABLE_ENGINE=auto       # auto | tableformer | paddle
PDF_FORMULA_OCR=true        # 是否启���公式识别
PDF_IMAGE_DESC=false        # 是否启用 VLM 图片描述（消耗 token）

# === RAG ===
RETRIEVER_TOP_K=5
CHUNK_SIZE=500
CHUNK_OVERLAP=50

# === Neo4j ===
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password

# === Mock 模式 ===
MOCK_LLM=false
```

---

## 三、技术栈总览

| 层级 | 技术 | 用途 |
|---|---|---|
| PDF 解析 | MinerU / Docling / Surya | 版面分析、表格识别、公式 OCR |
| 后端框架 | FastAPI + Uvicorn | HTTP API + WebSocket 流式 |
| LLM | DeepSeek V4 Flash (OpenAI 兼容) | 流式问答、实体抽取、摘要生成 |
| 向量检索 | FAISS + BM25 (EnsembleRetriever) | 混合检索 |
| 嵌入模型 | ModelScope iic/nlp_corom | 中文语义向量化 |
| 知识图谱 | Neo4j | 文档概念实体关系存储 |
| 前端框架 | Vue 3 + Vite + TypeScript | SPA |
| 状态管理 | Pinia | 聊天会话、知识图谱、上传状态 |
| UI 库 | Element Plus | 上传组件、进度条、标签 |
| 可视化 | ECharts + Mind Elixir | 知识图谱 + 思维导图 |
| 样式 | Less | 组件样式 |

---

## 四、简历呈现建议

### 项目描述

> **多模态 PDF 智能解析与知识抽取系统**
>
> 独立设计并实现了一套端到端的多模态文档智能处理系统，支持 PDF 上传后自动完成版面分析、表格识别、公式 OCR 和图片语义描述，构建结构化知识库并支持自然语言问答。
>
> - **多模态解析引擎**：集成 Docling/MinerU 实现版面分析，结合 TableFormer 做表格结构识别，LaTeX-OCR 做公式还原，支持文本/表格/公式/图片四类元素的分类处理与结构化 Markdown 输出
> - **混合 RAG 管道**：基于 FAISS + BM25 的 EnsembleRetriever 实现词汇-语义双路检索，配合 DeepSeek V4 Flash 提供流式 NDJSON 知识问答
> - **多 Agent 协作**：借鉴 ChatDev 的 Agent 编排思想，设计编排/解析/实体/摘要/整合五 Agent 分工协作流程，支持从非结构化文档到结构化知识的自动转化
> - **知识图谱构建**：基于 Neo4j 存储文档概念实体及其关系，支持可视化探索和导航
>
> 技术栈：Python, FastAPI, DeepSeek API, FAISS, BM25, Neo4j, Vue 3, TypeScript, Pinia, ECharts, Mind Elixir, MinerU, Docling

### 面试可能的追问

| 问题 | 准备要点 |
|---|---|
| 多模态解析具体怎么做？ | 版面分析定位区域 → 按类型分派处理器（TableFormer/LaTeX-OCR）→ 阅读顺序拼接 |
| 表格识别怎么处理的？ | 先检测表格区域，再用 TableFormer 识别行列结构，输出 Markdown 表格保���语义 |
| 和 ChatDev 的 Agent 设计有什么不同？ | ChatDev 是串行链式（Phase→Phase），本系统是并行分工（多个 Expert 同时处理不同维度） |
| 为什么用 BM25 + FAISS 而不是纯向量？ | 词汇匹配对专业术语/编号/代码等精确查询更准确，与语义检索互补 |

---

## 五、时间估算

| 阶段 | 内容 | 天数 |
|---|---|---|
| 第一阶段 | 基础验证 + prompt 切换 | 1-2 天 |
| 第二阶段 | PDF 多模态解析引擎 | 3-5 天 |
| 第三阶段 | 多 Agent 协作改造 | 3-5 天 |
| 第四阶段 | 前端主题 + 上传组件 | 2-3 天 |
| 第五阶段 | 整合测试 + 简历文档 | 1-2 天 |
| **总计** | | **10-17 天** |
