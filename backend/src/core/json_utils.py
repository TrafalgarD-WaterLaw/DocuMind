# -*- coding: utf-8 -*-
"""LLM 输出 JSON 解析工具——全项目 7 处 JSON 场景共用

JSON Output（response_format）保证模型输出合法 JSON，但官方承诺不是
代码保证——模型偶发空 content / 混入解释文字时仍可兜底。提取子串 +
解析失败的防御逻辑收口于此，调用点只负责业务容错（回退语义）。
"""
import json


def extract_json(raw: str) -> dict | None:
    """从 LLM 输出提取首个 JSON 对象

    JSON Output 模式下 raw 通常就是完整 JSON（提取整个串）；
    防御模型偶发混入解释文字/Markdown 代码块/空 content。
    """
    if not raw or not raw.strip():
        return None
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError:
        return None


def extract_json_array(raw: str) -> list | None:
    """从 LLM 输出提取 JSON 数组（兼容直接输出数组的格式）"""
    if not raw or not raw.strip():
        return None
    start, end = raw.find("["), raw.rfind("]") + 1
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError:
        return None


def extract_string_list(
    raw: str,
    key: str,
    *,
    min_len: int = 1,
    max_len: int | None = None,
    max_items: int | None = None,
    strict: bool = True,
) -> list[str] | None:
    """从 LLM JSON 输出提取字符串列表字段（清理 + 校验）

    模式: {"key": ["a", "b"]} → ["a", "b"]——查询分解 sub_queries /
    文档实体 entities 等"LLM 输出一个字符串数组"的场景共用。

    Args:
        raw: LLM 原始输出
        key: 目标字段名
        min_len / max_len: 元素清理后的长度下限/上限（不满足丢弃）
        max_items: 返回数量上限（超限截断;None 不截断）
        strict: True 时混入非字符串元素 → 整体返回 None（结构不可信,
            保守回退）;False 时跳过该元素（宽容,保部分结果）

    Returns:
        清理后的字符串列表;提取失败/字段非数组/(strict)混入非字符串 → None
    """
    data = extract_json(raw)
    if data is None:
        return None
    if key not in data:
        return None  # 期望字段缺失 = 结构非法（空数组 ≠ 缺失）
    items = data.get(key) or []
    if not isinstance(items, list):
        return None
    if strict and any(not isinstance(s, str) for s in items):
        return None
    cleaned = []
    for s in items:
        if not isinstance(s, str):
            continue  # 非 strict 时跳过
        s = s.strip()
        if len(s) < min_len:
            continue
        if max_len is not None and len(s) > max_len:
            continue
        cleaned.append(s)
        if max_items is not None and len(cleaned) >= max_items:
            break
    return cleaned
