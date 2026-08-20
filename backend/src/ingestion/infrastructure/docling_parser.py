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
        """按需构建带图片导出的转换器

        注意: docling 2.114 实测没有 PdfPipelineOptions.images_dir 参数
        （旧版才有），图片导出开关是 generate_picture_images。此处按字段
        探测兼容两个版本；降级时返回默认转换器（不阻塞解析）。
        """
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import (
                DocumentConverter,
                PdfFormatOption,
            )
            opts = PdfPipelineOptions()
            fields = set(type(opts).model_fields)
            if "images_dir" in fields:
                # 旧版 docling: 转换时直接把图片导出到该目录
                opts.images_dir = str(images_dir)
            if "generate_picture_images" in fields:
                # docling 2.10+: 转换时提取图片，落盘在 parse() 中完成
                opts.generate_picture_images = True
# 扫描件 OCR（settings.docling_ocr=True 时开启，需 easyocr 依赖）
            from core.config import settings

            if settings.docling_ocr and "do_ocr" in fields:
                try:
                    from docling.datamodel.pipeline_options import EasyOcrOptions

                    opts.do_ocr = True
                    opts.ocr_options = EasyOcrOptions()
                except ImportError:
                    # easyocr 未安装 → 明确关闭 OCR 并告警（不留无效配置）
                    opts.do_ocr = False
                    import logging

                    logging.getLogger(__name__).warning(
                        "OCR 依赖（easyocr）未安装，扫描件解析将跳过 OCR"
                    )
            return DocumentConverter(format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            })
        except (ImportError, TypeError) as e:
            # 版本差异时降级为无图片导出（不阻塞解析）
            import logging
            logging.getLogger(__name__).warning(f"Docling 图片导出不可用，降级: {e}")
            return self.converter

    @staticmethod
    def _images_dir_for(file_path: Path) -> Path:
        """图片导出目录：{源文件名}.images（追加而非替换后缀，与 upload.py 的
        image_path 契约一致）"""
        return file_path.resolve().with_name(file_path.name + ".images")

    @staticmethod
    def _ext_for_mimetype(mimetype: str) -> str:
        """图片 MIME 类型 → 文件扩展名（未知类型默认 .png）"""
        return {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
            "image/tiff": ".tiff",
        }.get((mimetype or "").split(";")[0].strip().lower(), ".png")

    def _save_picture(self, element, images_dir: Path, page: int, images: list[str]):
        """把 docling 图片块落盘到 images_dir，路径填入 images（上限 MAX_IMAGES）

        docling 2.114 实测: PictureItem.image 是 ImageRef，其 uri 为
        base64 data URI（AnyUrl，不是文件名），因此直接用 pil_image 保存。
        """
        try:
            img_ref = getattr(element, "image", None)
            if img_ref is None or len(images) >= self.MAX_IMAGES:
                return
            pil = getattr(img_ref, "pil_image", None)
            if pil is None:
                return
            ext = self._ext_for_mimetype(getattr(img_ref, "mimetype", "") or "")
            images_dir.mkdir(parents=True, exist_ok=True)
            out = images_dir / f"fig_{page}_{len(images) + 1}{ext}"
            pil.save(out)
            images.append(str(out))
        except Exception:
            # 单张图片失败不影响整体解析
            pass

    def parse(self, file_path: str | Path) -> ParsedDocument:
        if not self._available:
            raise RuntimeError(
                "Docling 未安装。运行: uv add docling"
            )

        file_path = Path(file_path)
        images_dir = self._images_dir_for(file_path)
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
                self._save_picture(element, images_dir, page, images)
                if not text.strip():
                    continue

            if not text.strip():
                continue

            block_type = self._map_type(label)

            # 表格保留 Markdown 格式 + 行列数（U4：表格块可被按结构过滤）
            meta: dict = {"label": label, "parser": "docling"}
            if block_type == BlockType.TABLE:
                text = self._table_to_markdown(element)
                rows = [l for l in text.splitlines() if l.strip().startswith("|")]
                meta["table_rows"] = len(rows)
                meta["table_cols"] = max(
                    (l.count("|") - 1 for l in rows), default=0
                )

            blocks.append(DocumentBlock(
                type=block_type,
                content=text,
                page=page,
                metadata=meta,
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
