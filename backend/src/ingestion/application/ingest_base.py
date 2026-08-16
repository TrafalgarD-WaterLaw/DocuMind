# -*- coding: utf-8 -*-
"""P2 统一 ingest 管道——BaseIngestor 抽象基类（数据契约治理）

设计文档: docs/superpowers/specs/2026-08-07-architecture-governance-design.md「三、P2」

新数据源接入范式（示例见 examples/porcelain_ingestor.py）:
    1. scan()      扫描数据源 → RawSource 列表
    2. build_chunks()  原始数据 → 入库块（dict 含 chunk_id/content/metadata）
    3. load()      入 vector + mark_bm25_dirty + 更新 image_index 映射表（幂等）

数据契约（P1-A/P1-C）由本基类强制:
    - 块 metadata 必须带 source（缺省跳过），chunk_type 缺失时按 text 默认补齐
    - source 命名须符合 registry.validate_source（{域}-{实体} / {域}-{实体}#图 /
      {timestamp}_{file}），run() 入口统一校验，非法数据源直接跳过
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 与 scripts/import_porcelain_chroma.py 一致的入库批大小
LOAD_BATCH_SIZE = 20

# 进度回调: (done: int, total: int) -> None
Progress = Callable[[int, int], None]


@dataclass
class RawSource:
    """原始数据源——scan() 的产物

    Attributes:
        source: 数据源名，须符合 P1-C 命名规范
                （{域}-{实体} / {域}-{实体}#图 / {timestamp}_{file}，
                由 registry.validate_source 强制校验）
        text: 原始文本内容（文本型数据源）
        path: 原始文件路径（PDF/Excel 等，可选）
        images: 图片映射 {图片名: 相对路径}（可选，路径不含 /api/images/ 前缀，
                与 scripts/import_dataset_images.py 映射表约定一致）
    """

    source: str
    text: str = ""
    path: str | None = None
    images: dict[str, str] = field(default_factory=dict)


class BaseIngestor(ABC):
    """ingestor 抽象基类——统一「扫描 → 构建 → 入库」三步

    Args:
        vector: 向量存储（默认 container.vector，测试/演练可注入临时 Chroma）
        image_index_path: image_index.json 路径（默认 src/data/image_index.json，
                与 multimodal.image_index.INDEX_PATH 一致；测试可注入临时路径）
    """

    def __init__(
        self,
        vector: Any = None,
        image_index_path: str | Path | None = None,
    ):
        self._vector = vector
        if image_index_path is None:
            # src/services/ingest/base.py → src/data/image_index.json
            # （与 services.multimodal.image_index.INDEX_PATH 一致）
            image_index_path = Path(__file__).resolve().parents[2] / "data" / "image_index.json"
        self.image_index_path = Path(image_index_path)
        from multimodal.image_index import FileBackedImageIndex

        self._image_index = FileBackedImageIndex(self.image_index_path)

    # ── 子类必须实现 ───────────────────────────────

    @abstractmethod
    def scan(self) -> list[RawSource]:
        """扫描数据源，返回 RawSource 列表"""

    @abstractmethod
    def build_chunks(self, raw: RawSource) -> list[dict]:
        """原始数据 → 入库块列表

        每块为 dict: {chunk_id, content, metadata}
        metadata 必须带 source；chunk_type（text/image）缺失时由 load 按 text 补齐。
        图片块: source 用 {域}-{实体}#图 后缀，metadata 带 image_path
        （/api/images/ 前缀完整路径，见 P1-B）。
        """

    # ── 公共能力 ──────────────────────────────────

    @property
    def vector(self) -> Any:
        """向量存储——默认应用容器（container.vector），测试可注入临时库"""
        if self._vector is None:
            from core.di import container

            self._vector = container.vector
        return self._vector

    def load(
        self,
        chunks: list[dict],
        progress: Progress | None = None,
    ) -> int:
        """入库：写 vector + 标记 BM25 过期 + 更新 image_index 映射表

        幂等策略（先删后写）: 按 source 先清掉旧块再写入，同一批块重复
        load 不会产生重复块——与 scripts/import_porcelain_chroma.py 的
        先删后写模式一致。

        Args:
            chunks: build_chunks 产物（dict 含 chunk_id/content/metadata）
            progress: 可选进度回调 (done, total)，每批入库后调用一次

        Returns:
            实际入库块数（0 表示无块可入）
        """
        valid: list[dict] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                logger.warning("跳过非法块（非 dict）: %r", chunk)
                continue
            metadata = chunk.get("metadata")
            if not isinstance(metadata, dict):
                logger.warning("跳过无 metadata 的块: %s", chunk.get("chunk_id", "?"))
                continue
            source = metadata.get("source", "")
            if not source:
                logger.warning("跳过无 source 的块（P1-C 契约）: %s", chunk.get("chunk_id", "?"))
                continue
            if not metadata.get("chunk_type"):
                # P1-A 契约: 新入库块统一补齐 chunk_type（文本块标记为 text）
                metadata["chunk_type"] = "text"
                logger.info("块缺失 chunk_type，已按 text 补齐: source=%s", source)
            if not chunk.get("chunk_id"):
                chunk["chunk_id"] = str(uuid.uuid4())
            valid.append(chunk)

        if not valid:
            logger.warning("load 收到 0 个合法块，跳过入库")
            return 0

        vector = self.vector

        # 1. 幂等: 先删后写——按 source 清掉旧块
        sources: set[str] = set()
        for chunk in valid:
            sources.add(chunk["metadata"]["source"])
        for source in sorted(sources):
            vector.delete(source)

        # 2. 分批入库 + 进度回调
        total = len(valid)
        done = 0
        for i in range(0, total, LOAD_BATCH_SIZE):
            vector.add_documents(valid[i : i + LOAD_BATCH_SIZE])
            done = min(i + LOAD_BATCH_SIZE, total)
            if progress:
                progress(done, total)

        # 3. BM25 失效标记——下次检索前重建（见 core/di.AppContainer.mark_bm25_dirty）
        from core.di import container

        container.mark_bm25_dirty()

        # 4. 更新 image_index 映射表（source → {primary, images}，幂等合并）
        self._update_image_index(valid)

        logger.info("ingest load 完成: %d 块（%d 个 source）", total, len(sources))
        return total

    # ── 内部实现 ──────────────────────────────────

    def _update_image_index(self, chunks: list[dict]) -> int:
        """从块 metadata 推导并合并进 image_index.json

        只登记 /api/images/ 前缀的图片块（P1-B 规范）——上传文档的
        /api/uploads/ 图片由 uploads 目录服务，不进映射表。
        合并是幂等的: 同一批块重复 load，映射条目被覆盖为相同内容。
        写表实现已收口 multimodal.image_index.FileBackedImageIndex
        （与 assets 门面同实现——映射表只此一处写逻辑;本类持有注入实例）。
        """
        entries: dict[str, list[str]] = {}
        for chunk in chunks:
            metadata = chunk.get("metadata", {})
            image_path = metadata.get("image_path", "")
            if not str(image_path).startswith("/api/images/"):
                continue
            source = metadata.get("source", "")
            rel = str(image_path)[len("/api/images/") :]
            if source and rel:
                entries.setdefault(source, []).append(rel)

        for source, files in entries.items():
            self._image_index.upsert(source, files)
        if entries:
            logger.info("image_index 映射表更新: %d 个 source", len(entries))
        return len(entries)
