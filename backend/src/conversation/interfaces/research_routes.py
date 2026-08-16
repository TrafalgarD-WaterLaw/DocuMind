"""研究 API——多 Agent 协作深度研究（流式 NDJSON）"""
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
router = APIRouter(prefix="/api", tags=["research"])


async def _generate_deep_research(
    query: str, history: list[dict] | None = None
) -> AsyncIterator[str]:
    """深度研究——多 Agent 协作流式输出

    Orchestrator 无状态,容器单例复用(与 chat 链路一致);
    LLM 异常兜底发 ERROR 事件,前端统一展示。
    """
    orchestrator = container.orchestrator
    try:
        async for event in orchestrator.deep_research(query, history):
            yield event.to_ndjson()
            await asyncio.sleep(0)
    except Exception as e:
        logger.exception("深度研究异常")
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=f"深度研究异常: {str(e)}",
            timestamp=time.time(),
        ).to_ndjson()


@router.post("/research")
async def deep_research(req: ChatRequest) -> StreamingResponse:
    """Agent 深度研究——多专家协作流式输出

    请求体: {"query": "..."}

    响应: application/x-ndjson 流,包含以下事件类型:
      - pipeline(expert): 专家执行进度(史官/工艺师/关联师/著述)
      - reasoning: 专家研究摘要
      - sources: 合并证据链
      - content: 研究报告内容(流式 chunk)
      - markdown_dict: 研究计划思维导图
      - error: 异常信息
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="研究问题不能为空")

    logger.info(f"Deep research query: {req.query[:100]}")

    history = [m.model_dump() for m in req.messages]
    return StreamingResponse(
        _generate_deep_research(req.query, history),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
