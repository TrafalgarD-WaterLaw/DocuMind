# -*- coding: utf-8 -*-
"""图片资产门面——映射表 + CLIP 索引的注册/删除唯一入口

三份图片资产: image_index.json 映射表 + clip_images collection +
documents 图片块（后者由调用方经 vector 写入）。register/remove
一处调用完成映射表与 CLIP 索引的联动。

用法:
  - 新数据源: BaseIngestor.load() 已自动合并映射表; 有本地图片文件时
    再调 `await ImageAssets.register(source, local_paths, urls)`
    （ingest 管道是同步的，CLIP 编码异步——由调用方在 async 上下文中调）
  - 删除: ImageAssets.remove(source)（_delete_source 调用）
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ImageAssets:
    """图片资产联动（映射表 + CLIP 索引）——静态方法，无需实例状态"""

    @staticmethod
    async def register(
        source: str, image_paths: list[str], urls: list[str],
    ) -> int:
        """注册图片资产: 映射表合并（/api/images/ 前缀）+ CLIP 增量索引

        Args:
            source: 器物/文档 source 名（CLIP 索引按契约存 {source}#图）
            image_paths: 本地图片文件路径（CLIP 编码用）
            urls: 对外服务 URL（/api/images/… 或 /api/uploads/…）
        Returns: CLIP 索引条数; 0 = 无图或 CLIP 不可用（调用方按提示降级）
        """
        from multimodal.clip_retrieval import ClipRetriever
        from multimodal.image_index import upsert

        # 映射表只登记数据集图（/api/images/ 前缀，与 ingest 管道同规则）;
        # 上传文档图走 /api/uploads/ 静态服务，不进映射表
        files = [u[len("/api/images/") :] for u in urls if u.startswith("/api/images/")]
        if files:
            upsert(source, files)
        if not image_paths:
            return 0
        # add_images 内部已兜底（加载失败/编码失败 → warning + 0）
        return await ClipRetriever().add_images(source, image_paths, urls)

    @staticmethod
    def remove(source: str) -> None:
        """删除联动: 映射表条目 + clip_images 向量（各自内部兜底不抛）"""
        from multimodal.clip_retrieval import ClipRetriever
        from multimodal.image_index import remove_source

        remove_source(source)
        ClipRetriever().remove_by_source(source)
