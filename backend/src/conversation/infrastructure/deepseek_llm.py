"""LLM Provider — DeepSeek V4 Flash (OpenAI 兼容接口)

容错与可观测性：
  - 显式超时（connect 10s / read 300s / write 60s / pool 30s）
  - usage_tracker 采集 token 用量（流式末尾 chunk 携带 usage）
"""
from collections.abc import AsyncGenerator

import httpx
from openai import AsyncOpenAI

from interfaces.llm import LLMProvider


class DeepSeekProvider(LLMProvider):
    """DeepSeek V4 Flash 客户端"""

    def __init__(self, api_key: str, base_url: str, model: str = "deepseek-v4-flash"):
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=30.0),
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        usage_tracker: dict[str, int] | None = None,
        response_format: dict | None = None,
    ) -> str:
        completion = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            # DeepSeek JSON Output——约束模型输出合法 JSON（官方接口）。
            # 调用方需保证 prompt 含 "json" 字样 + 样例；返回仍可能为空 content，
            # 调用方保留解析容错（回退语义不变）。
            response_format=response_format,
        )
        if usage_tracker is not None and completion.usage is not None:
            usage_tracker["prompt_tokens"] = completion.usage.prompt_tokens
            usage_tracker["completion_tokens"] = completion.usage.completion_tokens
        return completion.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        usage_tracker: dict[str, int] | None = None,
    ) -> AsyncGenerator[str, None]:
        completion = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in completion:
            # DeepSeek 流式 usage 在最后一个 chunk（choices 为空）
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if usage_tracker is not None:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    usage_tracker["prompt_tokens"] = usage.prompt_tokens
                    usage_tracker["completion_tokens"] = usage.completion_tokens

    def build_messages(
        self, system_prompt: str, user_prompt: str
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
