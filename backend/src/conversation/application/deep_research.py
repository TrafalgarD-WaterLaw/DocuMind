"""深度研究服务——多专家协作 + 著述融合"""
import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING, Any

from interfaces.graph_store import GraphStore
from interfaces.llm import LLMProvider
from models.response import StreamEvent, StreamEventType
from prompts import render_system, render_user
from conversation.application.experts import (
    BaseExpert,
    CraftsmanExpert,
    HistorianExpert,
    RelatorExpert,
)
from conversation.application.synthesizer import Synthesizer
from conversation.domain.research_plan import (
    AgentRole,
    AgentTask,
    ResearchMode,
    ResearchPlan,
)

if TYPE_CHECKING:
    from retrieval.hybrid import HybridRetriever

logger = logging.getLogger(__name__)

# 专家角色 → 思维导图中文标题
_ROLE_TITLES = {
    AgentRole.HISTORIAN: "史官 · 历史考证",
    AgentRole.CRAFTSMAN: "工艺师 · 器物工艺",
    AgentRole.RELATOR: "关联师 · 器物关联",
    AgentRole.SYNTHESIZER: "著述 · 综合研究",
    AgentRole.COORDINATOR: "协调 · 研究计划",
}


class DeepResearchService:
    """深度研究服务——多专家协作 + 著述融合"""

    def __init__(
        self,
        llm: LLMProvider,
        retriever: "HybridRetriever | None" = None,
        knowledge: GraphStore | None = None,
    ):
        """装配深度研究链路依赖（图谱 knowledge 供关联专家使用）"""
        self.llm = llm
        self.retriever = retriever
        self.knowledge = knowledge

        self._synthesizer = Synthesizer(llm)
        # 专家注册表（策略模式）——按角色取实例工厂，替代 if-elif 分派；
        # 每次 deep_research 经工厂 new 新实例（专家无共享状态，安全）
        self._expert_factories: dict[AgentRole, Callable[[], BaseExpert]] = {
            AgentRole.HISTORIAN: lambda: HistorianExpert(self.llm, self.retriever),
            AgentRole.CRAFTSMAN: lambda: CraftsmanExpert(self.llm, self.retriever),
            AgentRole.RELATOR: lambda: RelatorExpert(self.llm, self.knowledge),
        }

    # ── 意图分析 ─────────────────────────────────────────────

    async def _analyze_intent(self, query: str) -> ResearchPlan:
        """研究计划——固定深度模式 + 全部专家；LLM 仅生成摘要（标题）

        /api/research 是显式深度研究端点:mode 恒为 DEEP、史官/工艺/关联
        三专家全部参与——曾由 LLM 分流（mode/agents），但该端点语义下
        quick 分支永不成立、专家选择无信息量，简化后只剩摘要生成。
        摘要失败/解析失败 → 回退查询前 50 字。
        """
        summary = query[:50]
        try:
            messages = self.llm.build_messages(
                render_system("agent_intent"), render_user("agent_intent", query=query)
            )
            raw = await self.llm.chat(
                messages, temperature=0.3,
                response_format={"type": "json_object"},  # DeepSeek JSON Output
            )
            from core.json_utils import extract_json

            data = extract_json(raw)
            if data and data.get("summary"):
                summary = str(data["summary"])[:50]
        except Exception as e:
            logger.warning(f"意图摘要生成失败，回退查询截断: {e}")
        return ResearchPlan(
            mode=ResearchMode.DEEP,
            summary=summary,
            tasks=[
                AgentTask(agent=AgentRole.HISTORIAN, query=query),
                AgentTask(agent=AgentRole.CRAFTSMAN, query=query),
                AgentTask(agent=AgentRole.RELATOR, query=query),
            ],
        )

    # ── 专家调度 ─────────────────────────────────────────────

    def _get_expert(self, role: AgentRole) -> BaseExpert | None:
        """按角色取专家实例（注册表查表;未知角色 → None）"""
        factory = self._expert_factories.get(role)
        return factory() if factory else None

    # ── 工具 ─────────────────────────────────────────────────

    def _pipeline_event(self, stage: str, data: dict) -> StreamEvent:
        """pipeline 事件（检索流水线实时状态）"""
        return StreamEvent(
            type=StreamEventType.PIPELINE,
            data={"stage": stage, **data},
            timestamp=time.time(),
        )

    def _expert_event(
        self, agent: AgentRole, status: str, message: str,
        detail: dict | None = None, duration: float | None = None,
    ) -> StreamEvent:
        """专家执行事件（前端"专家执行"区渲染）——4 处事件构造的统一收口"""
        data: dict = {"agent": agent, "status": status, "message": message}
        if detail:
            data["detail"] = detail
        if duration is not None:
            data["duration"] = duration
        return self._pipeline_event("expert", data)

    # ── 深度研究 ─────────────────────────────────────────────

    async def deep_research(
        self,
        query: str,
        history: list[dict] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """深度研究模式——多专家协作 + 著述融合

        流程（每一步结束都会往前端发一条消息，专家区由 expert 事件渲染）:

          第 1 步 分析意图  LLM 制定研究计划（mode + 专家任务列表）
          第 2 步 研究启动  → 前端"👥 专家执行"区显示启动信息
          第 3 步 依次执行  每个专家:running ⏳ → 分析 → done ✅/failed ❌（带耗时）
          第 4 步 著述融合  证据来源 → 综合报告（synthesizer）
          第 5 步 流式输出  报告按段落逐段推送 + 思维导图数据
        """
        # 第 1 步:研究计划（固定深度模式 + 全部专家,LLM 仅生成摘要）
        plan = await self._analyze_intent(query)
        logger.info(f"深度研究计划: mode={plan.mode}, tasks={len(plan.tasks)}")

        # 第 2 步:研究启动（专家区首条——计划概要/参与专家/任务数）
        yield self._expert_event(
            AgentRole.COORDINATOR, "running",
            f"深度研究启动: {plan.summary}",
            detail={
                "mode": plan.mode,
                "experts": [t.agent for t in plan.tasks],
                "task_count": len(plan.tasks),
            },
        )
        await asyncio.sleep(0)

        # 第 3 步:依次执行各专家（running → done/failed 均带耗时——
        # 前端专家区可展示每个专家花了几秒；结果经 side-channel 传出
        # 与 _retrieve_and_merge 同模式——generator 无法 return 结果）
        expert_results: dict[str, str] = {}
        expert_sources: list[dict] = []  # 证据锚定：合并各专家来源
        async for ev in self._run_experts(
            plan.tasks, expert_results, expert_sources,
        ):
            yield ev

        # 4. 著述融合（先发送证据来源，供前端引用渲染；全局编号 1..N 与报告一致）
        async for ev in self._emit_sources_merged(expert_sources):
            yield ev

        report_out: list[str] = []
        async for ev in self._synthesize_report(
            query, history, expert_results, expert_sources, report_out,
        ):
            yield ev
        if not report_out:
            return
        report = report_out[0]

        # 5. 流式输出合成报告（按段落分块流式输出）+ 思维导图 + 收尾
        async for ev in self._stream_report(report):
            yield ev
        async for ev in self._emit_mindmap(expert_results):
            yield ev
        yield self._expert_event(
            AgentRole.COORDINATOR, "done", "深度研究完成",
            detail={
                "experts_completed": len(expert_results),
                "total_experts": len(plan.tasks),
            },
        )

    async def _run_experts(
        self, tasks: list[AgentTask],
        expert_results: dict[str, str], expert_sources: list[dict],
    ) -> AsyncGenerator[StreamEvent, None]:
        """并行执行各专家（总耗时 ≈ 最慢专家,而非串行累加）——第 3 步

        结果写入入参容器（generator 无法 return——side-channel 模式）；
        sources 去重后统一全局编号（证据锚定）。
        """
        # 先发全部 running 事件（前端流水线三专家同时点亮）
        for task in tasks:
            if self._get_expert(task.agent) is None:
                yield self._expert_event(
                    task.agent, "failed", f"专家 {task.agent} 不可用",
                )
                await asyncio.sleep(0)
                continue
            yield self._expert_event(
                task.agent, "running", f"{task.agent} 正在分析...",
            )
            await asyncio.sleep(0)

        async def _run_one(task: AgentTask) -> tuple[str, str, float, str | None]:
            expert = self._get_expert(task.agent)
            t0 = time.time()
            try:
                result = await expert.execute(task.query, task.context)
                return task.agent, result, t0, None
            except Exception as e:
                logger.exception(f"专家 {task.agent} 执行失败")
                return task.agent, "", t0, str(e)

        # asyncio.gather 并行执行——三专家同时跑,总耗时 = 最慢专家
        results = await asyncio.gather(
            *(_run_one(t) for t in tasks if self._get_expert(t.agent) is not None)
        )
        seen_sources: set[tuple] = set()  # 去重指纹集合（O(n)，替代每轮重建集合）
        for agent, result, t0, err in results:
            if err:
                yield self._expert_event(
                    agent, "failed", f"{agent} 分析失败: {err}",
                    duration=round(time.time() - t0, 1),
                )
                await asyncio.sleep(0)
                continue
            expert_results[agent] = result
            # reasoning 事件：专家研究摘要（前端"推理过程"展示）
            if result and result.strip():
                yield StreamEvent(
                    type=StreamEventType.REASONING,
                    data=f"【{_ROLE_TITLES.get(agent, agent)}】"
                         f"{result.strip()[:200]}…",
                    timestamp=time.time(),
                )
                await asyncio.sleep(0)
            # 证据锚定：收集专家检索来源（去重后统一全局编号 1..N）
            expert = self._get_expert(agent)
            for src in getattr(expert, "last_sources", []):
                key = (src.get("source"), src.get("content"))
                if key not in seen_sources:
                    seen_sources.add(key)
                    expert_sources.append(src)

            yield self._expert_event(
                agent, "done", f"{agent} 分析完成",
                detail={"result_length": len(result)},
                duration=round(time.time() - t0, 1),
            )
            await asyncio.sleep(0)

    async def _synthesize_report(
        self, query: str, history: list[dict] | None,
        expert_results: dict[str, str], expert_sources: list[dict],
        report_out: list[str],
    ) -> AsyncGenerator[StreamEvent, None]:
        """第 4 步 著述融合:运行事件 + 综合报告（失败发 ERROR,报告写 side-channel）

        generator 无法 return 结果——side-channel 模式与 _run_experts 同。
        """
        yield StreamEvent(
            type=StreamEventType.REASONING,
            data="【著述 · 综合研究】正在融合各专家观点撰写报告…",
            timestamp=time.time(),
        )
        await asyncio.sleep(0)
        yield self._expert_event(
            AgentRole.SYNTHESIZER, "running", "正在撰写综合研究报告...",
        )
        await asyncio.sleep(0)
        yield self._pipeline_event("generate", {"status": "start"})

        try:
            report = await self._synthesizer.synthesize(
                query, expert_results, history, expert_sources
            )
        except Exception as e:
            logger.exception("Synthesizer 执行失败")
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=f"报告合成失败: {str(e)}",
                timestamp=time.time(),
            )
            return
        report_out.append(report)
        yield self._expert_event(
            AgentRole.SYNTHESIZER, "done", "综合研究报告完成",
            detail={"report_length": len(report)},
        )
        await asyncio.sleep(0)

    async def _emit_sources_merged(
        self, expert_sources: list[dict],
    ) -> AsyncGenerator[StreamEvent, None]:
        """证据来源事件（全局编号 1..N 与报告一致,前端引用渲染）"""
        if not expert_sources:
            return
        global_sources = [
            {**src, "index": i + 1}
            for i, src in enumerate(expert_sources)
        ]
        yield StreamEvent(
            type=StreamEventType.SOURCES,
            data={"items": global_sources},
            timestamp=time.time(),
        )
        await asyncio.sleep(0)

    async def _stream_report(
        self, report: str,
    ) -> AsyncGenerator[StreamEvent, None]:
        """报告按段落流式输出（每段一条 CONTENT 事件）"""
        for para in report.split("\n\n"):
            yield StreamEvent(
                type=StreamEventType.CONTENT,
                data=para + "\n\n",
                timestamp=time.time(),
            )
            await asyncio.sleep(0)

    async def _emit_mindmap(
        self, expert_results: dict[str, str],
    ) -> AsyncGenerator[StreamEvent, None]:
        """结构化输出事件（前端思维导图：研究计划 → 专家分工 → 结论摘要）"""
        yield StreamEvent(
            type=StreamEventType.MARKDOWN_DICT,
            data={
                "mode": "deep",
                "sections": [
                    {
                        "title": _ROLE_TITLES.get(role, role),
                        # 完整专家分析（不再截断——计划折叠默认收起,
                        # 展开可读每个 agent 的完整回复）
                        "content": result or "",
                    }
                    for role, result in expert_results.items()
                ],
                "related_questions": [],
            },
            timestamp=time.time(),
        )
        await asyncio.sleep(0)
