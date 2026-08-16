# -*- coding: utf-8 -*-
"""问答评测运行器——量化检索准确性与引用正确性

用法:
  python eval/run_eval.py            # 跑全部（检索 + 引用）
  python eval/run_eval.py --retrieval-only   # 只跑检索（不消耗 LLM token）
  python eval/run_eval.py --chat-only        # 只跑真实问答（消耗 token）

输出: 控制台报告 + eval/reports/<时间戳>.json（历史对比用）
指标:
  - Recall@8: 期望来源是否在检索前 8 条
  - 引用越界率: [N] 超出来源数 / 总引用
  - 引用一致率: 引用句与来源全文关键词重叠
  - 多轮命中: 第二轮回溯到上轮实体
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

EVAL_DIR = Path(__file__).parent
DATASET = EVAL_DIR / "dataset.json"
REPORTS_DIR = EVAL_DIR / "reports"
API = "http://127.0.0.1:5172"

# 校验时忽略的常见虚词
STOP_WORDS = {
    "我们", "根据", "知识库", "以下", "主要", "以及", "进行", "具有",
    "体现", "相关", "可以", "属于", "采用", "包括", "此外", "这件",
    "上述", "这些", "整体", "特征", "特点", "方面", "以及", "还是",
    "什么", "如何", "请问", "瓷器", "文物", "青铜器", "有什么",
}


def load_dataset(path: Path | None = None) -> list[dict]:
    p = path or DATASET
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["cases"]


def retrieve(query: str) -> list[dict]:
    """调用混合检索器，返回前 8 条 [{source, paths}]"""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    import asyncio
    from core.di import container

    async def _run():
        results = await container.retriever.retrieve(query)
        return [
            {"source": r.get("source", ""), "paths": r.get("paths", [])}
            for r in results[:8]
        ]

    return asyncio.run(_run())


def chat_api(query: str, history: list[str]) -> tuple[str, list[dict]]:
    """调用 /api/chat，返回 (回答, sources)"""
    msgs = []
    for h in history:
        msgs.append({"role": "user", "content": h})
    payload = json.dumps({"query": query, "messages": msgs}).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
    )
    content, sources = [], []
    with urllib.request.urlopen(req, timeout=180) as resp:
        for line in resp:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev["type"] == "content":
                content.append(ev["data"])
            elif ev["type"] == "sources":
                sources = ev["data"]["items"]
    return "".join(content), sources


def full_texts_for(sources: list[dict]) -> dict[str, str]:
    """按 source 名取全文（校验引用真实性，避免 200 字截断误报）"""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from core.di import container

    all_docs = container.vector.get_all_documents()
    full = {}
    for s in sources:
        name = s.get("source") or ""
        full[name] = " ".join(
            d.get("content", "")
            for d in all_docs
            if d.get("metadata", {}).get("source") == name
        )
    return full


def check_citations(answer: str, sources: list[dict]) -> tuple[list, list]:
    """引用校验：越界 + 内容一致性。返回 (问题列表, 总引用数)"""
    issues = []
    cites = re.findall(r"\[(\d+)\]", answer)
    n = len(sources)
    if not cites:
        return ["回答无任何引用"], 0

    fulls = full_texts_for(sources)
    for c in cites:
        idx = int(c)
        if idx < 1 or idx > n:
            issues.append(f"越界引用 [{idx}]（来源共 {n} 条）")
            continue
        src = sources[idx - 1]
        src_name = src.get("source") or ""
        src_full = fulls.get(src_name, "")
        # 引用编号所在句
        pos = answer.rfind(f"[{c}]")
        if pos == -1:
            continue
        sent_start = max(
            answer.rfind("。", 0, pos), answer.rfind("；", 0, pos),
            answer.rfind("\n", 0, pos),
        ) + 1
        sent = answer[sent_start:pos + len(c) + 2]
        # 跳过结构化"依据: [N]"行（结论索引，非事实句）
        if "依据" in sent and len(sent) < 20:
            continue
        words = [w for w in re.findall(r"[一-鿿]{2,4}", sent) if w not in STOP_WORDS]
        overlap = [w for w in words if w in src_full]
        if not overlap and words:
            issues.append(
                f"引用可疑 [{idx}] 句「{sent[:35]}」与「{src_name[:20]}」全文无重叠（词: {words[:4]}）"
            )
    return issues, len(cites)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--chat-only", action="store_true")
    parser.add_argument("--dataset", default=None, help="评测集路径（默认 dataset.json）")
    parser.add_argument("--group", action="store_true", help="按 kind 分组输出统计")
    args = parser.parse_args()

    cases = load_dataset(Path(args.dataset) if args.dataset else None)
    report = {"timestamp": int(time.time()), "cases": []}
    group_stats: dict[str, dict] = {}
    n_recall_hit = n_recall_total = 0
    mrr_sum = 0.0
    n_path_ok = n_path_total = 0
    n_cite_ok = n_cite_total = 0

    print("=" * 72)
    print("问答评测：Recall@8 + MRR + 路径覆盖 + 引用正确性")
    print("=" * 72)

    for case in cases:
        q = case["query"]
        expected = case["expected_sources"]
        expected_paths = case.get("expected_paths", [])
        history = case.get("history", [])
        row = {"id": case["id"], "query": q, "recall": False, "mrr": 0.0, "path_coverage": [], "citation_issues": [], "cite_count": 0}

        # ── 1. 检索评测 ──
        if not args.chat_only:
            results = retrieve(f"{history[0]} {q}" if history else q)
            src_list = [r["source"] for r in results]
            hit = [e for e in expected if e in src_list]
            # 列举类查询（哪些/有什么/列举）：期望来源是"可接受代表"集合，
            # 检索命中同类正确来源即证明能力——任一命中即可（BEIR 风格）；
            # 事实类查询要求全部期望来源都在 top-8。
            # 空期望（图谱直查类用例）跳过判定。
            is_list_q = any(w in q for w in ("哪些", "有什么", "列举", "几种", "几个"))
            if not expected:
                ok = True
            elif is_list_q:
                ok = len(hit) > 0
            else:
                ok = len(hit) == len(expected)
            row["retrieved"] = src_list[:4]
            row["recall"] = ok
            n_recall_hit += int(ok)
            n_recall_total += 1

            # MRR：第一个期望来源的排名倒数
            for rank, r in enumerate(results, start=1):
                if r["source"] in expected:
                    row["mrr"] = round(1.0 / rank, 3)
                    mrr_sum += row["mrr"]
                    break

            # 路径覆盖：期望路径是否出现在结果 paths 中
            all_paths = {p for r in results for p in r["paths"]}
            covered = [p for p in expected_paths if p in all_paths]
            row["path_coverage"] = covered
            if expected_paths:
                n_path_total += 1
                if len(covered) == len(expected_paths):
                    n_path_ok += 1

            flag = "✅" if ok else "❌"
            if len(cases) <= 30:
                print(f"\n{flag} [{case['id']}] {q}")
                print(f"   期望来源: {expected}")
                print(f"   Recall: {len(hit)}/{len(expected)} | MRR: {row['mrr']}")
                if expected_paths:
                    print(f"   路径覆盖: {covered} / {expected_paths}")
                if not ok:
                    print(f"   实际: {src_list[:4]}")

            # 分组统计
            if args.group:
                kind = case.get("kind", "other")
                g = group_stats.setdefault(kind, {"total": 0, "hit": 0, "mrr": 0.0})
                g["total"] += 1
                g["hit"] += int(ok)
                g["mrr"] += row["mrr"]

        # ── 2. 引用评测（真实问答，消耗 token）──
        if not args.retrieval_only:
            try:
                answer, sources = chat_api(q, history)
                issues, cite_count = check_citations(answer, sources)
                row["cite_count"] = cite_count
                row["citation_issues"] = issues
                n_cite_total += 1
                if not issues:
                    n_cite_ok += 1
                if args.retrieval_only is False and not args.chat_only:
                    print(f"   引用: {cite_count} 处 → {'✅' if not issues else '⚠️ ' + issues[0][:60]}")
            except Exception as e:
                row["citation_issues"] = [f"API 调用失败: {e}"]
                print(f"   ❌ 引用校验失败: {e}")

        report["cases"].append(row)

    # ── 汇总 ──
    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    summary = {}
    if args.group and group_stats:
        print("\n分组统计（按数据源类型）:")
        for kind, g in sorted(group_stats.items()):
            rate = g["hit"] / g["total"]
            mrr = round(g["mrr"] / g["total"], 3)
            summary[f"group_{kind}"] = {"recall": round(rate, 3), "mrr": mrr}
            print(f"  {kind}: Recall {g['hit']}/{g['total']} = {rate:.0%} | MRR {mrr}")
    if n_recall_total:
        recall_rate = n_recall_hit / n_recall_total
        summary["recall_rate"] = round(recall_rate, 3)
        print(f"检索 Recall@8: {n_recall_hit}/{n_recall_total} = {recall_rate:.0%}")
    if n_recall_total:
        mrr = round(mrr_sum / n_recall_total, 3)
        summary["mrr"] = mrr
        print(f"MRR: {mrr}")
    if n_path_total:
        path_rate = n_path_ok / n_path_total
        summary["path_coverage_rate"] = round(path_rate, 3)
        print(f"路径覆盖: {n_path_ok}/{n_path_total} = {path_rate:.0%}")
    if n_cite_total:
        cite_rate = n_cite_ok / n_cite_total
        summary["citation_pass_rate"] = round(cite_rate, 3)
        total_cites = sum(r["cite_count"] for r in report["cases"])
        total_issues = sum(len(r["citation_issues"]) for r in report["cases"])
        summary["total_citations"] = total_cites
        summary["citation_issues"] = total_issues
        print(f"引用校验通过: {n_cite_ok}/{n_cite_total} = {cite_rate:.0%}（共 {total_cites} 处引用，{total_issues} 处问题）")
        for r in report["cases"]:
            for i in r["citation_issues"]:
                print(f"  ⚠️ [{r['id']}] {i}")

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"eval_{report['timestamp']}.json"
    out.write_text(json.dumps({"summary": summary, "report": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已保存: {out}")


if __name__ == "__main__":
    main()
