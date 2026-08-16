# -*- coding: utf-8 -*-
"""CLIP 文找图评测——text_search 中文视觉召回正确率（独立图片证据链的评测面）

数据: 人工标注的 (查询, 期望图源集合) —— 从 scripts/clip_text_probe.py
探测结果提炼（判定"视觉合理"的 top-5 命中）+ 3 条 probe 未测的泛化抽检。
期望集合允许 top-5 任一命中即该条通过（AnyHit@5）。

指标:
  AnyHit@5 : 期望集合在 top-5 出现的查询占比（主指标）
  平均命中数: 每条查询命中期望来源的个数（深度）
用法: python eval/clip_image_eval.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# Windows 控制台默认 GBK，器名含生僻字会崩——强制 UTF-8
sys.stdout.reconfigure(encoding="utf-8")

# (查询, 期望器名子串)——命中 source 含任一子串即通过。
# 子串而非精确匹配:source 带域前缀（河南博物院-…/青铜-…）且含别名
# （青铜-𫵒鼎（剸鼎）），精确相等会误判失败。
CASES: list[tuple[str, list[str]]] = [
    # 器物类别
    ("青铜鼎", ["重鼎", "杜岭二号方鼎", "史父丁鼎", "亞鼎", "朋伯鼎"]),
    ("青花瓷瓶", ["贯耳瓶", "玉壶春瓶", "天球瓶", "青花梅瓶"]),
    ("唐三彩", ["彩绘勾首马", "三彩马", "戴帷帽骑马女俑"]),
    # 材质 / 颜色
    ("白瓷", ["甜白釉僧帽壶", "甜白釉高足杯", "白釉碗"]),
    ("青釉瓷器", ["钧窑天兰釉瓷盘", "翠青釉罐", "汝窑天青釉盏托"]),
    # 纹饰
    ("兽面纹", ["青铜面饰", "兽面纹铜牌饰", "魚父乙鼎", "妯子鼎"]),
    ("兽面纹青铜鼎", ["仲義父鼎", "杜岭二号方鼎", "剸鼎", "圆涡四瓣目纹鼎",
                    "重鼎"]),
    # 抽象视觉语义（文本路图注未必覆盖——CLIP 的核心增量）
    ("外形像猫头鹰的青铜器", ["妇好鸮尊", "鸮壶"]),
    ("有威严感的青铜礼器", ["妇好鸮尊", "卧虎兽面纹方鼎", "牛首爵", "父乙角"]),
    ("玉龙", ["妇好墓玉龙", "玉韘形佩", "盘龙石砚"]),
    # 泛化抽检（probe 未测）
    ("玉韘形佩", ["玉韘形佩"]),
    ("汝窑天青釉", ["汝窑天青釉盏托"]),
    ("绿松石镶嵌", ["镶嵌绿松石兽面纹铜牌饰"]),
]

TOP_K = 5


async def main() -> None:
    from multimodal.clip_retrieval import clip_retriever

    col = clip_retriever._ensure()
    if col is None:
        print("!! ClipRetriever 加载失败，无法评测")
        return

    results: list[dict] = []
    t0 = time.time()
    for q, expected in CASES:
        hits = await clip_retriever.text_search(q, top_k=TOP_K)
        hit_sources = [h.get("source", "") for h in hits]
        # 子串匹配:任一命中 source 含期望器名即算命中
        matched = sorted({e for e in expected if any(e in s for s in hit_sources)})
        results.append({
            "query": q, "expected": expected,
            "hit": hit_sources, "matched": matched,
        })
        flag = "PASS" if matched else "FAIL"
        print(f"{flag}  {q}  命中 {matched[:3]} / 期望 {expected[:4]}")
        for h in hits[:3]:
            print(f"        {h['score']:.3f}  {h['source'][:30]}")

    total = len(CASES)
    passed = sum(1 for r in results if r["matched"])
    avg_hits = sum(len(r["matched"]) for r in results) / total
    print("\n" + "=" * 40)
    print(f"文找图 AnyHit@{TOP_K}: {passed}/{total} = {passed / total:.0%}")
    print(f"平均命中数: {avg_hits:.2f}（期望器名内每查询命中的条数）")
    print(f"耗时: {time.time() - t0:.1f}s")

    report = {
        "eval": "clip_text_search", "top_k": TOP_K,
        "anyhit_rate": passed / total, "avg_hits": avg_hits,
        "cases": results,
    }
    out = Path(__file__).resolve().parent / "reports" / f"clip_eval_{int(time.time())}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已保存: {out}")


if __name__ == "__main__":
    asyncio.run(main())
