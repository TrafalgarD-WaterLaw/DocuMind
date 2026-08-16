# -*- coding: utf-8 -*-
"""图+文联合检索评测——vision 链路的混合模态评测面（🔴 欠缺第 5.1 条）

口径（与文本 eval 的 --retrieval-only 一致，零 LLM 消耗）:
  真实复用 conversation.interfaces.vision_routes 的 _recognize（CLIP 图找图识别）+ _prepare_vision_query
  （top3 识别名多查询），再对每个检索词调 container.retriever.retrieve，
  按 (id, source) 去重合并取前 8——与 QuickAnswerService._retrieve_and_merge
  同款口径，不经过 CRAG/生成（LLM 段不在此评测面）。
  不含 clip 图片证据归并——自图 image_search 恒命中自身（余弦≈1.0），
  进证据链无区分度；文找图质量已由 clip_image_eval.py 覆盖。

GT 来源: image_index.json 映射表（图 → 器物 source），零人工标注。
匹配: 检索 source 精确等于 GT 或以 GT#图 结尾（图片块 source 带 #图 后缀）。

指标:
  AnyHit@8   : GT 来源出现在合并 top-8 的用例占比（主指标）
  识别正确率  : top1 识别名含 GT 尾名的用例占比（CLIP 图找图在真实图片上的表现）
  兜底生效数  : top1 识别名不含 GT 但 GT 仍进 top-8 的用例数（top3 候选机制价值）

用法: python eval/vision_eval.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# Windows 控制台默认 GBK，器名含生僻字会崩——强制 UTF-8
sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "data" / "images"

# (映射表 key, 用户问题, GT source 名列表)
# GT 多值用于同形双胞胎（釉里红玉壶春瓶 元代/洪武 外观几乎无差，
# 识别到任一都算合理命中——已在注释标注）。
CASES: list[tuple[str, str, list[str]]] = [
    # ── 河南博物院（7）──
    ("河南博物院-妇好鸮尊", "这是什么器物", ["河南博物院-妇好鸮尊"]),
    ("河南博物院-杜岭二号方鼎", "这件青铜器的纹饰有什么特点", ["河南博物院-杜岭二号方鼎"]),
    ("河南博物院-镶嵌绿松石兽面纹铜牌饰", "这是什么", ["河南博物院-镶嵌绿松石兽面纹铜牌饰"]),
    ("河南博物院-妇好墓玉龙", "这件玉器是什么", ["河南博物院-妇好墓玉龙"]),
    ("河南博物院-汝窑天青釉盏托", "这是哪个窑口的瓷器", ["河南博物院-汝窑天青釉盏托"]),
    ("河南博物院-云纹铜禁", "这是什么文物", ["河南博物院-云纹铜禁"]),
    ("河南博物院-窦泰墓志", "这是什么", ["河南博物院-窦泰墓志"]),
    # ── 瓷器（5）──
    ("永乐-甜白釉僧帽壶", "这个壶是什么釉色", ["永乐-甜白釉僧帽壶"]),
    ("宣德-青花十棱洗", "这是什么瓷器", ["宣德-青花十棱洗"]),
    # 双胞胎:元代/洪武 釉里红玉壶春瓶同形,任一命中即通过
    ("元代-釉里红玉壶春瓶", "这是什么瓶", ["元代-釉里红玉壶春瓶", "洪武-釉里红玉壶春瓶"]),
    ("宣德-青花梅瓶", "这个梅瓶是哪个朝代的", ["宣德-青花梅瓶"]),
    ("宣德-天球瓶", "这是什么瓶", ["宣德-天球瓶"]),
    # ── 青铜（3,每源仅 1 块——命中更难,真实难度）──
    ("青铜-兽面纹鼎", "这件鼎的纹饰是什么", ["青铜-兽面纹鼎"]),
    ("青铜-蕉叶纹鼎", "这是什么鼎", ["青铜-蕉叶纹鼎"]),
    ("青铜-蟠虺纹鼎", "这个青铜器是什么", ["青铜-蟠虺纹鼎"]),
]

TOP_K = 8


def gt_hit(source: str, gt_list: list[str]) -> bool:
    """source 命中任一 GT:精确相等或 GT#图 结尾（图片块 source 带 #图）"""
    return any(source == g or source.endswith(f"{g}#图") for g in gt_list)


def tail(name: str) -> str:
    """source 尾名（去域前缀）——识别名已被 _clean 剥过域,同口径比对"""
    return name.split("-", 1)[-1].strip()


async def run_case(img_path: Path, query: str, gt_list: list[str]) -> dict:
    """单用例:识别 → 多查询检索 → 合并 top-8 → GT 判定"""
    from conversation.interfaces.vision_routes import _prepare_vision_query, _recognize
    from core.di import container

    if not img_path.exists():
        return {
            "query": query, "gt": gt_list, "error": f"图片缺失: {img_path.name}",
            "recognition": "", "confidence": 0.0, "top8": [], "hit": False,
            "recog_correct": False, "fallback": False,
        }

    with Image.open(img_path) as im:
        image = im.convert("RGB").copy()

    # 1) CLIP 图找图识别（真实链路函数）
    result, confidence, _low_conf, clip_hits = await _recognize(image)

    # 2) top3 识别名多查询计划（真实链路函数）
    plan = _prepare_vision_query(query, confidence, False, clip_hits)

    # 3) 逐查询检索 + 去重合并（与 _retrieve_and_merge 同口径:
    #    id 优先、(source, content[:50]) 兜底指纹,查询顺序保留）
    seen: set = set()
    merged: list[str] = []
    for q in plan.retrieval_queries:
        docs = await container.retriever.retrieve(q)
        for d in docs:
            key = d.get("id") or (d.get("source", ""), d.get("content", "")[:50])
            if key in seen:
                continue
            seen.add(key)
            merged.append(d.get("source", ""))
    top8 = merged[:TOP_K]

    recog_correct = any(tail(g) in result or result in tail(g) for g in gt_list) if result else False
    hit = any(gt_hit(s, gt_list) for s in top8)
    return {
        "query": query, "gt": gt_list, "error": "",
        "recognition": result, "confidence": confidence,
        "queries": plan.retrieval_queries, "top8": top8,
        "hit": hit, "recog_correct": recog_correct,
        # 兜底生效:top1 识别名不含 GT 但检索仍捞回（top3 候选机制的价值）
        "fallback": hit and not recog_correct,
    }


async def main() -> None:
    index = json.loads(
        (DATA_DIR.parent / "image_index.json").read_text(encoding="utf-8")
    )

    results: list[dict] = []
    t0 = time.time()
    for key, query, gt_list in CASES:
        entry = index.get(key)
        if not entry:
            print(f"!! 映射表无 {key}，跳过")
            continue
        # 取第 2 张某器物照片（非 primary）：避免与索引内自图逐像素同源，
        # 测的是跨照片识别 + 检索链，而非恒 1.0 的自我匹配。
        # 不取末张——实测河南爬虫目录末张存在跨器物字节级重复图
        # （妇好鸮尊/27.jpg == 青铜神兽/20.jpg，MD5 相同），重复图查询
        # 会与多个无关器物并列距离 0，属数据质量噪声而非识别能力
        imgs = entry.get("images") or [entry["primary"]]
        rel = imgs[1] if len(imgs) > 1 else imgs[0]
        r = await run_case(DATA_DIR / rel, query, gt_list)
        results.append(r)

        flag = "FAIL" if r["error"] else ("PASS" if r["hit"] else "MISS")
        extra = f"（兜底生效）" if r["fallback"] else ""
        print(f"{flag}  {key}  [{query}]  识别={r['recognition'] or '-'} "
              f"conf={r['confidence']:.2f}{extra}")
        if r["error"]:
            print(f"       {r['error']}")
        elif not r["hit"]:
            print(f"       top8: {[s[:24] for s in r['top8']]}")

    total = len(results)
    hit = sum(1 for r in results if r["hit"])
    recog_ok = sum(1 for r in results if r["recog_correct"])
    fallback = sum(1 for r in results if r["fallback"])
    print("\n" + "=" * 50)
    print(f"混合模态 AnyHit@{TOP_K}: {hit}/{total} = {hit / total:.0%}")
    print(f"识别正确率（top1 名含 GT）: {recog_ok}/{total} = {recog_ok / total:.0%}")
    print(f"兜底生效（识别错但检索捞回）: {fallback} 例")
    print(f"耗时: {time.time() - t0:.1f}s")

    report = {
        "eval": "vision_mixed", "top_k": TOP_K,
        "anyhit_rate": hit / total, "recog_rate": recog_ok / total,
        "fallback_count": fallback, "cases": results,
    }
    out = Path(__file__).resolve().parent / "reports" / f"vision_eval_{int(time.time())}.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"报告已保存: {out}")


if __name__ == "__main__":
    asyncio.run(main())
