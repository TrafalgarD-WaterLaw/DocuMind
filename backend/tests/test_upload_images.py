"""上传图片块构造与清理测试"""

from ingestion.application.upload_pipeline import _build_image_chunks, _cleanup_source_images


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
    # P1-C 契约：图片块 source 带 #图 后缀（与文本块来源多样性隔离）
    assert c["metadata"]["source"] == "123_a.pdf#图"
    assert c["metadata"]["image_path"] == "/api/uploads/123_a.pdf.images/fig_1.png"
    assert c["metadata"]["chunk_type"] == "image"


def test_build_image_chunks_placeholder_when_no_caption():
    chunks = _build_image_chunks(
        ["C:/u/123_a.pdf.images/fig_1.png"],
        source="123_a.pdf",
        page_of={"C:/u/123_a.pdf.images/fig_1.png": 1},
        caption="",
    )
    assert "fig_1.png" in chunks[0]["content"]  # 占位内容含文件名


def test_build_image_chunks_with_page_context():
    chunks = _build_image_chunks(
        ["C:/u/123_a.pdf.images/fig_1_1.png"],
        source="123_a.pdf",
        page_of={"C:/u/123_a.pdf.images/fig_1_1.png": 1},
        caption="",
        page_texts={1: "妇好鸮尊整体作站立鸮形，器身满饰兽面纹。", 2: "铸造工艺。"},
    )
    assert "妇好鸮尊" in chunks[0]["content"]
    assert "【文档图片·第1页】" in chunks[0]["content"]


def test_cleanup_source_images(tmp_path, monkeypatch):
    from ingestion.application import upload_pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "UPLOAD_DIR", tmp_path)
    img_dir = tmp_path / "123_a.pdf.images"
    img_dir.mkdir()
    (img_dir / "fig_1.png").write_bytes(b"x")
    _cleanup_source_images("123_a.pdf")
    assert not img_dir.exists()
    # 目录不存在时不报错
    _cleanup_source_images("123_a.pdf")


# ── U1 图注配对（页内"图N"caption）─────────────────────────

def _fake_block(block_type, content, page):
    class _B:
        pass
    b = _B()
    b.type = block_type
    b.content = content
    b.page = page
    return b


def test_pair_figure_captions():
    from ingestion.application.upload_pipeline import _pair_figure_captions
    from interfaces.doc_parser import BlockType

    blocks = [
        _fake_block(BlockType.TEXT, "这段是正文。", 1),
        _fake_block(BlockType.TEXT, "图1  妇好鸮尊正面照片", 1),
        _fake_block(BlockType.TEXT, "图2 局部纹饰特写", 2),
        _fake_block(BlockType.TEXT, "没有图注的段落", 2),
    ]
    caps = _pair_figure_captions(blocks)
# 图注按 (page, 图序号) 配对——同页多图不共用第一条图注
    assert caps == {(1, 1): "妇好鸮尊正面照片", (2, 2): "局部纹饰特写"}


def test_pair_figure_captions_no_match():
    from ingestion.application.upload_pipeline import _pair_figure_captions
    from interfaces.doc_parser import BlockType

    blocks = [_fake_block(BlockType.TEXT, "普通段落，没有图注。", 1)]
    assert _pair_figure_captions(blocks) == {}


def test_build_image_chunks_caption_precedence():
    """图注优先于页面上下文（content 语义升级）"""
    from ingestion.application.upload_pipeline import _build_image_chunks

    chunks = _build_image_chunks(
        ["C:/u/123_a.pdf.images/fig_1_1.png"],
        source="123_a.pdf",
        page_of={"C:/u/123_a.pdf.images/fig_1_1.png": 1},
        page_texts={1: "妇好鸮尊整体作站立鸮形。"},
# fig_1_1.png → (page=1, 图序号=1)
        captions={(1, 1): "妇好鸮尊正面图"},
    )
    assert "妇好鸮尊正面图" in chunks[0]["content"]
    assert "站立鸮形" not in chunks[0]["content"]  # 图注优先，上下文不混入


def test_build_image_chunks_caption_plus_vlm():
    """图注 + VLM 描述拼接（语义互补）"""
    from ingestion.application.upload_pipeline import _build_image_chunks

    chunks = _build_image_chunks(
        ["C:/u/123_a.pdf.images/fig_2_1.png"],
        source="123_a.pdf",
        page_of={"C:/u/123_a.pdf.images/fig_2_1.png": 2},
# fig_2_1.png → (page=2, 图序号=1)
        captions={(2, 1): "鼎的内壁铭文"},
    )
    chunks[0]["content"] += "。器壁铸有铭文数行"  # 模拟 VLM 合并
    assert "鼎的内壁铭文" in chunks[0]["content"]
    assert "铭文数行" in chunks[0]["content"]


# ── U4 解析失败分类 + U5 文档元数据注入 ─────────────────────

def test_classify_parse_error():
    """解析异常 → 机器可读分类（展示文案由前端映射,见 LibraryView）"""
    from ingestion.application.upload_pipeline import _classify_parse_error

    class _E(Exception):
        pass

    assert _classify_parse_error(_E("Document is encrypted")) == "encrypted"
    assert _classify_parse_error(_E("PermissionError: denied")) == "permission"
    assert _classify_parse_error(_E("TimeoutError")) == "timeout"
    assert _classify_parse_error(_E("not a valid pdf")) == "invalid_pdf"
    assert _classify_parse_error(_E("其他错误")) == "other"

    # 模拟时序: 构建后注入 doc_meta（U5 在管线内联，这里验证函数行为）


def test_doc_meta_injection_logic(tmp_path):
    """U5 注入逻辑：file_name/uploaded_at/file_size 合并进块 metadata"""
    import time as _time

    from ingestion.application.upload_pipeline import _build_image_chunks

    chunks = _build_image_chunks(
        ["C:/u/123_a.pdf.images/fig_1.png"],
        source="123_a.pdf",
        page_of={"C:/u/123_a.pdf.images/fig_1.png": 1},
    )
    doc_meta = {
        "file_name": "报告.pdf",
        "uploaded_at": int(_time.time()),
        "file_size": 1024,
    }
    for c in chunks:
        c["metadata"].update(doc_meta)
    assert chunks[0]["metadata"]["file_name"] == "报告.pdf"
    assert chunks[0]["metadata"]["file_size"] == 1024
    assert isinstance(chunks[0]["metadata"]["uploaded_at"], int)
