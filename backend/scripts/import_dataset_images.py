# -*- coding: utf-8 -*-
"""数据集图片落盘 + 映射表生成（多模态阶段 2）

用法:
  uv run python scripts/import_dataset_images.py --source porcelain
  uv run python scripts/import_dataset_images.py --source bronze
  uv run python scripts/import_dataset_images.py --source henan   # 等爬虫跑完

行为:
  - 瓷器: YMbwp-Dataset 70 目录全部图片复制到 src/data/images/porcelain/{目录名}/
  - 青铜器: ori_png 3697 张复制到 src/data/images/bronze/{编号}.png
  - 河南: 读 henan_images.json（爬虫产物），不复制（已在目标位置），仅登记
  - 输出 src/data/image_index.json: {source: {primary, images[]}}（幂等合并）

映射键与 Chroma 文本块 source 一致: 青铜-{器名} / {窑口-器名} / 河南博物院-{文物名}
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# 允许 scripts/ 下直接运行
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

DATA_DIR = Path(__file__).parent.parent / "src" / "data"
IMAGES_ROOT = DATA_DIR / "images"
INDEX_OUT = DATA_DIR / "image_index.json"

# 数据源目录——环境变量 PORCELAIN/BRONZE_DATASET_DIR 覆盖（公开仓库不含本机路径）
PORCELAIN_BASE = Path(os.environ.get("PORCELAIN_DATASET_DIR", "datasets/porcelain"))
BRONZE_ROOT = Path(os.environ.get("BRONZE_DATASET_DIR", "datasets/bronze"))
BRONZE_BASE = BRONZE_ROOT / "ori_png"
# oripng 图片覆盖三份 Excel（train/val/test），合并编号→器名映射
BRONZE_EXCELS = [BRONZE_ROOT / f"{s}.xlsx" for s in ("train", "val", "test")]
HENAN_MANIFEST = DATA_DIR / "henan_images.json"


def _rel(p: Path, root: Path) -> str:
    """相对 root（默认 IMAGES_ROOT）的正斜杠路径（URL 友好）"""
    return p.relative_to(root).as_posix()


def scan_porcelain(base: Path, out_root: Path | None = None) -> dict:
    """瓷器: 目录名(=source) → 全部图片（复制后登记）"""
    root = out_root or IMAGES_ROOT
    mapping: dict[str, list[str]] = {}
    if not base.exists():
        return mapping
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        src_name = d.name  # 如 宣德-青花梅瓶（与 Chroma source 一致）
        target = root / "porcelain" / src_name
        target.mkdir(parents=True, exist_ok=True)
        files = []
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                dst = target / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
                files.append(_rel(dst, root))
        if files:
            mapping[src_name] = files
    return mapping


def scan_bronze(
    png_dir: Path, excel_paths: list[Path], out_root: Path | None = None
) -> tuple[dict, int]:
    """青铜器: 图片编号 → Excel 编号列 → 器名 → source=青铜-{器名}

    oripng 图片覆盖 train/val/test 三份 Excel，全部合并建映射（后出现覆盖先出现）。
    """
    import pandas as pd

    root = out_root or IMAGES_ROOT
    mapping: dict[str, list[str]] = {}
    if not png_dir.exists() or not any(p.exists() for p in excel_paths):
        return mapping, 0
    id2name: dict[str, str] = {}
    for excel_path in excel_paths:
        if not excel_path.exists():
            continue
        df = pd.read_excel(excel_path)
        for _, row in df.iterrows():
            cid = str(row.get("编号", "")).strip()
            name = str(row.get("器名", "")).strip()
            if cid and name and name != "nan":
                id2name[cid] = name  # 合并，后覆盖先

    target = root / "bronze"
    target.mkdir(parents=True, exist_ok=True)
    missed = 0
    for f in sorted(png_dir.glob("*.png")):
        cid = f.stem
        name = id2name.get(cid)
        if not name:
            missed += 1
            continue
        dst = target / f.name
        if not dst.exists():
            shutil.copy2(f, dst)
        mapping.setdefault(f"青铜-{name}", []).append(_rel(dst, root))
    return mapping, missed


def scan_henan(manifest: Path) -> dict:
    """河南: 读爬虫产物 henan_images.json → source=河南博物院-{文物名}

    与 porcelain/bronze 一致存无 images/ 前缀的相对路径（henan/{文物名}/{file}），
    由服务层 _to_url 统一拼 /api/images/ 前缀，避免双前缀。
    """
    mapping: dict[str, list[str]] = {}
    if not manifest.exists():
        return mapping
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for key, entry in data.items():
        name = entry.get("name", key)
        files = [f"henan/{key}/{img['file']}" for img in entry.get("images", [])]
        if files:
            mapping[f"河南博物院-{name}"] = files
    return mapping


def build_image_index(partials: list[dict]) -> dict:
    """合并多源映射为 {source: {primary, images[]}}"""
    merged: dict[str, dict] = {}
    for partial in partials:
        for source, files in partial.items():
            if not files:
                continue
            merged[source] = {"primary": files[0], "images": files}
    return merged


def main():
    parser = argparse.ArgumentParser(description="数据集图片落盘与映射表")
    parser.add_argument("--source", choices=["porcelain", "bronze", "henan"], required=True)
    args = parser.parse_args()

    IMAGES_ROOT.mkdir(parents=True, exist_ok=True)
    partials: list[dict] = []

    if args.source == "porcelain":
        partials.append(scan_porcelain(PORCELAIN_BASE))
        print(f"瓷器: 扫描 {PORCELAIN_BASE}")
    elif args.source == "bronze":
        mapping, missed = scan_bronze(BRONZE_BASE, BRONZE_EXCELS)
        partials.append(mapping)
        print(f"青铜器: 未匹配编号 {missed} 个（无 Excel 对应行）")
    elif args.source == "henan":
        partials.append(scan_henan(HENAN_MANIFEST))
        print(f"河南: 读 {HENAN_MANIFEST}")

    # 与既有映射合并（幂等，可重跑）
    existing: dict = {}
    if INDEX_OUT.exists():
        existing = json.loads(INDEX_OUT.read_text(encoding="utf-8"))
    for source, entry in build_image_index(partials).items():
        existing[source] = entry

    INDEX_OUT.write_text(
        json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"映射表: {INDEX_OUT}（{len(existing)} 个 source）")


if __name__ == "__main__":
    main()
