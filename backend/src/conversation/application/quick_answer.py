"""快速问答服务——快速问答链路（单专家 + 流式输出）"""
import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from core.config import settings
from core.tracing import RetrievalTrace, new_trace_id, write_trace_jsonl
from interfaces.graph_store import GraphStore
from interfaces.llm import LLMProvider
from models.response import StreamEvent, StreamEventType
from prompts import render_system, render_user
from core.llm_retry import llm_call_with_retry
from conversation.domain.verdict import RetrievalVerdict
from retrieval.context import filter_noise_chunks, resolve_parent_chunks
from multimodal.evidence import (
    collect_image_evidence,
    merge_source_images,
)
from conversation.domain.query_plan import QueryPlan
from conversation.application.query_understanding import build_query_plan, rewrite_query

logger = logging.getLogger(__name__)


class QuickAnswerService:
    """快速问答服务——快速问答链路"""

    def __init__(
        self,
        llm: LLMProvider,
        retriever=None,
    ):
        """装配快速问答链路依赖（retriever 缺省时为 None，走兼容分支）"""
        self.llm = llm
        self.retriever = retriever

    # ── 工具 ─────────────────────────────────────────────────

    async def _chat_stream_with_fallback(
        self,
        messages: list[dict[str, str]],
        usage: dict[str, int],
    ) -> AsyncGenerator[StreamEvent, None]:
        """流式生成：失败重试一次，仍失败发 ERROR + 兜底回答

        兜底回答保证前端不白屏（检索来源已在此之前发送，用户仍能看到证据）。
        空流（0 chunk 无异常）视为失败重试——deepseek-v4-flash 偶发
        空响应会致回答空白。
        已产出 chunk 的流中途失败**不再重试**——已 yield 的内容客户端
        已消费，从头重试会产生"半截+完整"重复内容；
        此时只走 ERROR + 兜底提示。
        """
        for attempt in (1, 2):
            got_chunk = False
            try:
                agen = self.llm.chat_stream(messages, usage_tracker=usage)
                # 首 token 45s 超时——DeepSeek 挂起多为不发首 token（读超时
                # 90s × 2 次重试 = 3 分钟无反馈,演示场景不可接受）;
                # 首 token 到达后 read=90s 继续兜底后续卡顿
                try:
                    first = await asyncio.wait_for(agen.__anext__(), timeout=45)
                except BaseException:
                    await agen.aclose()  # 超时/异常时关闭悬挂生成器,防资源泄漏
                    raise
                got_chunk = True
                yield StreamEvent(
                    type=StreamEventType.CONTENT,
                    data=first,
                    timestamp=time.time(),
                )
                async for chunk in agen:
                    yield StreamEvent(
                        type=StreamEventType.CONTENT,
                        data=chunk,
                        timestamp=time.time(),
                    )
                    await asyncio.sleep(0)
                return
            except Exception as e:
                logger.warning(f"LLM 流式失败（第 {attempt} 次）: {e}")
                if attempt == 1 and not got_chunk:
                    continue  # 空流重试一次
                break        # 已产出内容不再重试
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data="LLM 调用失败，请稍后重试",
            timestamp=time.time(),
        )
        yield StreamEvent(
            type=StreamEventType.CONTENT,
            data="（知识库检索已完成，但模型服务暂时不可用，请稍后重试）",
            timestamp=time.time(),
        )

    def _pipeline_event(self, stage: str, data: dict) -> StreamEvent:
        """pipeline 事件（检索流水线实时状态）"""
        return StreamEvent(
            type=StreamEventType.PIPELINE,
            data={"stage": stage, **data},
            timestamp=time.time(),
        )

    # ── 检索执行与证据组装（quick_answer / CRAG / 分解共用）────────

    async def _retrieve_and_merge(
        self, queries: list[str], trace: RetrievalTrace,
        pipe_queue: asyncio.Queue, results: list[dict[str, Any]],
    ) -> AsyncGenerator[StreamEvent, None]:
        """批量检索 + 去重合并（逐个查询，单个失败跳过）——主检索与回退共用

        每个查询走 _retrieve_with_progress（实时进度转发 + 断开取消）；
        结果按查询顺序合并，同一文本块只保留首次命中（seen 指纹去重）。
        """
        seen: set[Any] = set()
        if self.retriever is None:
            return  # 兼容分支:无检索器时不检索
        for q in queries:
            batch: list[dict[str, Any]] = []
            try:
                async for ev in self._retrieve_with_progress(q, trace, pipe_queue, batch):
                    yield ev
            except Exception as e:
                logger.warning(f"混合检索失败 ({q[:30]}): {e}")
                continue
            for d in batch:
                key = d.get("id") or (d.get("source"), d.get("content", "")[:50])
                if key not in seen:
                    seen.add(key)
                    results.append(d)

    async def _refuse_with_trace(
        self, trace: RetrievalTrace, t_start: float, reason: str,
    ) -> AsyncGenerator[StreamEvent, None]:
        """拒答 + 检索诊断（CRAG 拒答 / 空检索拒答共用）

        拒答也留一条检索诊断（拒答原因可见）；再发拒答事件（ERROR + 提示）。
        """
        ev = self._finalize_trace(trace, {}, t_start)
        if ev is not None:
            yield ev
        async for ev in self._refuse_answer(reason):
            yield ev

    async def _crag_correction(
        self, query: str, prep: QueryPlan, history: list[dict] | None,
        trace: RetrievalTrace, t_start: float, pipe_queue: asyncio.Queue,
        merged: list[dict[str, Any]],
    ) -> AsyncGenerator[StreamEvent, None]:
        """CRAG 纠错:换说法重查一轮 → 二次评估 → 仍不够拒答

        触发条件（评估 poor）由调用方判断;此处执行纠错与裁决。
        拒答时 merged 被清空——调用方据此结束（拒答事件已发出）。
        """
        yield self._pipeline_event("crag", {"status": "evaluating"})
        await asyncio.sleep(0)
# 二次改写以"已改写的检索词"为输入——否则 temperature=0.0 下
        # 输出与第一次相同，重检索是白跑一轮（白花 LLM + 抬高拒答率）。
        # 分解场景 eval_base = 原始问题（合并证据无单一改写词）。
        # 无 history 时改写原样返回——组合「原始问题+检索词」加宽召回面。
        rewritten = await rewrite_query(prep.eval_base, history, self.llm)
        if not rewritten or rewritten == prep.eval_base:
            rewritten = f"{query} {prep.eval_base}".strip()
        trace.crag_triggered = True
        logger.info(f"CRAG 重检索: {prep.eval_base[:30]} → {rewritten[:30]}")
        try:
            # 换说法再搜一次（和主检索相同的执行+转发模式;结果替换）
            merged.clear()
            async for ev in self._retrieve_with_progress(
                rewritten, trace, pipe_queue, merged
            ):
                yield ev
        except Exception as e:
            logger.warning(f"CRAG 重检索失败: {e}")
        yield self._pipeline_event("crag", {"status": "retried"})

        # 重检索后再评估一次——仍然不够 → 拒答（宁可不答，不编造）
        try:
            still_poor = (
                merged
                and await self._evaluate_retrieval(rewritten, merged) == RetrievalVerdict.POOR
            )
        except Exception as e:
            logger.warning(f"CRAG 二次评估失败: {e}")
            still_poor = False
        if still_poor:
            async for ev in self._refuse_with_trace(
                trace, t_start, "检索质量仍不足，知识库可能未覆盖该问题，已拒答"
            ):
                yield ev
            merged.clear()   # 拒答标记:调用方据此结束

    async def _retrieve_main(
        self, query: str, prep: QueryPlan,
        trace: RetrievalTrace, pipe_queue: asyncio.Queue,
        results: list[dict[str, Any]],
    ) -> AsyncGenerator[StreamEvent, None]:
        """统一检索入口:批量检索 + 回退兜底 + 融合事件——产出最终检索结果

        怎么搜（逐个查询、去重合并）、要不要回退兜底（子查询全失败 → 原始
        问题宽检索）都是检索内部考虑的问题;调用方拿到 results 即最终检索结果。
        CRAG 质检**不在此**——它是检索之后独立的"质量把关"步骤。
        """
        # 批量检索（逐个查询,去重合并;单个查询失败只跳过它）
        async for ev in self._retrieve_and_merge(prep.retrieval_queries, trace, pipe_queue, results):
            yield ev

        # 回退兜底:子查询全部失败 → 原始问题宽检索一次
        # （两阶段语义:必须等上面结果才知道是否回退,不能合入同一循环）
        if not results and len(prep.retrieval_queries) > 1:
            async for ev in self._retrieve_and_merge([query], trace, pipe_queue, results):
                yield ev

        # → 前端流水线显示: "融合完成（merged 条候选 / N 个来源）"
        yield self._pipeline_event("fuse", {
            "merged": len(results),
            "sources": len({d.get("source", "") for d in results}),
        })

    async def _retrieve_with_progress(
        self, retrieval_query: str, trace: RetrievalTrace,
        pipe_queue: asyncio.Queue, results: list[dict[str, Any]],
        graph_intent: bool = True,
    ) -> AsyncGenerator[StreamEvent, None]:
        """检索一次——后台任务执行 + Queue 事件实时转发

        主检索与 CRAG 重检索两段逐字重复的收口：事件轮询、客户端断开取消
        全部在此；结果写入 out（调用方持有的引用，generator 无法 return
        结果，用 side-channel 传递）。
        graph_intent: 允许图谱问答路直答（检索器内部意图路由；
        eval/纯检索调用不开启）。
        （同步 VectorStore 分派已删——全项目检索器均为 async，无同步使用方）
        """
        # 后台任务检索 + 主循环轮询转发（Queue 桥接）
        ret_task = asyncio.create_task(
            self.retriever.retrieve(
                retrieval_query, trace=trace, graph_intent=graph_intent
            )
        )
        try:
            while not ret_task.done() or not pipe_queue.empty():
                try:
                    ev = await asyncio.wait_for(
                        pipe_queue.get(), timeout=settings.pipe_poll_interval,
                    )
                    yield self._pipeline_event(ev["stage"], ev["data"])
                except asyncio.TimeoutError:
                    continue
            results.extend(ret_task.result())
        finally:
            # 客户端断开（生成器被 close）时取消后台任务，避免孤儿任务泄漏
            if not ret_task.done():
                ret_task.cancel()

    def _build_doc_context(
        self, docs: list[dict],
    ) -> tuple[list[dict], list[dict], str]:
        """证据净化 + 编号上下文（quick_answer / 分解共用）

        返回 (过滤后子块, 父块列表, 编号上下文)——父块列表供调用方
        统计 context_docs_count（ZERO_RESULT 事件），不需要的调用方用 _ 忽略。
        ① 块级噪声过滤——低分单票弱块不进 LLM（与 CRAG 互补）
        ② 父子替换——子块命中 → 取父块（节）送 LLM，语义完整；
           sources 事件仍用过滤后的子块（引用定位精确）
        """
        filtered = filter_noise_chunks(docs)
        context_docs = resolve_parent_chunks(
            filtered, getattr(self.retriever, "doc_store", None)
        )
        # 总量上限:多查询合并(复合问题)可能超 LLM 窗口——按块数截断,
        # 8 条 × 1500 字 ≈ 8-9k token 窗口内安全(settings.context_max_chunks)
        context_docs = context_docs[: settings.context_max_chunks]
        doc_context = ""
        if context_docs:
            doc_context = "\n".join(
                f"[{i + 1}] {d['content'][: settings.context_block_chars]}"
                for i, d in enumerate(context_docs)
            )
        return filtered, context_docs, doc_context

    def _emit_sources_event(
        self,
        docs: list[dict],
        clip_by_source: dict[str, list[str]] | None = None,
    ) -> StreamEvent:
        """SOURCES 事件（证据链）——quick / 分解共用；CLIP 图文归并可选"""
        clip_by_source = clip_by_source or {}
        return StreamEvent(
            type=StreamEventType.SOURCES,
            data={
                "items": [
                    {
                        "index": i + 1,
                        "id": d.get("id", ""),
                        "source": d.get("source", ""),
                        "paths": d.get("paths", []),
                        "graph_anchor": d.get("graph_anchor"),
                        "image_url": d.get("metadata", {}).get("image_path"),
# 归并兼容两种 key（映射表 + CLIP 视觉命中）
                        # 已收口 multimodal/evidence.merge_source_images
                        "images": merge_source_images(d.get("source", ""), clip_by_source),
                        "content": d["content"][:200],
                    }
                    for i, d in enumerate(docs)
                ]
            },
            timestamp=time.time(),
        )

    async def _generate_answer(
        self,
        query: str,
        history: list[dict] | None,
        docs: list[dict[str, Any]],
        trace: RetrievalTrace,
        t_start: float,
        clip_by_source: dict[str, list[str]] | None = None,
        label: str = "",
    ) -> AsyncGenerator[StreamEvent, None]:
        """证据组装 + 流式生成 + 检索诊断（quick / 分解共用尾部）

        第 6-8 步统一收口:
          6. 净化（噪声过滤 + 父子替换）→ 编号上下文 → 诊断统计 → 证据链
          7. 提示词（编号资料 + 问题）→ 流式生成（失败自动重试 + 兜底）
          8. 检索诊断 trace 事件 + jsonl 落盘

        clip_by_source 仅 quick 路传（CLIP 图文归并，vision 路传入识别命中）;
        分解路不传。图片证据（图注块）续接编号追加进上下文。
        label 非空时打证据统计日志（分解路）。
        """
        # 6a. 证据净化 + 编号上下文（两级净化见 _build_doc_context）
        filtered_docs, context_docs, doc_context = self._build_doc_context(docs)

        # 6a1. CLIP 图片证据:视觉命中 source 的图注块 → 续编号追加
        doc_context = await self._append_image_evidence(
            clip_by_source, context_docs, doc_context,
        )

        # 6b. → 前端诊断面板显示: 检索 N 条 / 上下文 M 条
        yield self._diagnostics_event(query, len(docs), len(context_docs))
        await asyncio.sleep(0)

        # 6c. → 前端右侧"证据链"显示（引用定位用过滤后的子块，精确）
        if filtered_docs:
            yield self._emit_sources_event(filtered_docs, clip_by_source)
            await asyncio.sleep(0)

        if label:
            logger.info("%s: 证据 %d 块（过滤后 %d）doc_context %d 字",
                        label, len(docs), len(filtered_docs), len(doc_context))

        # 7. AI 写答案:提示词要求按 [编号] 引用资料，不能自由发挥
        messages = self.llm.build_messages(
            render_system("agent_quick"),
            render_user(
                "agent_quick",
                doc_context=doc_context or "（未检索到相关知识）",
                query=await self._build_memory_context(query, history),
            ),
        )
        # → 前端流水线显示: "生成中"
        yield self._pipeline_event("generate", {"status": "start"})
        async for ev in self._stream_with_trace(messages, trace, t_start):
            yield ev

    async def _stream_with_trace(
        self, messages: list[dict], trace: RetrievalTrace, t_start: float,
    ) -> AsyncGenerator[StreamEvent, None]:
        """流式生成转发（失败自动重试一次 + 兜底）+ 检索诊断收尾（第 7-8 步）"""
        usage: dict[str, int] = {}
        # AI 每吐一个字 → 转发给前端（失败自动重试一次 + 兜底）
        async for ev in self._chat_stream_with_fallback(messages, usage):
            yield ev

        # 8. 检索诊断事件（trace 事件 + jsonl 落盘）
        ev = self._finalize_trace(trace, usage, t_start)
        if ev is not None:
            yield ev
        await asyncio.sleep(0)

    async def _append_image_evidence(
        self, clip_by_source: dict[str, list[str]] | None,
        context_docs: list[dict], doc_context: str,
    ) -> str:
        """CLIP 图片证据:视觉命中 source 的图注块 → 续编号追加

        独立证据链——不参与 RRF 排序,回答可引用视觉相似器物的图注;
        doc_store 缺失/取回失败自动降级为纯文本证据;
        取回逻辑收口 multimodal/evidence.collect_image_evidence。
        """
        if not clip_by_source or getattr(self.retriever, "doc_store", None) is None:
            return doc_context
        image_blocks = await collect_image_evidence(
            getattr(self.retriever, "doc_store"),
            clip_by_source,
            exclude_ids={d.get("id") for d in context_docs},
        )
        if not image_blocks:
            return doc_context
        start = len(context_docs) + 1
        doc_context += "\n" + "\n".join(
            f"[{start + i}] {b['content'][: settings.context_block_chars]}"
            for i, b in enumerate(image_blocks)
        )
        return doc_context

    def _diagnostics_event(
        self, query: str, retrieved_count: int, context_count: int,
    ) -> StreamEvent:
        """检索统计事件（前端诊断面板:检索 N 条 / 上下文 M 条）"""
        return StreamEvent(
            type=StreamEventType.ZERO_RESULT,
            data={
                "extracted_question": query,
                "retrieved_docs_count": retrieved_count,
                "context_docs_count": context_count,
            },
            timestamp=time.time(),
        )

    def _finalize_trace(self, trace: RetrievalTrace, usage: dict,
                        t_start: float) -> StreamEvent | None:
        """TRACE 事件 + jsonl 落盘（quick 生成完成/拒答/分解共用）

        之前拒答与分解路径从不发 TRACE——查询不可观测；此处统一收口，
        拒答也留一条检索诊断记录（含拒答时的总耗时）。
        """
        trace.total_ms = (time.time() - t_start) * 1000
        trace.llm_usage = usage
        ev = StreamEvent(
            type=StreamEventType.TRACE,
            data=trace.to_dict(),
            timestamp=time.time(),
        )
        try:
            write_trace_jsonl(trace)
        except Exception as e:
            logger.warning(f"trace 落盘失败: {e}")
        return ev

    async def _build_memory_context(
        self, query: str, history: list[dict] | None,
    ) -> str:
        """构建多轮记忆上下文（>6 轮旧轮次 LLM 摘要压缩，≤6 轮硬截断）"""
        from conversation.application.memory import build_context

        return await build_context(query, history, self.llm)

    # ── CRAG 检索质量评估 ──────────────────────────────────

    async def _evaluate_retrieval(
        self, query: str, docs: list[dict],
    ) -> RetrievalVerdict:
        """检索质量评估（CRAG）：GOOD / POOR

        LLM 判断 top-8 来源摘要是否足以回答；评估失败/解析失败时
        保守返回 GOOD（不触发重检索，避免异常循环）。
        """
        try:
            items = []
            for d in docs[:8]:
                src = d.get("source") or d.get("metadata", {}).get("source", "?")
                content = (d.get("content") or "")[:80].replace("\n", " ")
                items.append(f"- {src}: {content}")
            messages = self.llm.build_messages(
                render_system("eval_retrieval"),
                render_user("eval_retrieval", query=query, results="\n".join(items)),
            )
            raw = await llm_call_with_retry(
                messages, self.llm, temperature=0.0,
                response_format={"type": "json_object"},  # DeepSeek JSON Output
            )
            from core.json_utils import extract_json

            data = extract_json(raw)
            if data is not None:
                verdict = str(data.get("verdict", "good"))
                return (
                    RetrievalVerdict(verdict)
                    if verdict in ("good", "poor") else RetrievalVerdict.GOOD
                )
        except Exception as e:
            logger.warning(f"检索质量评估失败: {e}")
        return RetrievalVerdict.GOOD

    # ── 拒答机制（不编造：知识库未覆盖时明确告知）──────────────

    async def _refuse_answer(
        self, reason: str,
    ) -> AsyncGenerator[StreamEvent, None]:
        """拒答：不调 LLM 生成，明确告知知识库未覆盖（防幻觉编造）

        ERROR 事件供前端标记；CONTENT 给出可操作提示（换问法/上传文档）。
        拒答原因经 PIPELINE refuse 事件 → 前端流水线面板"已拒答"行展示。
        """
        yield self._pipeline_event("refuse", {"reason": reason})
        await asyncio.sleep(0)
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data="知识库未覆盖",
            timestamp=time.time(),
        )
        await asyncio.sleep(0)
        yield StreamEvent(
            type=StreamEventType.CONTENT,
            data="知识库中未找到与该问题直接相关的资料，无法给出准确回答。"
                 "您可以换一种问法，或上传相关文档后重试。",
            timestamp=time.time(),
        )
        await asyncio.sleep(0)

    def _query_ready_event(self, prep: "QueryPlan") -> StreamEvent:
        """查询准备完成事件(前端流水线面板展示)——拆解/改写两种形态

        len(queries) > 1 即拆解产物;拆解后只剩 1 个子查询(空子查询被过滤)
        时按改写展示、CRAG 基准用该子查询改写词(更精确)。
        """
        if len(prep.retrieval_queries) > 1:
            # → 前端显示: "✂ 复合问题: 拆分为 N 个子查询: ..."
            return self._pipeline_event("decompose", {
                "count": len(prep.retrieval_queries),
                "sub_queries": prep.retrieval_queries,
            })
        # → 前端显示: "改写: 妇好鸮尊属于什么朝代"
        return self._pipeline_event("rewrite", {"rewritten_query": prep.retrieval_queries[0]})

    # ── 统一回答编排（quick 与 vision 共用）──────────────────

    async def _answer_flow(
        self, query: str, history: list[dict] | None,
        prep: QueryPlan, trace: RetrievalTrace, t_start: float,
        clip_by_source: dict[str, list[str]] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """统一回答编排:查询准备事件 → 检索 → CRAG → 拒答 → 净化 → 生成

        quick_answer 与 vision 共用——差异只在"查询准备"阶段
        （quick 拆解/改写,vision 识别名+问题）与图片证据来源:
          - quick:  clip_by_source 缺省 → 内部 text_search(图文互检)
          - vision: 传入 image_search 命中(识别结果即图片证据)
        流程（每一步结束都会往前端发一条消息）:

          第 1 步 查询准备  → decompose/rewrite 事件
          第 2 步 找资料    _retrieve_main:批量检索 + 回退兜底 → 融合
          第 3 步 质检 CRAG 资料够吗? 不够 → 换说法重查 → 还不够 → 拒答
          第 4 步 空检索    一条资料都没有 → 拒答（不编造）
          第 5 步 生成      整理证据 → AI 写答案 → 发检索诊断
        """
        # 第 1 步:查询准备事件（前端流水线展示;vision 的 prep 由调用方构造）
        # 诊断面板改写词:拆解(多查询)无单一改写词 → None
        trace.rewritten_query = prep.retrieval_queries[0] if len(prep.retrieval_queries) == 1 else None
        yield self._query_ready_event(prep)
        await asyncio.sleep(0)

        # 搭"消息中转站"（PipeQueue）:
        # 检索在后台任务里跑，每完成一路就塞一条进度进中转站；
        # 下面循环盯着中转站，有消息就 yield 给前端 → 流水线节点逐条亮起。
        # （为什么不能直接 await 检索? Python 规则:await 等待期间不能 yield。
        #   所以拆成两个角色:后台任务专心干活 + 主循环专心转发。）
        pipe_queue = self._setup_pipe_queue(trace)

        # 第 2 步:检索（统一入口 _retrieve_main——批量检索 + 回退兜底,
        # 产出最终检索结果;CRAG 质检在检索之后独立进行）
        merged: list[dict[str, Any]] = []
        async for ev in self._retrieve_main(query, prep, trace, pipe_queue, merged):
            yield ev

        # 第 3 步:质检（CRAG）——检索之后独立的质量把关
        # 触发条件:开关 + 有结果 + 评估"不够"。纠错与裁决在 _crag_correction:
        # 换说法重查一轮 → 二次评估 → 仍不够拒答（merged 清空 → 下方结束）
        if (
            settings.crag_enabled
            and merged
            and await self._evaluate_retrieval(prep.eval_base, merged) == RetrievalVerdict.POOR
        ):
            async for ev in self._crag_correction(
                query, prep, history, trace, t_start, pipe_queue, merged
            ):
                yield ev
            if not merged:
                return   # 已拒答（诊断 + 拒答事件已发出）

        # 第 4 步:一条资料都没搜到? → 拒答
        # 完全没搜到时不调 LLM 编造，直接告知"知识库未覆盖"
        # （retriever=None 的测试/兼容场景跳过拒答，走原 LLM 兜底）
        if self.retriever is not None and not merged:
            async for ev in self._refuse_with_trace(
                trace, t_start, "未检索到相关资料，知识库可能未覆盖该问题，已拒答"
            ):
                yield ev
            return

        # 第 5 步:整理证据 → AI 写答案 → 检索诊断
        # CLIP 图片证据:quick 未传时内部 text_search(图文互检,按问题找图);
        # vision 传入 image_search 命中(识别结果即图片证据)
        if clip_by_source is None:
            clip_by_source = await self._collect_clip_by_source(query)

        # 净化 / 证据链 / 生成 / 诊断 统一收口
        async for ev in self._generate_answer(
            query, history, merged, trace, t_start, clip_by_source,
        ):
            yield ev

    def _setup_pipe_queue(self, trace: RetrievalTrace) -> asyncio.Queue:
        """搭 PipeQueue 并把 emitter 挂到 trace——检索进度实时转发桥

        后台任务每完成一路就塞一条进度;主循环盯着队列逐条 yield,
        流水线节点实时点亮。队列满丢进度（只影响展示,不影响回答）。
        """
        pipe_queue: asyncio.Queue = asyncio.Queue()

        def _emit_pipeline(stage: str, data: dict) -> None:
            try:
                pipe_queue.put_nowait({"stage": stage, "data": data})
            except Exception:
                pass   # 队列满了就丢这条进度（只影响展示，不影响回答质量）

        trace._emitter = _emit_pipeline
        return pipe_queue

    async def _collect_clip_by_source(self, query: str) -> dict[str, list[str]]:
        """quick 路内部图文互检:按问题 text_search 找视觉相关图（失败降级空）"""
        clip_by_source: dict[str, list[str]] = {}
        try:
            from multimodal.clip_retrieval import clip_retriever

            for h in await clip_retriever.text_search(query):
                if h.get("source") and h.get("image_path"):
                    clip_by_source.setdefault(h["source"], []).append(h["image_path"])
        except Exception as e:
            logger.warning(f"CLIP 图文互检失败: {e}")
        return clip_by_source

    # ── 快速问答 ─────────────────────────────────────────────

    async def quick_answer(
        self,
        query: str,
        history: list[dict] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """回答一个问题（快速模式）——查询准备 + 统一编排

        查询准备（拆解/改写）→ _answer_flow(检索/CRAG/拒答/生成)——与
        vision 链路共用编排,差异只在查询准备阶段。
        """
        trace = RetrievalTrace(trace_id=new_trace_id(), query=query)  # 本轮检索的"档案"
        t_start = time.time()
        # 查询准备(拆解/改写 → 查询计划)在 query_understanding,quick 只做编排
        prep = await build_query_plan(query, history, self.llm)
        async for ev in self._answer_flow(query, history, prep, trace, t_start):
            yield ev
