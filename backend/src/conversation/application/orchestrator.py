"""研究协调门面——装配 QuickAnswerService 与 DeepResearchService 并委托"""
from interfaces.graph_store import GraphStore
from interfaces.llm import LLMProvider
from conversation.application.deep_research import DeepResearchService
from conversation.application.quick_answer import QuickAnswerService


class ResearchOrchestrator:
    """研究协调门面——仅做装配与委托"""

    def __init__(
        self,
        llm: LLMProvider,
        retriever=None,
        knowledge: GraphStore | None = None,
    ):
        """retriever 支持两种：
        - HybridRetriever（async retrieve，quick 模式使用，带 RRF 融合与引用）
        - VectorStore（同步 retrieve，专家使用）
        """
        self._quick = QuickAnswerService(llm=llm, retriever=retriever)
        self._deep = DeepResearchService(
            llm=llm, retriever=retriever, knowledge=knowledge
        )

    async def quick_answer(
        self,
        query: str,
        history: list[dict] | None = None,
    ):
        """快速问答——委托 QuickAnswerService"""
        async for ev in self._quick.quick_answer(query, history):
            yield ev

    async def vision_answer(
        self,
        query: str,
        history: list[dict] | None,
        prep,
        trace,
        t_start: float,
        clip_by_source: dict[str, list[str]] | None = None,
    ):
        """多模态问答编排——vision 的查询准备由调用方构造（识别名+问题），
        统一回答编排委托 QuickAnswerService._answer_flow（与文本问答共用）。

        vision 与 chat/research 统一经 orchestrator 单例编排。
        """
        async for ev in self._quick._answer_flow(
            query, history, prep, trace, t_start, clip_by_source,
        ):
            yield ev

    async def deep_research(
        self,
        query: str,
        history: list[dict] | None = None,
    ):
        """深度研究——委托 DeepResearchService"""
        async for ev in self._deep.deep_research(query, history):
            yield ev
