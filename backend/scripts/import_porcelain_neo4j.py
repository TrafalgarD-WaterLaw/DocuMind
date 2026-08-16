# -*- coding: utf-8 -*-
"""Import porcelain kilns and artifacts into Neo4j.

Reads the porcelain dataset (E:/桌面/软创赛/datasets/瓷器/瓷器.xlsx), creates:
  - Kiln nodes (4: 洪武/宣德/永乐/元代) with kiln intro
  - Artifact nodes named "{kiln}-{artifact}" (matches Chroma source format,
    avoids name collisions across kilns) with kind=porcelain
  - BELONGS_TO_KILN relationships Artifact -> Kiln

Idempotent: MERGE by name, safe to re-run.
"""
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


def build_nodes(df: pd.DataFrame) -> tuple[list[dict], list[dict], list[dict]]:
    """Build (kiln_nodes, artifact_nodes, relationships)"""
    kiln_nodes = []
    artifact_nodes = []
    relationships = []

    seen_kilns: set[str] = set()
    for _, row in df.iterrows():
        kiln = str(row.get("kiln_name", "")) if pd.notna(row.get("kiln_name")) else ""
        artifact = str(row.get("artifact_name", "")) if pd.notna(row.get("artifact_name")) else ""
        kiln_intro = str(row.get("kiln_intro", "")) if pd.notna(row.get("kiln_intro")) else ""
        artifact_intro = str(row.get("artifact_intro", "")) if pd.notna(row.get("artifact_intro")) else ""

        if kiln and kiln not in seen_kilns:
            seen_kilns.add(kiln)
            kiln_nodes.append({
                "name": kiln,
                "label": "Kiln",
                "props": {"introduce": kiln_intro, "kind": "kiln"},
            })

        if kiln and artifact:
            # Name matches Chroma source format "{kiln}-{artifact}"
            full_name = f"{kiln}-{artifact}"
            artifact_nodes.append({
                "name": full_name,
                "label": "Artifact",
                "props": {
                    "kiln": kiln,
                    "kind": "porcelain",
                    "introduce": artifact_intro[:500],
                },
            })
            relationships.append({
                "source": full_name,
                "target": kiln,
                "type": "BELONGS_TO_KILN",
            })

    return kiln_nodes, artifact_nodes, relationships


def main():
    print("=== Import Porcelain to Neo4j ===\n")

    graph = container.graph
    if graph is None:
        print("[ERR] Neo4j unavailable - is it running?")
        sys.exit(1)

    df = load_data()
    print(f"Loaded: {len(df)} records")

    kiln_nodes, artifact_nodes, relationships = build_nodes(df)
    print(f"Kiln nodes: {len(kiln_nodes)}")
    print(f"Artifact nodes: {len(artifact_nodes)}")
    print(f"BELONGS_TO_KILN relationships: {len(relationships)}")

    graph.upsert_nodes(kiln_nodes)
    graph.upsert_nodes(artifact_nodes)
    graph.upsert_relationships(relationships)

    print(f"\nVerify:")
    print(f"  Kiln count: {graph.count_nodes('Kiln')}")
    print(f"  Artifact count: {graph.count_nodes('Artifact')}")

    # Spot-check: expand a porcelain artifact
    nodes, links = graph.expand_node("宣德-青花碗")
    print(f"  expand(宣德-青花碗): {len(links)} relationships")
    for l in links[:4]:
        print(f"    - {l['source']} -[{l['name']}]-> {l['target']}")

    print("\nDone!")


if __name__ == "__main__":
    main()
