# -*- coding: utf-8 -*-
"""漏召回归因诊断——对扩展集漏召回用例做三维分析

对每个漏召回用例检查：
  1. Q-to-Q 排名：目标问题在 16152 条问题索引中的真实检索排名
     （> 8 → question 路召回不足；≤ 8 但映射后没进 top-8 → 融合/多样性问题）
  2. 各路原始召回：期望 source 是否出现在 semantic/bm25 的原始召回里
  3. 目标 chunk 是否在 documents 集合中（数据完整性）

用法: python eval/diag_recall.py [评测报告路径]
输出: 每个漏召回用例的归因 + 汇总分类
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402
from interfaces.vector_store import VectorStore  # noqa: E402


def main():
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report_dir = Path(__file__).parent / "reports"
    if report_path is None:
        reports = sorted(report_dir.glob("eval_*.json"), key=lambda p: p.stat().st_mtime)
        report_path = str(reports[-1])
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    dataset = json.loads(
        (Path(__file__).parent / "dataset_large.json").read_text(encoding="utf-8")
    )
    cases = {c["id"]: c for c in dataset["cases"]}

    q_store: VectorStore = container.questions
    doc_store: VectorStore = container.vector

    failed = [r for r in report["report"]["cases"] if not r.get("recall")]
    print(f"漏召回用例: {len(failed)} 条\n")

    summary = {"q_rank_gt8": 0, "q_rank_le8": 0, "q_missing": 0, "doc_missing": 0, "semantic_hit": 0}
    for row in failed:
        cid = row["id"]
        case = cases.get(cid, {})
        query = case.get("query", "")
        expected = case.get("expected_sources", [])
        exp = expected[0] if expected else "?"

        # 1. 目标问题在 question 索引中的排名（top-30）
        q_results = q_store.retrieve(query, top_k=30)
        q_rank, q_hit_source = None, None
        for i, r in enumerate(q_results):
            meta = r.get("metadata", {})
            if meta.get("source") == exp:
                q_rank, q_hit_source = i + 1, meta.get("source_chunk_id", "?")
                break

        # 2. 期望 source 是否在 semantic 原始召回中（L: 变量/文案此前错标
        # 为 bm25——doc_store.retrieve 是语义路，BM25 命中从未被测量）
        sem = {d.get("metadata", {}).get("source") for d in doc_store.retrieve(query, top_k=15)}
        semantic_hit = exp in sem

        # 3. 期望 chunk 数据完整性（问题绑定是否可反查）
        doc_ok = False
        if q_hit_source:
            docs = doc_store.get_by_ids([q_hit_source])
            doc_ok = bool(docs)

        # 归因分类
        if q_rank is None:
            reason = "Q问题未命中（top-30 无该 source）"
            summary["q_missing"] += 1
        elif q_rank > 8:
            reason = f"Q-to-Q 排名 {q_rank}（>8，question 路召回不足）"
            summary["q_rank_gt8"] += 1
        else:
            reason = f"Q-to-Q 排名 {q_rank}（≤8 但未进最终 top-8，融合/多样性问题）"
            summary["q_rank_le8"] += 1
        if not doc_ok:
            reason += "；⚠️ 问题绑定 chunk 反查失败"
            summary["doc_missing"] += 1
        if semantic_hit:
            reason += "；语义路命中但被融合挤出"
            summary["semantic_hit"] += 1

        print(f"[{cid}] kind={case.get('kind', '?')} 期望={exp[:24]}")
        print(f"   查询: {query[:46]}")
        print(f"   {reason}")

    print("\n=== 汇总 ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
