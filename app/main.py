"""FastAPI 应用工厂：CORS + 路由挂载。

启动：uvicorn app.main:app --reload --port 8000  （必须 --workers 1，Qdrant local 单进程锁）
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ask, docs, health, ingest

app = FastAPI(
    title="RAG 产品 · fastapi-app × rag",
    description="FastAPI 壳 + rag 核心：文档入库（MinerU 解析）→ FastEmbed 向量 → Qdrant → 混合检索 + rerank → DeepSeek 生成 + 引用校验",
    version="1.0.0",
)

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
app.include_router(docs.router, prefix=API_PREFIX, tags=["docs"])
app.include_router(ingest.router, prefix=API_PREFIX, tags=["ingest"])
app.include_router(ask.router, prefix=API_PREFIX, tags=["ask"])


@app.get("/", tags=["root"])
async def root() -> dict:
    return {"name": "RAG 产品", "docs": "/docs", "api": API_PREFIX}