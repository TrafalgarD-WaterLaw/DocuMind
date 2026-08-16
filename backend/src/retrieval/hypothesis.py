"""入库侧假设性问题生成（Q-to-Q 匹配）

核心思路：用户在检索时大多以疑问句提问，而知识库存的是陈述句。
在入库时为每个 chunk 生成用户"可能问什么"的假设性问题，
查询时在问题索引上做 Q-to-Q 匹配，比语义检索更易命中。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from core.config import settings
from interfaces.llm import LLMProvider
from interfaces.vector_store import VectorStore
from prompts import render_system, render_user
from core.llm_retry import llm_call_with_retry

logger = logging.getLogger(__name__)


def parse_question_results(raw: str) -> dict[str, list[str]]:
    """解析 LLM 输出为 {chunk_id: [questions]}，容错多种格式"""
    result: dict[str, list[str]] = {}

    from core.json_utils import extract_json

    data = extract_json(raw)
    if data:
        results = data.get("results") or data.get("questions")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                chunk_id = str(item.get("chunk_id") or item.get("id") or "")
                questions = item.get("questions") or item.get("qs") or []
                if chunk_id and isinstance(questions, list):
                    result[chunk_id] = [str(q).strip() for q in questions if str(q).strip()]
        return result

    # 容错：直接输出 [{"chunk_id": ..., "questions": [...]}]
    from core.json_utils import extract_json_array

    arr = extract_json_array(raw)
    if arr:
        for item in arr:
            if not isinstance(item, dict):
                continue
            chunk_id = str(item.get("chunk_id") or "")
            questions = item.get("questions") or []
            if chunk_id:
                result[chunk_id] = [str(q).strip() for q in questions if str(q).strip()]
    return result


async def generate_questions_for_batch(
    llm: LLMProvider, batch: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """为一批 chunk 生成假设性问题

    Args:
        llm: LLM 提供者
        batch: [{"id": str, "content": str, "metadata": dict}]

    Returns:
        {chunk_id: [question, ...]}
    """
    pieces = "\n\n".join(
        f"[{i}]\n{d.get('content', '')[:800]}" for i, d in enumerate(batch)
    )

    messages = llm.build_messages(
        render_system("question_generation", n=settings.questions_per_chunk),
        render_user("question_generation", pieces=pieces),
    )

    # LLM 偶发空响应（deepseek-v4-flash 实测概率性返回空），重试兜底。
    # 统一 llm_call_with_retry（异常 + 空响应双失败模式，抖动退避）。
    # 注意：内置判定覆盖空串/空 JSON 字面量（"{}"），attempts=3 统一
    # （原手写 5 次容忍度略降——parse 级校验场景由 _parse 容错兜底）。
    # 不限制 max_tokens：flash 为推理模型，输出被截断会导致 JSON 不完整
    # → 解析失败（曾被误判为"空响应"），交给服务端默认上限即可。
    raw = await llm_call_with_retry(
        messages, llm, temperature=0.4,
        response_format={"type": "json_object"},  # DeepSeek JSON Output
    )
    by_id = parse_question_results(raw)

    # 将 LLM 输出的序号 [i] 映射回真实 chunk_id
    mapped: dict[str, list[str]] = {}
    for i, doc in enumerate(batch):
        chunk_id = doc["id"]
        # 优先用 chunk_id 匹配，失败则按序号回退
        questions = by_id.get(chunk_id) or by_id.get(str(i)) or by_id.get(f"id{i}")
        if questions:
            mapped[chunk_id] = questions[: settings.questions_per_chunk]
    return mapped


async def build_question_documents(
    llm: LLMProvider,
    documents: list[dict[str, Any]],
    questions_store: VectorStore,
    *,
    batch_size: int | None = None,
    skip_existing: bool = True,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """为文档列表生成问题并写入问题索引

    Args:
        llm: LLM 提供者
        documents: 原始文档 [{"id", "content", "metadata"}]
        questions_store: 问题索引 VectorStore（questions collection）
        skip_existing: 跳过已有问题的 chunk（断点续跑）
        on_progress: 进度回调，每处理完一批调用 on_progress(已完成批数, 总批数)

    Returns:
        生成的问题数量
    """
    # 已处理过的 chunk 集合（用于断点续跑）
    existing = _collect_existing_chunks(questions_store) if skip_existing else set()

    total = 0
    batch_size = batch_size or settings.hypothesis_batch_size
    total_batches = max(
        1, (len(documents) + batch_size - 1) // batch_size
    )
    done_batches = 0
    for start in range(0, len(documents), batch_size):
        batch = documents[start : start + batch_size]
        batch = [d for d in batch if d["id"] not in existing]
        done_batches += 1
        if on_progress:
            on_progress(done_batches, total_batches)
        if not batch:
            continue

        try:
            mapped = await generate_questions_for_batch(llm, batch)
        except Exception as e:
            logger.exception(f"问题生成失败 (batch {start}): {e}")
            continue

        qdocs, batch_total = _build_question_docs(batch, mapped)
        total += batch_total
        if qdocs:
            questions_store.add_documents(qdocs)

    logger.info(f"假设性问题生成完成：共 {total} 个问题")
    return total


async def build_question_index(
    llm: LLMProvider,
    documents_store: VectorStore,
    questions_store: VectorStore,
    *,
    skip_existing: bool = True,
) -> int:
    """一键构建问题索引：从文档库全量导出 → 生成问题 → 写入问题库"""
    documents = documents_store.get_all_documents()
    logger.info(f"从文档库导出 {len(documents)} 个 chunk")
    return await build_question_documents(
        llm, documents, questions_store, skip_existing=skip_existing
    )


def _collect_existing_chunks(questions_store: VectorStore) -> set[str]:
    """问题索引中已处理过的 chunk id 集合（断点续跑）"""
    existing: set[str] = set()
    for doc in questions_store.get_all_documents():
        src = doc.get("metadata", {}).get("source_chunk_id")
        if src:
            existing.add(src)
    return existing


def _build_question_docs(
    batch: list[dict[str, Any]], mapped: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], int]:
    """映射结果 → 问题索引文档列表（LLM 输出序号映射回真实 chunk_id）"""
    qdocs: list[dict[str, Any]] = []
    total = 0
    for chunk_id, questions in mapped.items():
        src_doc = next((d for d in batch if d["id"] == chunk_id), None)
        if not src_doc:
            continue
        meta = dict(src_doc.get("metadata", {}))
        meta["source_chunk_id"] = chunk_id
        for i, q in enumerate(questions):
            qdocs.append({
                "chunk_id": f"{chunk_id}::q{i}",
                "content": q,
                "metadata": {
                    **meta,
                    "question_index": i,
                },
            })
        total += len(questions)
        logger.info(f"  {chunk_id}: {len(questions)} 个问题")
    return qdocs, total
