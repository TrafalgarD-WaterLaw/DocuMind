# -*- coding: utf-8 -*-
"""json_utils 工具测试——extract_string_list（查询分解/实体抽取共用）"""
from core.json_utils import extract_json, extract_json_array, extract_string_list


def test_extract_json_basic():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json("解释文字 {a:1}") is None  # 非法 JSON
    assert extract_json("") is None


def test_extract_json_mixed_text():
    """模型混入解释文字 → 提取 JSON 子串"""
    assert extract_json('回答如下：{"sub_queries": ["x"]} 完毕') == {
        "sub_queries": ["x"],
    }


def test_extract_json_array():
    assert extract_json_array('[1, 2]') == [1, 2]
    assert extract_json_array('前文 [3]') == [3]
    assert extract_json_array("") is None


# ── extract_string_list ───────────────────────────────

def test_extract_string_list_basic():
    raw = '{"sub_queries": ["妇好鸮尊的年代", "司母戊鼎的年代"]}'
    assert extract_string_list(raw, "sub_queries") == [
        "妇好鸮尊的年代", "司母戊鼎的年代",
    ]


def test_extract_string_list_missing_or_invalid():
    """提取失败 / 字段非数组 / 混入非字符串 → None（strict 默认）"""
    assert extract_string_list("叩鼎的纹饰特点", "sub_queries") is None  # 非 JSON
    assert extract_string_list('{"other": 1}', "sub_queries") is None    # 字段缺失
    assert extract_string_list('{"sub_queries": "不是数组"}', "sub_queries") is None
    assert extract_string_list('{"sub_queries": ["a", 1]}', "sub_queries") is None


def test_extract_string_list_cleaning():
    """strip + 长度过滤 + 上限截断"""
    raw = '{"entities": [" 妇好鸮尊 ", "x", "", "殷墟", "商代", "西周"]}'
    assert extract_string_list(raw, "entities", min_len=2, max_items=3) == [
        "妇好鸮尊", "殷墟", "商代",
    ]
    # max_len 过滤
    raw2 = '{"entities": ["' + "长" * 40 + '", "正常实体"]}'
    assert extract_string_list(raw2, "entities", min_len=2, max_len=30) == ["正常实体"]


def test_extract_string_list_strict_false_skips_invalid():
    """strict=False:混入非字符串 → 跳过该元素（保部分结果）"""
    raw = '{"entities": ["妇好鸮尊", 42, "殷墟"]}'
    assert extract_string_list(raw, "entities", strict=False) == ["妇好鸮尊", "殷墟"]
    # strict=True 时同样输入 → None
    assert extract_string_list(raw, "entities") is None


def test_extract_string_list_empty_result():
    """清理后全空 → 返回空列表（非 None——由调用方判定业务语义）"""
    raw = '{"sub_queries": ["", "  "]}'
    assert extract_string_list(raw, "sub_queries", min_len=4) == []
