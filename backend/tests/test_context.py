# -*- coding: utf-8 -*-
"""上下文组装测试——块级噪声过滤 + 父子分块替换（蓝图第 2 步）"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402

from retrieval.context import filter_noise_chunks, resolve_parent_chunks  # noqa: E402


def _doc(score, paths=None, rank=None, **extra):
    d = {"id": f"id-{score}-{rank}", "content": f"内容{score}",
         "source": "青铜-叩鼎", "score": score, "paths": paths or [], "metadata": {}}
    if rank is not None:
        d["rank"] = rank
    d.update(extra)
    return d


# ── filter_noise_chunks ─────────────────────────────────────

def test_multipath_high_score_kept():
    docs = [_doc(0.0664, ["semantic", "question", "bm25"]), _doc(0.0154, ["bm25"])]
    assert filter_noise_chunks(docs) == docs


def test_low_score_single_path_beyond_rank4_dropped():
    """6 个低分单票块：前 4（排名保护）保留，后 2 被裁"""
    docs = [
        _doc(0.0167, ["bm25"], rank=1),
        _doc(0.0164, ["question"], rank=2),
        _doc(0.0161, ["semantic"], rank=3),
        _doc(0.0159, ["bm25"], rank=4),
        _doc(0.0154, ["bm25"], rank=5),
        _doc(0.0143, ["bm25"], rank=6),
    ]
    kept = filter_noise_chunks(docs)
    assert len(kept) == 4
    assert kept[0]["rank"] == 1  # 排序保持


def test_top4_kept_even_low_score():
    """排名 ≤ 4 的块即使单票低分也保留（强相关区保护）"""
    docs = [
        _doc(0.0167, ["bm25"], rank=1),
        _doc(0.0164, ["question"], rank=2),
        _doc(0.0161, ["semantic"], rank=3),
        _doc(0.0159, ["bm25"], rank=4),
        _doc(0.0145, ["bm25"], rank=5),
    ]
    kept = filter_noise_chunks(docs)
    assert len(kept) == 4


def test_graph_anchor_always_kept():
    """图谱锚定块分数 0.0 单票也必须保留（关系证据强信号）"""
    docs = [_doc(0.0, ["graph"], rank=6, graph_anchor={"entity": "叩鼎"})]
    assert filter_noise_chunks(docs) == docs


def test_entity_path_always_kept():
    """实体锚定路（器名精确匹配）分数低也不裁"""
    docs = [_doc(0.0143, ["entity"], rank=5)]
    assert filter_noise_chunks(docs) == docs


def test_image_chunk_always_kept():
    """图片块（chunk_type=image，语义直检通道）单票低分也不裁——
    图注级精确命中的强信号，被裁会破坏图片块直检回归（实测发现）"""
    docs = [{
        "id": "img-1", "content": "【图片·图1】龙字写法",
        "source": "河南博物院-妇好墓玉龙#图",
        "score": 0.0143, "paths": ["semantic"],
        "metadata": {"chunk_type": "image"},
    }]
    assert filter_noise_chunks(docs) == docs


def test_no_score_docs_kept():
    """同步 VectorStore 兼容分支（无 score/paths）原样保留"""
    docs = [{"id": "x", "content": "c", "source": "s", "metadata": {}}]
    assert filter_noise_chunks(docs) == docs


# ── resolve_parent_chunks ───────────────────────────────────

def _child(parent_id=None):
    return {"id": "child-1", "content": "子块内容", "source": "青铜-叩鼎",
            "score": 0.03, "paths": ["semantic"],
            "metadata": {"parent_id": parent_id} if parent_id else {}}


class _FakeStore:
    def __init__(self, parents: dict):
        self.parents = parents

    def get_by_ids(self, ids):
        return [self.parents[i] for i in ids if i in self.parents]


def test_child_replaced_by_parent():
    parent = {"id": "parent-1", "content": "父块完整内容" * 20, "metadata": {"is_parent": True}}
    store = _FakeStore({"parent-1": parent})
    out = resolve_parent_chunks([_child("parent-1")], store)
    assert out[0]["content"] == parent["content"]
    assert out[0]["id"] == "parent-1"
    assert out[0]["is_parent"] is True
    assert out[0]["score"] == 0.03  # 排序信息保留


def test_missing_parent_falls_back_to_child():
    store = _FakeStore({})
    out = resolve_parent_chunks([_child("parent-missing")], store)
    assert out[0]["content"] == "子块内容"
    assert out[0]["id"] == "child-1"


def test_no_parent_id_kept_asis():
    store = _FakeStore({})
    docs = [_child(), {"id": "img-1", "content": "图", "metadata": {"chunk_type": "image"}}]
    out = resolve_parent_chunks(docs, store)
    assert out == docs


def test_doc_store_none_kept_asis():
    docs = [_child("parent-1")]
    assert resolve_parent_chunks(docs, None) == docs


def test_store_error_falls_back():
    class _BadStore:
        def get_by_ids(self, ids):
            raise RuntimeError("boom")

    out = resolve_parent_chunks([_child("parent-1")], _BadStore())
    assert out[0]["content"] == "子块内容"


def test_order_preserved_with_mixed():
    """子块替换后保持原排序，混合块（子块+无 parent）不乱序"""
    parent = {"id": "p1", "content": "父块", "metadata": {}}
    store = _FakeStore({"p1": parent})
    docs = [_child("p1"), _doc(0.05, ["bm25"]), _child()]
    out = resolve_parent_chunks(docs, store)
    assert out[0]["id"] == "p1"
    assert out[1]["id"].startswith("id-")
    assert out[2]["id"] == "child-1"
