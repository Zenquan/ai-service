"""知识库文档管理：列表 + 删除（增量，不重建索引）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import rag

router = APIRouter()


class DocItem(BaseModel):
    doc: str
    chunks: int


@router.get("/docs", response_model=list[DocItem])
async def list_docs() -> list[dict]:
    return rag.docs_list()


@router.delete("/docs/{doc_name}")
async def delete_doc(doc_name: str) -> dict:
    n = rag.docs_delete(doc_name)
    if n == 0:
        raise HTTPException(status_code=404, detail=f"库中不存在文档: {doc_name}")
    return {"deleted": n, "doc": doc_name}