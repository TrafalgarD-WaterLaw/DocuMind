"""问题生成进度回调测试——on_progress 单调递增且总量正确"""
import json

import pytest

from retrieval.hypothesis import build_question_documents


class FakeLLM:
    """返回固定 JSON 的假 LLM"""

    def build_messages(self, *args, **kwargs):
        return []

    async def chat(self, messages, **kwargs):
        # 3 个 chunk → 3 条带 questions 的结果
        payload = {
            "results": [
                {"chunk_id": f"c{i}", "questions": [f"问题{i}-1", f"问题{i}-2"]}
                for i in range(3)
            ]
        }
        return json.dumps(payload, ensure_ascii=False)


class FakeStore:
    """内存问题库：get_all_documents + add_documents"""

    def __init__(self):
        self.docs = []

    def get_all_documents(self):
        return list(self.docs)

    def add_documents(self, documents):
        self.docs.extend(documents)


async def test_on_progress_reports_batches():
    llm = FakeLLM()
    store = FakeStore()
    docs = [
        {"id": f"c{i}", "content": f"内容{i}", "metadata": {"source": "s"}}
        for i in range(3)
    ]
    calls: list[tuple[int, int]] = []
    total = await build_question_documents(
        llm, docs, store, batch_size=1, on_progress=lambda d, t: calls.append((d, t))
    )
    assert total == 6  # 3 chunk × 2 问
    assert calls == [(1, 3), (2, 3), (3, 3)]  # 每批完成回调一次，总量正确


async def test_on_progress_optional():
    llm = FakeLLM()
    store = FakeStore()
    docs = [{"id": "c0", "content": "x", "metadata": {}}]
    total = await build_question_documents(llm, docs, store, batch_size=1)
    assert total == 2


async def test_skipped_batches_still_count_progress():
    """全部批被 skip_existing 过滤时，进度仍按批推进（断点续跑场景）"""
    llm = FakeLLM()
    store = FakeStore()
    # 预置：已有问题的 chunk（source_chunk_id 与 docs 的 id 相同 → 全部跳过）
    for i in range(3):
        store.add_documents([{
            "chunk_id": f"c{i}::q0",
            "content": "已有问题",
            "metadata": {"source_chunk_id": f"c{i}"},
        }])
    docs = [
        {"id": f"c{i}", "content": f"内容{i}", "metadata": {"source": "s"}}
        for i in range(3)
    ]
    calls: list[tuple[int, int]] = []
    total = await build_question_documents(
        llm, docs, store, batch_size=1, skip_existing=True,
        on_progress=lambda d, t: calls.append((d, t)),
    )
    assert total == 0          # 全部跳过，无新问题
    assert calls == [(1, 3), (2, 3), (3, 3)]  # 进度仍按批推进
