"""接口抽象层 — 所有业务逻辑依赖这些接口，不依赖具体实现"""

from interfaces.llm import LLMProvider
from interfaces.vector_store import VectorStore
from interfaces.graph_store import GraphStore
from interfaces.embedder import Embedder
from interfaces.image_captioner import ImageCaptioner
from interfaces.doc_parser import DocParser, ParsedDocument, DocumentBlock, BlockType

__all__ = [
    "LLMProvider",
    "VectorStore",
    "GraphStore",
    "Embedder",
    "ImageCaptioner",
    "DocParser",
    "ParsedDocument",
    "DocumentBlock",
    "BlockType",
]
