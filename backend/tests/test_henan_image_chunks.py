"""河南图注级图片块构造测试（纯函数，不触网不入库）"""
from scripts.import_henan_image_chunks import build_image_chunks


def test_build_image_chunks_with_caption():
    manifest = {
        "妇好墓玉龙": {
            "name": "妇好墓玉龙",
            "images": [
                {"file": "01.jpg", "figure_no": "1",
                 "caption": "图1  甲骨文所见“龙”字写法", "section": "", "context": ""},
                {"file": "02.jpg", "figure_no": "", "caption": "", "section": "", "context": ""},
            ],
        }
    }
    chunks = build_image_chunks(manifest, {})
    assert len(chunks) == 2
    c = chunks[0]
    assert "【图片·图1】" in c["content"]
    assert "甲骨文" in c["content"]
    assert c["metadata"]["source"] == "河南博物院-妇好墓玉龙#图"
    assert c["metadata"]["chunk_type"] == "image"
    assert c["metadata"]["image_path"] == "/api/images/henan/妇好墓玉龙/01.jpg"
    assert c["metadata"]["figure_no"] == "1"
    # 无图注的第二张：占位内容含文件名
    assert "02.jpg" in chunks[1]["content"]


def test_build_image_chunks_skips_empty():
    assert build_image_chunks({}, {}) == []
