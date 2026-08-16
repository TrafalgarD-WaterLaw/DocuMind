"""Rerank Provider — BGE-Reranker-V2-M3（本地加载，全程离线）

两阶段检索的第二阶段：cross-encoder 精排。
RRF 融合分只是"排名倒数"（多路投票），不是真正的相关性分数；
对 (query, doc) 做交互式打分，消除多路投票带来的噪声。
"""
import os as _os

# 禁用所有 HF Hub 网络请求（模型已本地缓存）
_os.environ.setdefault("HF_HUB_OFFLINE", "1")
_os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
_os.environ.pop("HF_ENDPOINT", None)  # 清除镜像配置，离线模式不需要

import logging  # noqa: E402

import torch  # noqa: E402

from sentence_transformers import CrossEncoder  # noqa: E402

from core.config import settings  # noqa: E402

logger = logging.getLogger(__name__)


class RerankerProvider:
    """BGE-Reranker-V2-M3 cross-encoder 精排（本地模型，离线加载）"""

    def __init__(self, model_path: str | None = None, max_length: int = 512):
        self.model_path = model_path or settings.reranker_model_path
        # 显式指定 device:CrossEncoder 默认解析在部分版本落到 CPU,
        # 32 候选 × 28 条 CPU 推理实测 18 分钟未完成——CUDA 可用则必须上卡
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = CrossEncoder(
            self.model_path,
            max_length=max_length,
            trust_remote_code=True,
            device=self._device,
        )
        logger.info(f"Reranker 加载完成: {self.model_path} (device={self._device})")

    def rerank(
        self, query: str, docs: list[dict], top_k: int | None = None
    ) -> list[dict]:
        """对候选文档按与查询的相关性精排

        Args:
            query: 用户查询
            docs: 候选文档列表（含 content 字段，会被原地加 rerank_score）
            top_k: 返回条数（None 返回全部）

        Returns:
            按相关性降序的 docs（带 rerank_score 字段）
        """
        if not docs:
            return []
        pairs = [(query, d.get("content", "")) for d in docs]
        scores = self._model.predict(pairs, show_progress_bar=False)
        for d, s in zip(docs, scores):
            d["rerank_score"] = float(s)
        docs.sort(key=lambda d: d["rerank_score"], reverse=True)
        return docs[:top_k] if top_k else docs
