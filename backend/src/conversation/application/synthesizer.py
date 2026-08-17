"""著述 Agent——融合多专家结果生成综合研究报告"""
import logging
from collections.abc import AsyncGenerator

from interfaces.llm import LLMProvider
from prompts import render_system, render_user
from core.llm_retry import llm_call_with_retry
from conversation.domain.research_plan import AgentRole

logger = logging.getLogger(__name__)


class Synthesizer:
    """融合多方专家结论，生成结构化研究报告"""

    role = AgentRole.SYNTHESIZER

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    async def synthesize(
        self,
        question: str,
        expert_results: dict[str, str],
        history: list[dict] | None = None,
        sources: list[dict] | None = None,
    ) -> str:
        """整合各专家分析结果，输出带思维导图结构的 Markdown 报告

        Args:
            question: 用户的原始研究问题
            expert_results: {expert_role_name: analysis_text} 映射
            history: 历史对话（可选）
            sources: 原始证据块（全局编号 1..N），报告引用直接锚定原文，
                     避免专家转述导致的编号失真/幻觉引用

        Returns:
            结构化的 Markdown 研究报告
        """
        messages = await self._prepare_messages(question, expert_results, history, sources)
        # 偶发空响应/异常统一 llm_call_with_retry 兜底（异常/空响应抖动退避；
        # 4xx 立即上抛）。失败返回空串——深度研究降级为仅专家分析输出。
        # max_tokens=8192——默认 4096 会截断长报告（实测在"思维导图"处断掉）
        try:
            return await llm_call_with_retry(messages, self.llm, max_tokens=8192)
        except Exception as e:
            logger.warning(f"Synthesizer LLM 调用失败，返回空: {e}")
            return ""

    async def synthesize_stream(
        self,
        question: str,
        expert_results: dict[str, str],
        history: list[dict] | None = None,
        sources: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式综合报告——chat_stream 逐 token 输出（前端逐字显示）

        流式失败/空流兜底非流式一次返回（max_tokens=8192 防截断）——
        报告不因流式异常丢失。
        """
        messages = await self._prepare_messages(question, expert_results, history, sources)
        try:
            got = False
            async for chunk in self.llm.chat_stream(messages):
                got = True
                yield chunk
            if got:
                return
            logger.warning("Synthesizer 流式空流，回退非流式")
        except Exception as e:
            logger.warning(f"Synthesizer 流式失败，回退非流式: {e}")
        try:
            report = await llm_call_with_retry(messages, self.llm, max_tokens=8192)
            if report:
                yield report
        except Exception as e:
            logger.warning(f"Synthesizer LLM 调用失败，返回空: {e}")

    async def _prepare_messages(
        self,
        question: str,
        expert_results: dict[str, str],
        history: list[dict] | None,
        sources: list[dict] | None,
    ) -> list[dict]:
        """拼综合报告消息（synthesize / synthesize_stream 共用）"""
        combined = self._assemble_sections(expert_results)

        system_prompt = render_system(
            "synthesizer", max_index=len(sources) if sources else 0
        )
        # 多轮记忆:>6 轮旧轮次 LLM 摘要,≤6 轮硬截断
        from conversation.application.memory import build_history_text

        history_text = await build_history_text(history, self.llm)
        if history_text:
            history_text += "\n\n"

        evidence_text = _build_evidence_text(sources)
        return self.llm.build_messages(
            system_prompt,
            render_user(
                "synthesizer",
                history_text=history_text,
                question=question,
                combined=combined,
                evidence_text=evidence_text or "（无证据块）",
                max_index=len(sources) if sources else 0,
            ),
        )

    @staticmethod
    def _assemble_sections(expert_results: dict[str, str]) -> str:
        """各专家结果 → 汇总章节文本（史官/工艺/关联各一节）"""
        role_labels = {
            "historian": "📜 史官分析（历史背景与断代）",
            "craftsman": "🔧 工艺分析（技法与特征鉴定）",
            "relator": "🔗 关联分析（谱系与传承关系）",
        }
        sections = [
            f"## {label}\n\n{expert_results[role_key]}"
            for role_key, label in role_labels.items()
            if role_key in expert_results
        ]
        return "\n\n".join(sections) if sections else "（暂无专家分析结果）"


def _build_evidence_text(sources: list[dict] | None) -> str:
    """原始证据块（全局编号 1..N）→ 引用锚定文本

    300 字截断按句子边界回退——证据残句不进报告上下文。
    """
    if not sources:
        return "（无证据块）"
    from ingestion.infrastructure.chunker import _truncate_at_sentence

    return "\n".join(
        f"[{i + 1}]（{s.get('source', '')}）\n"
        f"{_truncate_at_sentence(s.get('content', ''), 300)}"
        for i, s in enumerate(sources)
    )
