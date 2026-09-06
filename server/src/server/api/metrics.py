"""客服任务评测指标暴露（Prometheus 文本格式）。

挂载在 ``/metrics``（非 ``/api/v1`` 前缀），供 Prometheus / Grafana 抓取。
指标由 :class:`server.services.prom_exporter.PrometheusEvaluator` 从
``customer_service.recorder`` 增量聚合，用官方 ``prometheus_client`` 库序列化，
不手写协议。
"""
from __future__ import annotations

import threading

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from server.services.customer_service import customer_service
from server.services.prom_exporter import PrometheusEvaluator

router = APIRouter()

# 进程级单例：懒加载绑定到 customer_service 的 recorder，避免循环导入。
_evaluator: PrometheusEvaluator | None = None
_evaluator_lock = threading.Lock()


def _get_evaluator() -> PrometheusEvaluator:
    global _evaluator
    if _evaluator is None:
        with _evaluator_lock:
            if _evaluator is None:
                _evaluator = PrometheusEvaluator(customer_service.recorder)
    return _evaluator


@router.get("/metrics")
async def metrics() -> PlainTextResponse:
    """返回 Prometheus exposition 文本（cs_eval_* 指标）。"""
    evaluator = _get_evaluator()
    body = evaluator.render()
    return PlainTextResponse(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


__all__ = ["router"]
