# -*- coding: utf-8 -*-
"""端到端验证混合检索质量：基线单路 vs Hybrid(RRF) vs 树状剪枝"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402
from retrieval.tree import TreeRetriever  # noqa: E402

QUERIES = [
    "宣德青花的釉层特征",
    "元代瓷器的圈足处理",
    "景德镇的胎体工艺",
    "青花瓷的纹饰有什么讲究",
]


def fmt(doc: dict) -> str:
    meta = doc.get("metadata", {})
    return (
        f"[{doc.get('score', 0):.3f}] "
        f"{meta.get('artifact', '?')} / {meta.get('section', '?')} "
        f"(paths={doc.get('paths', 'single')})"
    )


async def main():
    print("=== Hybrid Retrieval Test ===\n")

    hybrid = container.retriever
    tree = TreeRetriever(container.vector)

    # 1. 基线：单路 Chroma
    print("--- Baseline: single-path Chroma ---")
    for q in QUERIES:
        results = container.vector.retrieve(q, top_k=3)
        print(f"\nQ: {q}")
        for r in results:
            print(f"  {fmt(r)}")

    # 2. 混合检索（semantic + question + bm25 + graph）
    print("\n\n--- Hybrid: semantic + question + bm25 + graph ---")
    for q in QUERIES:
        results = await hybrid.retrieve(q)
        print(f"\nQ: {q}")
        for r in results:
            print(f"  {fmt(r)}")

    # 3. 树状层级剪枝
    print("\n\n--- Tree pruning (kiln -> artifact -> section) ---")
    for q in QUERIES[:2]:
        results = tree.retrieve(q, top_k=3)
        print(f"\nQ: {q}")
        for r in results:
            meta = r.get("metadata", {})
            print(
                f"  [{r.get('score', 0):.3f}] pruned={r.get('pruned_path')} "
                f"{meta.get('artifact', '?')} / {meta.get('section', '?')}"
            )

    # 4. 假设性问题索引状态
    print("\n\n--- Question index status ---")
    q_count = container.questions.count()
    print(f"Questions in index: {q_count} (0 表示尚未生成，运行 generate_questions.py)")
    if q_count:
        q_results = container.questions.retrieve(QUERIES[0], top_k=3)
        for r in q_results:
            meta = r.get("metadata", {})
            print(f"  [Q] {r['content'][:50]} -> chunk {meta.get('source_chunk_id', '?')}")


if __name__ == "__main__":
    asyncio.run(main())
