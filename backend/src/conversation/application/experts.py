"""专家 Agent——史官、工艺、关联三方专家"""
import asyncio
import logging
from typing import TYPE_CHECKING

from interfaces.graph_store import GraphStore
from interfaces.llm import LLMProvider
from prompts import render_system, render_user
from core.llm_retry import llm_call_with_retry
from conversation.domain.research_plan import AgentRole

if TYPE_CHECKING:
    from retrieval.hybrid import HybridRetriever

logger = logging.getLogger(__name__)


class BaseExpert:
    """专家基类——依赖接口，不依赖具体实现"""

    role: AgentRole

    def __init__(self, llm: LLMProvider):
        self.llm = llm
        # 证据锚定：本次执行检索到的来源（编号带角色前缀，如 史官1）
        self.last_sources: list[dict] = []

    async def execute(self, query: str, context: dict | None = None) -> str:
        """执行专家任务，返回分析文本"""
        raise NotImplementedError

    async def _chat_with_retry(
        self, messages: list[dict], *, temperature: float = 0.5, max_tokens: int | None = None
    ) -> str:
        """LLM 调用 + 重试兜底（统一 llm_call_with_retry 装饰器）

        异常（网络/超时/5xx/429）/空响应抖动退避重试;4xx 立即上抛。
        耗尽仍返回 ""，调用方按空分析降级。
        """
        try:
            return await llm_call_with_retry(
                messages, self.llm,
                temperature=temperature, max_tokens=max_tokens,
            )
        except Exception as e:
            logger.warning(f"{self.role.value} LLM 调用失败，返回空: {e}")
            return ""


class HistorianExpert(BaseExpert):
    """史官专家——基于向量检索与文献考据"""

    role = AgentRole.HISTORIAN

    def __init__(self, llm: LLMProvider, retriever: HybridRetriever):
        super().__init__(llm)
        self.retriever = retriever

    async def execute(self, query: str, context: dict | None = None) -> str:
        """检索文献并结合 LLM 生成史官视角的分析（带 [N] 证据引用）"""
        docs: list[dict] = []
        if self.retriever:
            try:
                # 检索器统一为 async（同步分派已删）
                docs = await self.retriever.retrieve(query)
            except Exception as e:
                logger.warning(f"HistorianExpert 检索失败: {e}")

        # 证据锚定：编号化来源（史官1..N）
        self.last_sources = [
            {
                "index": f"史官{i + 1}",
                "source": d.get("source", ""),
                "paths": d.get("paths", []),
                "content": d.get("content", "")[:200],
            }
            for i, d in enumerate(docs[:8])
        ]

        doc_context = ""
        if self.last_sources:
            doc_context = "\n".join(
                f"[史官{i + 1}] {d.get('content', '')[:300]}"
                for i, d in enumerate(docs[:8])
            )
        else:
            doc_context = "（未检索到相关文献，请基于陶瓷史通识回答）"

        messages = self.llm.build_messages(
            render_system("expert_historian"),
            render_user(
                "expert_historian",
                doc_context=doc_context,
                query=query,
            ),
        )
        return await self._chat_with_retry(messages)


class CraftsmanExpert(BaseExpert):
    """工艺专家——从底足、胎体、釉层、纹饰角度分析器物"""

    role = AgentRole.CRAFTSMAN

    CRAFT_ANGLES = ["底足", "胎体", "釉层", "纹饰"]

    def __init__(self, llm: LLMProvider, retriever: HybridRetriever):
        super().__init__(llm)
        self.retriever = retriever

    async def execute(self, query: str, context: dict | None = None) -> str:
        """检索工艺资料并结合 LLM 生成工艺分析（带 [N] 证据引用）"""
        craft_query = f"工艺 技法 材质 {query}"
        docs: list[dict] = []
        if self.retriever:
            try:
                # 检索器统一为 async（同步分派已删）
                docs = await self.retriever.retrieve(craft_query)
            except Exception as e:
                logger.warning(f"CraftsmanExpert 检索失败: {e}")

        # 证据锚定：编号化来源（工艺1..N）
        self.last_sources = [
            {
                "index": f"工艺{i + 1}",
                "source": d.get("source", ""),
                "paths": d.get("paths", []),
                "content": d.get("content", "")[:200],
            }
            for i, d in enumerate(docs[:8])
        ]

        doc_context = ""
        if self.last_sources:
            doc_context = "\n".join(
                f"[工艺{i + 1}] {d.get('content', '')[:300]}"
                for i, d in enumerate(docs[:8])
            )
        else:
            doc_context = "（未检索到相关工艺资料，请基于陶瓷工艺学通识回答）"

        angles_desc = "、".join(self.CRAFT_ANGLES)
        messages = self.llm.build_messages(
            render_system("expert_craftsman", angles_desc=angles_desc),
            render_user(
                "expert_craftsman",
                angles_desc=angles_desc,
                doc_context=doc_context,
                query=query,
            ),
        )
        return await self._chat_with_retry(messages)


class RelatorExpert(BaseExpert):
    """关联专家——基于知识图谱发掘实体之间的关联"""

    role = AgentRole.RELATOR

    def __init__(self, llm: LLMProvider, knowledge: GraphStore | None):
        super().__init__(llm)
        self.knowledge = knowledge

    async def execute(self, query: str, context: dict | None = None) -> str:
        """查询知识图谱并结合 LLM 生成关联分析（带 [N] 证据引用）"""
        nodes: list[dict] = []
        links: list[dict] = []

        if self.knowledge:
            try:
                # 同步 Neo4j 驱动经 to_thread——不阻塞事件循环
                # （graph_query/knowledge 链已同口径,此处是最后残余）
                nodes, links = await asyncio.to_thread(
                    self.knowledge.search_path, query
                )
            except Exception as e:
                logger.warning(f"RelatorExpert 图谱查询失败: {e}")

        # 证据锚定：图谱关系编号化来源（关联1..N）
        self.last_sources = [
            {
                "index": f"关联{i + 1}",
                "source": f"图谱: {lnk.get('source', '?')} —[{lnk.get('name', '?')}]→ {lnk.get('target', '?')}",
                "paths": ["graph"],
                "content": f"{lnk.get('source', '?')} —[{lnk.get('name', '?')}]→ {lnk.get('target', '?')}",
            }
            for i, lnk in enumerate(links[:8])
        ]

        graph_context = ""
        if self.last_sources:
            graph_context = "\n".join(
                f"[关联{i + 1}] {lnk.get('source', '?')} —[{lnk.get('name', '?')}]→ {lnk.get('target', '?')}"
                for i, lnk in enumerate(links[:8])
            )
        elif nodes:
            node_names = [n.get("name", "?") for n in nodes[:10]]
            graph_context = f"关联节点 ({len(nodes)} 个): {', '.join(node_names)}"
        else:
            graph_context = "（未在知识图谱中找到直接关联节点）"

        messages = self.llm.build_messages(
            render_system("expert_relator"),
            render_user(
                "expert_relator",
                graph_context=graph_context,
                query=query,
            ),
        )
        return await self._chat_with_retry(messages)
