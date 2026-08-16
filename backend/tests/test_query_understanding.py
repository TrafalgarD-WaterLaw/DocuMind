# -*- coding: utf-8 -*-
"""查询理解测试——改写（代词消解）/ 分解 / 查询计划组装"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conversation.application.query_understanding import build_query_plan, decompose_query, rewrite_query  # noqa: E402


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = 0
        self.fail = False
        self.last_user = ""

    def build_messages(self, system: str, user: str):
        self.last_user = user
        return [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("LLM 挂了")
        return self.response


# ── rewrite_query（多轮代词消解）──────────────────────────

async def test_rewrite_with_pronoun():
    """有上文时 LLM 消解代词 → 独立检索词"""
    llm = _FakeLLM("妇好鸮尊 出土于哪里")
    history = [
        {"role": "user", "content": "妇好鸮尊是什么朝代?"},
        {"role": "assistant", "content": "商代晚期。"},
        {"role": "user", "content": "它出土于哪里?"},  # 当前问题也在列表里
    ]
    out = await rewrite_query("它出土于哪里?", history, llm)
    assert out == "妇好鸮尊 出土于哪里"
    # 传给 LLM 的上文是上一轮用户问题(排除当前问题);当前问题作 {query} 字段也在消息里
    assert "妇好鸮尊是什么朝代?" in llm.last_user


async def test_rewrite_no_history():
    """无历史 → 原样返回（零成本,不调 LLM）"""
    llm = _FakeLLM("x")
    assert await rewrite_query("妇好鸮尊是什么朝代", None, llm) == "妇好鸮尊是什么朝代"
    assert llm.calls == 0


async def test_rewrite_history_only_current():
    """历史只有当前问题 → 无上文可消解,原样返回"""
    llm = _FakeLLM("x")
    history = [{"role": "user", "content": "它出土于哪里?"}]
    assert await rewrite_query("它出土于哪里?", history, llm) == "它出土于哪里?"
    assert llm.calls == 0


async def test_rewrite_fallback_on_error():
    """LLM 失败 → 回退拼接（上轮前 50 字 + 当前问题）"""
    llm = _FakeLLM("")
    llm.fail = True
    history = [{"role": "user", "content": "妇好鸮尊是什么朝代?"},
               {"role": "user", "content": "它出土于哪里?"}]
    out = await rewrite_query("它出土于哪里?", history, llm)
    assert out.startswith("妇好鸮尊是什么朝代?")
    assert "它出土于哪里?" in out


# ── build_query_plan（拆解/改写两分支组装）────────────────

async def test_build_plan_compound():
    """复合问题 → N 个子查询(并行改写) + eval_base=原始问题"""
    llm = _FakeLLM('{"sub_queries": ["妇好鸮尊的年代", "司母戊鼎的年代"]}')
    plan = await build_query_plan("妇好鸮尊和司母戊鼎哪个更早", None, llm)
    assert len(plan.retrieval_queries) == 2
    assert plan.eval_base == "妇好鸮尊和司母戊鼎哪个更早"


async def test_build_plan_simple():
    """普通问题(有历史) → 单改写词 + eval_base=改写词"""
    llm = _FakeLLM("妇好鸮尊 铸造工艺")
    history = [{"role": "user", "content": "妇好鸮尊是什么朝代?"},
               {"role": "user", "content": "妇好鸮尊是怎么铸造的"}]
    plan = await build_query_plan("妇好鸮尊是怎么铸造的", history, llm)
    assert plan.retrieval_queries == ["妇好鸮尊 铸造工艺"]
    assert plan.eval_base == "妇好鸮尊 铸造工艺"


async def test_build_plan_no_history_returns_query():
    """无历史 → 改写短路,检索词 = 原始问题（零成本）"""
    llm = _FakeLLM("x")
    plan = await build_query_plan("妇好鸮尊是怎么铸造的", None, llm)
    assert plan.retrieval_queries == ["妇好鸮尊是怎么铸造的"]
    assert plan.eval_base == "妇好鸮尊是怎么铸造的"


# ── decompose_query（含 LLM 调用;结构解析已由 test_json_utils 覆盖）──

async def test_compound_query_decomposed():
    llm = _FakeLLM('{"sub_queries": ["妇好鸮尊的年代", "司母戊鼎的年代"]}')
    subs = await decompose_query("妇好鸮尊和司母戊鼎哪个更早", llm)
    assert subs == ["妇好鸮尊的年代", "司母戊鼎的年代"]
    assert llm.calls == 1


async def test_single_query_not_decomposed():
    """单实体问题必须原样返回 → 不分解（零变化）"""
    llm = _FakeLLM("叩鼎的纹饰特点")
    assert await decompose_query("叩鼎的纹饰特点", llm) is None


async def test_llm_error_falls_back():
    llm = _FakeLLM("")
    llm.fail = True
    assert await decompose_query("任意问题", llm) is None


async def test_empty_query():
    assert await decompose_query("", _FakeLLM("x")) is None
    assert await decompose_query("妇好鸮尊", None) is None


async def test_too_many_sub_queries_rejected():
    """LLM 输出超过 3 个子查询 → 保守回退（宁可不拆）"""
    raw = '{"sub_queries": ["甲" * 4, "乙" * 4, "丙" * 4, "丁" * 4]}'
    assert await decompose_query("复合问题", _FakeLLM(raw)) is None


async def test_empty_sub_queries_rejected():
    """LLM 输出空子查询数组 → 保守回退"""
    assert await decompose_query("复合问题", _FakeLLM('{"sub_queries": []}')) is None
