# -*- coding: utf-8 -*-
"""CLIP 图文互检服务（C3 接入）——多模态 RAG 跨模态检索

索引: src/data/chroma 的 clip_images collection（scripts/import_clip_images.py 构建）
模型: Chinese-CLIP base（懒加载——首次查询加载，与 reranker 同级）
能力:
  - text_search: 文本查询 → 命中图片（文找图，图文同空间 cosine）
  - image_search: 图片 → 相似图片（图找图，vision/chat 图找文用）

与 bge 图注路互补: CLIP 管"像什么"（外观/颜色/形态），图注路管"写什么"
（图内文字/图注语义）——两路合并图片检索覆盖完整。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import torch
from PIL import Image

from core.config import settings

logger = logging.getLogger(__name__)

# 懒加载锁——首次调用并发（如 vision/chat 与普通问答同时触发）会双加载
# 导致模型实例被替换、设备错位（实测 CUDA index_select 报错）
_LOAD_LOCK = threading.Lock()

# 模型路径
MODEL_PATH = settings.clip_model_path
# 索引目录;
# settings.chroma_persist_dir 为相对路径（"src/data/chroma"），
# 项目统一从 Backend 目录运行（uv run python src/main.py），cwd 解析与
# __file__ 推导的绝对路径等价（与 di.py 直接传 persist_dir 的行为一致）
CHROMA_DIR = Path(settings.chroma_persist_dir)
COLLECTION = "clip_images"


class _ClipTextFn:
    """CLIP 文本编码器（Chroma ef——query 文本编码）"""

    def __init__(self, model: Any, processor: Any):
        self._model = model
        self._processor = processor

    def name(self) -> str:
        return "chinese-clip"

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self.embed_query(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        with torch.no_grad():
            inputs = self._processor(text=input, return_tensors="pt", padding=True)
            # 模型在 CUDA 时 input_ids/attention_mask 必须同设备——
            # 漏掉此步会报 index on cpu / weight on cuda 的 device mismatch
            device = next(self._model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}
            out = self._model.get_text_features(
                input_ids=inputs["input_ids"],
                attention_mask=inputs["attention_mask"],
            )
            embeds = out.pooler_output
        return [v.tolist() for v in embeds.cpu().float()]

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self.embed_query(input)


class ClipRetriever:
    """CLIP 图文互检（懒加载模型 + Chroma clip_images collection）

    模型为**类级共享**（_shared_model/_shared_processor/_shared_col）——
    任何实例（prewarm 新实例 / 模块级单例 / 未来调用方）_ensure() 都复用
    同一份模型——类级共享防跨实例双加载（prewarm 与请求并发首次
    访问时各加载一次会阻塞请求 10-30s）。
    """

    _shared_model = None
    _shared_processor = None
    _shared_col = None
    # 加载失败时间戳——失败后 60s 冷却（与 di.py graph 失败冷却同模式）:
    # 模型文件损坏/磁盘异常时，不每次请求都同步重试 10-30s 的加载
    _failed_at = None

    def __init__(self):
        pass

    # 实例属性代理 → 类级共享（方法内 self._model 引用保持可读）
    @property
    def _model(self):
        return ClipRetriever._shared_model

    @property
    def _processor(self):
        return ClipRetriever._shared_processor

    def _ensure(self):
        if ClipRetriever._shared_model is None:
            # 冷却期内直接返回 None——调用侧降级（返回 []），不阻塞请求
            if ClipRetriever._failed_at is not None and (
                time.time() - ClipRetriever._failed_at < settings.failure_cooldown_seconds
            ):
                return None
            with _LOAD_LOCK:
                if ClipRetriever._shared_model is None:  # 双检锁
                    try:
                        from transformers import ChineseCLIPModel, ChineseCLIPProcessor

                        import chromadb

                        device = "cuda" if torch.cuda.is_available() else "cpu"
                        ClipRetriever._shared_model = ChineseCLIPModel.from_pretrained(
                            MODEL_PATH
                        ).to(device)
                        ClipRetriever._shared_processor = ChineseCLIPProcessor.from_pretrained(
                            MODEL_PATH
                        )
                        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
                        ClipRetriever._shared_col = client.get_collection(
                            COLLECTION,
                            embedding_function=_ClipTextFn(
                                ClipRetriever._shared_model,
                                ClipRetriever._shared_processor,
                            ),
                        )
                        ClipRetriever._failed_at = None  # 加载成功，解除冷却
                        logger.info(
                            f"ClipRetriever 就绪（{device}）: "
                            f"{ClipRetriever._shared_col.count()} 张图"
                        )
                    except Exception as e:
                        ClipRetriever._failed_at = time.time()
                        logger.warning(
                            f"ClipRetriever 加载失败（60s 内不再重试，"
                            f"图找文降级）: {e}"
                        )
                        return None
        return ClipRetriever._shared_col

    def _pack(self, res: dict) -> list[dict]:
        out = []
        for i in range(len(res["ids"][0])):
            meta = res["metadatas"][0][i] or {}
            out.append({
                "source": meta.get("source", ""),
                "image_path": meta.get("image_path", ""),
                "score": 1.0 - float(res["distances"][0][i]),
            })
        return out

    async def text_search(
        self, query: str, top_k: int | None = None,
    ) -> list[dict]:
        top_k = top_k or settings.clip_retrieval_top_k
        """文找图：文本查询 → 命中图片（带 source 关联）"""
        if not query:
            return []
        col = self._ensure()
        if col is None:  # 加载失败冷却期——图找文降级为空
            return []
        res = col.query(query_texts=[query], n_results=max(top_k, 1))
        return self._pack(res)

    def remove_by_source(self, source: str) -> int:
        """删除文档时同步清理 clip_images（source 与 {source}#图 变体）

        精确匹配两种 key（source == x / source == f"{x}#图"），
        不再子串包含匹配——否则删除「青铜-鼎」会连带删「青铜-鼎耳簋」等
        相似命名 source 的图片。
        独立打开 collection（get/delete 不需要模型/ef——删除时 CLIP 未加载
        也必须清理）。
        """
        if not source:
            return 0
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            try:
                col = client.get_collection(COLLECTION)
            except Exception:
                return 0  # 索引未构建
            res = col.get(include=["metadatas"], limit=1000000)
            ids = [
                doc_id for doc_id, meta in zip(res["ids"], res["metadatas"])
                if (meta or {}).get("source") in (source, f"{source}#图")
            ]
            if ids:
                col.delete(ids=ids)
                logger.info(f"clip_images 同步清理: {len(ids)} 条（source={source[:24]}）")
            return len(ids)
        except Exception as e:
            logger.warning(f"clip_images 同步清理失败: {e}")
            return 0

    async def add_images(self, source: str, image_paths: list[str],
                         urls: list[str]) -> int:
        """上传文档后增量写入 clip_images（L3：删除有联动、新增对称）

        source 存 {source}#图（与 import_clip_images.py 的命名一致——
        归并端按两种 key 查）。失败仅 warning（图片块已可检索，CLIP 增量
        是图找文的补充通道，不阻断上传）。
        """
        if not image_paths:
            return 0
        try:
            col = self._ensure()
            if col is None:
                return 0
            with torch.no_grad():
                images = [Image.open(p).convert("RGB") for p in image_paths]
                # 该 transformers 版本 image-only forward 报 input_ids 缺失
                # ——text(空串)+images 双输入取 image_embeds（与索引构建同路径）
                inputs = self._processor(text=[""] * len(images), images=images,
                                         return_tensors="pt", padding=True)
                device = next(self._model.parameters()).device
                inputs = {k: v.to(device) for k, v in inputs.items()}
                embeds = self._model(**inputs).image_embeds.cpu().float()
            col.add(
                ids=image_paths,
                documents=urls,
                metadatas=[{"source": f"{source}#图", "image_path": u}
                           for u in urls],
                embeddings=[v.tolist() for v in embeds],
            )
            logger.info(f"clip_images 增量写入: {len(image_paths)} 条（{source[:24]}）")
            return len(image_paths)
        except Exception as e:
            logger.warning(f"clip_images 增量写入失败: {e}")
            return 0

    async def image_search(self, image: Any, top_k: int = 3) -> list[dict]:
        """图找图：输入图片 → 相似图片（vision/chat 图找文用）

        该 transformers 版本 forward 强制 text+image 双输入——text 用空串
        （text 分支开销小）；显式 embeddings 查询绕过 Chroma 文本 ef。
        """
        col = self._ensure()
        if col is None:  # 加载失败冷却期——图找图降级为空
            return []
        with torch.no_grad():
            inputs = self._processor(text=[""], images=[image],
                                     return_tensors="pt", padding=True)
            device = next(self._model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}
            embeds = self._model(**inputs).image_embeds
        res = col.query(query_embeddings=[embeds.cpu().float().tolist()[0]],
                        n_results=max(top_k, 1))
        return self._pack(res)


# 模块级单例（懒加载）
clip_retriever = ClipRetriever()
