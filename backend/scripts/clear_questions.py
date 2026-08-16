# -*- coding: utf-8 -*-
"""S6 前置：清空 questions 索引（重切后全量重建，避免旧 source_chunk_id 残留）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.di import container  # noqa: E402


def main() -> None:
    col = container.questions.collection
    total = col.count()
    if total:
        ids = col.get(limit=1000000)["ids"]
        for i in range(0, len(ids), 5000):
            col.delete(ids=ids[i:i + 5000])
    print(f"questions 清空完成: 删除 {total} 条")


if __name__ == "__main__":
    main()
