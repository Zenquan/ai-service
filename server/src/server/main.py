"""FastAPI 应用工厂：CORS + 路由挂载 + 静态前端（单容器部署）。

启动：uvicorn server.main:app --reload --port 8000  （必须 --workers 1，Qdrant local 单进程锁）
单容器部署：构建前端后（web/dist），由本服务直接托管静态资源，与 /api/v1 同域访问。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from server.api import ask, auth, customer_service, docs, health, ingest
from server.core import config as rag_config
from server.observability import TraceIdMiddleware, setup_logging
from server.services import rag

# 模块导入即配置日志（本地/测试/CLI 兜底）；lifespan 里再调用一次，
# 覆盖 uvicorn 启动时对 logging 的默认重置。
setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时重建 RAG 云存储：部署后 Qdrant 本地集合为空且 MySQL 有文档切块时重建向量索引。"""
    # uvicorn 在 lifespan 前已配置过默认 logging，这里用我们的 dictConfig 覆盖，
    # 保证 INFO 可见、trace_id 格式统一。
    setup_logging()
    try:
        result = await run_in_threadpool(rag.restore_from_cloud)
        if result["restored_docs"]:
            logger.info(
                "RAG 云存储重建完成：%d 份文档 / %d chunks",
                result["restored_docs"], result["restored_chunks"],
            )
        elif result.get("error"):
            logger.warning("RAG 云存储重建跳过: %s", result["error"])
    except Exception:  # noqa: BLE001 —— 重建失败不影响服务启动
        logger.exception("RAG 云存储重建异常（已降级，文档可稍后重传）")
    yield


app = FastAPI(
    title="RAG 产品 · fastapi-app × rag",
    description="FastAPI 壳 + rag 核心：文档入库（MinerU 解析）→ FastEmbed 向量 → Qdrant → 混合检索 + rerank → DeepSeek 生成 + 引用校验",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(TraceIdMiddleware)

# 前端 dev server（Vite）跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",   # vite preview
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"

app.include_router(health.router, prefix=API_PREFIX, tags=["health"])
app.include_router(auth.router, prefix=API_PREFIX, tags=["auth"])
app.include_router(docs.router, prefix=API_PREFIX, tags=["docs"])
app.include_router(ingest.router, prefix=API_PREFIX, tags=["ingest"])
app.include_router(ask.router, prefix=API_PREFIX, tags=["ask"])
app.include_router(customer_service.router, prefix=API_PREFIX, tags=["customer-service"])


@app.get("/api-info", tags=["root"])
async def root() -> dict:
    """API 基本信息（挂在 /api-info，/ 留给静态前端 SPA）。"""
    return {"name": "RAG 产品", "docs": "/docs", "api": API_PREFIX}


# 静态前端（单容器部署模式）：web/dist 存在时挂载 SPA，/ 返回 index.html。
# 路径推导：SERVER_ROOT（cwd）= server/ 或容器 /app/server，web/dist 在其上一级仓库根的 web/dist。
_WEB_DIST = rag_config.SERVER_ROOT.parent / "web" / "dist"
if _WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="web")
