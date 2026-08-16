# -*- coding: utf-8 -*-
"""S1 结构感知切分器——Docling 语义块 → 父子两层分块

设计文档: docs/superpowers/specs/2026-08-07-chunking-context-optimization-design.md
「三、方案设计 T1」「四、数据契约变化」

输入 ParsedDocument（有序 DocumentBlock 列表），输出:
    - children: 子块（检索粒度，metadata 带 parent_id，指向所属父块）
    - parents:  父块（节——送 LLM 的完整语义，上限 max_parent_chars，超出截断）

切分规则:
    1. 节分组（父子关系骨架）: HEADING 开始新节（标题属于该节，后续段落并入）；
       TABLE / FORMULA 各自独立成节（结构块不并入段落节）；
       无标题段落流（TEXT/LIST）按累计 ~max_parent_chars/2 字分组为节
    2. 子块: 段落独立成块（超过 sentence_split_chars 按句子边界拆分，
       复用 scripts/import_henan_chroma.py::split_sections 思路）；标题/表格/公式
       各自独立成块；IMAGE 跳过（图片由上传管线 _build_image_chunks 独立构建，
       不在此重复）；空内容块跳过
    3. 父块: 每节一个父块 = 节内所有块内容拼接（含标题行），上限 max_parent_chars，
       超出截断并附注释说明，不跨节

纯逻辑实现——不触 embedding / vector / LLM，可独立单测。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from interfaces.doc_parser import BlockType, DocumentBlock, ParsedDocument

# 句子边界正则（与 scripts/import_henan_chroma.py 的 split_sections 一致）
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；])")

# 父块截断注释后缀（截断时保留，保证总长度仍 ≤ max_parent_chars）
_TRUNCATION_NOTE = "\n…（内容超出父块上限，已截断）"

# 句子边界（与 _SENTENCE_SPLIT_RE 一致——截断回退用）
_SENTENCE_SEPS = ("。", "！", "？", "；")

# 超长表格行数阈值（>20 行按行拆分，保留表头）
TABLE_SPLIT_ROWS = 20


def _truncate_at_sentence(content: str, max_len: int) -> str:
    """按句子边界截断（避免语义单元被从中间切断）

    长度超限时先切到 max_len，再回退到最后一个句子边界（。！？；）——
    残句不进 LLM 上下文；无句子边界（如超长表格）才原样截断。
    """
    if len(content) <= max_len:
        return content
    cut = content[:max_len]
    for sep in _SENTENCE_SEPS:
        idx = cut.rfind(sep)
        if idx != -1:
            return cut[: idx + 1]
    return cut


@dataclass
class ChunkResult:
    """切分结果——子块（检索）+ 父块（送 LLM）

    Attributes:
        children: 子块列表，每块 dict 含 chunk_id/content/metadata，
                  metadata 带 parent_id（指向 parents 中某块的 chunk_id）
        parents:  父块（节）列表，metadata 带 is_parent: true
    """

    children: list[dict] = field(default_factory=list)
    parents: list[dict] = field(default_factory=list)


def _make_block(content: str, metadata: dict) -> dict:
    """构造入库契约块: {chunk_id, content, metadata}"""
    return {"chunk_id": str(uuid.uuid4()), "content": content, "metadata": metadata}


def _split_by_sentences(text: str, max_chars: int) -> list[str]:
    """按句子边界把长文本拆成多块，目标每块 ~max_chars 字

    复用 import_henan_chroma.py::split_sections 的贪婪打包思路:
    句子是"。"！？；"后的最小不可拆单元，累计超过 max_chars 时切出新块；
    单句超长时整句保留（不破坏句子边界）。
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        if buf and len(buf) + len(s) > max_chars:
            chunks.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        chunks.append(buf)
    return chunks


def _build_sections(
    blocks: list[DocumentBlock], untitled_threshold: int
) -> list[list[DocumentBlock]]:
    """把有序块流分组为节（父子关系的骨架）

    - HEADING 开始新节（标题本身属于该节，后续段落直接并入，不按字数分界）
    - TABLE / FORMULA 各自独立成节（结构块不并入段落节）
    - 无标题段落流（TEXT/LIST/CODE）按累计 untitled_threshold 字分组
    - IMAGE 与空内容块整体跳过（不进任何节，图片由独立链路构建）
    """
    sections: list[list[DocumentBlock]] = []
    buf: list[DocumentBlock] = []
    buf_chars = 0

    def flush() -> None:
        nonlocal buf, buf_chars
        if buf:
            sections.append(buf)
            buf, buf_chars = [], 0

    for block in blocks:
        content = block.content or ""
        if block.type == BlockType.IMAGE or not content.strip():
            continue
        if block.type in (BlockType.TABLE, BlockType.FORMULA):
            flush()
            sections.append([block])
        elif block.type == BlockType.HEADING:
            flush()
            buf, buf_chars = [block], len(content)
        else:
            # TEXT / LIST / CODE 段落流
            if not buf:
                buf, buf_chars = [block], len(content)
            elif buf[0].type == BlockType.HEADING:
                # 标题引导的节: 段落直接并入（父块上限 max_parent_chars 兜底）
                buf.append(block)
                buf_chars += len(content)
            elif buf_chars + len(content) > untitled_threshold:
                # 无标题流超过分组阈值 → 新开一节
                flush()
                buf, buf_chars = [block], len(content)
            else:
                buf.append(block)
                buf_chars += len(content)
    flush()
    return sections


def _make_parent(
    section: list[DocumentBlock], source: str, max_parent_chars: int
) -> dict:
    """节 → 父块: 内容拼接（含标题行），超上限截断并附注释，不跨节"""
    content = "\n".join(b.content for b in section)
    if len(content) > max_parent_chars:
        keep = max_parent_chars - len(_TRUNCATION_NOTE)
        # 句子边界截断——残句不进 LLM 上下文
        content = _truncate_at_sentence(content, keep) + _TRUNCATION_NOTE
    return _make_block(
        content,
        {
            "source": source,
            "chunk_type": "text",
            "block_type": "parent",
            "page": section[0].page,  # 节首块页码，供溯源
            "is_parent": True,
        },
    )


def _make_children(
    section: list[DocumentBlock],
    parent_id: str,
    source: str,
    max_child_chars: int,
    sentence_split_chars: int,
) -> list[dict]:
    """节 → 子块: 段落/标题/表格/公式各自独立成块，长段按句子边界拆分

    IMAGE 已在节分组时排除；空内容块不产出子块。
    每个子块 metadata 带 parent_id，指向所属父块 chunk_id。
    """
    children: list[dict] = []
    for block in section:
        content = block.content or ""
        if not content.strip():
            continue
        if block.type in (BlockType.TEXT, BlockType.LIST, BlockType.CODE):
            children.extend(_make_text_children(
                block, parent_id, source, max_child_chars, sentence_split_chars,
            ))
        elif block.type == BlockType.TABLE and len(content.splitlines()) > TABLE_SPLIT_ROWS:
            children.extend(_make_table_children(block, parent_id, source))
        else:
            children.append(_make_single_child(block, parent_id, source))
    return children


def _make_text_children(
    block: DocumentBlock, parent_id: str, source: str,
    max_child_chars: int, sentence_split_chars: int,
) -> list[dict]:
    """段落子块: 长段（超过 sentence_split_chars）按句子边界拆分,短段整段成块"""
    content = block.content or ""
    pieces = (
        _split_by_sentences(content, max_child_chars)
        if len(content) > sentence_split_chars else [content]
    )
    return [
        _make_block(
            piece,
            {
                "source": source,
                "chunk_type": "text",
                "block_type": "text",
                "page": block.page,
                "parent_id": parent_id,
            },
        )
        for piece in pieces
    ]


def _make_table_children(
    block: DocumentBlock, parent_id: str, source: str,
) -> list[dict]:
    """U7a: 超长表格（>20 行）按行拆分多块——每块保留表头 + 分隔行，

    避免整表单块过长被父块截断；小块仍可被精确检索。
    """
    lines = (block.content or "").splitlines()
    header = lines[:2]  # 表头 + 分隔行
    body = lines[2:]
    pieces = [header + body[i:i + TABLE_SPLIT_ROWS - 2]
              for i in range(0, len(body), TABLE_SPLIT_ROWS - 2)]
    return [
        _make_block(
            "\n".join(piece),
            {
                "source": source,
                "chunk_type": "text",
                "block_type": "table",
                "page": block.page,
                "parent_id": parent_id,
            },
        )
        for piece in pieces
    ]


def _make_single_child(
    block: DocumentBlock, parent_id: str, source: str,
) -> dict:
    """HEADING / 短 TABLE / FORMULA: 整块独立成子块（表格不拆行）"""
    return _make_block(
        block.content or "",
        {
            "source": source,
            "chunk_type": "text",
            "block_type": block.type.value,
            "page": block.page,
            "parent_id": parent_id,
        },
    )


def chunk_document(
    parsed: ParsedDocument,
    source: str,
    *,
    max_child_chars: int = 250,
# 与 settings.context_block_chars=1500 对齐——父块长度即组装上限，
    # 两处改一处需同步（chunker 保持纯函数不引 settings）
    max_parent_chars: int = 1500,
    sentence_split_chars: int = 500,
) -> ChunkResult:
    """结构感知切分: ParsedDocument → 子块 + 父块

    Args:
        parsed: Docling 解析产物（有序 DocumentBlock 列表）
        source: 数据源名（P1-C 规范，写入所有块 metadata）
        max_child_chars: 子块目标长度（长段按句子边界打包到该值附近）
        max_parent_chars: 父块（节）内容上限，超出截断并附注释
        sentence_split_chars: 段落超过该字数才触发句子边界拆分

    Returns:
        ChunkResult: children（检索粒度，带 parent_id）+ parents（节，送 LLM）
    """
    # 无标题段落流的分组阈值 = 父块上限的一半（默认 1500//2 = 750 ≈ 800 字）
    sections = _build_sections(parsed.blocks, untitled_threshold=max_parent_chars // 2)

    parents: list[dict] = []
    children: list[dict] = []
    for section in sections:
        parent = _make_parent(section, source, max_parent_chars)
        parents.append(parent)
        children.extend(
            _make_children(
                section,
                parent["chunk_id"],
                source,
                max_child_chars,
                sentence_split_chars,
            )
        )

    # 父子闭环断言: 每个子块的 parent_id 必须命中某个父块 chunk_id
    # （每节恰好产出一个父块，构造上成立；防御性校验防后续改动破坏引用）
    parent_ids = {p["chunk_id"] for p in parents}
    assert all(c["metadata"]["parent_id"] in parent_ids for c in children), (
        "子块 parent_id 未命中父块 chunk_id，父子引用断裂"
    )

    return ChunkResult(children=children, parents=parents)
