# -*- coding: utf-8 -*-
"""P1-A 数据契约迁移：chunk_type 全量标记（文本块补 text）

背景: 历史入库的文本块没有 chunk_type 字段（仅图片块有 "image"），
     检索侧依赖 where={"chunk_type": "image"} 精确直检图片块——
     缺失字段不会误伤该过滤（undefined ≠ "image"），但契约不完整。

做法: Chroma collection.update 只改 metadata，不重 embedding、不动内容，
     幂等（再跑一次迁移数为 0）。

用法: python scripts/migrate_chunk_type.py [--collection documents]
"""
import sys
from pathlib import Path

import chromadb

DEFAULT_PERSIST = str(Path(__file__).resolve().parent.parent / "src" / "data" / "chroma")


def migrate(persist_dir: str = DEFAULT_PERSIST, collection_name: str = "documents") -> dict:
    """补全缺失的 chunk_type（文本块标记为 text），返回迁移统计"""
    client = chromadb.PersistentClient(path=persist_dir)
    col = client.get_or_create_collection(collection_name)
    data = col.get(include=["metadatas"])
    ids: list[str] = []
    metas: list[dict] = []
    for doc_id, meta in zip(data["ids"], data["metadatas"]):
        if "chunk_type" not in (meta or {}):
            ids.append(doc_id)
            new_meta = dict(meta or {})
            new_meta["chunk_type"] = "text"
            metas.append(new_meta)
    if ids:
        # 仅更新 metadata——Chroma 保留原 embedding 与 document
        # 分批（Chroma 单批上限 5461）
        for i in range(0, len(ids), 1000):
            col.update(ids=ids[i:i + 1000], metadatas=metas[i:i + 1000])
    return {"migrated": len(ids), "total": len(data["ids"])}


def main() -> None:
    collection = "documents"
    if "--collection" in sys.argv:
        collection = sys.argv[sys.argv.index("--collection") + 1]
    result = migrate(collection_name=collection)
    print(f"collection={collection}: 迁移 {result['migrated']} 块 / 共 {result['total']} 块")
    if result["migrated"]:
        print("（幂等：再次运行应为 0）")


if __name__ == "__main__":
    main()
