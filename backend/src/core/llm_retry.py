"""LLM 非流式调用重试——tenacity @retry 装饰器（指数+抖动退避,异常分类）

流式回答不重试（已消费的 token 无法回退），由 orchestrator 层做
"重试一次 + 兜底回答"。

两种失败模式统一进装饰器:
- 可重试异常:临时故障（网络/超时/429/5xx）——openai SDK 异常不继承
  内置 TimeoutError,必须显式枚举;4xx（参数错误/模型不支持/鉴权）不重试,
  立即上抛,不白等
- 空响应:调用成功但结果是空串/纯空白/空 JSON 字面量（LLM 偶发空输出）
  ——与可重试异常同路重试

用法（幂等调用专用:改写/评估/实体提取/问题生成）:
    raw = await llm_call_with_retry(
        messages, llm, temperature=0.0, max_tokens=256,
        response_format={"type": "json_object"},
    )
"""
from __future__ import annotations

import logging
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

# 可重试异常:仅临时性故障（openai SDK 异常体系独立于内置异常,显式枚举）
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    APIConnectionError,   # 连接失败（DNS/握手）
    APITimeoutError,      # 请求/读取超时
    RateLimitError,       # 429 限流（退避正好对症）
    InternalServerError,  # 5xx 服务端错误
)


def _is_bad_result(result: str) -> bool:
    """空响应判定（retry_if_result 语义:返回 True = 需要重试）

    空串/纯空白/空 JSON 字面量（LLM 偶发输出 "{}"）都视为无效——
    后者覆盖问题生成的解析级校验（parse 后为空字典）。
    """
    if not result or not result.strip():
        return True
    return result.strip() in ("{}", "[]", "null")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=10),  # 抖动退避,避免重试风暴
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS)
    | retry_if_result(_is_bad_result),
    before_sleep=before_sleep_log(logger, logging.WARNING),  # 自动记录重试
    reraise=True,  # 最终失败抛原始异常（非 RetryError）
)
async def llm_call_with_retry(
    messages: list[dict], llm: Any, **kwargs: Any,
) -> str:
    """LLM 非流式调用 + 重试兜底（调用方透传 chat 参数）

    kwargs 透传给 llm.chat（temperature/max_tokens/response_format）。
    非幂等的流式回答不重试——见模块 docstring。
    """
    return await llm.chat(messages, **kwargs)
