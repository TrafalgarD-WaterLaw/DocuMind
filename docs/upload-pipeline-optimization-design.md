# 文档上传管道优化方案设计（逐环节）

日期：2026-08-08
前置：docs/upload-pipeline-analysis.md（问题分析）——本文为每个环节的可实施方案设计
状态：待确认（确认后按顺序实施）

---

## 环节 1：解析优化

### 1.1 开启 OCR（扫描件支持）

**目标**：扫描件/图片型 PDF 可解析（当前解析为空白）。

**方案**：
```python
# core/config.py
docling_ocr: bool = False   # 扫描件 OCR（需 easyocr；默认关避免依赖膨胀）

# docling_parser.py
if settings.docling_ocr:
    opts.do_ocr = True
    from docling.datamodel.pipeline_options import EasyOcrOptions
    opts.ocr_options = EasyOcrOptions()
```

**验证**：用图片型 PDF（纯扫描）解析出文本块。

### 1.2 表格行列 metadata

**目标**：表格块可被按结构过滤（"找有 N 列数据的表格"）。

**方案**：
```python
# _table_to_markdown 后
md = element.export_to_markdown()
rows = [l for l in md.splitlines() if l.strip().startswith("|")]
block.metadata["table_rows"] = len(rows)
block.metadata["table_cols"] = max((l.count("|") - 1 for l in rows), default=0)
```

**验证**：解析含表格 PDF → 块 metadata 带 table_rows/table_cols。

### 1.3 解析失败分类

**目标**：失败原因友好化（权限/损坏/加密/超时）。

**方案**：
```python
# upload.py 管线 except 分支
def _classify_parse_error(e: Exception) -> str:
    if "PermissionError" in type(e).__name__ or "Access" in str(e): return "文件无读取权限"
    if "Encrypted" in str(e) or "password" in str(e).lower(): return "PDF 已加密，请提供无密码版本"
    if "Timeout" in type(e).__name__: return "解析超时，请重试"
    return f"解析失败: {str(e)[:100]}"
# FAILED 时 stage_text = _classify_parse_error(e)
```

---

## 环节 2：切片优化

### 2.1 文档级元数据注入

**目标**：块 metadata 携带文档身份（文件名/时间/大小）——检索过滤与展示维度扩展。

**方案**：upload.py `_run_pipeline` 分块后统一注入：
```python
DOC_META = {"file_name": file_name, "uploaded_at": int(time.time()), "file_size": file_path.stat().st_size}
for c in doc_dicts:
    c["metadata"].update(DOC_META)
# 图片块同样注入
```

**验证**：上传后块 metadata 含 file_name/uploaded_at/file_size；检索结果展示可用。

### 2.2 超长表格块拆分（低优先）

**目标**：>20 行的表格避免单块过长被父块截断。

**方案**：chunker `_make_children` 对 TABLE：行数 > 20 → 按行拆 2 块（每块保留表头行 + 注释"（续表）"），共享同一 parent_id。

---

## 环节 3：文档实体抽取（P0——上传文档检索闭环）

### 3.1 抽取

**目标**：上传文档的实体进 entity 路锚定（当前时间戳 source 无法实体匹配——**上传文档检索最大短板**）。

**方案**：
```
解析后（分块前）: LLM 从全文提取 ≤5 个文物/遗址/朝代实体
  prompt: prompts/document_entity_extraction.md（JSON: {"entities": ["妇好鸮尊", ...]}）
  失败/空 → entities=[]（不阻断）
注入: 所有块 metadata["entities"] = [实体列表]
```

```python
# services/document_entities.py
async def extract_entities(text: str, llm) -> list[str]:
    """全文前 2000 字 + LLM 提取 ≤5 实体；失败回退空列表"""
```

### 3.2 entity 路扩展

**目标**：`_path_entity_anchor` 能命中 metadata.entities 含实体的块。

**方案**（hybrid.py `_path_entity_anchor` 扩展）：
```python
# 现状: source 名子串匹配（get_by_source_like）
# 新增: entities 数组匹配——先用 list_sources + 块级 where 过滤
# Chroma where 数组 contains: {"entities": {"$contains": entity}}
candidates = self.doc_store.retrieve("", top_k=5, where={"entities": {"$contains": entity}})
# 与 source 名匹配结果合并去重
```

**数据契约**：块 metadata 新增可选字段 `entities: list[str]`（仅上传文档注入；存量数据无此字段 → 检索跳过，兼容）。

**验证**：
1. 上传含"妇好鸮尊"内容的文档 → 问"文档里提到的妇好鸮尊在哪里"→ entity 路命中上传文档块（paths 含 entity）
2. 存量数据无 entities 字段 → 行为不变
3. eval 回归（entity 路逻辑扩展不破坏存量匹配）

---

## 环节 4：图片链路优化（P0——图注配对移植）

### 4.1 图注配对（复用河南体系）

**目标**：上传文档图片块 content 从"页面上下文 120 字"升级为"图注 + 上下文"——图片检索语义直追河南图注级。

**方案**：upload.py 图片块构建前，从 `parsed.blocks`（Docling 阅读顺序）配对图注：
```python
def _pair_figure_captions(blocks) -> dict[int, str]:
    """页内 '图N' 模式 caption → {page: caption}（复用 crawl_henan_images 配对思路）
    规则: 每页找第一个以 '图N' 开头、长度 ≤80 的文本块作为该页图片图注"""
    captions = {}
    for i, b in enumerate(blocks):
        if b.type in (BlockType.TEXT, BlockType.LIST):
            m = re.match(r"^图\s*(\d+)[\s.、:：]*(.{1,80})", b.content.strip())
            if m:
                captions.setdefault(b.page, m.group(2).strip())
    return captions
```
图片块 content 优先级：图注 > VLM 描述 > 页面上下文 > 文件名（图注与描述可拼接：`【文档图片·第N页】图注。VLM描述`）。

**验证**：上传含"图1 xxx"图注的 PDF → 问图注主题词 → 图片块检索命中。

### 4.2 VLM 描述增强（有 key 时）

**方案**：`_build_image_chunks` caption 参数传入图注后，VLM prompt 改为"描述图片并判断是否与图注'xxx'一致"——描述与图注互相校验，减少幻觉描述。

### 4.3 图片去重（低优先，可选）

**方案**：入库前感知哈希（`imagehash.phash`，需装 imagehash 包）——重复图片跳过（同文档多页相同 logo/图重复导出）。

---

## 环节 5：入库优化

### 5.1 文件 hash 去重

**目标**：重复上传提示（同内容不再产生第二个 source）。

**方案**：
```python
# data/document_hashes.json: {sha256: source}（服务层读写，与 image_index 同级）
# POST /api/upload:
#   1. 读文件流算 sha256（边读边算）
#   2. 查 document_hashes.json → 命中返回 409 {"detail": "文件已存在(source)，可传 replace=true 覆盖"}
#   3. 入库成功后写 {hash: source}；删除文档时清理
```

**验证**：同文件传两次 → 第二次 409；replace=true 正常覆盖并更新 hash 映射。

### 5.2 任务状态持久化（P2）

**目标**：后端重启后任务列表恢复（当前内存态全丢）。

**方案**：
```python
# task_manager.py: 状态变更时 append JSONL（data/tasks.jsonl，保留最近 50 条）
# AppContainer 启动时加载恢复；重启时未完成任务标记 FAILED("服务重启中断")
```
**妥协**：文档已入库不受影响（documents 列表兜底），任务恢复仅保展示与错误提示。

### 5.3 分阶段耗时明细（P2）

**方案**：task 增加 `timings: {"parse_ms": ..., "chunk_ms": ..., "index_ms": ..., "questions_ms": ...}`（管线各段 time.perf_counter 差）→ 前端任务卡片展示。

---

## 环节 6：检索集成

### 6.1 entity 路 entities 匹配（与 3.2 合并，P0）

### 6.2 不做
多知识库/文档级过滤/权限（蓝图明确不做清单）。

---

## 环节 7：可观测性

### 7.1 timings 展示（见 5.3）
### 7.2 前端失败重试（低优先）

**方案**：LibraryView 失败任务卡加"重新上传"按钮——前端缓存 File 对象（或提示重新选择），调用同一 upload 接口（replace=true）。

---

## 实施顺序与依赖

```
第 1 批（P0，2-3 天）:
  U1 图注配对移植（4.1）——纯 upload.py + 测试
  U2 文档实体抽取 + entity 路扩展（3.1+3.2）——prompt + 新服务 + hybrid 扩展 + 测试
  U3 文件 hash 去重（5.1）——upload.py + hash 映射文件 + 测试
第 2 批（P1，1 天）:
  U4 OCR 开关 + 表格行列 metadata + 解析失败分类（1.1-1.3）
  U5 文档元数据注入（2.1）
第 3 批（P2，1-2 天）:
  U6 任务持久化 + timings（5.2+5.3+7.1）
  U7 超长表格拆分（2.2）+ 图片去重（4.3）+ 前端重试（7.2）
```

## 验收总则

- 每批实施后：全量 pytest + eval --retrieval-only 回归 + 上传含图 PDF 冒烟（图注/实体/hash 各自验证）
- 数据契约变化均向后兼容（新增字段，存量无则跳过）
