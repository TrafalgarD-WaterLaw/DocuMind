# -*- coding: utf-8 -*-
"""导入分片生成的问题到 Chroma（合并所有 shard JSON）"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402

OUT_DIR = Path("/tmp") if sys.platform != "win32" else Path("C:/Users/GR/AppData/Local/Temp")


def main():
    docs = container.vector.get_all_documents()
    by_id = {d["id"]: d for d in docs}

    qdocs = []
    total_chunks = 0
    for shard_file in sorted(OUT_DIR.glob("questions_shard_*.json")):
        data = json.loads(shard_file.read_text(encoding="utf-8"))
        total_chunks += len(data)
        for chunk_id, questions in data.items():
            src_doc = by_id.get(chunk_id)
            if not src_doc:
                print(f"⚠️ 找不到 chunk {chunk_id[:20]}（已删除？）")
                continue
            meta = dict(src_doc.get("metadata", {}))
            meta["source_chunk_id"] = chunk_id
            for i, q in enumerate(questions):
                qdocs.append({
                    "chunk_id": f"{chunk_id}::q{i}",
                    "content": q,
                    "metadata": meta,
                })

    # Chroma 单次 add 有 batch 上限（~5461），分批导入
    BATCH = 2000
    for i in range(0, len(qdocs), BATCH):
        container.questions.add_documents(qdocs[i : i + BATCH])
        print(f"  导入批次 {i // BATCH + 1}: {min(i + BATCH, len(qdocs))}/{len(qdocs)}")
    print(f"导入 {len(qdocs)} 条问题（覆盖 {total_chunks} chunk）→ questions 集合")


if __name__ == "__main__":
    main()
