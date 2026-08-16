# 文档上传管道深度分析（现状 + 业界对照 + 优化建议）

日期：2026-08-08
范围：上传文档从文件到可检索的全部环节——解析 / 切片 / 提取 / 图片 / 入库 / 检索集成 / 可观测性
定位：面试叙事核心资产（与 rag-optimization-blueprint.md 互补：蓝图是全局，本文是上传管道纵深）

## 〇、当前全链路总览（代码实证）

```
POST /api/upload → BackgroundTasks 异步任务（task_manager 状态机）
  → Docling 解析（版面/表格/公式/图片导出，PyPDF 回退）
  → 文本分块（chunker 结构感知：子块+父块）
  → 图片链路（导出 → QwenVL 描述（无 key 占位）→ 图片块 contextual 内容）
  → 入库（Chroma add + BM25 dirty）
  → 假设问题生成（LLM，失败不阻断）
  → 任务完成/失败（前端轮询 /upload/tasks/{id}）
```

## 一、解析（Docling）

### 现状
- `providers/parser/docling_parser.py`：Docling 2.114 全管线（版面/表格 markdown/公式 LaTeX/图片导出），`iterate_items` 按阅读顺序产出 DocumentBlock（type/page），图片单独导出到 `{source}.images/`（上限 20 张）
- 无 Docling 时 PyPDF 回退（纯文本，无结构）

### 问题
| # | 问题 | 严重度 |
|---|---|---|
| P1 | **无 OCR 配置**——扫描件/图片型 PDF 解析为空白 | ⚠️ 中（Docling 支持 OCR 但未开） |
| P2 | 表格转 Markdown 后语义弱——长表格是整块文本，无结构化（行列）利用 | ⚠️ 中 |
| P3 | 公式（LaTeX）块进检索但无人问津（场景无数学内容） | 低 |
| P4 | 解析无重试/失败分类（权限/损坏 PDF/超大文件无区分提示） | 低 |
| P5 | MAX_IMAGES=20 静默截断——第 21 张起无图片块（无提示） | 低 |

### 业界方案
- **MinerU（OpenDataLab）**：版面分析 + 公式识别（LaTeX）+ 阅读顺序重排，输出 Markdown/JSON——对扫描件、复杂排版强于 Docling；缺点重（GPU 友好）
- **RAGFlow DeepDoc**：版面识别（表格转 HTML，保留行列结构）+ OCR（Tesseract/飞桨）；表格以 HTML 保留结构（我们转 markdown 丢了行列语义）
- **unstructured**：`partition_pdf` 按文档类型（扫描/电子）自动选 OCR 或文本提取
- **Docling（IBM）**：本身即业界认可（我们已用）——可开 `do_ocr`（easyocr 回退）

### 建议（优先级）
1. **开启 Docling OCR**（扫描件可用，一行配置）——低成本高价值
2. 表格块：保留 Markdown 同时注入 `num_rows/num_cols` metadata（表格检索可过滤）；不做 Text2SQL（超范围）

## 二、切片

### 现状
- `services/chunker.py`：结构感知（段落/标题/表格/公式独立块 + 句子边界拆分）+ 父子分块（子块 ~250 检索 / 父块节 ≤1500 送 LLM）+ 句子边界截断
- 静态数据（青铜/瓷器/河南）为章节级切分（split_sections）
- 旧 `RecursiveCharacterTextSplitter(500/50)` 仅作 chunker 异常回退

### 问题
| # | 问题 | 严重度 |
|---|---|---|
| C1 | **文档级元数据缺失**——块 metadata 只有 source/chunk_type/block_type/page/parent_id；无文件名标题、无文档类型、无上传时间（检索过滤维度单一） | ⚠️ 中 |
| C2 | 超长表格块不拆分（整表一个子块，可能 >1500 字被父块截断） | 低 |
| C3 | 标题块/列表块短且零散（被噪声过滤裁掉，但浪费候选名额） | 低 |
| C4 | 无块间语义关联（父子是结构关联，无"相邻段落互为上下文"窗口） | 低 |

### 业界方案
- **Dify**：分段（auto/自定义分隔符）+ **父子分块（Parent-Child）**——与我们一致；另支持**文档级元数据注入**（标题/标签）
- **LlamaIndex**：`HierarchicalNodeParser`（层级分块）+ `SentenceWindowNodeParser`（句子窗口——检索命中句 → 展开前后 N 句送 LLM，与我们的父子分块同思想不同实现）
- **RAGFlow**：按版面块切分 + 块级重排（检索后按版面顺序重组上下文）

### 建议
1. **文档元数据注入**：上传时把 `file_name/size/uploaded_at` 写入所有块 metadata（低成本，检索过滤 + 展示维度扩展）
2. 句子窗口：在父子之上可选加"相邻段窗口"（父块已近似覆盖，优先级低）

## 三、提取（结构化信息）

### 现状
- 文本块直接入库；假设问题生成（`hypothesis.py`，每 chunk 3 问题 → question 索引）是唯一的"提取"环节
- **实体/关系不提取**——上传文档不进图谱（Neo4j 只有内置 Excel 数据）

### 问题
| # | 问题 | 严重度 |
|---|---|---|
| E1 | **上传文档与图谱隔离**——文档里的文物实体无法参与图谱问答/锚定 | ⚠️ 中（功能缺口） |
| E2 | 假设问题生成质量无评估（问题集无人工抽检） | 低 |
| E3 | 无文档摘要/关键词提取（检索外的文档理解维度） | 低 |

### 业界方案
- **图谱抽取**：微软 GraphRAG（文档 → LLM 抽实体关系 → 建图）——我们已有图谱体系，文档实体抽取后可**增量接入现有图**（entity 路已支持 source 名匹配，抽取后文档实体进图谱可扩展查询）
- **LlamaIndex**：摘要索引（文档级摘要进检索）、结构化抽取（Pydantic 模板）
- **Dify**：元数据自动提取（LLM 从文档抽取标签/作者/日期）

### 建议
1. **文档实体抽取**（成本 1-2 天）：上传时 LLM 抽实体（≤5 个）写入 source metadata → entity 路对上传文档生效（当前时间戳 source 无法实体锚定——**这是上传文档检索的实际短板**）
2. 可选：文档摘要块（第一块 = 文档摘要，提升长文档问答）

## 四、图片

### 现状
- Docling 导出 → `{source}.images/` → QwenVL 描述（无 key 时 NoopCaptioner 返回空 → contextual 占位：页面上下文前 120 字）→ 图片块（`chunk_type: image`，`{source}#图`，image_path `/api/uploads/...`）→ 直检通道（`where={"chunk_type": "image"}`）+ 前端展示
- 河南图注级图片块：图注配对 → 精确到张的检索（更成熟）

### 问题
| # | 问题 | 严重度 |
|---|---|---|
| IM1 | **无 key 时图片块是"页面文本"占位**——内容不含图片本身语义，检索"图里的 X"命中率低（contextual 120 字尽力而为） | ⚠️ 中（有 key 即解） |
| IM2 | **图片与文本无引用关系**——图片块不知道自己在哪个段落附近（只有 page 相同）；图注配对（河南体系）未用于上传文档 | ⚠️ 中 |
| IM3 | 图片质量/去重无过滤（重复 logo、低质截图都入库） | 低 |
| IM4 | 图片描述单图独立（无文档上下文描述） | 低 |

### 业界方案
- **MinerU/RAGFlow**：图片裁剪 + **上下文关联**（图片块绑定所在版面块，检索文本块时联动返回图片）
- **ColPali / ColQwen（SOTA）**：整页图像编码进多模态检索——直接按"页面图像"检索（我们走"文本描述图片"路线，ColPali 是另一条路，重）
- **CLIP/SigLIP**：图片本身嵌入向量库（跨模态检索）——我们未做（图片只通过文本块内容检索）

### 建议
1. **图注配对移植到上传文档**（复用 `crawl_henan_images.py` 的配对思路）：Docling 块流里 img 块 + 相邻 caption 文本 → 图片块 content = `【文档图片·图N】图注`（河南已验证 89% 配对率）——低成本，检索质量直追河南体系
2. VLM 描述（有 key 时）与图注互补：图注做内容前缀，VLM 做语义补充
3. 图片去重（感知哈希，可选后置）

## 五、入库

### 现状
- 异步任务：`BackgroundTasks.add_task` + `task_manager`（内存状态机 PENDING→PARSING→CHUNKING→INDEXING→QUESTIONS→DONE/FAILED，progress + stage_text）
- 幂等替换（同名文件替换旧 source，含 #图 联动删除）
- 失败清理（删 PDF + images 目录）
- Chroma 入库 + `mark_bm25_dirty`（惰性重建）+ 问题生成（LLM，失败不阻断文档可用）

### 问题
| # | 问题 | 严重度 |
|---|---|---|
| IN1 | **任务内存态**——后端重启任务消失（前端轮询失败无恢复；文档已入库但状态丢失） | ⚠️ 中 |
| IN2 | **BackgroundTasks 是"fire-and-forget"**——进程崩溃任务中断，无断点续跑 | ⚠️ 中 |
| IN3 | 重复上传无检测（同文件传两次 → 两个 source，除非手动 replace） | ⚠️ 中 |
| IN4 | 入库无完整性校验（块数/内容 hash 与解析产物一致性） | 低 |
| IN5 | 问题生成失败静默（stage_text 有标记但无重试入口） | 低 |

### 业界方案
- **Dify**：文档入库走任务队列（worker），状态持久化（DB），失败可重试；文件 hash 去重（同内容跳过）
- **RAGFlow**：Celery/Redis 队列 + 任务状态持久化 + 断点续传；文档版本管理（同文件重新解析）
- **LangChain 生态**：向量存储 upsert + 文档指纹（hash）增量更新

### 建议
1. **文件 hash 去重**（低成本高价值）：上传时算 SHA-256 → 与已有 source 对比 → 相同内容提示"已存在，可替换"
2. **任务状态持久化**（JSONL 或 SQLite 轻量）：重启恢复任务列表（1-2 天，面试叙事"工程化"）
3. 可接受折中：维持内存态但前端"文档已入库"提示（任务丢失时按 documents 列表兜底）

## 六、检索集成

### 现状
- 上传文档 source = `{timestamp}_{file}`——五路混合中 semantic/question/bm25 正常；**entity 路（source 名精确匹配）对上传文档天然失效**（时间戳前缀不是实体名）；graph 路不覆盖（无图谱）
- 图片块直检通道 ✓；来源多样性 ✓；替换/删除联动 ✓

### 问题
| # | 问题 | 严重度 |
|---|---|---|
| R1 | **上传文档无实体锚定**——问"文档里的妇好鸮尊"时 entity 路不参与（依赖 semantic 路） | ⚠️ 中 |
| R2 | 上传文档与内置数据无关联（图谱、跨文档引用） | 低（设计如此） |
| R3 | 上传文档的检索无法按文档过滤（问"只在某文档里找"无 where 维度） | 低（多知识库不做） |

### 业界方案
- Dify/RAGFlow：文档级元数据过滤（`where source=xxx`）——前端"指定文档检索"
- 实体提取注入（见三、E1）——业界标配（上传文档同样参与实体锚定）

### 建议
1. **文档实体抽取**（与 E1 合并）：source metadata 加 `entities` 字段 → entity 路 `get_by_source_like` 扩展支持 metadata 匹配——上传文档实体锚定闭环
2. 不做：多知识库/文档级权限（明确不做清单）

## 七、可观测性

### 现状
- 任务轮询（progress/stage_text/pages/blocks/chunks）+ 失败 error 字段 ✓
- 检索 trace（query_trace.jsonl + 前端诊断面板）覆盖检索，不含上传

### 问题
| # | 问题 | 严重度 |
|---|---|---|
| O1 | 无分阶段耗时明细（解析 X 秒/分块 Y 秒/embedding Z 秒——排障靠猜） | 低 |
| O2 | 失败任务前端无重试按钮（需重新上传） | 低 |

### 建议
- task 增加 `timings: {parse_ms, chunk_ms, index_ms}`（一行一个 time.perf_counter 差）——面试展示"工程意识"
- 前端失败任务加"重新上传"（复用原文件？前端已持有 File——低配）

## 八、优先级总表

| 优先级 | 项 | 成本 | 收益 |
|---|---|---|---|
| P0 | 图片块图注配对（上传文档复用河南体系） | 半天 | 上传文档图片检索质量直追图注级 |
| P0 | 文档实体抽取 → entity 路生效 | 1-2 天 | 上传文档检索闭环（当前最大短板） |
| P1 | 文件 hash 去重 | 0.5 天 | 重复上传体验 |
| P1 | 开启 Docling OCR | 半小时 | 扫描件可用 |
| P1 | 文档元数据注入（file_name/uploaded_at） | 0.5 天 | 检索维度 + 展示 |
| P2 | 任务状态持久化 | 1-2 天 | 工程化叙事 |
| P2 | 分阶段耗时明细 | 0.5 天 | 可观测性 |
| P2 | 表格行列 metadata | 0.5 天 | 表格检索维度 |
| 不做 | Text2SQL / ColPali / 多知识库 / 权限体系 | — | 超范围（见蓝图明确不做） |

## 九、面试叙事建议

上传管道一句话总结：
**"上传文档走 Docling 多模态解析 → 结构感知切片（父子分块）→ 图片块直检通道 → 异步任务入库 + Q-to-Q 问题索引，失败不阻断文档可用；当前短板是实体锚定与图片语义描述，业界方案（实体抽取、图注配对、VLM 描述）已列出实施路径。"**
