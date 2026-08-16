# -*- coding: utf-8 -*-
"""P1-B 数据契约迁移：image_path 统一带 /api/uploads/ 前缀

背景: 早期上传文档的图片块 metadata.image_path 为裸相对路径
     （{source}.images/{name}），与 /api/images/ 前缀体系不一致。
     统一为 /api/uploads/ 前缀后，前端可删裸相对拼接分支。

范围: 只迁移上传文档图片块（{source}.images/ 开头、无前缀的存量），
     映射表体系（image_index.json）保持相对路径由服务层拼前缀，不迁移。
     Chroma update 仅改 metadata，不重 embedding；幂等。
"""
import sys
from pathlib import Path

import chromadb

DEFAULT_PERSIST = str(Path(__file__).resolve().parent.parent / "src" / "data" / "chroma")


def migrate(persist_dir: str = DEFAULT_PERSIST, collection_name: str = "documents") -> dict:
    """为缺失前缀的上传文档图片块补 /api/uploads/ 前缀，返回迁移统计"""
    client = chromadb.PersistentClient(path=persist_dir)
    col = client.get_or_create_collection(collection_name)
    data = col.get(include=["metadatas"])
    ids: list[str] = []
    metas: list[dict] = []
    for doc_id, meta in zip(data["ids"], data["metadatas"]):
        path = (meta or {}).get("image_path", "")
        # 裸相对 = {source}.images/... 开头且不以 / 或 http 开头
        if path and not path.startswith("/") and not path.startswith("http"):
            ids.append(doc_id)
            new_meta = dict(meta or {})
            new_meta["image_path"] = f"/api/uploads/{path}"
            metas.append(new_meta)
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
