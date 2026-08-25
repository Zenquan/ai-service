"""健康检查 + 知识库统计。"""
from __future__ import annotations

from fastapi import APIRouter

from app.services import rag

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return rag.health()