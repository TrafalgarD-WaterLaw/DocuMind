"""DeepSeek provider usage 采集与 timeout 测试（mock OpenAI 客户端）"""
from types import SimpleNamespace

import pytest

from conversation.infrastructure.deepseek_llm import DeepSeekProvider


class _FakeStream:
    """模拟 OpenAI 流式 chunk：末尾 chunk 携带 usage"""

    def __init__(self):
        self._chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你"))], usage=None),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好"))], usage=None),
            SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)),
        ]

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk


async def test_chat_stream_usage_collected(monkeypatch):
    provider = DeepSeekProvider(api_key="k", base_url="http://x")

    class FakeCreate:
        async def __call__(self, **kwargs):
            return _FakeStream()

    monkeypatch.setattr(provider.client.chat.completions, "create", FakeCreate())

    usage: dict = {}
    text = ""
    async for chunk in provider.chat_stream([{"role": "user", "content": "hi"}], usage_tracker=usage):
        text += chunk
    assert text == "你好"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5}


async def test_chat_usage_collected(monkeypatch):
    provider = DeepSeekProvider(api_key="k", base_url="http://x")

    class FakeCreate:
        async def __call__(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="答"))],
                usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
            )

    monkeypatch.setattr(provider.client.chat.completions, "create", FakeCreate())

    usage: dict = {}
    text = await provider.chat([{"role": "user", "content": "hi"}], usage_tracker=usage)
    assert text == "答"
    assert usage == {"prompt_tokens": 7, "completion_tokens": 3}


def test_client_has_timeout():
    provider = DeepSeekProvider(api_key="k", base_url="http://x")
    assert provider.client._client._timeout.connect == 10.0
    assert provider.client._client._timeout.read == 300.0
