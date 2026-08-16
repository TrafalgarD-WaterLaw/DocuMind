"""PyPDF 文档解析器 — 纯文本提取（已安装，立即可用）

不支持表格/公式/图片语义提取，仅作回退方案。
生产环境推荐替换为 DoclingParser。
"""
import re
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader

from interfaces.doc_parser import (
    BlockType,
    DocParser,
    DocumentBlock,
    ParsedDocument,
)


class PyPDFParser(DocParser):
    """基于 PyPDF 的基础文本解析器"""

    def parse(self, file_path: str | Path) -> ParsedDocument:
        file_path = Path(file_path)
        loader = PyPDFLoader(str(file_path), extract_images=True)
        documents = loader.load()

        blocks: list[DocumentBlock] = []
        for page_idx, doc in enumerate(documents, start=1):
            text = doc.page_content
            # 清理中文段落内换行
            text = re.sub(r"(?<=[一-鿿])\n(?=[一-鿿])", "", text)
            # 合并连续空行
            text = re.sub(r"\n{3,}", "\n\n", text)

            if text.strip():
                blocks.append(DocumentBlock(
                    type=BlockType.TEXT,
                    content=text.strip(),
                    page=page_idx,
                    metadata={"loader": "pypdf"},
                ))

        return ParsedDocument(
            source=file_path.name,
            blocks=blocks,
            metadata={"parser": "pypdf", "pages": len(documents)},
        )
