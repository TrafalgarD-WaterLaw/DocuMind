# -*- coding: utf-8 -*-
"""河南图注级图片块批量补 VLM 描述（94% 图片块只有文件名占位）

背景: henan_images.json 爬取时大部分图注缺失（282 source 仅 144 个含
非空 caption）→ documents 里图片块 content 是「【图片】文件名」占位，
LLM 拿不到图内信息。本脚本对缺失图注的图片批量跑 VLM 描述,
重写 content 为「【图片·图N】描述」（与有图注块的格式一致）。

断点重续（免费额度友好）:
  - 进度 = append-only 行文件（source<TAB>file），每完成一张立即追加
    → 任何时刻中断（Ctrl+C / 崩 / 断网），已完成的不重跑
  - 失败图不记录 → 重跑自动重试；换模型后重跑，跳过已完成、只补失败的
  - 跳过 henan_images.json 已有非空 caption 的图（保留爬取图注）

用法:
  python scripts/backfill_henan_captions.py --dry-run   # 统计待补,不调 API
  python scripts/backfill_henan_captions.py --limit 10  # 试跑 10 张（验证模型）
  python scripts/backfill_henan_captions.py             # 全量
依赖: .env 配置 DASHSCOPE_API_KEY 与 IMAGE_CAPTION_MODEL（免费额度用完
可在 .env 换模型名后直接重跑）。
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.stdout.reconfigure(encoding="utf-8")

PROGRESS_PATH = Path(__file__).resolve().parent / "henan_caption_progress.txt"
CONCURRENCY = 3  # VLM 并发限流（与 upload.py 图片描述一致）


def load_index() -> dict:
    """henan_images.json → {source: {file: {"figure_no", "caption"}}}"""
    raw = json.loads(
        (Path(__file__).resolve().parent.parent / "src/data/henan_images.json")
        .read_text(encoding="utf-8")
    )
    index: dict = {}
    for source, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        files = {}
        for im in entry.get("images", []) or []:
            if isinstance(im, dict) and im.get("file"):
                files[im["file"]] = {
                    "figure_no": im.get("figure_no", ""),
                    "caption": im.get("caption", "") or "",
                }
        if files:
            index[source] = files
    return index


def load_done() -> set[tuple[str, str]]:
    """进度行文件 → {(source, file)} 已完成集合（断点续跑/换模型跳过）"""
    if not PROGRESS_PATH.exists():
        return set()
    done: set[tuple[str, str]] = set()
    for line in PROGRESS_PATH.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            done.add((parts[0], parts[1]))
    return done


def mark_done(source: str, fname: str) -> None:
    """单张图完成即追加一行（append-only:崩溃/中断不丢已完成部分）"""
    with PROGRESS_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{source}\t{fname}\n")
        f.flush()


async def caption_one(captioner, img_path: Path) -> str:
    """VLM 描述单图（失败返回空串,不记录——换模型/重跑自动重试）"""
    for _ in range(2):  # 单图重试一次（网络抖动）
        desc = await captioner.caption(img_path)
        if desc:
            return desc
        await asyncio.sleep(1)
    return ""


def image_path(source: str, fname: str) -> Path:
    """图片真实路径（src/data/images/henan/{source}/{fname}）"""
    return (Path(__file__).resolve().parent.parent
            / "src/data/images/henan" / source / fname)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只统计,不调 API")
    parser.add_argument("--limit", type=int, default=0, help="本轮最多处理 N 张(0=不限)")
    args = parser.parse_args()

    from core.config import settings
    from multimodal.image_caption import QwenVLCaptioner

    if not settings.dashscope_api_key:
        print("!! 未配置 DASHSCOPE_API_KEY——.env 加入后重跑")
        return
    captioner = QwenVLCaptioner(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.image_caption_model,
    )
    print(f"模型: {settings.image_caption_model}（换模型: 改 .env IMAGE_CAPTION_MODEL 后重跑,已完成自动跳过）")

    import chromadb

    from core.di import _ChromaEmbeddingFn, container

    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    doc_col = client.get_collection("documents")
    # update 不带 embeddings 时 Chroma 用默认 ef(all-MiniLM 384 维)重编码,
    # 与集合的 bge-small-zh 512 维冲突——必须显式传集合同款 ef 的向量
    ef = _ChromaEmbeddingFn(container.embedder)

    index = load_index()
    done = load_done()
    done_count = len(done)
    # 全量待补清单:图注缺失 + 未完成
    todo: list[tuple[str, str]] = []
    for source, files in index.items():
        for fname, meta in files.items():
            if not meta["caption"] and (source, fname) not in done:
                todo.append((source, fname))
    print(f"待补 {len(todo)} 张（已完成 {done_count} 张）")
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("无事可做——全部已处理")
        return
    if args.dry_run:
        print(f"[dry-run] 本轮将处理 {len(todo)} 张,不调 API")
        return

    # 块 id 映射:content 含文件名 → id（按 source 批量取,避免全量拉取）
    # 双 key 候选:块 source 命名可能带或不带「河南博物院-」前缀
    by_source_ids: dict[str, dict[str, str]] = {}
    for source in {s for s, _ in todo}:
        res = doc_col.get(
            where={"$and": [{"source": {"$in": [f"{source}#图",
                                                 f"河南博物院-{source}#图"]}},
                            {"chunk_type": "image"}]},
            include=["documents"], limit=1000,
        )
        mapping = {}
        for cid, content in zip(res["ids"], res["documents"]):
            for fname in index[source]:
                if fname in content:
                    mapping[fname] = cid
                    break
        by_source_ids[source] = mapping

    sem = asyncio.Semaphore(CONCURRENCY)
    failed = 0
    succeeded = 0
    t0 = time.time()

    async def _work(source: str, fname: str) -> None:
        nonlocal failed, succeeded
        async with sem:
            cid = by_source_ids.get(source, {}).get(fname)
            if cid is None:
                return  # 块不存在（数据漂移）——跳过
            img = image_path(source, fname)
            if not img.exists():
                return
            desc = await caption_one(captioner, img)
            if not desc:
                failed += 1
                return
            figure_no = (index[source][fname].get("figure_no")
                         or fname.split(".")[0].lstrip("0"))
            new_content = f"【图片·图{figure_no}】{desc}"
            # 显式 embeddings:同 ef 重编码新文本,与集合维度一致,
            # 且图片块向量从"文件名占位"升级为"描述"——语义检索同样受益
            emb = ef.embed_documents([new_content])[0]
            doc_col.update(
                ids=[cid], documents=[new_content], embeddings=[emb],
            )
            mark_done(source, fname)  # 完成即落盘——中断不丢
            succeeded += 1
            if succeeded % 20 == 0:
                print(f"  ... {succeeded} 张完成（失败 {failed}）", flush=True)

    # 逐张提交（不 gather 整批——Ctrl+C 时已完成的任务都已落盘）
    tasks = [asyncio.create_task(_work(s, f)) for s, f in todo]
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n!! 中断——已完成部分已落盘,重跑续传")
        sys.exit(130)

    print("=" * 40)
    print(f"本轮完成 {succeeded}/{len(todo)}（失败 {failed},重跑自动重试）")
    print(f"总进度: {done_count + succeeded} 张 · 耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
