# -*- coding: utf-8 -*-
"""模型预热——启动时后台加载懒加载模型，避免首次请求被同步加载阻塞

问题: bge embed / bge-reranker / CLIP 均为首次访问时在请求线程内同步加载
（10-30s），期间事件循环卡死——检索流水线的 rewrite/path_done 事件全部
积压，前端一次性看到全部节点（动态点亮失效）。
方案: 应用启动时后台线程预热（daemon 线程不阻塞启动），请求时模型已就绪，
事件按真实时序逐条到达。DI 容器懒加载已加双检锁（prewarm 与请求并发安全）。

v2: 吸收原 lifespan 的同步预检（container.vector）——它会在事件循环内
同步加载 embedder（数秒~10s），与后台预热的设计意图矛盾；统一移入线程，
启动即响应 /api/health。失败语义不变（非致命）。
"""
import logging
import threading

logger = logging.getLogger(__name__)

# 预热线程句柄——container.close() 退出时 join 收尾（di.py）
_prewarm_thread: threading.Thread | None = None


def prewarm() -> None:
    """后台线程预热全部懒加载服务（embed/reranker/CLIP/BM25/vector）"""
    def _warm() -> None:
        from core.di import container

        # 每项独立 try——单项失败只降级该项，不阻断其余预热
        # 向量存储 + embedder（Chroma 连接 + bge 模型——原在 lifespan
        # 主线程同步预检，会阻塞事件循环数秒~10s，移至后台）
        try:
            _ = container.vector
            logger.info("VectorStore: ready")
        except Exception as e:
            logger.warning(f"VectorStore init failed (non-fatal): {e}")

        # 懒加载模型（bge embed / bge-reranker / CLIP / BM25）
        try:
            _ = container.embedder          # bge 语义向量模型
            _ = container.reranker          # bge-reranker cross-encoder 精排
            from multimodal.clip_retrieval import ClipRetriever

            ClipRetriever()._ensure()       # CLIP 图文模型（内部双检锁 + 失败冷却）
            from retrieval.bm25 import BM25Index

# BM25 真预热——直接 build（dirty=False 时 rebuild_if_dirty
            # 是空操作，jieba 分词字典从未预热；build 触发全量分词加载）
            BM25Index().build(container.vector.get_all_documents())
            logger.info("模型预热完成（embed/reranker/CLIP/BM25 就绪）")
        except Exception as e:
            logger.warning(f"模型预热失败（首次请求将重新加载）: {e}")

    global _prewarm_thread
    _prewarm_thread = threading.Thread(
        target=_warm, daemon=True, name="model-prewarm"
    )
    _prewarm_thread.start()
    logger.info("模型预热任务已启动（后台线程）")
