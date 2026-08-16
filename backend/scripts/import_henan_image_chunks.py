# -*- coding: utf-8 -*-
"""河南图注级图片块入库（多模态阶段 4）——等 crawl_henan_images.py 跑完再执行

用法: uv run python scripts/import_henan_image_chunks.py

行为:
  - 读 henan_images.json（爬虫产物: file/figure_no/caption/section）
  - 每张图 1 个图片块进 documents 集合:
      content = 【图片·图{N}】{caption}（无图注 → 【图片】{文物名} {file}）
      metadata = {source: "河南博物院-{文物名}#图", chunk_type: "image",
                  image_path: "/api/images/henan/{文物名}/{file}", figure_no}
  - source 的 #图 后缀: 与文本块隔离来源多样性上限
  - 幂等: 已存在的 image_path 跳过（按 metadata 查重）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

DATA_DIR = Path(__file__).parent.parent / "src" / "data"
MANIFEST = DATA_DIR / "henan_images.json"


def build_image_chunks(manifest: dict, museum: dict) -> list[dict]:
    """manifest → 图片块文档列表（纯函数，可测试）"""
    import uuid

    chunks = []
    for key, entry in manifest.items():
        name = entry.get("name", key)
        for img in entry.get("images", []):
            fname = img.get("file", "")
            if not fname:
                continue
            fig = img.get("figure_no", "")
            caption = (img.get("caption") or "").strip()
            if fig:
                content = f"【图片·图{fig}】{caption}" if caption else f"【图片·图{fig}】{name}"
            else:
                content = f"【图片】{name} {fname}"
            chunks.append({
                "chunk_id": str(uuid.uuid4()),
                "content": content,
                "metadata": {
                    "source": f"河南博物院-{name}#图",
                    "chunk_type": "image",
                    "image_path": f"/api/images/henan/{key}/{fname}",
                    "figure_no": fig,
                    "section": img.get("section", ""),
                },
            })
    return chunks


def main():
    if not MANIFEST.exists():
        print("henan_images.json 不存在——先跑 crawl_henan_images.py")
        return
    from core.di import container

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    museum_path = DATA_DIR / "henan_museum.json"
    museum = json.loads(museum_path.read_text(encoding="utf-8")) if museum_path.exists() else {}
    chunks = build_image_chunks(manifest, museum)
    print(f"构造图片块 {len(chunks)} 个")

    # 幂等：跳过已入库（按 image_path 查重）
    existing = set()
    try:
        docs = container.vector.get_all_documents()
        for d in docs:
            ip = d.get("metadata", {}).get("image_path")
            if ip and ip.startswith("/api/images/henan/"):
                existing.add(ip)
    except Exception as e:
        print(f"查重失败（继续全量）: {e}")
    fresh = [c for c in chunks if c["metadata"]["image_path"] not in existing]
    print(f"新增 {len(fresh)} 个（已存在 {len(chunks) - len(fresh)} 个）")

    if fresh:
        container.vector.add_documents(fresh)
        container.mark_bm25_dirty()
    print("完成")


if __name__ == "__main__":
    main()
