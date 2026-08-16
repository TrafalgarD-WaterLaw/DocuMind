# -*- coding: utf-8 -*-
"""C2 构建 CLIP 图片索引——图文互检（多模态 RAG 跨模态检索）

数据源:
  1. 数据集图片: src/data/images/{bronze,henan,porcelain}/...（image_index.json
     映射表 source → 图片路径，检索命中可反查 source）
  2. 上传文档图片: src/data/uploads/*.pdf.images/fig_*.png（source 为 {时间戳}_{file}#图）

索引: 新 collection `clip_images`（与 documents 同 chroma 目录）——图片 CLIP
图像编码（显式 embeddings）；查询时 Chroma 用 CLIP 文本编码器编码 query
（图文同空间 cosine）。

幂等: 先清 clip_images 再重建。
用法: python scripts/import_clip_images.py [--limit N] [--device cpu]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import chromadb  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402

CHROMA_DIR = Path(__file__).resolve().parent.parent / "src" / "data" / "chroma"
DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "data"
IMAGES_DIR = DATA_DIR / "images"
UPLOADS_DIR = DATA_DIR / "uploads"
MODEL_PATH = "D:/cache/modelscope/models/OFA-Sys--chinese-clip-vit-base-patch16"

BATCH = 32


class _ClipTextFn:
    """CLIP 文本编码器（Chroma ef——query 文本编码用）"""

    def __init__(self, model, processor):
        self._model = model
        self._processor = processor

    def name(self) -> str:
        return "chinese-clip"

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self.embed_query(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        with torch.no_grad():
            # 该 transformers 版本 get_text_features 返回
            # BaseModelOutputWithPooling——pooler_output 即 512 维投影特征
            inputs = self._processor(text=input, return_tensors="pt", padding=True)
            out = self._model.get_text_features(
                input_ids=inputs["input_ids"],
                attention_mask=inputs["attention_mask"],
            )
            embeds = out.pooler_output
        return [v.tolist() for v in embeds.cpu().float()]

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self.embed_query(input)


def collect_images() -> list[dict]:
    """收集全部图片条目: {path, source, url}"""
    import json

    items: list[dict] = []

    # 1. 数据集图片（映射表 source → images 相对路径）
    index_path = DATA_DIR / "image_index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for source, entry in index.items():
            for rel in (entry.get("images") or []):
                if not rel:
                    continue
                p = IMAGES_DIR / rel
                if p.exists():
                    items.append({
                        "path": p,
                        "source": source,
                        "url": f"/api/images/{rel}",
                    })

    # 2. 上传文档图片（{source}.images/fig_*.png）
    if UPLOADS_DIR.exists():
        for img_dir in UPLOADS_DIR.glob("*.pdf.images"):
            source = img_dir.name.removesuffix(".images")
            for img in sorted(img_dir.glob("fig_*.png")):
                items.append({
                    "path": img,
                    "source": f"{source}#图",
                    "url": f"/api/uploads/{source}.images/{img.name}",
                })
    return items


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    device = "cpu" if "--device" in sys.argv and sys.argv[sys.argv.index("--device") + 1] == "cpu" else ("cuda" if torch.cuda.is_available() else "cpu")

    from transformers import ChineseCLIPModel, ChineseCLIPProcessor

    print(f"加载 Chinese-CLIP（{device}）…", flush=True)
    t0 = time.time()
    model = ChineseCLIPModel.from_pretrained(MODEL_PATH).to(device)
    processor = ChineseCLIPProcessor.from_pretrained(MODEL_PATH)
    print(f"  加载完成 {time.time() - t0:.0f}s", flush=True)

    items = collect_images()
    if limit:
        items = items[:limit]
    print(f"图片总数: {len(items)}", flush=True)

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    col = client.get_or_create_collection(
        "clip_images", embedding_function=_ClipTextFn(model, processor),
        metadata={"hnsw:space": "cosine"},
    )
    stale = col.get(limit=1000000)["ids"]
    if stale:
        col.delete(ids=stale)
        print(f"幂等清理: {len(stale)} 条", flush=True)

    # 图像编码（主线程，分批显式 add——避免 Chroma 内部线程 ef 全量编码死锁）
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        images = [Image.open(it["path"]).convert("RGB") for it in batch]
        with torch.no_grad():
            # 注: 该 transformers 版本 get_image_features 返回
            # BaseModelOutputWithPooling、image-only forward 报 input_ids 缺失——
            # 用已验证路径: text(空串)+images 同时传入取 image_embeds
            inputs = processor(text=[""] * len(batch), images=images,
                               return_tensors="pt", padding=True)
            inputs = {k: v.to(device) for k, v in inputs.items()}
            embeds = model(**inputs).image_embeds.cpu().float()
        col.add(
            ids=[str(it["path"]) for it in batch],
            documents=[it["url"] for it in batch],
            metadatas=[{"source": it["source"], "image_path": it["url"]} for it in batch],
            embeddings=[v.tolist() for v in embeds],
        )
        done = start + len(batch)
        if done % 512 == 0 or done == len(items):
            print(f"  编码 {done}/{len(items)}", flush=True)

    print(f"\n完成: clip_images {col.count()} 条（{time.time() - t0:.0f}s）")


if __name__ == "__main__":
    main()
