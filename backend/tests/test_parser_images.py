"""解析器图片导出测试——ParsedDocument.images 契约"""
from pathlib import Path

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


def test_images_dir_for_appends_not_replaces():
    from ingestion.infrastructure.docling_parser import DoclingParser

    d = DoclingParser._images_dir_for(Path("C:/u/123_a.pdf"))
    assert d.name == "123_a.pdf.images"
    assert d.parent == Path("C:/u")
