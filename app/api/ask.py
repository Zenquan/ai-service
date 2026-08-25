"""问答接口：混合检索 + rerank → DeepSeek 生成 → 引用校验。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import rag

router = APIRouter()


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="问题")
    use_rerank: bool | None = Field(None, description="None=按配置默认开；False=关闭；True=强制开")
    top_k: int | None = Field(None, ge=1, le=50, description="检索条数（默认 config.TOP_K=5）")


@router.post("/ask")
async def ask(req: AskRequest) -> dict:
    try:
        return rag.ask(req.query, use_rerank=req.use_rerank, top_k=req.top_k)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e