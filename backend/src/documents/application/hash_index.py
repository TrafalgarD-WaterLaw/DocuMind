# -*- coding: utf-8 -*-
"""文档指纹映射——文件 SHA-256 → source（U3 重复上传检测）

存储: src/data/document_hashes.json（与 image_index 同级，懒加载 + 实例缓存）
用途: POST /api/upload 时同内容文件返回 409 提示（可 replace 覆盖）；
     删除文档时同步清理映射。

实例状态（容器装配 core.di.container.document_hashes）；模块函数为无状态
委托入口;测试可直接构造 DocumentHashIndex(tmp_path) 注入。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

HASHES_PATH = Path(__file__).resolve().parent.parent / "data" / "document_hashes.json"


class DocumentHashIndex:
    """文档指纹索引——SHA-256 → source（文件由本服务独占读写）"""

    def __init__(self, hashes_path: Path | None = None) -> None:
        self._path = hashes_path or HASHES_PATH
        self._cache: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        """懒加载 + 实例缓存（文件由本服务独占读写）"""
        if self._cache is None:
            try:
                if self._path.exists():
                    self._cache = json.loads(self._path.read_text(encoding="utf-8"))
                else:
                    self._cache = {}
            except Exception as e:
                logger.warning(f"document_hashes 加载失败: {e}")
                self._cache = {}
        return self._cache

    def find_by_hash(self, sha256: str) -> str | None:
        """文件哈希 → 已入库 source；无返回 None"""
        return self._load().get(sha256)

    def register(self, sha256: str, source: str) -> None:
        """入库成功后登记指纹（重复上传检测用）"""
        data = self._load()
        data[sha256] = source
        self._persist(data)

    def remove_by_source(self, source: str) -> None:
        """删除文档时清理指纹映射（同 source 可能对应多个哈希——同名覆盖）"""
        data = self._load()
        stale = [h for h, s in data.items() if s == source]
        for h in stale:
            del data[h]
        if stale:
            self._persist(data)

    def _persist(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )


def _default_index() -> DocumentHashIndex:
    """组合根装配的默认索引——缓存状态归容器,委托函数无全局状态"""
    from core.di import container

    return container.document_hashes


def find_by_hash(sha256: str) -> str | None:
    return _default_index().find_by_hash(sha256)


def register(sha256: str, source: str) -> None:
    _default_index().register(sha256, source)


def remove_by_source(source: str) -> None:
    _default_index().remove_by_source(source)
