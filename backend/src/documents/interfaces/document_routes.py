"""文档管理 API——已入库文档的列表与删除（DDD 接口层）

"""
import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException

from core.di import container
from documents.application.task_manager import task_manager
from ingestion.application.upload_pipeline import _delete_source

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["documents"])

@router.get("/documents")
async def list_documents() -> dict[str, Any]:
    """列出已入库文档源（含切片/问题数/最近任务状态）"""
    from collections import Counter

    # 批量统计：一次 get 全量 metadata + Python 分组计数，
    # 替代逐 source count_by_source（2855 source × 2 次 Chroma get ≈ 90s → 1s）
    # 同步全量拉取经 to_thread——不阻塞事件循环（其他请求不被文档列表卡住）
    chunk_counts: Counter = Counter()
    q_counts: Counter = Counter()

    async def _batch_count() -> None:
        try:
# 走接口方法（get_all_metadatas），不直接摸 collection
            for meta in await asyncio.to_thread(container.vector.get_all_metadatas):
                chunk_counts[(meta or {}).get("source", "")] += 1
            for meta in await asyncio.to_thread(container.questions.get_all_metadatas):
                q_counts[(meta or {}).get("source", "")] += 1
        except Exception as e:
            logger.warning(f"文档统计批量计数失败，回退逐 source: {e}")
            sources_now = await asyncio.to_thread(container.vector.list_sources)
            for source in sources_now:
                chunk_counts[source] = await asyncio.to_thread(
                    container.vector.count_by_source, source
                )
                q_counts[source] = await asyncio.to_thread(
                    container.questions.count_by_source, source
                )

    sources = set(await asyncio.to_thread(container.vector.list_sources))
    await _batch_count()

# #图 后缀的图片块 source 不列为独立文档行（图片归属文本文档），
    # 避免"删除 #图 行造成半删除"的混乱 UI
    sources = {s for s in sources if not s.endswith("#图")}

    docs = []
    for source in sorted(sources):
        latest = task_manager.latest_by_source(source)
        docs.append({
            "source": source,
            "chunks": chunk_counts.get(source, 0),
            "questions": q_counts.get(source, 0),
            "pages": latest.pages if latest else 0,
            "status": latest.status.value if latest else "done",
            "created_at": latest.created_at if latest else 0,
        })
    return {"documents": docs, "count": len(docs)}


@router.delete("/documents/{source}")
async def delete_document(source: str) -> dict[str, int | str]:
    """按来源删除文档及其所有 chunk（含对应假设性问题与任务记录）

    路由去掉 :path（uvicorn 会 unquote %2F——{source:path} 会放行
    `..%2F..%2F` 路径穿越到 _cleanup_source_images 的 rmtree）；单段
    参数天然挡掉含 / 的路径。再加白名单校验做深度防御。
    入参归一化 removesuffix("#图")——#图 行删除等价于删整篇。
    """
    if not source:
        raise HTTPException(status_code=400, detail="来源为空")
    # H1 深度防御：source 仅允许安全字符（数据源: 青铜-xxx / 河南博物院-xxx；
    # 上传: {时间戳}_{文件名}——清洗后不含 / \\ # ? %），禁止 .. 逃逸
    if not re.fullmatch(r"[^/\\]+\Z", source) or ".." in source:
        raise HTTPException(status_code=400, detail="非法的 source 参数")
    removed = _delete_source(source.removesuffix("#图"))
    return {"removed": removed, "source": source}
