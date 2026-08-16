# -*- coding: utf-8 -*-
"""Import porcelain identification data into Chroma with proper chunking.

Splits each artifact's full appraisal text into sections by topic
(circle foot, body, glaze, decoration, mark, etc.) for granular retrieval.
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402

EXCEL_PATH = Path(r"E:/桌面/软创赛/datasets/瓷器/瓷器.xlsx")
COL_NAMES = ["kiln_name", "kiln_intro", "source_url", "artifact_name", "artifact_intro", "source_url2"]


def load_data() -> pd.DataFrame:
    df = pd.read_excel(EXCEL_PATH)
    df.columns = COL_NAMES[: len(df.columns)]
    df["kiln_name"] = df["kiln_name"].ffill()
    df["kiln_intro"] = df["kiln_intro"].ffill()
    return df


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split appraisal text into sections like '圈足', '胎体', etc.

    Returns list of (section_title, section_content).
    """
    # Pattern: "N. 标题：" or "N.标题："
    pattern = r"(\d+)\.\s*([^\n：]+)[：:]\s*"
    parts = re.split(pattern, text)

    sections = []
    # parts[0] is before first match, skip if empty
    i = 1
    while i < len(parts) - 1:
        num = parts[i]
        title = parts[i + 1].strip()
        content = parts[i + 2].strip() if i + 2 < len(parts) else ""
        if title and content:
            sections.append((title, content))
        i += 3
    return sections


def build_chunks(df: pd.DataFrame) -> list[dict]:
    documents = []
    for _, row in df.iterrows():
        kiln = str(row.get("kiln_name", "")) if pd.notna(row.get("kiln_name")) else ""
        name = str(row.get("artifact_name", "")) if pd.notna(row.get("artifact_name")) else ""
        intro = str(row.get("artifact_intro", "")) if pd.notna(row.get("artifact_intro")) else ""
        kiln_intro = str(row.get("kiln_intro", "")) if pd.notna(row.get("kiln_intro")) else ""

        # Always include kiln intro as a chunk (if exists)
        if kiln_intro.strip():
            documents.append({
                "content": f"【{kiln}】窑口特征\n{kiln_intro}",
                "metadata": {
                    "source": f"窑口-{kiln}",
                    "kiln": kiln,
                    "artifact": name,
                    "section": "窑口特征",
                },
            })

        # Split artifact intro into sections
        sections = split_sections(intro)
        if sections:
            for title, content in sections:
                chunk_text = f"【{kiln}】{name} — {title}\n{content}"
                if len(chunk_text) < 20:
                    continue
                documents.append({
                    "content": chunk_text,
                    "metadata": {
                        "source": f"{kiln}-{name}",
                        "kiln": kiln,
                        "artifact": name,
                        "section": title,
                    },
                })
        elif intro.strip():
            # Fallback: no sections found, use the whole text
            chunk_text = f"【{kiln}】{name}\n{intro}"
            documents.append({
                "content": chunk_text,
                "metadata": {
                    "source": f"{kiln}-{name}",
                    "kiln": kiln,
                    "artifact": name,
                    "section": "鉴定全文",
                },
            })

    return documents


def main():
    print("=== Import Porcelain to Chroma (with chunking) ===\n")

    df = load_data()
    print(f"Loaded: {len(df)} records")

    docs = build_chunks(df)
    print(f"Chunked: {len(docs)} sections")

    # Clear old data and re-import
    vs = container.vector
    old_sources = vs.list_sources()
    for src in old_sources:
        vs.delete(src)
    print(f"Cleared: {len(old_sources)} old sources")

    # Import in batches
    batch = 20
    for i in range(0, len(docs), batch):
        vs.add_documents(docs[i : i + batch])
        print(f"  {min(i + batch, len(docs))}/{len(docs)}")

    print(f"\nDone! Total chunks: {vs.count()}")
    print(f"Sources: {len(vs.list_sources())}")

    # Test retrieval quality
    print("\n=== Retrieval Test ===")
    for query in ["宣德青花的釉层特征", "元代瓷器的圈足处理", "景德镇的胎体工艺"]:
        results = vs.retrieve(query, top_k=2)
        print(f"\nQuery: {query}")
        for r in results:
            section = r.get("metadata", {}).get("section", "?")
            artifact = r.get("metadata", {}).get("artifact", "?")
            print(f"  [{r['score']:.3f}] {artifact} / {section}")


if __name__ == "__main__":
    main()
