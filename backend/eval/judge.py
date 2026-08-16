# -*- coding: utf-8 -*-
"""LLM-as-judge 答案质量评估——GT 事实包含率 + faithfulness（忠实度）

两种模式（--faithfulness 切换）：
  [默认]  GT 事实包含率：裁判检查 GT 事实是否被回答包含（答案-标准事实）
  [--faithfulness] 忠实度：裁判逐句判回答的每个事实断言是否被检索上下文支持
    （答案-检索上下文，幻觉度量——噪声过滤/上下文组装的改造前后对比依据）
"""
import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402
from prompts import render_system, render_user  # noqa: E402

API = "http://127.0.0.1:5172"


def chat_api(query: str, history: list[str], collect_context: bool = False):
    """调用 /api/chat；collect_context=True 时额外返回检索上下文（sources 事件）

    Returns:
        默认: 答案文本 (str)
        collect_context=True: (答案, 上下文文本列表)
    """
    msgs = [{"role": "user", "content": h} for h in history]
    payload = json.dumps({"query": query, "messages": msgs}).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
    )
    content = []
    context = []
    with urllib.request.urlopen(req, timeout=180) as resp:
        for line in resp:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev["type"] == "content":
                content.append(ev["data"])
            elif collect_context and ev["type"] == "sources":
                context.extend(
                    it.get("content", "") for it in ev["data"].get("items", [])
                )
    answer = "".join(content)
    if collect_context:
        return answer, [c for c in context if c]
    return answer


async def judge_facts(question: str, gt_facts: list[str], answer: str) -> dict:
    """裁判 LLM 判定 GT 事实包含率"""
    gt_text = "\n".join(f"- {f}" for f in gt_facts)
    messages = container.llm.build_messages(
        render_system("eval_judge"),
        render_user("eval_judge", question=question, gt_facts=gt_text, answer=answer),
    )
    raw = await container.llm.chat(messages, temperature=0.0, max_tokens=1024)
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        results = data.get("results", [])
        covered = sum(1 for r in results if r.get("covered"))
        return {"covered": covered, "total": len(results), "raw": results}
    except Exception:
        return {"covered": 0, "total": len(gt_facts), "raw": [], "parse_error": raw[:100]}


async def judge_faithfulness(question: str, answer: str, context: list[str]) -> dict:
    """裁判 LLM 逐句判忠实度——回答事实断言是否被检索上下文支持"""
    ctx_text = "\n".join(f"- {c}" for c in context) or "（无检索上下文）"
    messages = container.llm.build_messages(
        render_system("eval_faithfulness"),
        render_user("eval_faithfulness", question=question, context=ctx_text, answer=answer),
    )
    raw = await container.llm.chat(messages, temperature=0.0, max_tokens=2048)
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        facts = data.get("facts", [])
        supported = sum(1 for f in facts if f.get("supported"))
        return {"supported": supported, "total": len(facts), "raw": facts}
    except Exception:
        return {"supported": 0, "total": 0, "raw": [], "parse_error": raw[:100]}


async def main_gt():
    """GT 模式：裁判检查 GT 事实是否被回答包含"""
    dataset = json.loads(Path("eval/dataset.json").read_text(encoding="utf-8"))
    cases = [c for c in dataset["cases"] if c.get("gt_facts")]

    print("=" * 72)
    print("答案质量评估（LLM-as-judge）：GT 事实包含率")
    print("=" * 72)

    total_covered = total_facts = 0
    report_rows = []

    for i, case in enumerate(cases):
        q = case["query"]
        facts = case["gt_facts"]
        history = case.get("history", [])

        answer = chat_api(q, history)
        verdict = await judge_facts(q, facts, answer)

        covered, total = verdict["covered"], verdict["total"]
        total_covered += covered
        total_facts += total
        rate = covered / total if total else 1.0
        report_rows.append({"id": case["id"], "covered": covered, "total": total, "rate": round(rate, 2)})

        flag = "✅" if rate >= 0.6 else "⚠️" if rate >= 0.33 else "❌"
        print(f"\n{flag} [{case['id']}] {q}")
        print(f"   GT 包含: {covered}/{total} = {rate:.0%}")
        if verdict.get("parse_error"):
            print(f"   裁判解析失败: {verdict['parse_error']}")
        elif rate < 1.0:
            for r in verdict["raw"]:
                if not r.get("covered"):
                    print(f"   未覆盖: {facts[r['fact']] if r['fact'] < len(facts) else '?'} — {r.get('reason', '')[:40]}")
        await asyncio.sleep(0.3)

    overall = total_covered / total_facts if total_facts else 0
    print("\n" + "=" * 72)
    print(f"GT 事实总包含率: {total_covered}/{total_facts} = {overall:.0%}")
    out = Path("eval/reports") / f"judge_{int(time.time())}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"overall": round(overall, 3), "cases": report_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")


async def main_faithfulness():
    """faithfulness 模式：回答事实断言是否被检索上下文支持（幻觉度量）"""
    dataset = json.loads(Path("eval/dataset.json").read_text(encoding="utf-8"))
    cases = dataset["cases"]

    print("=" * 72)
    print("忠实度评估（LLM-as-judge）：回答事实被检索上下文支持率")
    print("=" * 72)

    total_supported = total_facts = 0
    report_rows = []

    for i, case in enumerate(cases):
        q = case["query"]
        history = case.get("history", [])

        answer, context = chat_api(q, history, collect_context=True)
        verdict = await judge_faithfulness(q, answer, context)

        supported, total = verdict["supported"], verdict["total"]
        total_supported += supported
        total_facts += total
        rate = supported / total if total else 1.0
        report_rows.append({
            "id": case["id"], "supported": supported, "total": total,
            "rate": round(rate, 2), "context_chunks": len(context),
        })

        flag = "✅" if rate >= 0.8 else "⚠️" if rate >= 0.6 else "❌"
        print(f"\n{flag} [{case['id']}] {q}")
        print(f"   faithful: {supported}/{total} = {rate:.0%}（上下文 {len(context)} 块）")
        if verdict.get("parse_error"):
            print(f"   裁判解析失败: {verdict['parse_error']}")
        elif rate < 1.0:
            for r in verdict["raw"]:
                if not r.get("supported"):
                    print(f"   无支撑: {r.get('sentence', '')[:60]} — {r.get('reason', '')[:40]}")
        await asyncio.sleep(0.3)

    overall = total_supported / total_facts if total_facts else 0
    print("\n" + "=" * 72)
    print(f"faithfulness 总支持率: {total_supported}/{total_facts} = {overall:.0%}")
    out = Path("eval/reports") / f"judge_faithfulness_{int(time.time())}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"mode": "faithfulness", "overall": round(overall, 3), "cases": report_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")


async def main():
    parser = argparse.ArgumentParser(description="LLM-as-judge 答案质量评估")
    parser.add_argument("--faithfulness", action="store_true",
                        help="faithfulness 模式（回答事实 vs 检索上下文）")
    args = parser.parse_args()
    if args.faithfulness:
        await main_faithfulness()
    else:
        await main_gt()


if __name__ == "__main__":
    asyncio.run(main())
