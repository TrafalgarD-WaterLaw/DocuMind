# 文档图片链路（T2 接口 + T3 展示）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF 内图片导出落盘 + 可插拔 ImageCaptioner 接口（无 key 时占位内容）+ 静态服务 + 前端证据链显示原图。

**Architecture:** Docling `images_dir` 导出图片到 `{source}.images/` 目录；`ParsedDocument.images` 携带路径；`ImageCaptioner` ABC + QwenVL/Noop 双实现（di 按 key 选择）；upload 管线为每张图建「图片块」chunk（有描述用描述，无描述用文件名占位）只进向量库；`/api/uploads` StaticFiles 挂载；sources 事件带 `image_url`；EvidencePanel 用 el-image 渲染缩略图+点击预览。

**Tech Stack:** 同 P0/P1（FastAPI + pytest + Vue3；无 git，Commit 步骤替换为验证命令）。

**Spec:** `docs/superpowers/specs/2026-08-06-document-images-design.md`

## Global Constraints

- 注释/文案中文；项目无 git 仓库。
- 后端测试：`cd Backend && uv run pytest tests/ -v`（当前 42 全绿）。
- 前端构建门：`cd Frontend && npm run build`（vue-tsc 零错误）。
- API key 不落日志/不复制：DashScope key 只经 .env 注入（`DASHSCOPE_API_KEY`），代码里只读 `settings.dashscope_api_key`。
- Docling 2.114 的确切图片导出 API（`PdfPipelineOptions(images_dir=...)`、picture 元素 `.image.uri`）以实现时实测为准，版本不符按实际 API 适配，但**输出契约不变**：`ParsedDocument.images: list[str]`（绝对路径）。

---

### Task D1: 解析器图片导出

**Files:**
- Modify: `Backend/src/interfaces/doc_parser.py`、`Backend/src/providers/parser/docling_parser.py`、`Backend/src/providers/parser/pypdf_parser.py`
- Test: `Backend/tests/test_parser_images.py`

**Interfaces:**
- Produces: `ParsedDocument.images: list[str] = field(default_factory=list)`（绝对路径）；DoclingParser 解析含图 PDF 时导出图片到 `上传文件路径.with_suffix(".images")` 目录并把路径填入 images（上限 20 张）；PyPDFParser 恒返回空列表；`markdown` 属性不变。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_parser_images.py`:

```python
"""解析器图片导出测试——ParsedDocument.images 契约"""
from interfaces.doc_parser import BlockType, DocumentBlock, ParsedDocument


def test_parsed_document_images_default_empty():
    doc = ParsedDocument(source="a.pdf", blocks=[])
    assert doc.images == []


def test_parsed_document_images_custom():
    doc = ParsedDocument(
        source="a.pdf",
        blocks=[DocumentBlock(type=BlockType.TEXT, content="x", page=1)],
        images=["/tmp/a.pdf.images/fig_1.png"],
    )
    assert doc.images == ["/tmp/a.pdf.images/fig_1.png"]
    # markdown 拼接行为不变
    assert "x" in doc.markdown
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_parser_images.py -v`
Expected: FAIL（ParsedDocument 无 images 字段）。

- [ ] **Step 3: 实现接口字段**

Modify `Backend/src/interfaces/doc_parser.py`：`ParsedDocument` 加字段：

```python
@dataclass
class ParsedDocument:
    """完整解析结果"""
    source: str                          # 文件名或路径
    blocks: list[DocumentBlock]
    metadata: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)   # 导出图片绝对路径（T3 展示用）

```

- [ ] **Step 4: 实现 Docling 图片导出**

Modify `Backend/src/providers/parser/docling_parser.py` 完整重写为：

```python
"""Docling 多模态文档解析器 — 版面分析 + 表格 + 公式 + 图片导出（需安装 docling）

安装: uv add docling
"""
from pathlib import Path

from interfaces.doc_parser import (
    BlockType,
    DocParser,
    DocumentBlock,
    ParsedDocument,
)


class DoclingParser(DocParser):
    """基于 IBM Docling 的多模态解析器

    能力:
      - 版面分析 (段落/标题/列表/代码块)
      - 表格识别 → Markdown 表格
      - 公式识别 → LaTeX
      - 图片区域标注 + 导出（T3：证据链展示原图）
    """

    # 单文档最多导出的图片数（防超大文档撑爆目录）
    MAX_IMAGES = 20

    def __init__(self):
        try:
            from docling.document_converter import DocumentConverter
            self.converter = DocumentConverter()
            self._available = True
        except ImportError:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def _make_converter(self, images_dir: Path):
        """按需构建带图片导出的转换器（docling 2.x: PdfPipelineOptions.images_dir）"""
        try:
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import (
                DocumentConverter, PdfFormatOption,
            )
            opts = PdfPipelineOptions(images_dir=images_dir)
            return DocumentConverter(format_options={
                "pdf": PdfFormatOption(pipeline_options=opts),
            })
        except (ImportError, TypeError) as e:
            # 版本差异时降级为无图片导出（不阻塞解析）
            import logging
            logging.getLogger(__name__).warning(f"Docling 图片导出不可用，降级: {e}")
            return self.converter

    def parse(self, file_path: str | Path) -> ParsedDocument:
        if not self._available:
            raise RuntimeError(
                "Docling 未安装。运行: uv add docling"
            )

        file_path = Path(file_path)
        images_dir = file_path.with_suffix(".images")
        converter = self._make_converter(images_dir)
        result = converter.convert(str(file_path))
        doc = result.document

        blocks: list[DocumentBlock] = []
        images: list[str] = []

        for element, _level in doc.iterate_items():
            label = getattr(element, "label", "").lower()
            text = getattr(element, "text", "") or ""
            page = int(getattr(element.prov[0], "page_no", 1)) if getattr(element, "prov", None) else 1

            # 图片块：导出文件记录到 images，文本非空时仍保留为文本块
            if label == "picture":
                try:
                    img_ref = getattr(element, "image", None)
                    uri = getattr(img_ref, "uri", "") if img_ref else ""
                    if uri and len(images) < self.MAX_IMAGES:
                        images.append(str(images_dir / Path(uri).name))
                except Exception:
                    pass
                if not text.strip():
                    continue

            if not text.strip():
                continue

            block_type = self._map_type(label)

            # 表格保留 Markdown 格式
            if block_type == BlockType.TABLE:
                text = self._table_to_markdown(element)

            blocks.append(DocumentBlock(
                type=block_type,
                content=text,
                page=page,
                metadata={"label": label, "parser": "docling"},
            ))

        return ParsedDocument(
            source=file_path.name,
            blocks=blocks,
            metadata={
                "parser": "docling",
                "pages": max((b.page for b in blocks), default=1),
            },
            images=images,
        )

    @staticmethod
    def _map_type(label: str) -> BlockType:
        mapping = {
            "title": BlockType.HEADING,
            "section_header": BlockType.HEADING,
            "heading": BlockType.HEADING,
            "text": BlockType.TEXT,
            "table": BlockType.TABLE,
            "formula": BlockType.FORMULA,
            "picture": BlockType.IMAGE,
            "list_item": BlockType.LIST,
            "code": BlockType.CODE,
        }
        return mapping.get(label, BlockType.TEXT)

    @staticmethod
    def _table_to_markdown(element) -> str:
        """Docling 表格 → Markdown 表格字符串"""
        try:
            return element.export_to_markdown()
        except Exception:
            return str(element.text)
```

**注意（实现者必读）**：docling 2.114 的 API 以 venv 实测为准——先跑：

```bash
cd E:/projects/DocuMind/Backend && PYTHONIOENCODING=utf-8 uv run python -c "
from docling.datamodel.pipeline_options import PdfPipelineOptions
import inspect
print('images_dir' in inspect.signature(PdfPipelineOptions.__init__).parameters)
"
```

若 `images_dir` 不存在，用 `pipeline_options` 上其它等效参数（如 `images_dir` 改走旧式 `ImageExportSettings` 或 docling 2.x 的 `images_dir` 在 `PdfPipelineOptions` 的属性——以实测为准），保证输出契约（images 绝对路径）不变。

- [ ] **Step 5: 修改 PyPDFParser 返回空 images**

Modify `Backend/src/providers/parser/pypdf_parser.py`：`ParsedDocument(...)` 调用处加 `images=[]`（或省略——dataclass 默认空列表，可不改。为契约显式起见在 DoclingParser 处已填，pypdf 保持默认即可，**无需改动**）。

- [ ] **Step 6: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_parser_images.py -v`
Expected: 2 passed。

- [ ] **Step 7: 冒烟（需 D5 的含图 PDF，可延后）**

在 D5 完成后，用含图测试 PDF 运行 Docling 转换确认 `images` 非空且目录出现文件：

```bash
cd E:/projects/DocuMind/Backend && PYTHONIOENCODING=utf-8 uv run python -c "
from providers.parser import DoclingParser
p = DoclingParser()
doc = p.parse(r'E:/projects/DocuMind/docs/test-data/妇好鸮尊测试文档.pdf')
print('images:', len(doc.images))
for i in doc.images: print(' ', i)
"
```

Expected: images ≥ 1，文件存在于 `docs/test-data/妇好鸮尊测试文档.pdf.images/`。

---

### Task D2: ImageCaptioner 可插拔接口

**Files:**
- Create: `Backend/src/interfaces/image_captioner.py`、`Backend/src/services/image_caption.py`
- Modify: `Backend/src/core/config.py`、`Backend/src/core/di.py`
- Test: `Backend/tests/test_captioner.py`

**Interfaces:**
- Produces:
  ```python
  # interfaces/image_captioner.py
  class ImageCaptioner(ABC):
      @abstractmethod
      async def caption(self, image_path: Path) -> str: ...
  # services/image_caption.py
  class QwenVLCaptioner(ImageCaptioner): ...   # 阿里云百炼，base64 → 中文描述
  class NoopCaptioner(ImageCaptioner): ...     # 返回 ""
  # core/config.py 新增
  dashscope_api_key: str = ""                  # .env: DASHSCOPE_API_KEY
  dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
  image_caption_model: str = "qwen-vl-max-latest"
  # core/di.py
  @property
  def captioner(self) -> ImageCaptioner   # key 非空 → QwenVLCaptioner；否则 NoopCaptioner
  ```

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_captioner.py`:

```python
"""ImageCaptioner 选择与实现测试（不触网：mock OpenAI 客户端）"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.image_caption import NoopCaptioner, QwenVLCaptioner


async def test_noop_returns_empty():
    c = NoopCaptioner()
    assert await c.caption(Path("x.png")) == ""


async def test_qwen_builds_multimodal_message(monkeypatch):
    c = QwenVLCaptioner(api_key="test-key", base_url="http://x", model="qwen-vl-max-latest")

    captured = {}

    class FakeCreate:
        async def __call__(self, **kwargs):
            captured["messages"] = kwargs.get("messages")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="一件青铜鼎"))],
                usage=None,
            )

    monkeypatch.setattr(c.client.chat.completions, "create", FakeCreate())
    # 1x1 PNG（最简合法图片）
    import base64, io
    import struct, zlib

    def _tiny_png() -> bytes:
        raw = b"\x00" + zlib.compress(b"\xff\xff\xff\xff")  # 1x1 白色
        def chunk(t, d):
            c = struct.pack(">I", len(d)) + t + d
            return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b""))

    img_path = Path("t.png")
    img_path.write_bytes(_tiny_png())
    try:
        desc = await c.caption(img_path)
    finally:
        img_path.unlink(missing_ok=True)

    assert desc == "一件青铜鼎"
    msgs = captured["messages"]
    assert len(msgs) == 2  # system + user
    user_content = msgs[1]["content"]
    assert any(isinstance(p, dict) and p.get("type") == "image_url" for p in user_content)
    img_part = next(p for p in user_content if p.get("type") == "image_url")
    assert img_part["image_url"]["url"].startswith("data:image/png;base64,")


async def test_di_selects_captioner(monkeypatch):
    from core.di import AppContainer
    from core.config import settings
    from interfaces.image_captioner import ImageCaptioner
    from services.image_caption import NoopCaptioner, QwenVLCaptioner

    # 无 key → Noop
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    container = AppContainer()
    assert isinstance(container.captioner, NoopCaptioner)

    # 有 key → QwenVL
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test")
    container2 = AppContainer()
    assert isinstance(container2.captioner, QwenVLCaptioner)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_captioner.py -v`
Expected: FAIL（ModuleNotFoundError: services.image_caption）。

- [ ] **Step 3: 实现**

1) Create `Backend/src/interfaces/image_captioner.py`:

```python
"""图片描述接口——文档图片 → 中文描述（T2，可插拔视觉模型）"""
from abc import ABC, abstractmethod
from pathlib import Path


class ImageCaptioner(ABC):
    """将图片文件转为中文描述（失败/无能力返回空串，调用方降级）"""

    @abstractmethod
    async def caption(self, image_path: Path) -> str:
        """返回中文描述；无法描述时返回 ""（调用方用文件名占位）"""
        ...
```

2) Create `Backend/src/services/image_caption.py`:

```python
"""图片描述服务——QwenVL（阿里云百炼 OpenAI 兼容）/ Noop 双实现

QwenVL 需要 .env 配置 DASHSCOPE_API_KEY；无 key 时 di 选择 NoopCaptioner，
文档图片链路降级为「文件名占位」（图片仍导出与展示，T3 不受影响）。
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

from openai import AsyncOpenAI

from interfaces.image_captioner import ImageCaptioner

logger = logging.getLogger(__name__)

_CAPTION_PROMPT = (
    "请用不超过 40 字的中文概括这张文档图片：主体内容、可辨识的纹饰或文字。"
    "若无法辨认则只回答：图片内容无法辨认。"
)


class QwenVLCaptioner(ImageCaptioner):
    """阿里云百炼 Qwen-VL（OpenAI 兼容多模态接口）"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-vl-max-latest",
    ):
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def caption(self, image_path: Path) -> str:
        try:
            b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
            messages = [
                {"role": "system", "content": _CAPTION_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        }
                    ],
                },
            ]
            completion = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                max_tokens=128,
            )
            text = (completion.choices[0].message.content or "").strip()
            return text
        except Exception as e:
            logger.warning(f"QwenVL 图片描述失败 ({image_path.name}): {e}")
            return ""


class NoopCaptioner(ImageCaptioner):
    """无视觉能力时的降级实现"""

    async def caption(self, image_path: Path) -> str:
        return ""
```

3) Modify `Backend/src/core/config.py`，在「── LLM ──」区块末尾加：

```python
    # ── 文档图片描述（T2 可插拔；无 key 时降级为文件名占位）──
    dashscope_api_key: str = ""            # .env: DASHSCOPE_API_KEY（阿里云百炼）
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    image_caption_model: str = "qwen-vl-max-latest"
```

4) Modify `Backend/src/core/di.py`：

   a) import 区加：`from interfaces.image_captioner import ImageCaptioner`
   b) dataclass 字段加：`_captioner: ImageCaptioner | None = field(default=None, init=False)`
   c) 新增 property（放在 reranker 之后）：

```python
    # ── Image Captioner（文档图片描述，可插拔）──────────

    @property
    def captioner(self) -> ImageCaptioner:
        """文档图片描述器——配置了百炼 key 用 Qwen-VL，否则降级 Noop"""
        if self._captioner is None:
            from services.image_caption import NoopCaptioner, QwenVLCaptioner

            if self.config.dashscope_api_key:
                self._captioner = QwenVLCaptioner(
                    api_key=self.config.dashscope_api_key,
                    base_url=self.config.dashscope_base_url,
                    model=self.config.image_caption_model,
                )
            else:
                self._captioner = NoopCaptioner()
        return self._captioner
```

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_captioner.py -v`
Expected: 3 passed。

---

### Task D3: 上传管线图片块 + 静态服务 + sources image_url

**Files:**
- Modify: `Backend/src/api/upload.py`、`Backend/src/main.py`、`Backend/src/services/agent/orchestrator.py`
- Test: `Backend/tests/test_upload_images.py`

**Interfaces:**
- Consumes: `ParsedDocument.images`（D1）、`container.captioner`（D2）。
- Produces:
  - `_build_image_chunks(images: list[str], source: str, page_of: dict[str,int]) -> list[dict]`——图片块文档 dict 列表（content 占位或描述、metadata 含 image_path 相对路径）。
  - `_cleanup_source_images(source: str) -> None`——删除 `UPLOAD_DIR/{source}.images` 目录。
  - upload 管线：图片块并入 `doc_dicts` 入库（**不参与问题生成**）。
  - `main.py`：`app.mount("/api/uploads", StaticFiles(directory=settings.upload_dir))`。
  - orchestrator quick_answer 的 sources items 增加 `image_url`（取 `d["metadata"].get("image_path")`）。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_upload_images.py`:

```python
"""上传图片块构造与清理测试"""
from pathlib import Path

from api.upload import _build_image_chunks, _cleanup_source_images


def test_build_image_chunks_with_caption():
    chunks = _build_image_chunks(
        ["C:/u/123_a.pdf.images/fig_1.png"],
        source="123_a.pdf",
        page_of={"C:/u/123_a.pdf.images/fig_1.png": 2},
        caption="一件青铜鼎",
    )
    assert len(chunks) == 1
    c = chunks[0]
    assert "【文档图片·第2页】一件青铜鼎" in c["content"]
    assert c["metadata"]["source"] == "123_a.pdf"
    assert c["metadata"]["image_path"] == "123_a.pdf.images/fig_1.png"
    assert c["metadata"]["chunk_type"] == "image"


def test_build_image_chunks_placeholder_when_no_caption():
    chunks = _build_image_chunks(
        ["C:/u/123_a.pdf.images/fig_1.png"],
        source="123_a.pdf",
        page_of={"C:/u/123_a.pdf.images/fig_1.png": 1},
        caption="",
    )
    assert "fig_1.png" in chunks[0]["content"]  # 占位内容含文件名


def test_cleanup_source_images(tmp_path, monkeypatch):
    from api import upload as upload_mod

    monkeypatch.setattr(upload_mod, "UPLOAD_DIR", tmp_path)
    img_dir = tmp_path / "123_a.pdf.images"
    img_dir.mkdir()
    (img_dir / "fig_1.png").write_bytes(b"x")
    _cleanup_source_images("123_a.pdf")
    assert not img_dir.exists()
    # 目录不存在时不报错
    _cleanup_source_images("123_a.pdf")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_upload_images.py -v`
Expected: FAIL（ModuleNotFoundError/AttributeError）。

- [ ] **Step 3: 实现 upload.py 图片块**

Modify `Backend/src/api/upload.py`：

1) import 区加（shutil 用于目录清理）：

```python
import shutil
```

2) 新增两个函数（放在 `_delete_source` 之后）：

```python
def _build_image_chunks(
    images: list[str], source: str, page_of: dict[str, int],
    caption: str = "",
) -> list[dict]:
    """图片 → 入库块（T3：携带 image_path 供前端展示原图）

    content 优先用 VLM 描述；无描述时用「第N页 + 文件名」占位
    （保证图片块可被检索到，且 metadata 链路一致）。
    """
    chunks = []
    for img in images:
        name = Path(img).name
        page = page_of.get(img, 0)
        if caption:
            content = f"【文档图片·第{page}页】{caption}"
        else:
            content = f"【文档图片·第{page}页】{name}"
        chunks.append({
            "chunk_id": str(uuid.uuid4()),
            "content": content,
            "metadata": {
                "source": source,
                "image_path": f"{source}.images/{name}",
                "chunk_type": "image",
            },
        })
    return chunks


def _cleanup_source_images(source: str) -> None:
    """删除 source 关联的图片目录（删除文档/替换/失败清理用）"""
    shutil.rmtree(UPLOAD_DIR / f"{source}.images", ignore_errors=True)
```

3) `_run_pipeline` 中，在 `doc_dicts = [...] for c in chunks]` 定义之后、`# ── 3. 同名替换 ──` 之前，插入图片块构造与合并（最终顺序：chunks → doc_dicts → 图片块 3.5 → doc_dicts.extend(image_chunks) → 同名替换 → 入库）：

```python
        # ── 3.5 文档图片：导出图 → VLM 描述（无 key 占位）→ 图片块入库 ──
        image_chunks: list[dict] = []
        if getattr(parsed, "images", None):
            tm.update_task(task_id, progress=47,
                           stage_text="生成图片描述中…")
            sem = asyncio.Semaphore(3)

            async def _caption_one(img: str) -> str:
                async with sem:
                    return await container.captioner.caption(Path(img))

            cap_results = await asyncio.gather(
                *[_caption_one(img) for img in parsed.images]
            )
            page_of = {img: i + 1 for i, img in enumerate(parsed.images)}
            image_chunks = _build_image_chunks(
                parsed.images, source, page_of, caption="",
            )
            # 逐图合并描述（并发结果顺序与 images 一致）
            for i, chunk in enumerate(image_chunks):
                if i < len(cap_results) and cap_results[i]:
                    chunk["content"] = (
                        f"【文档图片·第{page_of[parsed.images[i]]}页】"
                        f"{cap_results[i]}"
                    )

        doc_dicts.extend(image_chunks)
        total_chunks = len(doc_dicts)
```

（注意：现有代码里 `total_chunks` 在「入库」阶段 `container.vector.add_documents(doc_dicts)` 之后才赋值——把该赋值移到 extend 之后即可，保持图片块计入总数。）

5) **问题生成不包含图片块**——`build_question_documents` 调用处传入的列表改为仅文本块：

```python
                    container.llm,
                    [
                        {"id": d["chunk_id"], "content": d["content"], "metadata": d["metadata"]}
                        for d in doc_dicts
                        if d["metadata"].get("chunk_type") != "image"
                    ],
```

6) `_delete_source` 末尾加图片目录清理：

```python
    container.mark_bm25_dirty()
    task_manager.remove_by_source(source)
    _cleanup_source_images(source)
    return removed
```

7) `_run_pipeline` FAILED 分支（except 里清理 PDF 后）加：

```python
                _cleanup_source_images(source)
```

（`_run_pipeline` 的 `source` 参数即 safe_name，与图片目录前缀一致。）

- [ ] **Step 4: 实现静态服务与 sources image_url**

1) Modify `Backend/src/main.py`，在挂载 API 路由之后、`/api/health` 之前加（`StaticFiles` 与 `Path` 文件顶部已 import，直接复用）：

```python
# ── 上传图片静态服务（文档图片 T3 展示；StaticFiles 自带路径穿越防护）──
UPLOAD_DIR = Path(settings.upload_dir)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
```

2) Modify `Backend/src/services/agent/orchestrator.py` quick_answer 的 sources items 映射加：

```python
                            "image_url": d.get("metadata", {}).get("image_path"),
```

（该 items 列表推导中 `d` 为 retrieved_docs 项，metadata 存在。）

- [ ] **Step 5: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_upload_images.py -v`
Expected: 3 passed。

- [ ] **Step 6: 全量回归**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/ -v`
Expected: 42 + 3 + 2（D1）+ 3（D2）+ 3（D3）= 53 passed。

---

### Task D4: 前端证据链展示原图

**Files:**
- Modify: `Frontend/src/types/api.ts`、`Frontend/src/components/EvidencePanel.vue`

**Interfaces:**
- Consumes: 后端 sources 事件 `items[].image_url`（相对路径 `/api/uploads/...`）。
- Produces: `SourceItem.image_url?: string`；EvidencePanel 来源项图片缩略图（el-image，fit contain，点击预览）。

- [ ] **Step 1: 类型与展示**

1) Modify `Frontend/src/types/api.ts`，`SourceItem` 加：

```ts
  /** 文档图片（T3：命中图片块时展示原图，相对路径 /api/uploads/...） */
  image_url?: string
```

2) Modify `Frontend/src/components/EvidencePanel.vue`：

   a) Template 的 source-item 中、`src-name` 之前插入：

```vue
          <img
            v-if="s.image_url"
            :src="imageAbsUrl(s.image_url)"
            class="src-image"
            loading="lazy"
            @click="openImage(s.image_url)"
          />
```

   b) Script 增加（PATH_LABELS 定义后）：

```ts
/** 后端图片静态服务（前端 API base 拼接） */
const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5172'

function imageAbsUrl(rel: string): string {
  return `${API_BASE}${rel}`
}

/** 点击放大：新窗口打开原图（简单可靠，避免引入图片预览组件依赖） */
function openImage(rel: string) {
  window.open(imageAbsUrl(rel), '_blank')
}
```

   c) Style 增加：

```less
.src-image {
  display: block;
  max-width: 100%;
  max-height: 120px;
  margin-bottom: 6px;
  border: 1px solid rgba(201, 169, 110, 0.35);
  border-radius: 6px;
  cursor: zoom-in;
  transition: opacity 0.2s;
  &:hover { opacity: 0.85; }
}
```

- [ ] **Step 2: 构建验证**

Run: `cd E:/projects/DocuMind/Frontend && npm run build`
Expected: vue-tsc 零错误 + `✓ built`。

---

### Task D5: 测试图片 + 含图测试 PDF

**Files:**
- Create: `E:\projects\DocuMind\docs\test-data\fig_ou_zun.png`（PIL 生成）、更新 `妇好鸮尊测试文档.html`、重新生成 `妇好鸮尊测试文档.pdf`

**Interfaces:**
- Produces: 含一张 PNG 图片的测试 PDF（供 D1 冒烟与用户端上传冒烟）。

- [ ] **Step 1: 生成测试图（PIL，中文用 Windows 字体）**

Run:

```bash
cd E:/projects/DocuMind && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe - <<'EOF'
```
（注意：后端 venv 在 Backend/.venv，用 `cd E:/projects/DocuMind/Backend && uv run python`）

实际命令：

```python
from PIL import Image, ImageDraw, ImageFont

W, H = 480, 340
img = Image.new("RGB", (W, H), "#fdf6e3")
d = ImageDraw.Draw(img)
# 鼎身（梯形 + 双耳 + 三足）
d.polygon([(160, 120), (320, 120), (300, 260), (180, 260)], outline="#8b4513", width=3)
d.ellipse([190, 100, 230, 130], outline="#8b4513", width=3)
d.ellipse([250, 100, 290, 130], outline="#8b4513", width=3)
d.line([(165, 130), (145, 210)], fill="#8b4513", width=3)
d.line([(315, 130), (335, 210)], fill="#8b4513", width=3)
# 兽面纹示意
d.arc([210, 160, 270, 220], 180, 360, fill="#c41e3a", width=3)
font = ImageFont.truetype("C:/Windows/Fonts/simsun.ttc", 22)
d.text((140, 290), "图1 妇好鸮尊线描示意图", font=font, fill="#2c2c2c")
img.save(r"E:/projects/DocuMind/docs/test-data/fig_ou_zun.png")
print("saved", img.size)
```

Expected: `saved (480, 340)`，文件存在。

- [ ] **Step 2: 嵌入 HTML 并重新生成 PDF**

Modify `E:\projects\DocuMind\docs\test-data\妇好鸮尊测试文档.html`：在「二、形制与纹饰特点」标题后插入图片：

```html
<h2>二、形制与纹饰特点</h2>
<p><img src="fig_ou_zun.png" alt="图1 妇好鸮尊线描示意图" style="width: 420px;"></p>
```

（img 引用同目录 PNG——Edge 打印本地 HTML 时相对路径图片可加载。若相对路径不加载，改用 base64 data URI 内联。）

Run（重新生成 PDF）：

```bash
cd E:/projects/DocuMind/docs/test-data && "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless --disable-gpu --no-pdf-header-footer --print-to-pdf="E:/projects/DocuMind/docs/test-data/妇好鸮尊测试文档.pdf" "file:///E:/projects/DocuMind/docs/test-data/妇好鸮尊测试文档.html" 2>&1 | tail -1
```

Expected: `xxx bytes written to file ...妇好鸮尊测试文档.pdf`。

- [ ] **Step 3: 验证 PDF 含图 + 解析正常**

Run:

```bash
cd E:/projects/DocuMind/Backend && PYTHONIOENCODING=utf-8 uv run python -c "
from pypdf import PdfReader
r = PdfReader(r'E:/projects/DocuMind/docs/test-data/妇好鸮尊测试文档.pdf')
imgs = []
for page in r.pages:
    imgs.extend(page.images)
print('pages:', len(r.pages), 'embedded images:', len(imgs))
"
```

Expected: `embedded images: 1`。

- [ ] **Step 4: Docling 导出冒烟（D1 的 Step 7）**

Run: 上接 D1 Step 7 的命令（若 D1 已执行过则跳过，确认 images ≥ 1 即可）。

Expected: `images: 1`，且 `docs/test-data/妇好鸮尊测试文档.pdf.images/` 下有文件。

---

## 回归清单

| 任务 | 回归命令 |
|------|---------|
| D1-D3 | `cd Backend && uv run pytest tests/ -v`（最终 53 passed） |
| D4 | `cd Frontend && npm run build` |
| 用户侧 | 上传含图 PDF → 任务完成 → 提问命中图片块 → 证据链显示原图 |

## 交付顺序

D1 → D2 → D3 → D4 → D5（D5 独立可先行，供 D1 冒烟）
