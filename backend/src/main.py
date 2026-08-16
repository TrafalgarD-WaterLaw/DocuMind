"""FastAPI 应用入口"""
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

# uvicorn 的 dictConfig 不配置 root logger——项目 logger（propagate 到
# root）默认 WARNING 级别，logger.info() 会静默丢失。basicConfig 先于
# uvicorn 执行；uvicorn 的 LOGGING_CONFIG 有 disable_existing_loggers=False，
# 不会覆盖这里的 root 配置。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
# watchfiles 的 Rust 层在 uvicorn 过滤前打 "N changes detected"——启动期间
# src/data 下的数据写入（任务日志恢复/Chroma sqlite）会触发 2-3 条噪音日志。
# 这些变化不匹配 reload_includes（默认 *.py），不会触发重启；仅压日志级别。
logging.getLogger("watchfiles.main").setLevel(logging.WARNING)

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from conversation.interfaces.chat_routes import router as chat_router
from graph.interfaces.knowledge_routes import router as knowledge_router
from conversation.interfaces.research_routes import router as research_router
from api.stats import router as stats_router
from documents.interfaces.document_routes import router as document_router
from ingestion.interfaces.upload_routes import router as upload_router
from conversation.interfaces.vision_routes import router as vision_router
from core.config import settings
from core.di import container

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理——重服务全部后台预热，启动不阻塞

    向量存储/embedder/CLIP/BM25 统一由 prewarm 后台线程预热
    （vector 等重资源不在事件循环内同步加载）。这里仅做轻量探测
    （graph 连接）与耗时统计。
    """
    t0 = time.perf_counter()

    # ── 预热图存储（轻量探测；未配置/宕机时静默降级文本检索）──
    g = container.graph
    if g:
        logger.info("GraphStore: ready")
    else:
        logger.info("GraphStore: unavailable (Neo4j may not be running)")

    # ── 后台预热懒加载服务（vector/embedder/CLIP/BM25——请求前就绪，
    # 首问不被 10-30s 同步加载阻塞；单项失败只降级该项）──
    try:
        from core.prewarm import prewarm

        prewarm()
    except Exception as e:
        logger.warning(f"模型预热启动失败: {e}")

    # 暴露容器给路由
    app.state.container = container
    logger.info(f"启动完成，总耗时 {time.perf_counter() - t0:.1f}s（重模型后台预热中）")

    yield

    # ── 清理 ──
    try:
        container.close()
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")
    logger.info(f"应用退出，运行时长 {time.perf_counter() - t0:.1f}s")


app = FastAPI(
    title="智慧文物探索 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(  # type: ignore[call-arg]  # Pylance 对 starlette 1.3.1 的
    # ParamSpec Protocol（__call__ 带位置专用 app 参数）推导失败而误报；
    # pyright 实测 0 errors，运行时正常。升级 starlette 后可移除本忽略。
    CORSMiddleware,
    # 本地演示固定 origin（前端 Vite 默认 0.0.0.0:5173，局域网访问经 192.168.137.1）
    allow_origins=["http://localhost:5173", "http://192.168.137.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 挂载 API 路由 ──
app.include_router(chat_router)
app.include_router(knowledge_router)
app.include_router(research_router)
app.include_router(stats_router)
app.include_router(upload_router)
app.include_router(document_router)
app.include_router(vision_router)

# ── 上传图片静态服务（文档图片 T3 展示；StaticFiles 自带路径穿越防护）──
UPLOAD_DIR = Path(settings.upload_dir)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# ── 数据集图片静态服务（多模态：映射表驱动展示，与 uploads 同根 src/data）──
IMAGES_DIR = Path(settings.upload_dir).parent / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        app="main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        reload_dirs=["src"],
    )
