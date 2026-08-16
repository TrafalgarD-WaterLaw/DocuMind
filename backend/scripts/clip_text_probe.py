# -*- coding: utf-8 -*-
"""CLIP 文找图质量探测——验证 text_search 中文召回是否靠谱（步骤①）

用法: python scripts/clip_text_probe.py [--topk N]
输出: 每组查询的 top-N 命中（source / image_path / score）,
      供人工判断"按视觉相似"召回是否可信,决定接入方案。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# Windows 控制台默认 GBK，青铜器名含生僻字（CJK Ext-B）会崩——强制 UTF-8 输出
sys.stdout.reconfigure(encoding="utf-8")

# 查询设计:覆盖 器物类别 / 具体器物 / 纹饰形态 / 材质颜色 / 抽象场景
QUERIES = [
    # 器物类别
    "青铜鼎",
    "青花瓷瓶",
    "玉龙",
    "唐三彩",
    # 具体器物
    "妇好鸮尊",
    "兽面纹青铜鼎",
    # 纹饰 / 形态
    "兽面纹",
    "饕餮纹",
    "莲瓣纹",
    # 材质 / 颜色
    "青釉瓷器",
    "白瓷",
    # 抽象 / 场景（视觉语义而非文字）
    "外形像猫头鹰的青铜器",
    "有威严感的青铜礼器",
]


async def main(top_k: int) -> None:
    from multimodal.clip_retrieval import clip_retriever

    # 触发懒加载,统计就绪状态
    col = clip_retriever._ensure()
    if col is None:
        print("!! ClipRetriever 加载失败（检查模型路径/索引）")
        return
    print(f"clip_images 就绪: {col.count()} 张图\n")

    for q in QUERIES:
        hits = await clip_retriever.text_search(q, top_k=top_k)
        print(f"=== {q} ===")
        if not hits:
            print("  (无命中)")
            continue
        for h in hits:
            score = h.get("score", 0)
            src = h.get("source", "")[:34]
            img = h.get("image_path", "")[-44:]
            print(f"  {score:.3f}  {src:<34}  ...{img}")
        print()


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topk", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.topk))
