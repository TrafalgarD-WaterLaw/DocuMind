# -*- coding: utf-8 -*-
"""Import bronze artifacts into Chroma with synthesized description chunks.

The bronze dataset has only structured fields (name / era code / site /
museum / size) - no free text. We synthesize a template chunk per artifact
so RAG can answer era / excavation / museum questions with real data.

Chunk metadata mirrors the porcelain format (kiln/artifact/section) so the
tree-pruning retriever works: kiln="青铜器" groups all bronze items.

Idempotent: deletes previous bronze chunks (source prefix "青铜-") then
re-imports. Deduplicates by artifact name to stay aligned with the 2248
unique Artifact nodes in Neo4j.
"""
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402

# 数据源目录——环境变量 BRONZE_DATASET_DIR 覆盖（公开仓库不含本机路径）
DATASET_DIR = Path(os.environ.get("BRONZE_DATASET_DIR", "datasets/bronze"))
ERA_MAP = {
    1: "商代", 2: "西周", 3: "春秋", 4: "战国",
    5: "秦代", 6: "汉代", 7: "魏晋", 8: "南北朝",
    9: "隋代", 10: "唐代", 11: "宋代", 12: "元代",
    13: "明代", 14: "清代", 15: "民国", 16: "近现代",
    17: "新石器时代", 18: "夏代",
}
COLUMNS = ["idx", "编号", "器名", "时代", "著录", "器形", "现藏", "出土时地"]


def load_all() -> pd.DataFrame:
    frames = []
    for name in ("train", "val", "test"):
        df = pd.read_excel(DATASET_DIR / f"{name}.xlsx")
        df.columns = COLUMNS[: len(df.columns)]
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def clean_value(v) -> str:
    if pd.isna(v):
        return ""
    s = str(v).strip()
    return "" if s in ("-", "nan", "None") else s


def build_chunks(df: pd.DataFrame) -> list[dict]:
    """Synthesize one template chunk per unique artifact name."""
    # Deduplicate by name, keep the first record with the most info
    df["_era"] = df["时代"].map(ERA_MAP).fillna("未知")
    df["_site"] = df["出土时地"].map(clean_value)
    df["_museum"] = df["现藏"].map(clean_value)
    df["_size"] = df["著录"].map(clean_value)
    df["_name"] = df["器名"].map(clean_value)

    best: dict[str, pd.Series] = {}
    for _, row in df.iterrows():
        name = row["_name"]
        if not name:
            continue
        cur = best.get(name)
        if cur is None or (len(row["_site"]) + len(row["_museum"]) >
                           len(cur["_site"]) + len(cur["_museum"])):
            best[name] = row

    documents = []
    for name, row in best.items():
        parts = []
        if row["_site"]:
            parts.append(f"出土于{row['_site']}")
        if row["_museum"]:
            parts.append(f"现藏于{row['_museum']}")
        if row["_size"]:
            parts.append(f"尺寸{row['_size']}")

        content = f"【{row['_era']}】{name}（青铜器）"
        if parts:
            content += "\n" + "，".join(parts)

        documents.append({
            "content": content,
            "metadata": {
                "source": f"青铜-{name}",
                "kiln": "青铜器",
                "artifact": name,
                "section": "考古信息",
                "era": row["_era"],
                "kind": "bronze",
            },
        })
    return documents


def main():
    print("=== Import Bronze to Chroma (synthesized chunks) ===\n")

    df = load_all()
    print(f"Loaded: {len(df)} records (train+val+test)")

    docs = build_chunks(df)
    print(f"Chunks: {len(docs)} (unique artifact names)")

    # Clear previous bronze chunks (source prefix 青铜-)
    vs = container.vector
    old = [s for s in vs.list_sources() if s.startswith("青铜-")]
    for src in old:
        vs.delete(src)
    print(f"Cleared: {len(old)} old bronze sources")

    # Import in batches
    batch = 100
    for i in range(0, len(docs), batch):
        vs.add_documents(docs[i : i + batch])
        print(f"  {min(i + batch, len(docs))}/{len(docs)}")

    print(f"\nDone! Total chunks now: {vs.count()}")

    # Spot-check retrieval
    print("\n=== Retrieval Test ===")
    for query in ["叩鼎是什么朝代的", "兽面纹鱼形扁足鼎出土于哪里", "哪些青铜器有兽面纹"]:
        results = vs.retrieve(query, top_k=2)
        print(f"\nQ: {query}")
        for r in results:
            m = r.get("metadata", {})
            print(f"  [{r['score']:.3f}] {m.get('artifact', '?')} / {m.get('era', '?')}")


if __name__ == "__main__":
    main()
