"""LLM 非流式调用重试测试（tenacity @retry:异常分类 + 空响应校验）"""
import pytest

from core.llm_retry import llm_call_with_retry


class _FlakyLLM:
    """前 fail_times 次抛 exc(或返回 bad_resp),之后返回 good"""

    def __init__(self, fail_times=0, exc=None, bad_resp="", good="ok"):
        self.fail_times = fail_times
        self.exc = exc
        self.bad_resp = bad_resp
        self.good = good
        self.calls = 0
        self.last_kwargs = None

    async def chat(self, messages, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.calls <= self.fail_times:
            if self.exc is not None:
                raise self.exc
            return self.bad_resp
        return self.good


# ── 可重试异常（临时故障:网络/超时/5xx/429）────────────────

async def test_retry_succeeds_after_timeouts():
    """临时故障退避重试 → 恢复后成功;kwargs 透传"""
    llm = _FlakyLLM(fail_times=2, exc=TimeoutError("网络超时"))
    result = await llm_call_with_retry(
        [{"role": "user", "content": "hi"}], llm,
        temperature=0.0, max_tokens=128,
    )
    assert result == "ok"
    assert llm.calls == 3
    assert llm.last_kwargs == {"temperature": 0.0, "max_tokens": 128}


async def test_retry_exhausted_raises_original():
    """耗尽抛原始异常（reraise=True,非 RetryError）"""
    llm = _FlakyLLM(fail_times=99, exc=TimeoutError("boom"))
    with pytest.raises(TimeoutError):
        await llm_call_with_retry([], llm)
    assert llm.calls == 3


async def test_non_retryable_exception_immediate():
    """4xx 类错误（参数错误/模型不支持）不重试——立即上抛,不白等"""
    llm = _FlakyLLM(fail_times=99, exc=ValueError("模型不支持该参数"))
    with pytest.raises(ValueError):
        await llm_call_with_retry([], llm)
    assert llm.calls == 1  # 只调一次


async def test_first_try_success_no_delay():
    llm = _FlakyLLM(fail_times=0)
    assert await llm_call_with_retry([], llm) == "ok"
    assert llm.calls == 1


# ── 空响应（调用成功但结果无效）同样触发重试 ───────────────

async def test_empty_response_retried():
    """空串 → 判无效重试 → 恢复后成功"""
    llm = _FlakyLLM(fail_times=2, bad_resp="", good='{"results": [1]}')
    result = await llm_call_with_retry([], llm)
    assert result == '{"results": [1]}'
    assert llm.calls == 3


async def test_empty_json_literal_retried():
    """空 JSON 字面量（"{}"）→ 判无效重试（LLM 偶发输出空 JSON）"""
    llm = _FlakyLLM(fail_times=1, bad_resp="{}", good="好")
    result = await llm_call_with_retry([], llm)
    assert result == "好"
    assert llm.calls == 2


async def test_blank_result_retried_until_exhausted():
    """纯空白 → 始终无效 → 耗尽抛 RetryError（装饰器内置空响应判定）"""
    from tenacity import RetryError

    llm = _FlakyLLM(fail_times=99, bad_resp="   ")
    with pytest.raises(RetryError):
        await llm_call_with_retry([], llm)
    assert llm.calls == 3
