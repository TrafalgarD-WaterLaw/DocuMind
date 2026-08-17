"""图像识别 + RAG 联动 API——多模态问答

流程: 上传图片 → CLIP 图找文识别（跨模态零样本）→ 混合检索
      → 证据锚定流式回答

识别主通道: CLIP 图找文按外观相似度在 9468 张图索引中返回真实
条目,是识别-检索一体通道。
"""
import asyncio
import io
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image

from core.config import settings
from core.di import container
from core.tracing import RetrievalTrace, new_trace_id
from models.response import StreamEvent, StreamEventType
from conversation.domain.query_plan import QueryPlan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vision", tags=["vision"])

# 置信度阈值（settings.vision_low_conf_threshold）: CLIP 图-图余弦,
# 低于阈值说明库内无外观相似文物,避免拿不确定识别名去检索编造
LOW_CONF_THRESHOLD = settings.vision_low_conf_threshold


def _make_event(type_: StreamEventType, data: Any) -> StreamEvent:
    return StreamEvent(type=type_, data=data, timestamp=time.time())


async def _recognize(
    image: Image.Image,
) -> tuple[str, float, bool, list[dict]]:
    """CLIP 图找文识别——识别名 + 置信度 + 低置信标记 + 全部命中

    识别主通道:图找图按外观返回真实条目(top1 source 即识别名,
    score 即置信度)。识别名友好化:上传文档图 source 为
    {时间戳}_{文件名}.pdf#图,剥离 #图 后缀与时间戳前缀,
    展示/检索用文档名。
    """
    clip_hits: list[dict] = []
    try:
        from multimodal.clip_retrieval import clip_retriever

        # top_k=6——过滤原图本身后仍留同器物其他照片做证据（识别候选仍取前 3）
        clip_hits = await clip_retriever.image_search(image, top_k=6)
    except Exception as e:
        logger.warning(f"CLIP 图找文失败: {e}")

    result = ""
    confidence = 0.0
    if clip_hits and clip_hits[0].get("source"):
        # 识别名友好化——剥离 #图 后缀与时间戳前缀
        result = re.sub(r"^\d+_", "", clip_hits[0]["source"].removesuffix("#图"))
        confidence = float(clip_hits[0].get("score", 0.0))
    low_confidence = (not result) or confidence < LOW_CONF_THRESHOLD
    return result, confidence, low_confidence, clip_hits


def _prepare_vision_query(
    query: str, confidence: float, low_confidence: bool,
    clip_hits: list[dict],
) -> QueryPlan:
    """vision 查询准备:识别名 top3 候选 + 用户问题 → 多查询计划

    与文本问答 build_query_plan 的产出同构——消费方(_answer_flow)无感知。
    识别名 "{域}-{器物名}" 剥域前缀(检索词更干净);top1 识别错时
    top2/3 候选兜底(复用 QueryPlan 多查询机制——逐个检索后合并,与
    文本问答的拆解路径同款);无相似物时退回纯用户问题。
    """
    def _clean(src: str) -> str:
        parts = src.split("-", 1)
        return parts[1].strip() if len(parts) > 1 else parts[0].strip()

    names: list[str] = []
    for h in (clip_hits or [])[:3]:
        if h.get("source"):
            n = _clean(str(h["source"]))
            if n and n not in names:
                names.append(n)
    if names:
        queries = [f"{n} {query}".strip() for n in names]
    else:
        queries = [query.strip()]
    eval_base = queries[0]
    if low_confidence:
        logger.info(f"vision 未找到相似文物（conf={confidence:.2f}）→ 检索词={eval_base[:60]}")
    return QueryPlan(retrieval_queries=queries, eval_base=eval_base)


async def _generate_vision(
    query: str, image: Image.Image
) -> AsyncIterator[str]:
    """多模态问答——CLIP 识别 + 统一回答编排（与文本问答共用）

    与 /api/chat 的差异只有两点（编排完全一致:检索/CRAG/拒答/净化/生成/
    流水线/trace/兜底全由 _answer_flow 提供）:
      1. 查询准备 = 识别名 + 用户问题（不走拆解/改写）
      2. 图片证据 = image_search 命中（识别结果即图片证据）
    """
    # 1. CLIP 识别（识别名/置信度/低置信标记/全部命中）
    result, confidence, low_confidence, clip_hits = await _recognize(image)

    # 识别事件（带置信度——前端可显示"低置信/未收录"标记）
    yield _make_event(
        StreamEventType.RECOGNITION,
        {
            "result": result or "（知识库中未找到外观相似的文物）",
            "introduce": "",
            "confidence": round(confidence, 3),
            "low_confidence": low_confidence,
        },
    ).to_ndjson()
    await asyncio.sleep(0)

    # 2. 查询准备（统一接口 QueryPlan——识别名 top3 候选 + 用户问题）
    prep = _prepare_vision_query(query, confidence, low_confidence, clip_hits)

    # 3. 图片证据归并:image_search 命中 → clip_by_source（识别结果即图片,
    #    SOURCES 证据链自动携带;M2 双 key 归并在 _emit_sources_event 内）
    #    过滤 score≈1.0 的命中（= 与上传原图同一张,展示无意义）——证据
    #    展示同器物其他照片;回答内容不受影响（识别名仍取 top1 source）
    clip_by_source: dict[str, list[str]] = {}
    for h in clip_hits:
        if (
            h.get("source") and h.get("image_path")
            and float(h.get("score", 0)) < 0.99
        ):
            clip_by_source.setdefault(h["source"], []).append(h["image_path"])

    # 4. 统一编排（与文本问答共用——检索/CRAG/拒答/净化/生成/诊断/兜底）
    # 走 orchestrator 单例的 vision_answer 门面（与 chat/research 同模式）
    # 识别名强约束注入 prompt——LLM 只围绕识别结果作答,不列举其他文物
    vision_hint = (
        f"用户上传的照片已识别为「{result}」（置信度 {confidence:.2f}）。"
        "请**只围绕这一件文物**作答,回答它是什么、什么时期、材质、尺寸、"
        "出土信息等;不要列举或比较知识库中的其他文物。"
        if result else ""
    )
    trace = RetrievalTrace(trace_id=new_trace_id(), query=query)
    t_start = time.time()
    async for ev in container.orchestrator.vision_answer(
        query, None, prep, trace, t_start, clip_by_source, vision_hint,
    ):
        yield ev.to_ndjson()


@router.post("/chat")
async def vision_chat(
    file: UploadFile = File(...),
    query: str = Form(""),
) -> StreamingResponse:
    """上传图片（可附文字问题）→ CLIP 识别 → 检索 → 流式问答"""
    if not file:
        raise HTTPException(status_code=400, detail="请上传图片文件")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="上传的文件为空")

    try:
        with Image.open(io.BytesIO(contents)) as img:
            pil_image = img.copy()
    except Exception as e:
        logger.exception("图片解析失败")
        raise HTTPException(status_code=400, detail=f"无法解析图片: {str(e)}")

    logger.info(f"vision chat: query={query[:60] or '(无)'}")

    return StreamingResponse(
        _generate_vision(query, pil_image),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
