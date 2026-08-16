# -*- coding: utf-8 -*-
"""CLIP 图片证据链——视觉命中 → 图注块证据（独立于 RRF 排序）

与五路文本证据的关系：不进 RRF 排序、不改文本证据排名——视觉命中
（如"外形像猫头鹰的青铜器"，文本路图注里未必写）按图注块追加到
编号上下文末尾，回答可引用视觉相似器物的图注。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)


def merge_source_images(
    source: str, clip_by_source: dict[str, list[str]],
) -> list[str]:
    """文本块证据的图片归并：映射表图片 + CLIP 视觉命中图片（双 key 兼容）

    CLIP 索引里上传文档图片的 source 带 #图 后缀，文本块不带；
    文本块命中时必须能取回该文档的图片——两种 key 都查。
    """
    from multimodal.image_index import get_images_for_source

    return list(dict.fromkeys(
        get_images_for_source(source)
        + clip_by_source.get(source, [])
        + clip_by_source.get(f"{source}#图", [])
    ))


async def collect_image_evidence(
    doc_store: Any,
    clip_by_source: dict[str, list[str]],
    exclude_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """CLIP 视觉命中 → 对应图注块（独立图片证据链）

    clip_by_source 的 key 为 {source}#图（P1-C 契约: 图片块专属命名，
    与 clip_images 索引一致）。返回顺序 = 视觉命中顺序（Chroma where
    返回顺序不定，按 key 稳定排序）；失败仅降级为纯文本证据，不阻断回答。

    Args:
        doc_store: 向量库接口（documents collection，get_by_where 查询）
        clip_by_source: 视觉命中 {source: [图片 URL]}
        exclude_ids: 已进上下文的块 id——图片证据与文本证据去重
    """
    if not doc_store or not clip_by_source:
        return []
    # 命名差异兼容: clip_images 索引数据集图片 source 不带 #图
    # （import_clip_images.py），上传文档图片带 #图（add_images）；
    # documents 里的图注块一律带 #图（P1-C）——两种 key 都查。
    keys: list[str] = []
    order: dict[str, int] = {}
    for i, k in enumerate(clip_by_source):
        keys.append(k)
        order[k] = i
        if not k.endswith("#图"):
            keys.append(f"{k}#图")
            order[f"{k}#图"] = i
    try:
        # chunk_type=image 必加: 河南数据集文本块 source 不带 #图
        # （与 clip_images 索引命名一致），$in 会误捞正文块——
        # 文本证据归五路检索，图片证据只取图注块（P1-C 契约）
        blocks = await asyncio.to_thread(
            doc_store.get_by_where,
            {"$and": [{"source": {"$in": keys}}, {"chunk_type": "image"}]},
            limit=settings.clip_evidence_max_blocks,
        )
    except Exception as e:
        logger.warning(f"图片证据取回失败（降级为纯文本证据）: {e}")
        return []
    blocks.sort(key=lambda b: order.get(b.get("metadata", {}).get("source", ""), 99))
    if exclude_ids:
        blocks = [b for b in blocks if b.get("id") not in exclude_ids]
    return blocks[: settings.clip_evidence_max_blocks]
