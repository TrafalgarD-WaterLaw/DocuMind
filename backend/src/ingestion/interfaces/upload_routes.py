"""文档上传与解析 API——异步任务化（提交即返回 task_id，前端轮询进度）

HTTP 层只做协议（路由/请求校验/响应组装）；解析管线业务
（分块/图片块/实体/问题生成/删除联动）在 services/upload_pipeline.py。
"""
import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from documents.application.task_manager import TaskStatus, UploadTask, task_manager
from ingestion.application.upload_pipeline import (
    UPLOAD_DIR,
    _delete_source,
    _run_pipeline,
)
from core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["upload"])


def _write_file(path: Path, contents: bytes) -> None:
    """文件落盘（线程池执行,不阻塞事件循环）"""
    with open(path, "wb") as f:
        f.write(contents)


def _sanitize_filename(name: str) -> str:
    """清洗上传文件名（M5）——客户端文件名不可信

    - 剥路径组件（\\ / 可构造路径逃逸）
    - 过滤 URL/路径特殊字符（# ? % & 会截断图片 URL 或产生歧义）
    返回纯文件名；空名回退 upload.pdf。
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[#?%&]|[/\\]", "_", base)
    return base.strip() or "upload.pdf"



@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]  # FastAPI 注入,无默认值亦可
    replace: bool = Form(False),
    chunk_size: int = Form(0),
    chunk_overlap: int = Form(0),
) -> JSONResponse:
    """上传文档 → 创建任务 → 后台解析入库（立即返回 task_id）"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")

    ext = Path(file.filename).suffix.lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail=f"暂不支持 {ext} 格式，仅支持 PDF")

    # P2 大小限制：先按 Content-Length 预检（不读文件），读后按实际字节复核——
    # 客户端可能不送 Content-Length（chunked 传输），预检只是快速失败路径
    max_size = settings.upload_max_size
    content_length = file.size if hasattr(file, "size") else None
    if content_length and content_length > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{content_length} 字节 > 上限 {max_size}），"
                   f"仅支持 {max_size // (1024 * 1024)}MB 以内 PDF",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
# 文件名清洗（剥路径组件 + 过滤 URL 特殊字符）后作为 source 前缀
    safe_name = f"{int(time.time())}_{_sanitize_filename(file.filename)}"
    file_path = UPLOAD_DIR / safe_name

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(contents) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(contents)} 字节 > 上限 {max_size}），"
                   f"仅支持 {max_size // (1024 * 1024)}MB 以内 PDF",
        )

    # U3 文件去重：SHA-256 指纹 → 已存在则 409 提示（可 replace 覆盖）
    from documents.application.hash_index import find_by_hash

    sha256 = hashlib.sha256(contents).hexdigest()
    existing = await asyncio.to_thread(find_by_hash, sha256)
    if existing and not replace:
        raise HTTPException(
            status_code=409,
            detail=f"文件内容已存在（{existing}），可传 replace=true 覆盖",
        )

    # 文件落盘（同步 I/O 经 to_thread——大 PDF 写入不阻塞事件循环）
    await asyncio.to_thread(_write_file, file_path, contents)

    task_id = task_manager.create_task(file_name=file.filename, source=safe_name)
    background_tasks.add_task(
        _run_pipeline, task_id, file_path, file.filename,
        safe_name, replace, chunk_size, chunk_overlap, sha256,
    )
    return JSONResponse(content={"task_id": task_id, "file_name": file.filename})


@router.get("/upload/tasks")
async def list_upload_tasks() -> dict[str, list[dict]]:
    """最近上传任务列表（前端挂载时恢复轮询）"""
    return {"tasks": [_task_dict(t) for t in task_manager.list_tasks()]}


@router.get("/upload/tasks/{task_id}")
async def get_upload_task(task_id: str) -> dict[str, Any]:
    """单个任务状态"""
    task = task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _task_dict(task)


def _task_dict(t: UploadTask) -> dict[str, Any]:
    """UploadTask → dict（供 JSON 序列化）"""
    return {
        "task_id": t.task_id,
        "file_name": t.file_name,
        "source": t.source,
        "status": t.status.value,
        "progress": t.progress,
        "stage_text": t.stage_text,
        "error": t.error,
        "pages": t.pages,
        "blocks": t.blocks,
        "chunks": t.chunks,
        "timings": t.timings,  # U6b 分阶段耗时（前端展示）
        "created_at": t.created_at,
        "finished_at": t.finished_at,
    }



