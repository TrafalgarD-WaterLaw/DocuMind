"""Embed Provider — BGE-Small-ZH（本地加载，全程离线）"""
import os as _os

# 禁用所有 HF Hub 网络请求（模型已本地缓存）
_os.environ.setdefault("HF_HUB_OFFLINE", "1")
_os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
_os.environ.pop("HF_ENDPOINT", None)  # 清除镜像配置，离线模式不需要

from sentence_transformers import SentenceTransformer  # noqa: E402

from core.config import settings  # noqa: E402
from interfaces.embedder import Embedder  # noqa: E402


class SentenceTransformersEmbedder(Embedder):
    """BGE-Small-ZH 向量化（本地模型，离线加载）"""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.bge_model_path
        self._model = SentenceTransformer(self.model_name, trust_remote_code=True)

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        ).tolist()

    @property
    def dimension(self) -> int:
        return self._model.get_sentence_embedding_dimension()
