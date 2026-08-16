# -*- coding: utf-8 -*-
"""ingest 管道 CLI 入口

用法:
  python -m ingestion                        # 打印已注册 ingestor 列表
  python -m ingestion --source porcelain     # 全量入库（幂等，先删后写）
  python -m ingestion --source porcelain --dry-run --limit 5
                                             # 小样本演练：扫描 + 构建块，不写库

--dry-run 模式不触碰任何数据（不写 Chroma / image_index / BM25 标记）；
未注册的 --source 打印可用列表并退出码 1。
"""
from __future__ import annotations

import argparse
import sys

from .application import ingest_service


def _print_stats(stats: dict) -> None:
    print(f"source: {stats['name']}")
    mode = "dry-run（不写库）" if stats["dry_run"] else "入库（先删后写，幂等）"
    print(f"模式: {mode}")
    print(f"扫描数据源: {stats['scanned']} 个")
    if stats["invalid"]:
        print(f"  （P1-C 契约过滤 {len(stats['invalid'])} 个非法 source: "
              f"{', '.join(stats['invalid'])})")
    print(f"合法数据源: {stats['sources']} 个")
    print(f"构建块: {stats['chunks']} 个")
    if stats["dry_run"]:
        print("dry-run 结束——未写入任何数据")
    else:
        print(f"入库完成: loaded={stats['loaded']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ingestion",
        description="统一 ingest 管道——扫描数据源 → 构建块 → 入库（幂等，数据契约强制校验）",
    )
    parser.add_argument("--source", metavar="NAME",
                        help="数据源名（须已注册；缺省打印可用列表）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只扫描 + 构建块，不写库（打印统计）")
    parser.add_argument("--limit", type=int, default=None,
                        help="最多处理 N 个合法数据源（小样本验证）")
    args = parser.parse_args(argv)

    # 加载 ingestors 包——其中的 register(...) 默认处于注释状态，解开即注册生效
    try:
        from .infrastructure import ingestors  # noqa: F401
    except Exception as exc:  # pragma: no cover - 示例包加载失败不阻断 CLI
        print(f"[warn] 加载 ingestion.infrastructure.ingestors 失败: {exc}")

    names = ingest_service.ingestors()

    if not args.source:
        print("已注册的 ingestor（--source 可选项）:")
        if names:
            for n in names:
                print(f"  {n}")
        else:
            print("  （暂无——接入范式见 ingestion/infrastructure/ingestors/porcelain_ingestor.py）")
        return 0

    if args.source not in names:
        print(f"未注册的 source: {args.source!r}，可用列表:")
        for n in names or ["（暂无——见 ingestion/infrastructure/ingestors/）"]:
            print(f"  {n}")
        return 1

    stats = ingest_service.run(args.source, dry_run=args.dry_run, limit=args.limit)
    _print_stats(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
