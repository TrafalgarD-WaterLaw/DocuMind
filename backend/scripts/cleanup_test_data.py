# -*- coding: utf-8 -*-
"""P1-D 测试数据清理：删除旧版测试文档残留（保留最新 1786074778）

背景: 测试 PDF 多次上传产生 3 个旧版本（1786070691/1786071360/1786072774），
     其中 1360/2774 有 Chroma 块残留、0691 仅磁盘文件——均为过期资产。

清理范围:
  - Chroma documents/questions: source 以 1786071360_ / 1786072774_ 开头的块
  - 磁盘 uploads/: 1786070691 / 1786071360 / 1786072774 三份 PDF
  （1786074778 为当前版，保留）

幂等: 再次运行删除数为 0。
"""
import sys
from pathlib import Path

import chromadb

STALE_PREFIXES = ("1786071360_", "1786072774_")
STALE_PDFS = ("1786070691_", "1786071360_", "1786072774_")

CHROMA_DIR = Path(__file__).resolve().parent.parent / "src" / "data" / "chroma"
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "src" / "data" / "uploads"


def _delete_stale_collection(client: chromadb.ClientAPI, collection_name: str) -> int:
    col = client.get_or_create_collection(collection_name)
    data = col.get(include=["metadatas"])
    stale = [
        doc_id for doc_id, meta in zip(data["ids"], data["metadatas"])
        if (meta or {}).get("source", "").startswith(STALE_PREFIXES)
    ]
    if stale:
        col.delete(ids=stale)
    return len(stale)


def cleanup() -> dict:
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    docs_removed = _delete_stale_collection(client, "documents")
    questions_removed = _delete_stale_collection(client, "questions")

    pdfs_removed = 0
    for f in UPLOAD_DIR.iterdir():
        if f.suffix.lower() == ".pdf" and f.name.startswith(STALE_PDFS):
            f.unlink(missing_ok=True)
            pdfs_removed += 1
    return {
        "docs_chunks": docs_removed,
        "question_chunks": questions_removed,
        "pdfs": pdfs_removed,
    }


def main() -> None:
    result = cleanup()
    print(f"清理完成: documents {result['docs_chunks']} 块 / "
          f"questions {result['question_chunks']} 块 / PDF {result['pdfs']} 份")
    if sum(result.values()):
        print("（幂等：再次运行应为全 0）")


if __name__ == "__main__":
    main()
