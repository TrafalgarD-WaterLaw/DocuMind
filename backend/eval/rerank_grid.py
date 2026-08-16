# -*- coding: utf-8 -*-
"""R1 rerank 网格评测——单进程跑完 rerank on/off × 候选池配置网格

背景（蓝图第 3 步）: bge-reranker-v2-m3 已装但禁用——历史结论「无增益」的根因
是候选池太小（top-8 内微排序）。本脚本直接构造 HybridRetriever（绕过 settings
单例），对每种配置独立评测 Recall@8 / MRR / 路径覆盖 / 平均耗时，数据驱动决策。

用法:
  python eval/rerank_grid.py                # 核心集全网格（决策用）
  python eval/rerank_grid.py --dataset large --grid off,on-p32-c128   # 扩展集对比格

输出: 控制台对比表 + eval/reports/rerank_grid_*.json
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402
from retrieval.rerank import RerankerProvider  # noqa: E402
from retrieval.bm25 import BM25Index  # noqa: E402
from retrieval.hybrid import HybridRetriever  # noqa: E402

EVAL_DIR = Path(__file__).parent
REPORTS_DIR = EVAL_DIR / "reports"

# 网格定义: 名称 → 构造参数（path_k 为每路召回量；candidates 为 RRF 后候选池）
GRID = {
    # rerank 关闭 = 现状基线（candidate_pool 64）
    "off": {"path_k": 8, "candidate_pool": 64},
    # rerank 开启 × 候选供给（path_k）× 候选池（candidates）
    "on-p16-c32": {"path_k": 16, "rerank_candidates": 32},
    "on-p16-c64": {"path_k": 16, "rerank_candidates": 64},
    "on-p32-c64": {"path_k": 32, "rerank_candidates": 64},
    "on-p32-c128": {"path_k": 32, "rerank_candidates": 128},
    "on-p64-c128": {"path_k": 64, "rerank_candidates": 128},
}

LIST_WORDS = ("哪些", "有什么", "列举", "几种", "几个")


def load_cases(name: str) -> list[dict]:
    path = EVAL_DIR / ("dataset_large.json" if name == "large" else "dataset.json")
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def evaluate(retriever: HybridRetriever, cases: list[dict]) -> dict:
    """对一组用例跑检索评测（Recall@8 / MRR / 路径覆盖 / 平均耗时）"""
    n_hit = n_total = 0
    mrr_sum = 0.0
    n_path_ok = n_path_total = 0
    q_times: list[float] = []

    async def _retrieve(q: str) -> list[dict]:
        return await retriever.retrieve(q)

    for case in cases:
        q = case["query"]
        history = case.get("history", [])
        query = f"{history[0]} {q}" if history else q
        expected = case["expected_sources"]
        expected_paths = case.get("expected_paths", [])

        t0 = time.perf_counter()
        results = asyncio.run(_retrieve(query))
        q_times.append(time.perf_counter() - t0)

        src_list = [r["source"] for r in results]
        hit = [e for e in expected if e in src_list]
        # 列举类任一命中即可（BEIR 风格）；事实类要求全部在 top-8；空期望跳过
        if not expected:
            ok = True
        elif any(w in q for w in LIST_WORDS):
            ok = len(hit) > 0
        else:
            ok = len(hit) == len(expected)
        n_hit += int(ok)
        n_total += 1

        for rank, r in enumerate(results, start=1):
            if r["source"] in expected:
                mrr_sum += 1.0 / rank
                break

        if expected_paths:
            n_path_total += 1
            all_paths = {p for r in results for p in r.get("paths", [])}
            if all(p in all_paths for p in expected_paths):
                n_path_ok += 1

    return {
        "recall": round(n_hit / n_total, 4) if n_total else 1.0,
        "mrr": round(mrr_sum / n_total, 4) if n_total else 0.0,
        "path_coverage": round(n_path_ok / n_path_total, 4) if n_path_total else 1.0,
        "avg_ms": round(sum(q_times) / len(q_times) * 1000) if q_times else 0,
        "n": n_total,
    }


def build_retriever(name: str, reranker) -> HybridRetriever:
    """按格子参数构造 HybridRetriever（共享 store/bm25，绕开 settings 单例）"""
    params = dict(GRID[name])
    on = name.startswith("on")
    bm25 = BM25Index()
    bm25.build(container.vector.get_all_documents())
    # graph 传 None：网格只测 rerank 变量（on/off 两侧一致地缺 graph 路，
    # 差异纯归因于 rerank；生产 graph 路已确认工作——内存压力下网格进程
    # 的 3s 连接超时会误标不可用，隔离之）。顺带省掉 LLM 提实体调用。
    retriever = HybridRetriever(
        doc_store=container.vector,
        question_store=container.questions,
        bm25=bm25,
        graph=None,
        llm=container.llm,
        reranker=reranker if on else None,
        path_k=params["path_k"],
        top_k=8,
    )
    # 候选池参数从 settings 读取，非构造参数——构造后直接覆盖（网格变量）
    if on:
        retriever.rerank_candidates = params["rerank_candidates"]
    else:
        retriever.candidate_pool = params["candidate_pool"]
    return retriever


def main():
    parser = argparse.ArgumentParser(description="rerank 网格评测")
    parser.add_argument("--dataset", default="core", choices=["core", "large"])
    parser.add_argument("--grid", default=None,
                        help="格子名逗号分隔（默认全部；off,on-p32-c128）")
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    names = list(GRID) if not args.grid else [g.strip() for g in args.grid.split(",")]
    if any(n not in GRID for n in names):
        sys.exit(f"未知格子: {[n for n in names if n not in GRID]}，可用: {list(GRID)}")

    print(f"=== rerank 网格评测（{args.dataset} 集，{len(cases)} 题）===", flush=True)
    print("reranker 模型加载中（首次）…", flush=True)
    reranker = RerankerProvider()

    results = {}
    for name in names:
        t0 = time.perf_counter()
        print(f"  格子 {name} 评测中…", flush=True)
        retriever = build_retriever(name, reranker)
        stats = evaluate(retriever, cases)
        results[name] = stats
        print(f"  {name:<12} Recall {stats['recall']:.1%} | MRR {stats['mrr']:.4f} | "
              f"路径 {stats['path_coverage']:.1%} | 平均 {stats['avg_ms']}ms | "
              f"{(time.perf_counter() - t0):.0f}s", flush=True)

    print("\n对比（按 MRR 排序）:")
    for name, s in sorted(results.items(), key=lambda kv: -kv[1]["mrr"]):
        print(f"  {name:<12} Recall {s['recall']:.1%} | MRR {s['mrr']:.4f} | "
              f"路径 {s['path_coverage']:.1%} | {s['avg_ms']}ms")

    out = REPORTS_DIR / f"rerank_grid_{args.dataset}_{int(time.time())}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(
        {"dataset": args.dataset, "results": results}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
