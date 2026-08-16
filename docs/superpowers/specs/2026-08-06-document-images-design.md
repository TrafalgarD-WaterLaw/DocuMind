# 文档内图片处理设计（T2 接口 + T3 展示）

日期：2026-08-06
状态：已获用户批准（T2 先做可插拔接口，无视觉 key 时保留图片走 T3）

## 背景与现状

上传 PDF 中的图片当前被完全丢弃（Docling picture 元素 text 为空 → `continue` 跳过；未配置图片导出）。表格/公式已文本化（达标）。项目无现成 VLM 凭证（.env 仅 DeepSeek 官方 key，无视觉能力）。

## 目标

1. **T3（本轮全做）**：解析时导出文档图片 → 图片落盘 → 静态服务可访问 → 检索命中图片块时前端证据链显示原图。
2. **T2（做接口，实现待 key）**：`ImageCaptioner` 可插拔接口；有阿里云百炼 key 时用 Qwen-VL 生成中文图注入库，无 key 时图片块以「第 N 页图片 + 文件名」占位内容入库（图片仍可展示）。

## 数据流

```
PDF → Docling(images_dir 导出) → ParsedDocument.images[]
     → 每张图: [captioner 有 key → Qwen-VL 描述] + 图片块 chunk
       {content: "【文档图片·第N页】描述或占位", metadata: {source, image_path, chunk_type:"image"}}
     → vector 入库（不参与假设问题生成）
     → 检索命中 → sources 事件携带 image_url → 前端证据链 el-image 缩略图+点击预览
```

## 详细设计

### 1. 解析器图片导出（docling_parser.py）

- `DocumentConverter(format_options={"pdf": PdfFormatOption(pipeline_options=PdfPipelineOptions(images_dir=...))})`——images_dir = `上传文件路径.with_suffix(".images")`（与 PDF 同目录同位，天然按 source 组织）。
- 遍历 picture 元素时：`element.image.uri` 给出相对路径 → 收集绝对路径到 `ParsedDocument.images`（最多 20 张，防超大文档）；picture 元素文本非空时仍保留原行为（文本块）。
- `ParsedDocument`（interfaces/doc_parser.py）新增字段 `images: list[str]`（默认空列表）；PyPDFParser 返回 `images=[]`。
- 实现时验证 docling 2.114 的确切 API（images_dir 参数名、picture.image.uri），版本不符则按实际 API 适配。

### 2. ImageCaptioner 接口（可插拔）

- `interfaces/image_captioner.py`：`ImageCaptioner` ABC，`async def caption(image_path: Path) -> str`（返回中文描述，失败/无能力返回 ""）。
- `services/image_caption.py`：
  - `QwenVLCaptioner`：阿里云百炼 OpenAI 兼容接口（base_url `https://dashscope.aliyuncs.com/compatible-mode/v1`），图片 base64 → messages 含 `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}` → 中文描述（提示词要求 40 字内概括主体/纹饰/文字内容）。
  - `NoopCaptioner`：返回 ""（默认）。
- config.py 新增：`dashscope_api_key: str = ""`、`dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"`、`image_caption_model: str = "qwen-vl-max-latest"`。
- di.py：`container.captioner`——`dashscope_api_key` 非空 → QwenVLCaptioner，否则 NoopCaptioner。

### 3. 上传管线接入（upload.py）

- 解析后：`parsed.images` 非空 → 每张图（并行 3 个，asyncio.Semaphore）：
  - `desc = await container.captioner.caption(img_path)`（Noop 返回空）
  - 图片块 content：有描述 → `【文档图片·第N页】{desc}`；无描述 → `【文档图片·第N页】{文件名}`（占位，保证可检索到图片块）
  - metadata：`{"source": safe_name, "image_path": 相对路径, "chunk_type": "image"}`，相对路径 = `{safe_name}.images/{文件名}`
  - 图片块**只进 vector，不参与 build_question_documents**（短文本生成问题浪费 token）。
- 图片块计入 chunks 计数；任务 stage_text 提示「图片描述中…」仅在有图片且 captioner 非 Noop 时显示。
- `_delete_source`：同时删除 `{source}.images` 目录（shutil.rmtree，missing_ok）。
- `_run_pipeline` FAILED 分支：同 PDF 文件清理逻辑删除 images 目录。

### 4. 图片静态服务（main.py）

- `app.mount("/api/uploads", StaticFiles(directory=settings.upload_dir))`——FastAPI StaticFiles 自带路径穿越防护。
- 前端 `image_url` 存相对路径（`/api/uploads/{safe_name}.images/xxx.png`），前端拼接 API base。

### 5. sources 事件 + 前端展示

- orchestrator quick_answer 的 sources items 增加 `image_url: d.get("metadata", {}).get("image_path")`；vision 链路的 sources 同样处理（若有）。
- `SourceItem` 增加 `image_url?: string`。
- EvidencePanel 来源项：`image_url` 存在 → `<el-image>` 缩略图（fit contain，`:preview-src-list` 点击放大），API base 来自 `import.meta.env.VITE_API_BASE_URL || 'http://localhost:5172'`。

## 明确不做

- 不做多模态向量检索（CLIP 类，T4）。
- 不做图片块的 BM25/实体路特殊处理（文本嵌入即可，命中靠描述/占位文本）。
- 讯飞星火视觉实现（接口已可插拔，未来加 `SparkCaptioner` 即可）。

## 验收标准

1. 含图片的 PDF 上传 → 任务完成 → `uploads/` 下出现 `{source}.images/` 目录与图片文件。
2. `GET /api/uploads/{source}.images/{file}` 可访问图片（浏览器直接打开）。
3. 问答命中图片块（问"文档里的图"相关内容，或诊断面板确认 semantic 路命中图片 chunk）→ 证据链来源项显示原图缩略图，点击可放大。
4. 无 DashScope key 时：上传正常（图片占位块入库），问答链路无异常。
5. 删除文档 → images 目录一并清理。
6. 后端测试全绿（新增单测：captioner 选择逻辑、图片块 metadata 构造）。

## 冒烟用图

用 PIL 生成一张测试图（青铜鼎简笔示意图 + 图注"图1 妇好鸮尊线描图"）嵌入测试 HTML → 重新生成 `docs/test-data/妇好鸮尊测试文档.pdf`（含图片），供上传冒烟。
