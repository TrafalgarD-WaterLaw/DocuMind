# -*- coding: utf-8 -*-
"""U3 文档指纹测试——重复上传检测映射（文件级读写，tmp 隔离）"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402

from documents.application.hash_index import DocumentHashIndex  # noqa: E402


@pytest.fixture
def index(tmp_path):
    """阶段 2 注入化:每次测试独立实例 + 独立映射文件（天然隔离）"""
    return DocumentHashIndex(tmp_path / "document_hashes.json")


def test_register_and_find(index):
    index.register("abc123", "1786000000_a.pdf")
    assert index.find_by_hash("abc123") == "1786000000_a.pdf"
    assert index.find_by_hash("nope") is None


def test_register_persists_across_reload(index):
    index.register("abc", "src1")
    index._cache = None  # 模拟进程重启（重新懒加载）
    assert index.find_by_hash("abc") == "src1"


def test_remove_by_source(index):
    index.register("h1", "src1")
    index.register("h2", "src1")
    index.register("h3", "src2")
    index.remove_by_source("src1")
    assert index.find_by_hash("h1") is None
    assert index.find_by_hash("h2") is None
    assert index.find_by_hash("h3") == "src2"


def test_missing_file_returns_none(index):
    assert index.find_by_hash("anything") is None
