# -*- coding: utf-8 -*-
"""文档实体抽取——上传文档实体锚定（U2，upload-pipeline 优化第 1 批）

设计: docs/upload-pipeline-optimization-design.md 环节 3
动机: 上传文档 source 为 {timestamp}_{file}，entity 路（source 名匹配）天然失效；
     抽取实体注入块 metadata.entities 后，entity 路可按数组匹配命中。

保守原则: LLM 失败/空 → 空列表（不阻断上传，不写 entities 字段）。
"""
from __future__ import annotations

import logging
from typing import Any

from core.json_utils import extract_string_list
from prompts import render_system, render_user

logger = logging.getLogger(__name__)

MAX_ENTITIES = 5
MAX_TEXT_CHARS = 2000


def _parse_entities(raw: str) -> list[str]:
    """解析 LLM 输出 JSON → 实体列表（非法返回空列表）

    结构解析在 json_utils.extract_string_list（strict=False:混入非字符串
    跳过——实体部分结果可接受）;此处映射 None → 空列表（不阻断上传）。
    """
    try:
        cleaned = extract_string_list(
            raw, "entities", min_len=2, max_len=30,
            max_items=MAX_ENTITIES, strict=False,
        )
    except Exception as e:
        logger.warning(f"实体抽取解析失败: {e}")
        return []
    return cleaned or []


async def extract_entities(text: str, llm: Any) -> list[str]:
    """从文档全文提取 ≤5 个文物/遗址/朝代实体；失败回退空列表"""
    if not text or not llm:
        return []
    messages = llm.build_messages(
        render_system("document_entity_extraction"),
        render_user("document_entity_extraction", text=text[:MAX_TEXT_CHARS]),
    )
    try:
        raw = await llm.chat(
            messages, temperature=0.0,
            response_format={"type": "json_object"},  # DeepSeek JSON Output
        )
    except Exception as e:
        logger.warning(f"实体抽取 LLM 失败: {e}")
        return []
    return _parse_entities(raw or "")
