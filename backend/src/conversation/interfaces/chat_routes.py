"""聊天 API——NDJSON 流式问答（统一走 ResearchOrchestrator）"""
import asyncio
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from core.di import container
from models.request import ChatRequest
from models.response import StreamEvent, StreamEventType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


async def _generate_quick(
    query: str, history: list[dict] | None = None
) -> AsyncIterator[str]:
    """快速问答——走 Orchestrator 单 Agent 流式输出

    Orchestrator 无状态（仅持有 llm/retriever/graph 引用），容器单例复用；
    LLM 网络错误等异常在此兜底——发 ERROR 事件再结束，前端统一展示，
    而不是收到一条残缺的流。
    """
    orchestrator = container.orchestrator
    try:
        async for event in orchestrator.quick_answer(query, history):
            yield event.to_ndjson()   # NDJSON 序列化收口在 StreamEvent 模型
            await asyncio.sleep(0)
    except Exception as e:
        logger.exception(f"Quick 问答流式中断: {e}")
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=f"回答中断: {e}",
            timestamp=time.time(),
        ).to_ndjson()


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """快速问答——NDJSON 流式输出"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="查询内容不能为空")

    logger.info(f"Chat: {req.query[:100]}")

    history = [m.model_dump() for m in req.messages]
    return StreamingResponse(
        _generate_quick(req.query, history),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
