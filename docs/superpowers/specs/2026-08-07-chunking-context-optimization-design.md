# 切片 + 上下文优化设计（结构感知切分 / 父子分块 / 噪声过滤 / faithfulness 评测）

日期：2026-08-07
状态：待确认（实施前评审）
前置：第 1 步架构治理已完成（P1 数据契约 / P2 ingest 管道 / P3 文档）
参考：docs/rag-optimization-blueprint.md「二、切片优化」「六、上下文组装」「七、评估体系」

## 一、现状分析（代码实证）

| 层 | 现状 | 问题 |
|---|---|---|
| 解析 | Docling 输出结构化 `blocks`（DocumentBlock: type/page/bbox，见 interfaces/doc_parser.py） | **结构信息被丢弃**——upload.py 只用 `parsed.markdown`（拼接文本）再 `RecursiveCharacterTextSplitter(500/50)` 字符级硬切 |
| 切分（静态数据） | 瓷器/河南按章节+句子边界切（import_porcelain/henan_chroma.py 的 split_sections） | 已是伪结构感知，**不重切**（增量原则） |
| 切分（上传文档） | 纯字符级 500/50 | 跨段落硬切、表格被拆散、标题与正文割裂——噪声块源头 |
| 检索 | 五路混合 + 加权 RRF + 来源多样性 + 候选池 64 → top-8 | 结果带 `score`（RRF 融合分）但**从未消费**——无阈值过滤 |
| 上下文组装 | orchestrator.py:414 `[i+1] {content[:400]}` top-8 全塞；synthesizer.py:71 `[:300]` | **不过滤噪声块**：低分弱相关块与强相关块同权进 LLM，稀释注意力、增幻觉、浪费 token；CRAG 只管"整体不足→重检索"，不管"块内噪声" |
| 父子分块雏形 | question 路：假设问题块 `source_chunk_id` → 原文块（hybrid.py:160-177） | 只覆盖 question 路，且映射到"原文块"（正是噪声源头块） |
| 评测 | eval（Recall/MRR）+ judge（GT 事实包含率） | 缺 **faithfulness**（答案每个事实是否被检索上下文支持）——幻觉不可量化 |

## 二、范围界定（直接全量重切 + 改造前基线对比）

**关键认知（评测可行性）**：eval/judge 评测跑的是**存量数据**——新切分若只对"未来上传文档"生效，评测根本测不到切分收益；且没有新文档做实验组。因此正确路径是 **直接全量重切，与既有基线对比**：
- 改造前基线**已有**：eval 32 份历史报告（当前 Recall 100% / MRR 0.69）+ judge 7 份（GT 事实包含率 ~85%）
- faithfulness 为新增维度，改造前基线需**先采集**（实现评测工具后、改检索/组装前跑一次当前状态）
- 回滚保障：重切前备份 `src/data/chroma/`（复制目录），评测不成立立即恢复备份

**做**：
1. 结构感知切分器（Docling blocks → 语义块，metadata 契约化）
2. 父子分块（子块检索 + 父块送 LLM，Small-to-Big）
3. 块级噪声过滤（RRF 分数阈值裁剪，上下文压缩轻量版）
4. faithfulness 评测维度（judge 新模式，含改造前基线采集）
5. **全量重切**（上传文档 + 静态数据统一走新契约）+ 问题索引重建
6. **评测对比决策**：eval 无退化 且 faithfulness 提升 → 保留；否则回滚备份

**不做**（理由）：
- LLM 逐块判断过滤——准确但每查询 N 次 LLM 调用，成本高，后置
- 语义切分（embedding 判边界）——复杂度高，收益与结构感知重叠
- 检索端拓扑（top_k/候选池/rerank）——第 3 步专属，本步不动

## 三、方案设计

### T1 结构感知切分器（`src/services/chunker.py` 新增）

输入 `ParsedDocument`，输出子块 + 父块两层（dict 列表，契约与 ingest 管道一致）：

```
blocks（Docling）→ 节分组（heading 引导）→ 父块（节内容拼接，上限 1500 字）
                                        → 子块（段落/表格/句子边界，目标 ~250 字）
```

| 块类型 | 切分规则 | 子块 metadata |
|---|---|---|
| TEXT/LIST 段落 | 独立子块；>500 字按句子边界拆（复用河南 split_sections 思路） | block_type:text/page/parent_id |
| HEADING 标题 | 独立子块（短块，检索强信号）+ 节起始标记 | block_type:heading/page/parent_id |
| TABLE | 独立子块（表格 markdown 原样，不拆行） | block_type:table/page/parent_id |
| FORMULA | 独立子块 | block_type:formula/page/parent_id |
| IMAGE | **不在此切**——已有独立图片块链路（_build_image_chunks，contextual page_texts） | — |

- 父块 = 节（heading + 该节段落流；无标题段落流按 ~800 字分组），上限 1500 字防 token 爆炸
- 子块 metadata 全带 `parent_id`（指向父块 chunk_id）；父块 metadata 带 `is_parent: true`
- 所有块带 `chunk_type: "text"`（P1-A 契约已强制）+ `page`（来自 block.page，供溯源）

### T2 父子分块生效（Small-to-Big 接入组装端）

**检索不变**（子块照常五路召回）；**组装端替换**：

```
orchestrator quick_answer:
  retrieved（子块，带 parent_id）
    → 批量 get_by_ids 取父块（去重、保持排序、缺失回退子块）
    → doc_context 用父块（完整语义，无 400 字截断截断父块？——父块已限 1500 字，整块直拼）
sources 事件（前端引用）: 仍用子块（id/source/paths/image_url 不变，content 展示子块 200 字）
```

- 设计决策：**LLM 上下文用父块，引用定位用子块**——"引用精确（子块定位页码段落）、语义完整（父块全文）"分离，面试叙事要点
- question 路兼容：假设问题 `source_chunk_id` → 子块（原映射不动）→ 子块 parent_id → 父块，走同一替换函数，自动统一
- 图片块无 parent_id → 原样保留（图注级证据不受影响）
- deep_research（synthesizer）同样替换（expert_sources → 父块），上限 max_index 逻辑不变

### T3 块级噪声过滤（上下文压缩轻量版）

**现状**：top-8 全塞，`score`（RRF 融合分）零消费。
**方案**（三重条件，保守裁剪，不伤强相关块）：

```
保留条件 = 分数 ≥ 阈值 或 路径数 ≥ 2 或 排名 ≤ 4
（阈值 settings.rrf_score_threshold 默认 0.25，评测后调）
```

- 单票弱块（1 路命中且分数低且排名 > 4）才被裁——多路票/强相关块绝不误伤
- 实现于 orchestrator 组装前（retriever 返回后统一过滤，deep_research 共用）
- 与 CRAG 互补：CRAG = 整体不足重检索（外层）；块级过滤 = 块内噪声（内层），写进代码注释
- 先跑真实查询统计 rrf 分数分布再定默认值（数据驱动）

### T4 faithfulness 评测维度

**现状**：judge.py 只判「GT 事实包含率」（答案-标准事实）。
**新增** `judge.py --faithfulness` 模式：
- 复用数据集问题 → 调 /api/chat 拿答案 + 检索上下文（sources content 拼接）
- 新 prompt `prompts/eval_faithfulness.md`：裁判逐句判「答案每个事实是否被检索上下文支持」→ 支持句/总句 = faithfulness rate
- 改造前后跑同一批问题：faithfulness ↑ = 噪声过滤有效（幻觉可量化，面试讲"我怎么度量幻觉"）

## 四、数据契约变化

```
子块 metadata: {source, chunk_type: text, block_type: text|heading|table|formula,
                page, parent_id: <父块 chunk_id>}
父块 metadata: {source, chunk_type: text, block_type: parent, page, is_parent: true}
（存量块无 parent_id → 兼容：替换函数缺 parent_id 时原样保留）
```

## 五、实施任务拆分（顺序即执行顺序）

| 任务 | 内容 | 验证 |
|---|---|---|
| S1 | chunker.py 结构感知切分（含测试：段落/标题/表格/超长段/图片不重复/父块上限） | pytest 新用例全绿 |
| S2 | 组装端改动：orchestrator/synthesizer 父子替换（无 parent_id 存量退化为原样）+ 块级噪声过滤（settings + 三重条件）+ sources 事件保持子块 | 弱块被裁、强块不丢；存量无 parent_id 原样工作 |
| S3 | faithfulness 评测：judge.py --faithfulness + prompts/eval_faithfulness.md | 模式可跑 |
| S4 | **改造前基线采集**（当前数据 + 当前代码，改库前）：eval 核心+扩展 + judge GT + judge faithfulness | 报告存档 eval/reports/（faithfulness 基线必须有） |
| S5 | **备份 + 全量重切**：备份 src/data/chroma/ → 静态数据重切（瓷器/青铜/河南，已有章节切分 → 补 block_type/parent_id 契约）→ 上传文档重新 Docling 解析重切 | 重切后全库新契约覆盖；无旧契约块 |
| S6 | 问题索引重建（generate_questions 重跑，source_chunk_id → 新子块） | question 路 eval 无退化 |
| S7 | **改造后评测对比**：同一批问题跑 eval + judge GT + faithfulness，对比 S4 基线 → 数字决策（保留 / 恢复备份回滚） | 对比报告存档；决策记录 |

**决策规则**：eval（Recall/MRR）无退化 **且** faithfulness 提升 → 保留；否则恢复备份（回滚成本 = 复制目录）。

## 六、验收标准

1. **切分**：全量重切后块按结构切（标题/表格独立块、不跨段落）；metadata 契约完整（block_type/page/parent_id）；图片块链路无回归
2. **父子**：LLM 上下文含父块完整内容；前端引用仍指向子块（sources 事件结构不变）
3. **过滤**：低分单票弱块不送 LLM；强相关块（多路票/高排名）不丢——构造用例 + 真实查询抽查
4. **评测对比（决策依据，数字决定）**：
   - S4 改造前基线：eval（Recall 100% / MRR 0.69）+ judge GT（~85%）+ faithfulness（新采集）
   - S7 改造后同一批问题复测：eval Recall/MRR 无退化 **且** faithfulness 提升 → 保留；否则回滚
5. **回归**：全量 pytest 通过（当前 93 + 新增）；前端 build 通过
6. **兼容**：无 parent_id 块原样工作（回滚兼容）；河南图片块直检通道（where chunk_type=image）不受影响
7. **重切后**：全库无旧契约块；问题索引重建后 question 路映射有效（eval question 路无退化）；前端冒烟（/chat、/knowledge、/library 正常）

## 七、风险与对策

| 风险 | 对策 |
|---|---|
| 父块变长 → token 开销 | 父块上限 1500 字；配合 T3 过滤（top-8 → 保留强块换父块，总量可控：~6 父块 × 1500 ≈ 9k 字，与现状 8×400=3.2k 相比需评测权衡——若 token 溢出则父块上限降到 1000） |
| 子块引用与父块内容不一致（引用显示 200 字子块，LLM 读父块） | 展示层截断不变（子块开头即段落开头，一致）；编号一一对应（父块替换不改序号） |
| 标题块短（检索噪声） | 标题块权重天然低（RRF 分数由多路票决定）；标题块通常只有 question/bm25 单票，可被 T3 裁掉，无碍 |
| chunker 异常 | 回退旧 RecursiveCharacterTextSplitter（防御性，不阻断上传） |
| 问题索引重建（16.5k 条 LLM 生成） | 一次性成本（数十分钟），重建后 question 路映射全部指向新子块——干净无残留；重切必然触发，成本明确 |
| 重切后检索结果整体变化 | 预期（块边界即语义），但召回质量由验收第 4 条兜底：S7 对比不成立立即恢复备份（复制目录，分钟级） |
