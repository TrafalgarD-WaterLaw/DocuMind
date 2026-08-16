"""依赖注入容器 — 组装整个应用

所有业务服务从这里获取依赖，不直接 import 具体 provider。
切换实现只需改这里的工厂函数。
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.config import Settings, settings
from interfaces.embedder import Embedder
from interfaces.graph_store import GraphStore
from interfaces.image_captioner import ImageCaptioner
from interfaces.llm import LLMProvider
from interfaces.vector_store import VectorStore

if TYPE_CHECKING:
    # 仅类型检查用——运行时保持方法内懒加载，避免引入循环依赖
    from conversation.application.orchestrator import ResearchOrchestrator
    from retrieval.hybrid import HybridRetriever

logger = logging.getLogger(__name__)


@dataclass
class AppContainer:
    """应用依赖容器 — 持有所有 provider 实例"""

    config: Settings = field(default_factory=lambda: settings)
    _llm: LLMProvider | None = field(default=None, init=False)
    _vector: VectorStore | None = field(default=None, init=False)
    _questions: VectorStore | None = field(default=None, init=False)
    _graph: GraphStore | None = field(default=None, init=False)
    _embedder: Embedder | None = field(default=None, init=False)
    _reranker: Any = field(default=None, init=False)
    _retriever: Any = field(default=None, init=False)
    _orchestrator: Any = field(default=None, init=False)
    _captioner: ImageCaptioner | None = field(default=None, init=False)
    # 实例状态服务的唯一装配点;在此懒加载,业务模块经无状态委托函数访问
    _renderer: Any = field(default=None, init=False)
    _trace_writer: Any = field(default=None, init=False)
    _memory: Any = field(default=None, init=False)
    _document_hashes: Any = field(default=None, init=False)
    _image_index: Any = field(default=None, init=False)
    # 模型懒加载双检锁——启动预热线程与请求线程可能并发首次访问
    _model_lock: threading.Lock = field(default_factory=threading.Lock,
                                        init=False)
# Neo4j 连接失败时间戳（60s 冷却后自动重试）
    _graph_failed_at: float | None = field(default=None, init=False)

    # ── LLM ───────────────────────────────────────

    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = self._create_llm()
        return self._llm

    def _create_llm(self) -> LLMProvider:
        from conversation.infrastructure.deepseek_llm import DeepSeekProvider

        return DeepSeekProvider(
            api_key=self.config.deepseek_api_key,
            base_url=self.config.deepseek_base_url,
            model=self.config.llm_model,
        )

    # ── Vector Store ──────────────────────────────

    @property
    def vector(self) -> VectorStore:
        if self._vector is None:
            self._vector = self._create_vector()
        return self._vector

    def _create_vector(self) -> VectorStore:
        return self._create_chroma(collection_name="documents")

    def _create_chroma(self, collection_name: str) -> VectorStore:
        from ingestion.infrastructure.chroma_store import ChromaStore

        # Chroma 需要 embedding function；用 langchain 适配器包装
        ef = _ChromaEmbeddingFn(self.embedder)

        return ChromaStore(
            persist_dir=self.config.chroma_persist_dir,
            embedding_function=ef,
            collection_name=collection_name,
        )

    # ── Hybrid Retriever（五路召回 + RRF）─────────

    @property
    def retriever(self) -> HybridRetriever:
        """混合检索器：向量语义 + Q-to-Q 问题匹配 + BM25 + 图谱锚定"""
        if self._retriever is None:
            self._retriever = self._create_retriever()
        return self._retriever

    def _create_retriever(self):
        from retrieval.bm25 import BM25Index
        from retrieval.hybrid import HybridRetriever

        bm25 = BM25Index()
        bm25.build(self.vector.get_all_documents())

        return HybridRetriever(
            doc_store=self.vector,
            question_store=self.questions,
            bm25=bm25,
            graph=self.graph,
            llm=self.llm,  # 实体提取为低成本非流式调用，不随 mock 模式关闭
            reranker=self.reranker,  # cross-encoder 精排（懒加载，评测可关）
        )

    # ── Question Index（假设性问题集合）──────────

    @property
    def questions(self) -> VectorStore:
        """问题索引——入库侧假设性问题，Q-to-Q 匹配用"""
        if self._questions is None:
            self._questions = self._create_chroma(collection_name="questions")
        return self._questions

    # ── Graph Store ───────────────────────────────

    @property
    def graph(self) -> GraphStore | None:
        if self._graph is None and self.config.neo4j_password:
# 连接失败缓存失败状态（60s 后自动重试）——Neo4j 宕机时
            # 每请求重建 driver + 3s 连接超时阻塞，快速失败机制因实例重建
            # 永远不生效
            if self._graph_failed_at is not None and (
                time.time() - self._graph_failed_at < settings.failure_cooldown_seconds
            ):
                return None
            try:
                self._graph = self._create_graph()
                self._graph_failed_at = None
            except Exception as e:
                logger.warning(f"Graph store unavailable: {e}")
                self._graph = None
                self._graph_failed_at = time.time()
        return self._graph

    def _create_graph(self) -> GraphStore:
        from graph.infrastructure.neo4j_store import Neo4jStore

        return Neo4jStore(
            uri=self.config.neo4j_uri,
            user=self.config.neo4j_user,
            password=self.config.neo4j_password,
        )

    # ── Orchestrator（快速问答协调器，无状态单例）──────

    @property
    def orchestrator(self) -> ResearchOrchestrator:
        """ResearchOrchestrator——无状态（仅持有 llm/retriever/graph 引用，
        trace 均为方法内局部变量），容器单例复用，避免每请求重建"""
        if self._orchestrator is None:
            from conversation.application.orchestrator import ResearchOrchestrator

            self._orchestrator = ResearchOrchestrator(
                llm=self.llm,
                retriever=self.retriever,
                knowledge=self.graph,
            )
        return self._orchestrator

    # ── Reranker ──────────────────────────────────

    @property
    def reranker(self):
        """cross-encoder 精排器（本地模型，懒加载 + 双检锁）

        双检锁：预热线程与请求线程可能同时首次访问——模型加载 10-30s
        同步阻塞，双加载浪费且产生两个实例。
        """
        if self._reranker is None and self.config.use_rerank:
            with self._model_lock:
                if self._reranker is None:
                    from retrieval.rerank import RerankerProvider

                    self._reranker = RerankerProvider()
        return self._reranker

    # ── Image Captioner（文档图片描述，可插拔）──────────

    @property
    def captioner(self) -> ImageCaptioner:
        """文档图片描述器——配置了百炼 key 用 Qwen-VL，否则降级 Noop"""
        if self._captioner is None:
            from multimodal.image_caption import NoopCaptioner, QwenVLCaptioner

            if self.config.dashscope_api_key:
                self._captioner = QwenVLCaptioner(
                    api_key=self.config.dashscope_api_key,
                    base_url=self.config.dashscope_base_url,
                    model=self.config.image_caption_model,
                )
            else:
                self._captioner = NoopCaptioner()
        return self._captioner


    @property
    def renderer(self) -> Any:
        """提示词渲染器——mtime 缓存为实例状态"""
        if self._renderer is None:
            from prompts import PromptRenderer

            self._renderer = PromptRenderer()
        return self._renderer

    @property
    def trace_writer(self) -> Any:
        """trace jsonl 写入器——追加句柄缓存为实例状态"""
        if self._trace_writer is None:
            from core.tracing import TraceLogWriter

            self._trace_writer = TraceLogWriter()
        return self._trace_writer

    @property
    def memory(self) -> Any:
        """多轮记忆摘要器——LRU 摘要缓存为实例状态（跨调用方共享）"""
        if self._memory is None:
            from conversation.application.memory import ConversationMemory

            self._memory = ConversationMemory()
        return self._memory

    @property
    def document_hashes(self) -> Any:
        """文档指纹索引——映射缓存为实例状态"""
        if self._document_hashes is None:
            from documents.application.hash_index import DocumentHashIndex

            self._document_hashes = DocumentHashIndex()
        return self._document_hashes

    @property
    def image_index(self) -> Any:
        """图片映射表——mtime 失效缓存为实例状态（默认路径实例）"""
        if self._image_index is None:
            from multimodal.image_index import FileBackedImageIndex

            self._image_index = FileBackedImageIndex()
        return self._image_index

    # ── Embedder ──────────────────────────────────

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            with self._model_lock:
                if self._embedder is None:
                    self._embedder = self._create_embedder()
        return self._embedder

    def _create_embedder(self) -> Embedder:
        from retrieval.embedder import SentenceTransformersEmbedder

        return SentenceTransformersEmbedder()  # 使用默认本地路径

    # ── Lifecycle ────────────────────────────────

    def close(self) -> None:
        """关闭所有连接——graph driver + Chroma client + 预热线程收尾"""
        if self._graph is not None:
            try:
                self._graph.close()
            except Exception:
                pass
        for store in (self._vector, self._questions):
            if store is not None:
                try:
                    store.close()
                except Exception:
                    pass
        # 预热线程收尾（daemon 线程进程退出即死，超时后不强等——
        # 避免模型加载 30s 时关停无限等待）
        try:
            from core.prewarm import _prewarm_thread

            if _prewarm_thread is not None and _prewarm_thread.is_alive():
                _prewarm_thread.join(timeout=5)
        except Exception:
            pass

    # ── BM25 失效标记 ─────────────────────────────

    def mark_bm25_dirty(self) -> None:
        """上传/删除文档后标记 BM25 过期（下次检索前重建）"""
        try:
            if self._retriever is not None:
                self._retriever.bm25.mark_dirty()
        except Exception as e:
            logger.warning(f"mark_bm25_dirty 失败: {e}")


class _ChromaEmbeddingFn:
    """ChromaDB 兼容的 embedding function"""
    def __init__(self, embedder: Embedder):
        self._e = embedder

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._e.embed_batch(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self._e.embed_batch(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self._e.embed_batch(input)

    def name(self) -> str:
        return "bge-small-zh-v1.5"


# 全局容器实例
container = AppContainer()
