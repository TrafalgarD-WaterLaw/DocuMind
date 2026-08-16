"""文档解析接口"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class BlockType(StrEnum):
    TEXT = "text"
    TABLE = "table"
    FORMULA = "formula"
    IMAGE = "image"
    HEADING = "heading"
    CODE = "code"
    LIST = "list"


@dataclass
class DocumentBlock:
    """解析后的单个文档块"""
    type: BlockType
    content: str                         # 文本 / Markdown表格 / LaTeX / 图片描述
    page: int
    bbox: tuple[float, float, float, float] | None = None  # (x1,y1,x2,y2)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    """完整解析结果"""
    source: str                          # 文件名或路径
    blocks: list[DocumentBlock]
    metadata: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)   # 导出图片绝对路径（T3 展示用）

    @property
    def markdown(self) -> str:
        """将解析结果拼接为 Markdown"""
        lines: list[str] = []
        for block in self.blocks:
            if block.type == BlockType.HEADING:
                lines.append(f"\n## {block.content}\n")
            elif block.type == BlockType.TABLE:
                lines.append(f"\n{block.content}\n")
            elif block.type == BlockType.FORMULA:
                lines.append(f"\n{block.content}\n")
            elif block.type == BlockType.IMAGE:
                lines.append(f"\n> {block.content}\n")
            elif block.type == BlockType.CODE:
                lines.append(f"\n```\n{block.content}\n```\n")
            else:
                lines.append(block.content)
        return "\n\n".join(lines)


class DocParser(ABC):
    """文档解析器抽象 — 输入文件，输出结构化 ParsedDocument"""

    @abstractmethod
    def parse(self, file_path: str | Path) -> ParsedDocument:
        """解析文档为结构化语义块"""
        ...
