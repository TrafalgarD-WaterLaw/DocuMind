# -*- coding: utf-8 -*-
"""Import crawled Henan Museum artifacts into Neo4j.

Reads src/data/henan_museum.json and creates:
  - Artifact nodes (name=artifact name, props: introduce/era/kind=henan)
  - BELONGS_TO Era relationships when the era can be inferred and the
    Era node exists in the graph (merges cleanly with the 2248 bronze
    Artifacts; duplicates like 妇好鸮尊 get enriched, not duplicated).

Idempotent: MERGE by name.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402

DATA_FILE = Path(__file__).parent.parent / "src" / "data" / "henan_museum.json"
ERA_WORDS = [
    "新石器时代", "夏代", "商代", "西周", "春秋", "战国", "秦代", "汉代",
    "魏晋", "南北朝", "隋代", "唐代", "五代", "宋代", "辽代", "金代",
    "元代", "明代", "清代", "民国",
]


def infer_era(intro: str) -> str:
    for w in ERA_WORDS:
        if w in intro:
            return w
    return ""


def main():
    print("=== Import Henan Museum to Neo4j ===\n")

    graph = container.graph
    if graph is None:
        print("[ERR] Neo4j unavailable - is it running?")
        sys.exit(1)

    if not DATA_FILE.exists():
        print(f"[ERR] {DATA_FILE} not found - run crawl_henan_museum.py first")
        sys.exit(1)

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    print(f"Artifacts from JSON: {len(data)}")

    nodes, rels = [], []
    no_era = 0
    for art in data.values():
        name = art.get("name", "")
        secs = art.get("sections", [])
        intro = secs[1] if len(secs) > 1 else (secs[0] if secs else "")
        if not name:
            continue
        era = infer_era(intro)
        if not era:
            no_era += 1
        nodes.append({
            "name": name,
            "label": "Artifact",
            "props": {
                "introduce": intro[:500],
                "era": era,
                "kind": "henan",
                "source": "河南博物院",
            },
        })
        if era:
            rels.append({"source": name, "target": era, "type": "BELONGS_TO"})

    print(f"Artifact nodes: {len(nodes)} (era inferred for {len(nodes) - no_era})")
    print(f"BELONGS_TO relationships: {len(rels)}")

    graph.upsert_nodes(nodes)
    graph.upsert_relationships(rels)

    print(f"\nVerify:")
    print(f"  Artifact count: {graph.count_nodes('Artifact')}")

    # Spot-check expansion (enriched node with era relationship)
    nodes2, links = graph.expand_node("妇好鸮尊")
    if links:
        print(f"  expand(妇好鸮尊): {len(links)} relationships")
        for l in links[:4]:
            print(f"    - {l['source']} -[{l['name']}]-> {l['target']}")
    else:
        print("  expand(妇好鸮尊): no relationships (name mismatch?)")

    print("\nDone!")


if __name__ == "__main__":
    main()
