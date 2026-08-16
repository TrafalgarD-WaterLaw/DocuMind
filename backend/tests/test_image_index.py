"""image_index 扫描与映射测试（纯文件系统，不触网）"""
from pathlib import Path

from scripts.import_dataset_images import scan_porcelain, scan_bronze


def test_scan_porcelain(tmp_path):
    d1 = tmp_path / "宣德-青花梅瓶"
    d2 = tmp_path / "元代-釉里红玉壶春瓶"
    d1.mkdir(); d2.mkdir()
    (d1 / "a.png").write_bytes(b"1")
    (d1 / "b.png").write_bytes(b"2")
    (d2 / "c.png").write_bytes(b"3")

    mapping = scan_porcelain(tmp_path, out_root=tmp_path)
    # 映射表存相对 IMAGES_ROOT 的路径（不含 images/ 前缀，服务层拼 /api/images/）
    assert mapping["宣德-青花梅瓶"] == ["porcelain/宣德-青花梅瓶/a.png",
                                        "porcelain/宣德-青花梅瓶/b.png"]
    assert mapping["元代-釉里红玉壶春瓶"] == ["porcelain/元代-釉里红玉壶春瓶/c.png"]


def test_scan_bronze_matches_excel(tmp_path):
    import pandas as pd

    png = tmp_path / "png"; png.mkdir()
    (png / "101001.png").write_bytes(b"1")
    (png / "999999.png").write_bytes(b"2")  # Excel 无此行 → 未匹配
    excel = tmp_path / "train.xlsx"
    pd.DataFrame({
        "编号": ["101001", "銘三_0034"],
        "器名": ["素面弦纹鼎", "叩鼎"],
        "时代": [1, 1],
        "器形": [17, 18],
        "现藏": ["翁牛特旗博物馆", "-"],
        "出土时地": ["内蒙古赤峰", "-"],
    }).to_excel(excel, index=False)

    mapping, missed = scan_bronze(png, [excel], out_root=tmp_path)
    assert mapping["青铜-素面弦纹鼎"] == ["bronze/101001.png"]
    assert missed == 1  # 999999 无对应行
    assert "999999" not in str(mapping)


def test_build_image_index_merges(tmp_path):
    from scripts.import_dataset_images import build_image_index

    merged = build_image_index([
        {"宣德-青花梅瓶": ["porcelain/宣德-青花梅瓶/a.png"]},
        {"青铜-叩鼎": ["bronze/101014.png"]},
    ])
    assert merged["宣德-青花梅瓶"]["primary"] == "porcelain/宣德-青花梅瓶/a.png"
    assert merged["青铜-叩鼎"]["primary"] == "bronze/101014.png"
