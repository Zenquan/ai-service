"""智能客服会话 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.services.customer_service import customer_service

router = APIRouter()


class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="客户消息")


@router.get("/conversations")
async def list_conversations(limit: int = 50) -> list[dict]:
    return customer_service.list_conversations(limit)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict:
    return customer_service.get_conversation(conversation_id)


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str) -> list[dict]:
    return customer_service.get_messages(conversation_id)


@router.post("/conversations/{conversation_id}/messages")
async def create_message(conversation_id: str, req: MessageRequest) -> dict:
    try:
        return await run_in_threadpool(customer_service.handle_message, conversation_id, req.message)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
