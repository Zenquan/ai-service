"""客服评测告警查询 API。

- ``GET /api/v1/alerts/recent``：返回近期告警（倒序，默认 50 条）。
  需运营角色（``get_operator``），避免把系统指标暴露给访客。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from server.auth.accounts import AuthUser
from server.auth.dependencies import get_operator
from server.services.customer_service import customer_service

router = APIRouter()


@router.get("/alerts/recent")
async def recent_alerts(
    limit: int = 50,
    _operator: AuthUser = Depends(get_operator),
) -> list[dict]:
    """近期告警列表（倒序）。"""
    store = customer_service.alert_store
    return store.recent(limit=min(max(limit, 1), 200))


__all__ = ["router"]
