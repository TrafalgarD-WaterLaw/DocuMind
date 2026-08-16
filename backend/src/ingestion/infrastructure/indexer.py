"""索引构建服务——PDF 加载与文本分块"""
import json
import logging
import os
import re
import uuid

from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.config import settings

logger = logging.getLogger(__name__)


class IndexerService:
    """PDF 加载、文本分块"""

    def load_pdf(
        self, file_path: str, chunk_size: int = settings.chunk_size,
        chunk_overlap: int = settings.chunk_overlap,
    ) -> list[Document]:
        """加载 PDF 并分割为文本块

        默认分块参数收敛进 settings（src/core/config.py: chunk_size=500/
        chunk_overlap=50）。调用方显式传参时不受默认值影响；上传管线
        （api/upload.py）实际走 load_chunks_from_text 并显式传参，本函数
        默认值仅兜底。
        """
        loader = PyPDFLoader(file_path, extract_images=True)
        documents = loader.load()
        for page in documents:
            page.page_content = re.sub(
                r"(?<=[一-鿿])\n(?=[一-鿿])",
                "",
                page.page_content,
            )
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ".", ",", "．", "。"],
        )
        chunks = splitter.split_documents(documents)
        for chunk in chunks:
            chunk.metadata["chunk_id"] = str(uuid.uuid4())
        logger.info(f"PDF loaded: {len(chunks)} chunks from {file_path}")
        return chunks

    def load_chunks_from_text(
        self, text: str, source: str = "",
        chunk_size: int = 500, chunk_overlap: int = 50,
    ) -> list[Document]:
        """从文本字符串创建文档块"""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", ".", "，", ","],
        )
        doc = Document(page_content=text, metadata={"source": source})
        chunks = splitter.split_documents([doc])
        for chunk in chunks:
            chunk.metadata["chunk_id"] = str(uuid.uuid4())
            chunk.metadata["source"] = source
        logger.info(f"Text chunked: {len(chunks)} chunks from source={source}")
        return chunks

    def save_chunks(self, chunks: list[Document], output_path: str):
        """保存文本块到 JSON"""
        data = [
            {"page_content": c.page_content, "metadata": c.metadata}
            for c in chunks
        ]
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Chunks saved to {output_path}")

    def load_chunks(self, file_path: str) -> list[Document]:
        """从 JSON 加载文本块"""
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        return [
            Document(
                page_content=item["page_content"],
                metadata=item.get("metadata", {}),
            )
            for item in data
        ]
