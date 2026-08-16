# -*- coding: utf-8 -*-
"""U2 文档实体抽取测试——抽取/解析/失败回退 + entity 路数组匹配"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402

from retrieval.entity_anchor import _parse_entities, extract_entities  # noqa: E402


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.fail = False

    def build_messages(self, system: str, user: str):
        return [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    async def chat(self, messages, **kwargs):
        if self.fail:
            raise RuntimeError("LLM 挂了")
        return self.response


# ── _parse_entities ────────────────────────────────────────────────

def test_parse_entities():
    assert _parse_entities('{"entities": ["妇好鸮尊", "殷墟", "商代"]}') == \
        ["妇好鸮尊", "殷墟", "商代"]


def test_parse_empty():
    assert _parse_entities('{"entities": []}') == []
    assert _parse_entities("") == []
    assert _parse_entities("无实体") == []


def test_parse_caps_at_5():
    import json
    raw = json.dumps({"entities": ["aaa"] * 8})
    assert len( _parse_entities(raw)) == 5


def test_parse_filters_invalid():
    assert _parse_entities('{"entities": ["妇好鸮尊", "x", "", "   "] }') == ["妇好鸮尊"]


def test_parse_bad_json():
    assert _parse_entities("不是 JSON") == []
    assert _parse_entities('{"other": [1]}') == []


# ── extract_entities ──────────────────────────────────────

async def test_extract_ok():
    llm = _FakeLLM('{"entities": ["妇好鸮尊", "殷墟"]}')
    assert await extract_entities("妇好鸮尊出土于殷墟妇好墓", llm) == \
        ["妇好鸮尊", "殷墟"]


async def test_extract_llm_error_returns_empty():
    llm = _FakeLLM("")
    llm.fail = True
    assert await extract_entities("文本", llm) == []


async def test_extract_empty_text():
    assert await extract_entities("", _FakeLLM("x")) == []
    assert await extract_entities("文本", None) == []
