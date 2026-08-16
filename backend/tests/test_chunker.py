# -*- coding: utf-8 -*-
"""S1 结构感知切分器测试（纯函数，不触网不入库不加载模型）

覆盖:
- 段落 + 标题 → 正确的子块/父块数量与 parent_id 闭环
- 超长段落按句子边界拆分（>500 字拆成多块，不丢字）
- TABLE 独立块不拆行；FORMULA 独立块
- IMAGE 块被跳过（不产出子块，也不进父块）
- 父块上限: 超长节截断不跨节（字数 ≤ max_parent_chars）
- 无标题段落流分组: 多个段落分组成多个父块
- 契约完整性: 所有子块 metadata 含 source/chunk_type/block_type/page/parent_id
- 空内容块跳过
"""
from interfaces.doc_parser import BlockType, DocumentBlock, ParsedDocument
from ingestion.infrastructure.chunker import chunk_document


# ── 构造辅助 ─────────────────────────────────────

def _doc(*blocks: DocumentBlock) -> ParsedDocument:
    """构造 ParsedDocument（块顺序即文档顺序）"""
    return ParsedDocument(source="test.pdf", blocks=list(blocks))


def _text(content: str, page: int = 1) -> DocumentBlock:
    return DocumentBlock(type=BlockType.TEXT, content=content, page=page)


def _heading(content: str, page: int = 1) -> DocumentBlock:
    return DocumentBlock(type=BlockType.HEADING, content=content, page=page)


# ── 用例 ─────────────────────────────────────────

def test_headings_and_paragraphs_closure():
    """段落 + 标题 → 子块/父块数量正确，parent_id 闭环"""
    parsed = _doc(
        _heading("一、形制", page=1),
        _text("鼎为圆形三足，口沿外折。", page=1),
        _heading("二、纹饰", page=2),
        _text("腹部饰饕餮纹，双目突出。", page=2),
    )
    result = chunk_document(parsed, "青铜-素面弦纹鼎")

    # 子块: 2 标题 + 2 段落；父块: 2 节
    assert len(result.children) == 4
    assert len(result.parents) == 2

    # 子块按文档顺序产出，block_type 正确
    assert [c["metadata"]["block_type"] for c in result.children] == [
        "heading", "text", "heading", "text",
    ]

    # 父子闭环: 每个子块 parent_id 都命中某个父块
    parent_ids = {p["chunk_id"] for p in result.parents}
    assert all(c["metadata"]["parent_id"] in parent_ids for c in result.children)

    # 父块内容含标题行；页数为节首块页码
    assert result.parents[0]["content"] == "一、形制\n鼎为圆形三足，口沿外折。"
    assert result.parents[0]["metadata"]["page"] == 1
    assert result.parents[1]["content"].startswith("二、纹饰")
    assert result.parents[1]["metadata"]["page"] == 2


def test_long_paragraph_split_by_sentence():
    """超长段落（>500 字）按句子边界拆成多块，拼接还原不丢字"""
    sentence = "妇好墓出土的青铜方斝通高约六十厘米，器身满布兽面纹与云雷纹等精美纹饰，铸造工艺极为精湛，是商代晚期贵族礼器的重要代表。"
    para = sentence * 10  # 10 句，约 650 字 > sentence_split_chars(500)
    parsed = _doc(_text(para, page=3))
    result = chunk_document(parsed, "青铜-方斝")

    children = result.children
    assert len(children) > 1  # 被拆成多块
    assert all(c["metadata"]["block_type"] == "text" for c in children)
    assert all(c["metadata"]["page"] == 3 for c in children)

    # 句子边界拆分不丢字: 子块拼接 == 原文
    assert "".join(c["content"] for c in children) == para
    # 每块长度不超过 max_child_chars + 单句长度（不跨句贪心打包上限）
    assert all(len(c["content"]) <= 250 + len(sentence) for c in children)


def test_table_and_formula_standalone():
    """TABLE 整表独立子块不拆行；FORMULA 独立子块；各自独立成节"""
    table = "| 名称 | 时代 |\n|------|------|\n| 妇好鸮尊 | 商代 |"
    formula = r"x = \frac{a}{b}"
    parsed = _doc(
        _heading("三、铭文", page=1),
        _text("铭文铸于器底。", page=1),
        DocumentBlock(type=BlockType.TABLE, content=table, page=2),
        DocumentBlock(type=BlockType.FORMULA, content=formula, page=2),
    )
    result = chunk_document(parsed, "青铜-妇好鸮尊")

    children = result.children
    table_children = [c for c in children if c["metadata"]["block_type"] == "table"]
    formula_children = [c for c in children if c["metadata"]["block_type"] == "formula"]
    assert len(table_children) == 1
    assert table_children[0]["content"] == table  # 整表原样，不拆行
    assert len(formula_children) == 1
    assert formula_children[0]["content"] == formula

    # heading 节 + 表格节 + 公式节 = 3 个父块
    assert len(result.parents) == 3
    # 表格节父块内容即整张表
    assert any(p["content"] == table for p in result.parents)
    # 表格子块 parent_id 指向表格父块
    table_parent_id = table_children[0]["metadata"]["parent_id"]
    assert any(p["chunk_id"] == table_parent_id for p in result.parents)


def test_image_blocks_skipped():
    """IMAGE 块不产出子块，内容不进父块，也不阻断段落流分组"""
    parsed = _doc(
        _text("第一段文字。", page=1),
        DocumentBlock(type=BlockType.IMAGE, content="青铜鼎照片", page=2),
        _text("第二段文字。", page=3),
    )
    result = chunk_document(parsed, "青铜-鼎")

    # 不产出图片子块；两个段落各一个子块
    assert len(result.children) == 2
    assert all(c["metadata"]["block_type"] != "image" for c in result.children)
    # 图片内容不进入任何父块
    assert all("青铜鼎照片" not in p["content"] for p in result.parents)
    # 图片跳过不阻断段落流: 两段仍同节（不足 750 字分组阈值）
    assert len(result.parents) == 1
    assert result.parents[0]["content"] == "第一段文字。\n第二段文字。"


def test_parent_truncated_within_section():
    """超长节父块截断（≤ max_parent_chars 并附注释），不跨节"""
    long_body = "鼎身纹饰细节考述。" * 300  # 约 2700 字 > max_parent_chars(1500)
    parsed = _doc(
        _heading("一、纹饰详考", page=1),
        _text(long_body, page=1),
        _heading("二、铭文考释", page=2),
        _text("铭文简短。", page=2),
    )
    result = chunk_document(parsed, "青铜-鼎")

    assert len(result.parents) == 2
    p0, p1 = result.parents[0], result.parents[1]

    # 第一节截断: 总长度 ≤ 上限且附截断注释
    assert len(p0["content"]) <= 1500
    assert "已截断" in p0["content"]
    # 第二节完整不受影响（不跨节）
    assert len(p1["content"]) <= 1500
    assert p1["content"] == "二、铭文考释\n铭文简短。"
    assert "已截断" not in p1["content"]
    # 截断后首尾仍属于第一节（不串入第二节内容）
    assert p0["content"].startswith("一、纹饰详考")
    assert "二、铭文考释" not in p0["content"]


def test_untitled_flow_grouped_into_sections():
    """无标题段落流按累计 ~max_parent_chars/2 字分组成多个父块"""
    para = "段落正文。" * 50  # 每段 250 字
    parsed = _doc(_text(para, page=1), _text(para, page=1),
                  _text(para, page=1), _text(para, page=1))
    result = chunk_document(parsed, "青铜-鼎")

    # 250×3 = 750 ≤ 阈值 750，250×4 = 1000 > 750 → 拆成 2 个父块
    assert len(result.parents) == 2
    # 每段 250 字 ≤ 500 不触发句子拆分 → 4 个子块
    assert len(result.children) == 4

    # 第一节含前三段（含换行），第二节含第四段
    assert result.parents[0]["content"] == "\n".join([para, para, para])
    assert result.parents[1]["content"] == para
    # 父子闭环
    parent_ids = {p["chunk_id"] for p in result.parents}
    assert all(c["metadata"]["parent_id"] in parent_ids for c in result.children)


def test_metadata_contract_complete():
    """契约完整性: 子块含 source/chunk_type/block_type/page/parent_id，父块含 is_parent"""
    parsed = _doc(
        _heading("一、概述", page=1),
        _text("概述正文。", page=1),
        DocumentBlock(type=BlockType.TABLE, content="| a | b |", page=2),
    )
    result = chunk_document(parsed, "青铜-鼎")

    for child in result.children:
        meta = child["metadata"]
        assert meta["source"] == "青铜-鼎"
        assert meta["chunk_type"] == "text"
        assert meta["block_type"] in {"text", "heading", "table", "formula"}
        assert isinstance(meta["page"], int)
        assert isinstance(meta["parent_id"], str) and meta["parent_id"]

    for parent in result.parents:
        meta = parent["metadata"]
        assert meta["source"] == "青铜-鼎"
        assert meta["chunk_type"] == "text"
        assert meta["block_type"] == "parent"
        assert meta["is_parent"] is True
        assert isinstance(meta["page"], int)


def test_empty_blocks_skipped():
    """空内容块（空白文本/空标题/空图片）不产出子块，不新增节"""
    parsed = _doc(
        _text("有效内容。", page=1),
        _text("   ", page=1),
        _heading("", page=2),
        DocumentBlock(type=BlockType.IMAGE, content="", page=2),
    )
    result = chunk_document(parsed, "青铜-鼎")

    assert len(result.children) == 1
    assert len(result.parents) == 1
    assert result.children[0]["content"] == "有效内容。"


# ── 句子边界截断（残句不进上下文）──────────────────────────

def test_truncate_at_sentence_boundary():
    from ingestion.infrastructure.chunker import _truncate_at_sentence

    # 截断点落在"很长"段中间（无句号）→ 回退到最近的句号
    text = "第一句完整。第二句有内容。" + "很长" * 100 + "第三句尾部。"
    cut = _truncate_at_sentence(text, 30)
    assert len(cut) <= 30
    assert cut == "第一句完整。第二句有内容。"  # 完整句子，无残句

    # 不超限原样返回
    assert _truncate_at_sentence("短文本。", 100) == "短文本。"

    # 无句子边界（长表格）→ 原样截断
    long_tbl = "甲" * 100
    assert len(_truncate_at_sentence(long_tbl, 50)) == 50


# ── U7a 超长表格拆分（保留表头）───────────────────────────

def test_long_table_split_with_header():
    """>20 行表格拆多块，每块保留表头"""
    from ingestion.infrastructure.chunker import chunk_document

    rows = ["| 器物 | 朝代 |", "|---|---|"] + [f"| 器物{i} | 商代 |" for i in range(30)]
    parsed = _doc(DocumentBlock(type=BlockType.TABLE, content="\n".join(rows), page=1))
    result = chunk_document(parsed, "青铜-表格")

    table_children = [c for c in result.children if c["metadata"]["block_type"] == "table"]
    assert len(table_children) > 1  # 拆成多块
    assert all(c["content"].startswith("| 器物 | 朝代 |\n|---") for c in table_children)
    # 行数覆盖完整（30 行数据都在）
    all_rows = sum(c["content"].count("器物") - 1 for c in table_children)  # 每块表头含 1 个"器物"
    assert all_rows == 30


def test_short_table_not_split():
    """≤20 行表格保持单块"""
    from ingestion.infrastructure.chunker import chunk_document

    rows = ["| 器物 | 朝代 |", "|---|---|"] + [f"| 器物{i} | 商代 |" for i in range(5)]
    parsed = _doc(DocumentBlock(type=BlockType.TABLE, content="\n".join(rows), page=1))
    result = chunk_document(parsed, "青铜-表格")

    tables = [c for c in result.children if c["metadata"]["block_type"] == "table"]
    assert len(tables) == 1
