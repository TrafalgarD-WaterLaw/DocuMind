# -*- coding: utf-8 -*-
"""扩展评测集生成器——从假设问题索引自动构建大规模评测对

原理：每条假设问题是 LLM 为某个 chunk 生成的"用户可能提问"，
天然构成 (查询 → 期望来源) 评测对：
  query = 假设问题文本
  expected_sources = [问题绑定的 source（source_chunk_id 反查）]
  expected_paths = ["question"]  # Q-to-Q 路径自检

抽样策略：按 source 分层均匀抽样（每 source 最多 1 条），
保证覆盖瓷器/青铜/河南全部来源类型。
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402

OUT = Path(__file__).parent / "dataset_large.json"
TARGET = 500
SEED = 42


def main():
    print("=== Generate Large Eval Dataset ===\n")

    # 1. 收集问题 → 绑定 source
    q_docs = container.questions.get_all_documents()
    by_source: dict[str, list[str]] = {}
    for d in q_docs:
        meta = d.get("metadata", {})
        src = meta.get("source", "")
        if src:
            by_source.setdefault(src, []).append(d.get("content", ""))

    print(f"问题总数: {len(q_docs)}，覆盖 {len(by_source)} 个来源")

    # 2. 分层抽样：每 source 最多 1 条，随机选 TARGET 个来源
    random.seed(SEED)
    sources = sorted(by_source.keys())
    chosen = random.sample(sources, min(TARGET, len(sources)))

    # 3. 构造评测对（同一来源内随机选一个问题）
    cases = []
    for i, src in enumerate(chosen):
        query = random.choice(by_source[src])
        # 标记来源类型（用于分组统计）
        kind = "porcelain" if src.startswith(("宣德", "洪武", "永乐", "元代")) and "河南" not in src else (
            "bronze" if src.startswith("青铜-") else "henan"
        )
        cases.append({
            "id": f"auto-{i:03d}",
            "query": query,
            "expected_sources": [src],
            "expected_paths": ["question"],
            "kind": kind,
            "note": "扩展集·Q-to-Q 自动生成",
        })

    data = {"_说明": "程序化生成的扩展评测集（gen_dataset.py）——Q-to-Q 链路自检", "cases": cases}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"生成 {len(cases)} 条 → {OUT}")

    # 4. 分组统计
    from collections import Counter
    kinds = Counter(c["kind"] for c in cases)
    print(f"分组: {dict(kinds)}")


if __name__ == "__main__":
    main()
