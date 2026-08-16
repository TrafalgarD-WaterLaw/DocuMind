"""LLM 服务提供者接口"""
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator


class LLMProvider(ABC):
    """大语言模型调用抽象 — 支持流式/非流式"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict | None = None,
        usage_tracker: dict[str, int] | None = None,
    ) -> str:
        """非流式对话，返回完整回复

        response_format: DeepSeek JSON Output（{"type": "json_object"}）——
        约束模型输出合法 JSON，全项目 JSON 场景统一传；实现不支持的
        提供者可忽略（调用方保留解析容错）。
        usage_tracker: 可选 token 用量累计（prompt/completion tokens）。
        """
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        usage_tracker: dict[str, int] | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话，逐 chunk 产出（usage_tracker 累计最终用量）"""
        ...

    @abstractmethod
    def build_messages(
        self, system_prompt: str, user_prompt: str
    ) -> list[dict[str, str]]:
        """构建标准消息格式"""
        ...
