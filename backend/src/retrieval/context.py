# -*- coding: utf-8 -*-
"""上下文组装工具——块级噪声过滤 + 父子分块替换

设计: docs/superpowers/specs/2026-08-07-chunking-context-optimization-design.md
定位: 检索（五路混合）与生成（证据锚定）之间的组装层——决定哪些块进 LLM。
"""
from __future__ import annotations

import logging
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)


def filter_noise_chunks(
    docs: list[dict[str, Any]], *, threshold: float | None = None
) -> list[dict[str, Any]]:
    """块级噪声过滤（上下文压缩轻量版）——RRF 分数三重条件裁剪

    保留条件（任一满足）:
      1. score ≥ threshold（默认 settings.rrf_score_threshold=0.025）
      2. 多路径票（len(paths) ≥ 2）——多路命中 = 强相关信号
      3. 排名 ≤ 4（RRF 融合分排序前 4，强相关区）
      4. 图谱/实体锚定块（graph_anchor 或 paths 含 graph/entity）——
         器名精确匹配/关系证据是强信号，分数低但不可裁

    被裁的只有「单票弱块」：1 路命中且分数低且排名 > 4。
    与 CRAG 互补: CRAG 管「整体检索不足 → 重检索」（外层），
    本函数管「块内噪声」（内层）——两个粒度互不冲突。
    无 score/paths 的块（同步 VectorStore 兼容分支）原样保留。
    """
    if threshold is None:
        threshold = settings.rrf_score_threshold
    kept: list[dict[str, Any]] = []
    dropped = 0
    for i, d in enumerate(docs):
        if _keep_decision(d, i, threshold):
            kept.append(d)
        else:
            dropped += 1
            logger.info(
                "噪声过滤裁剪块: rank=%d score=%s paths=%s source=%s",
                i + 1, d.get("score"), d.get("paths"), d.get("source"),
            )
    if dropped:
        logger.info(f"噪声过滤: {dropped}/{len(docs)} 块被裁（阈值 {threshold}）")
    return kept


def _keep_decision(
    doc: dict[str, Any], rank: int, threshold: float,
) -> bool:
    """单块保留判定——强信号块恒保留,弱块按 RRF 三重条件

    恒保留: 图片块（语义直检通道,单票低分但图注级精确）/ 图谱锚定块
    （关系证据）/ graph·entity 路块（器名精确匹配强信号）。
    恒裁剪: 父块（A4——长节文本送 LLM 与子块内容重复,父块仅由
    resolve_parent_chunks 按 parent_id 取回）。
    其余: score ≥ threshold / 多路票 / 排名 ≤ 4 任一满足保留。
    """
    meta = doc.get("metadata") or {}
    if meta.get("is_parent"):
        return False
    if meta.get("chunk_type") == "image" or doc.get("graph_anchor"):
        return True
    paths = doc.get("paths") or []
    if "graph" in paths or "entity" in paths:
        return True
    score = doc.get("score")
    return (score is not None and score >= threshold) or len(paths) >= 2 or rank < 4


def resolve_parent_chunks(
    docs: list[dict[str, Any]], doc_store: Any
) -> list[dict[str, Any]]:
    """父子分块（Small-to-Big）——检索子块 → 组装父块替换

    检索命中子块（metadata.parent_id）→ 批量 get_by_ids 取父块替换，
    保持原排序与 score/paths（引用编号不变）；无 parent_id 的块
    （存量数据/图片块/图谱子图）原样保留。
    仅替换 content/metadata/id——sources 事件仍用替换前的子块
    （引用定位精确与语义完整解耦）。
    """
    needs = [
        d for d in docs
        if isinstance(d.get("metadata"), dict) and d["metadata"].get("parent_id")
    ]
    if not needs or doc_store is None:
        return docs
    ids = list({d["metadata"]["parent_id"] for d in needs})
    try:
        parents = doc_store.get_by_ids(ids)
    except Exception as e:
        logger.warning(f"父块批量取回失败，退化为子块原样: {e}")
        return docs
    by_id = {p["id"]: p for p in parents}
    out: list[dict[str, Any]] = []
    for d in docs:
        pid = (d.get("metadata") or {}).get("parent_id")
        parent = by_id.get(pid) if pid else None
        if parent is None:
            out.append(d)
            continue
        out.append({
            **d,
            "id": parent["id"],
            "content": parent.get("content", d["content"]),
            "metadata": parent.get("metadata", d["metadata"]),
            "is_parent": True,
        })
    return out
