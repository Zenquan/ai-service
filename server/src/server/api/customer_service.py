"""智能客服会话 API。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from server.services.customer_service import customer_service

router = APIRouter()


class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="客户消息")


def _sse(event: dict) -> str:
    """把事件 dict 序列化为 SSE 帧：event: <type>\\ndata: <json>\\n\\n。"""
    kind = event.pop("event", "message")
    return f"event: {kind}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


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


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(conversation_id: str, req: MessageRequest) -> StreamingResponse:
    """SSE 流式回复：meta/materials/token/done 事件（见 customer_service.stream_message）。

    事件序列：
      meta      —— 转人工/澄清分支（含完整回答），知识问答分支不发
      materials —— 检索结果（生成前）
      token     —— 生成 token 增量（多个）
      done      —— 终态（answer/citations/citation_valid/storage）
      error     —— 检索或生成失败
    """

    def event_source():
        try:
            for event in customer_service.stream_message(conversation_id, req.message):
                payload = dict(event)
                yield _sse(payload)
        except Exception as exc:  # noqa: BLE001 —— 流中途异常兜底
            yield _sse({"event": "error", "error": str(exc)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关闭反向代理缓冲，保证逐 token 推送
        },
    )
