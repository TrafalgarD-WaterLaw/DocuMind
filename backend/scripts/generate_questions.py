# -*- coding: utf-8 -*-
"""Generate hypothetical questions for all chunks (ingestion-side Q-to-Q).

Reads all chunks from the documents collection, asks the LLM to propose
questions a user might ask per chunk, and stores them in the questions
collection. Skips chunks that already have questions (resume-safe).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402
from retrieval.hypothesis import build_question_index  # noqa: E402


async def main():
    print("=== Generate Hypothetical Questions (ingestion-side) ===\n")

    # 小批量验证：--limit N 只处理前 N 个 chunk
    limit = None
    if len(sys.argv) > 1 and sys.argv[1].startswith("--limit="):
        limit = int(sys.argv[1].split("=")[1])

    docs = container.vector.get_all_documents()
    if limit:
        docs = docs[:limit]
    print(f"Chunks to process: {len(docs)}")

    existing = len(container.questions.get_all_documents())
    print(f"Existing question docs: {existing}")

    total = await build_question_index(
        container.llm,
        container.vector,
        container.questions,
        skip_existing=True,
    )

    print(f"\nDone! Generated {total} questions.")
    print(f"Questions collection now: {container.questions.count()}")


if __name__ == "__main__":
    asyncio.run(main())
