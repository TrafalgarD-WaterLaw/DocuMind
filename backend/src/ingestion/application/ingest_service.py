# -*- coding: utf-8 -*-
"""ingest 注册表 + 统一入口——数据源接入点（DDD 应用层）

- register / get_ingestor / ingestors: 数据源接入点（类或实例均可注册）
- run: 统一入口——取 ingestor → scan → 契约校验 → build_chunks → load
- 契约校验收口 ingestion.domain.source_contract（领域规则）

CLI 入口在 python -m ingestion（ingestion/__main__.py）。
"""
from __future__ import annotations

import logging
from typing import Any

from ingestion.application.ingest_base import BaseIngestor, Progress
from ingestion.domain.source_contract import validate_source

logger = logging.getLogger(__name__)


# ── 注册表 ───────────────────────────────────────

class IngestorNotFoundError(LookupError):
    """未注册的 ingestor 名称"""


# 名称 → ingestor 类（类注册时 run 时实例化）或实例（直接复用）
_REGISTRY: dict[str, type[BaseIngestor] | BaseIngestor] = {}


def register(name: str, ingestor: type[BaseIngestor] | BaseIngestor) -> None:
    """注册 ingestor——类或实例均可

    类注册: 每次 run/get 按 opts 新建实例（推荐，携带可注入参数）；
    实例注册: 直接复用（测试注入用）。
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"注册名必须为非空字符串: {name!r}")
    _REGISTRY[name] = ingestor
    logger.info("ingestor 已注册: %s（%s）", name, getattr(ingestor, "__name__", type(ingestor).__name__))


def get_ingestor(name: str, **opts) -> BaseIngestor:
    """按名称取 ingestor 实例（类注册时按 opts 实例化）"""
    entry = _REGISTRY.get(name)
    if entry is None:
        raise IngestorNotFoundError(
            f"未注册的 ingestor: {name!r}（已注册: {ingestors()}）"
        )
    if isinstance(entry, type):
        return entry(**opts)
    return entry


def ingestors() -> list[str]:
    """已注册的 ingestor 名称列表（排序）"""
    return sorted(_REGISTRY.keys())


def run(
    name: str,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    progress: Progress | None = None,
    **ctor_opts,
) -> dict[str, Any]:
    """统一 ingest 入口: 取 ingestor → scan → 构建块 → load

    P1-C 强制校验: scan 产物中 source 不合法的数据源被跳过（记入 stats.invalid），
    新数据源接入必须符合 validate_source 规范。

    Args:
        name: 已注册的 ingestor 名
        dry_run: True 时只扫描 + 构建块，不写库（CLI --dry-run 模式）
        limit: 最多处理 N 个合法数据源（小样本验证用，CLI --limit N）
        progress: 入库进度回调 (done, total)，透传给 ingestor.load
        **ctor_opts: 传给 ingestor 构造器的额外参数（类注册时生效，
                如 vector=… / image_index_path=… 测试注入）

    Returns:
        统计 dict: {name, scanned, sources, chunks, invalid, dry_run, loaded}
    """
    ingestor = get_ingestor(name, **ctor_opts)

    raw_list = ingestor.scan()
    valid: list = []
    invalid: list[str] = []
    for raw in raw_list:
        if validate_source(raw.source):
            valid.append(raw)
        else:
            invalid.append(raw.source)
            logger.warning(
                "source 不合法（P1-C 契约），已跳过: %r——须为 {域}-{实体} / "
                "{域}-{实体}#图 / {timestamp}_{file}", raw.source,
            )

    if limit is not None and limit > 0:
        valid = valid[:limit]

    chunks: list[dict] = []
    for raw in valid:
        built = ingestor.build_chunks(raw) or []
        chunks.extend(built)

    stats: dict[str, Any] = {
        "name": name,
        "scanned": len(raw_list),
        "sources": len(valid),
        "chunks": len(chunks),
        "invalid": invalid,
        "dry_run": bool(dry_run),
        "loaded": False,
    }

    if dry_run:
        return stats

    ingestor.load(chunks, progress=progress)
    stats["loaded"] = True
    return stats
