"""问答接口：混合检索 + rerank → DeepSeek 生成 → 引用校验。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.services import rag

router = APIRouter()


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="问题")
    use_rerank: bool | None = Field(None, description="None=按配置默认开；False=关闭；True=强制开")
    top_k: int | None = Field(None, ge=1, le=50, description="检索条数（默认 config.TOP_K=5）")
    rerank_threshold: float | None = Field(None, ge=0, description="rerank 最低相关性分数")


@router.post("/ask")
async def ask(req: AskRequest) -> dict:
    try:
        params = {"use_rerank": req.use_rerank, "top_k": req.top_k}
        if req.rerank_threshold is not None:
            params["rerank_threshold"] = req.rerank_threshold
        return rag.ask(req.query, **params)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
