# -*- coding: utf-8 -*-
"""S5 全量重切（蓝图第 2 步：结构感知切分 + 父子分块全库生效）

执行前必须先备份（外部命令）:
  cp -r src/data/chroma src/data/chroma.bak_pre_rechunk

两类数据的重切策略（数据源可复现，source 名不变）:
  1. 上传文档（source 以 .pdf 结尾）:
     重新 Docling 解析 → chunk_document（结构感知：段落/标题/表格独立块 + 父块）
     删除旧文本块 → 入新子块+父块；图片块保留原样（chunk_type=image 不动）
  2. 静态数据（瓷器/青铜/河南）:
     子块内容与 embedding 不变（已验证数据），只做父子层重组——
     父块 = (source, artifact) 分组拼接（上限 1500 字截断），子块 metadata
     补 parent_id / block_type（Chroma update 仅 metadata，不重 embedding）

幂等: 重跑前先删除本脚本产出的父块（block_type=parent），子块 metadata 覆盖写。
用法: python scripts/rechunk_all.py [--dry-run]
"""
from __future__ import annotations

import logging
import sys
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.config import settings  # noqa: E402
from core.di import container  # noqa: E402
from ingestion.infrastructure.chunker import chunk_document  # noqa: E402

logger = logging.getLogger(__name__)

MAX_PARENT_CHARS = 1500
TRUNC_NOTE = "\n…（内容超出父块上限，已截断）"
BATCH = 1000


def _group_key(meta: dict) -> tuple:
    """父块分组键 = (source, artifact)；无 artifact 时仅 (source)"""
    artifact = meta.get("artifact") or ""
    return (meta.get("source", ""), artifact)


def _make_parent_block(source: str, group: list[dict], parent_id: str) -> dict:
    """分组块流 → 父块（拼接 ≤1500 截断，附截断注释）"""
    content = "\n".join(d["content"] for d in group)
    if len(content) > MAX_PARENT_CHARS:
        keep = MAX_PARENT_CHARS - len(TRUNC_NOTE)
        content = content[:keep] + TRUNC_NOTE
    return {
        "chunk_id": parent_id,
        "content": content,
        "metadata": {
            "source": source,
            "chunk_type": "text",
            "block_type": "parent",
            "is_parent": True,
        },
    }


def _chunk_batches(items: list[dict], size: int = BATCH):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def rechunk(dry_run: bool = False) -> dict:
    vector = container.vector
    all_docs = vector.get_all_documents()
    stats = {"total": len(all_docs), "pdf_chunks": 0, "pdf_children": 0,
             "pdf_parents": 0, "static_parents": 0, "static_updated": 0,
             "old_parents_deleted": 0}

    # ── 0. 幂等清理：删除本脚本旧产出的父块（block_type=parent）──
    old_parents = [
        d["id"] for d in all_docs
        if d["metadata"].get("block_type") == "parent"
    ]
    if old_parents:
        stats["old_parents_deleted"] = len(old_parents)
        if not dry_run:
            for i in range(0, len(old_parents), BATCH):
                vector.collection.delete(ids=old_parents[i:i + BATCH])
        logger.info("幂等清理: 删除旧父块 %d 个", len(old_parents))

    texts = [d for d in all_docs if d["id"] not in old_parents]
    pdf_docs = [d for d in texts if d["metadata"].get("source", "").endswith(".pdf")]
    static_docs = [
        d for d in texts
        if not d["metadata"].get("source", "").endswith("#图")
        and not d["metadata"].get("source", "").endswith(".pdf")
    ]
    image_docs = [
        d for d in texts if d["metadata"].get("source", "").endswith("#图")
    ]
    stats["pdf_chunks"] = len(pdf_docs)
    logger.info("分类: PDF 文本块 %d | 静态文本块 %d | 图片块 %d（保留）",
                len(pdf_docs), len(static_docs), len(image_docs))

    # ── 1. 上传文档：重新解析 + 结构感知切分 ──
    pdf_sources = sorted({d["metadata"]["source"] for d in pdf_docs})
    for source in pdf_sources:
        pdf_path = Path(settings.upload_dir) / source
        if not pdf_path.exists():
            logger.warning("PDF 源文件缺失，跳过重切: %s", pdf_path)
            continue
        # Docling 优先，PyPDF 回退（同上传管线 _get_parser）
        from ingestion.interfaces.upload_routes import _get_parser
        parsed = _get_parser().parse(str(pdf_path))
        result = chunk_document(parsed, source)
        stats["pdf_children"] += len(result.children)
        stats["pdf_parents"] += len(result.parents)
        if dry_run:
            logger.info("[dry-run] %s: 子块 %d + 父块 %d", source,
                        len(result.children), len(result.parents))
            continue
        # 删除该 source 旧文本块（图片块 chunk_type=image 保留原样）
        stale_ids = [
            d["id"] for d in pdf_docs if d["metadata"]["source"] == source
            and d["metadata"].get("chunk_type") != "image"
        ]
        if stale_ids:
            for i in range(0, len(stale_ids), BATCH):
                vector.collection.delete(ids=stale_ids[i:i + BATCH])
        # 入新块（子块 + 父块）
        for batch in _chunk_batches(result.children + result.parents):
            vector.add_documents(batch)
        logger.info("%s: 重切完成（子块 %d + 父块 %d）", source,
                    len(result.children), len(result.parents))

    # ── 2. 静态数据：父子层重组（子块内容/embedding 不变）──
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for d in static_docs:
        groups[_group_key(d["metadata"])].append(d)

    parent_blocks: list[dict] = []
    child_updates: list[tuple[str, dict]] = []  # (id, new_metadata)
    for key, group in sorted(groups.items()):
        parent_id = str(uuid.uuid4())
        parent_blocks.append(_make_parent_block(key[0], group, parent_id))
        for d in group:
            new_meta = dict(d["metadata"])
            new_meta["parent_id"] = parent_id
            new_meta["block_type"] = "text"
            child_updates.append((d["id"], new_meta))
        stats["static_parents"] += 1
        stats["static_updated"] += len(group)

    if dry_run:
        logger.info("[dry-run] 静态父块 %d 个 / 子块更新 %d 个",
                    stats["static_parents"], stats["static_updated"])
        return stats

    for batch in _chunk_batches(parent_blocks):
        vector.add_documents(batch)
    for i in range(0, len(child_updates), BATCH):
        ids = [u[0] for u in child_updates[i:i + BATCH]]
        metas = [u[1] for u in child_updates[i:i + BATCH]]
        vector.collection.update(ids=ids, metadatas=metas)

    container.mark_bm25_dirty()
    logger.info("重切完成: 父块 %d（静态 %d + PDF %d）| 子块更新 %d | PDF 子块 %d",
                stats["static_parents"] + stats["pdf_parents"],
                stats["static_parents"], stats["pdf_parents"],
                stats["static_updated"], stats["pdf_children"])
    return stats


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info("=== dry-run 模式（不写库）===")
    stats = rechunk(dry_run=dry_run)
    print(json_dump(stats))


def json_dump(stats: dict) -> str:
    import json
    return json.dumps(stats, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    main()
