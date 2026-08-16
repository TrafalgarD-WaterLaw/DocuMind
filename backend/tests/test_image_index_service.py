"""image_index 服务测试（阶段 2 注入化:直接构造实例注入临时路径）"""
import json

from multimodal.image_index import FileBackedImageIndex


def _make_index(tmp_path) -> FileBackedImageIndex:
    return FileBackedImageIndex(tmp_path / "image_index.json")


def test_load_and_query(tmp_path):
    idx = {
        "青铜-叩鼎": {"primary": "images/bronze/101014.png",
                      "images": ["images/bronze/101014.png"]},
        "宣德-青花梅瓶": {"primary": "images/porcelain/宣德-青花梅瓶/a.png",
                        "images": ["images/porcelain/宣德-青花梅瓶/a.png",
                                   "images/porcelain/宣德-青花梅瓶/b.png"]},
    }
    (tmp_path / "image_index.json").write_text(
        json.dumps(idx, ensure_ascii=False), encoding="utf-8"
    )
    index = _make_index(tmp_path)

    urls = index.get_images_for_source("青铜-叩鼎")
    # 映射表存相对路径（bronze/101014.png），服务层拼 /api/images/ 前缀，不重复 images/
    assert urls == ["/api/images/bronze/101014.png"]
    assert index.get_images_for_source("不存在的source") == []


def test_get_images_no_prefix(tmp_path):
    """无前缀 fixture（真实数据形态）：scan_* 产物不带 images/ 前缀，
    服务层直接拼 /api/images/，不经剥离分支"""
    idx = {
        "青铜-叩鼎": {"primary": "bronze/101014.png",
                      "images": ["bronze/101014.png"]},
        "河南博物院-妇好墓玉龙": {"primary": "henan/妇好墓玉龙/01.jpg",
                                "images": ["henan/妇好墓玉龙/01.jpg"]},
    }
    (tmp_path / "image_index.json").write_text(
        json.dumps(idx, ensure_ascii=False), encoding="utf-8"
    )
    index = _make_index(tmp_path)

    assert index.get_images_for_source("青铜-叩鼎") == ["/api/images/bronze/101014.png"]
    assert index.get_images_for_source("河南博物院-妇好墓玉龙") == [
        "/api/images/henan/妇好墓玉龙/01.jpg"
    ]
