# -*- coding: utf-8 -*-
"""Export crawled Henan Museum data to project data/ directory.

Copies the raw JSON and generates a browsable Excel:
  data/henan_museum.json        raw crawl output
  data/henan_museum.xlsx        one row per artifact (name/era/material/intro)
Era and material are inferred from the first prose section
(e.g. "商代", "瓷质" appear in the opening line).
"""
import json
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SRC = Path(__file__).parent.parent / "src" / "data" / "henan_museum.json"
DEST_DIR = Path(__file__).parent.parent.parent / "data"

ERA_WORDS = [
    "新石器时代", "夏代", "商代", "西周", "春秋", "战国", "秦朝", "秦代",
    "汉代", "魏晋", "南北朝", "隋代", "唐朝", "唐代", "五代", "宋代",
    "辽代", "金代", "元代", "明代", "清代", "民国",
]
MAT_WORDS = ["青铜", "瓷", "陶", "玉", "金银", "金", "银", "铁", "木", "骨",
             "漆", "绢", "纸", "琉璃", "砖", "象牙", "水晶", "玛瑙", "石"]


def infer_era(intro: str) -> str:
    for w in ERA_WORDS:
        if w in intro:
            return w
    return ""


def infer_material(intro: str) -> str:
    for w in MAT_WORDS:
        if w in intro:
            return w + ("质" if w != "青铜" else "器")
    return ""


def main():
    if not SRC.exists():
        print(f"[ERR] {SRC} not found - run crawl_henan_museum.py first")
        sys.exit(1)

    data = json.loads(SRC.read_text(encoding="utf-8"))
    print(f"Loaded {len(data)} artifacts from {SRC}")

    # 1) Copy raw JSON
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, DEST_DIR / "henan_museum.json")
    print(f"Copied JSON -> {DEST_DIR / 'henan_museum.json'}")

    # 2) Build Excel
    rows = []
    for cid, art in data.items():
        # sections[0] 是标题行，正文首段在 sections[1]
        secs = art.get("sections", [])
        intro = secs[1] if len(secs) > 1 else (secs[0] if secs else "")
        rows.append({
            "id": cid,
            "名称": art.get("name", ""),
            "时代": infer_era(intro),
            "材质": infer_material(intro),
            "简介": intro[:200],
            "正文长度": len(art.get("full_text", "")),
            "小节数": len(art.get("sections", [])),
            "链接": art.get("url", ""),
        })

    df = pd.DataFrame(rows)
    xlsx_path = DEST_DIR / "henan_museum.xlsx"
    df.to_excel(xlsx_path, index=False)
    print(f"Excel -> {xlsx_path} ({len(df)} rows)")

    # Summary
    era_counts = df["时代"].value_counts().head(8)
    print("\nTop eras:", dict(era_counts))
    mat_counts = df["材质"].value_counts().head(8)
    print("Top materials:", dict(mat_counts))


if __name__ == "__main__":
    main()
