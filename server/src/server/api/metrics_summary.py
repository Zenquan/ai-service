"""客服评测性能摘要 API（运营端「性能」看板数据源）。

- ``GET /api/v1/metrics/summary``：返回 JSON 聚合指标
  （总量 / 一次解决 / 转人工 / 追问 / 出错 / 工具成功率 / 引用有效率 / 时延 P50·P95 / 维度分布）。
  需运营角色（``get_operator``），避免把系统指标暴露给访客。

与 ``server.api.metrics``（Prometheus 文本，供抓取端）互补：这里输出结构化 JSON，
直接给前端看板消费。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from server.auth.accounts import AuthUser
from server.auth.dependencies import get_operator
from server.services.customer_service import customer_service

router = APIRouter()


@router.get("/metrics/summary")
async def metrics_summary(
    _operator: AuthUser = Depends(get_operator),
) -> dict:
    """聚合评测指标摘要（Memory / MySQL 同构 JSON）。"""
    recorder = customer_service.recorder
    return recorder.summary()


__all__ = ["router"]
