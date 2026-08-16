# -*- coding: utf-8 -*-
"""CLIP 图文双塔直检端到端演示——文字问题 → 视觉命中 → 图注上下文

用法: python scripts/clip_demo.py [查询词]（缺省用"外形像猫头鹰的青铜器"）
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# Windows 控制台 GBK——器名含生僻字会崩,强制 UTF-8
sys.stdout.reconfigure(encoding="utf-8")

QUERY = sys.argv[1] if len(sys.argv) > 1 else "外形像猫头鹰的青铜器"


async def main() -> None:
    from core.config import settings
    from multimodal.clip_retrieval import clip_retriever

    print(f"查询: 「{QUERY}」\n")

    col = clip_retriever._ensure()
    if col is None:
        print("!! ClipRetriever 不可用")
        return

    # ── ① 文本塔: query → CLIP 文本编码 → 与图像向量余弦(图文同空间) ──
    hits = await clip_retriever.text_search(QUERY, top_k=3)
    print("① 文本塔编码 → 与 %d 张图像向量余弦(同空间),命中:" % col.count())
    for h in hits:
        print(f"   {h['score']:.3f}  {h['source']}")
        print(f"        {h['image_path']}")

    # ── ② 视觉命中 → 图注块取回(documents 里的 #图 块, P1-C 契约) ──
    import chromadb

    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    doc_col = client.get_collection("documents")
    keys: list[str] = []
    for h in hits:
        src = h.get("source", "")
        keys.append(src)
        if not src.endswith("#图"):
            keys.append(f"{src}#图")
    # chunk_type=image:只取图注块——文本块归五路检索,不得混入图片证据
    # （Chroma where 顶层只能单 operator,AND 用显式 $and）
    res = doc_col.get(
        where={"$and": [{"source": {"$in": keys}}, {"chunk_type": "image"}]},
        limit=6,
    )
    print("\n② 命中 source → 取回图注块(documents collection):")
    if not res["ids"]:
        print("   (无图注块——索引中无对应图片块)")
    for i, cid in enumerate(res["ids"]):
        meta = (res["metadatas"] or [{}] * len(res["ids"]))[i] or {}
        print(f"   [{cid[:20]}] {res['documents'][i][:80]}")
        print(f"        source={meta.get('source')}")

    # ── ③ 最终形态: 图注块续编号追加进回答上下文 ──
    print("\n③ 最终上下文(独立图片证据链,续接文本证据编号):")
    for i, cid in enumerate(res["ids"]):
        print(f"   [{i + 1}] {res['documents'][i][:80]}")
    print("   → LLM 回答「像猫头鹰的青铜器有哪些」时,可引用图注编号,"
          "并随 SOURCES 事件带出图片 URL 供前端展示")


if __name__ == "__main__":
    asyncio.run(main())
