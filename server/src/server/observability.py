"""统一日志 + trace_id 链路追踪。

迭代 1（线上稳定与可观测）：
- ``setup_logging()``：INFO 起统一输出到 stdout，中文不乱码，每行自动带
  ``trace_id``；uvicorn 与应用日志走同一套格式，不再“哑火”。
- ``TraceIdMiddleware``：每个 HTTP 请求生成/透传 ``x-request-id``，写入响应头，
  并通过 contextvars 挂到整个请求作用域（含 SSE generator / 线程池）。

CLI、启动重建等无 HTTP 请求场景的日志 ``trace_id`` 显示 ``-``。
"""
from __future__ import annotations

import logging
import logging.config
import os
import uuid
from contextvars import ContextVar

from starlette.datastructures import Headers, MutableHeaders

TRACE_ID_HEADER = "x-request-id"

_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


def get_trace_id() -> str:
    """当前请求作用域内的 trace_id；无请求场景返回 ``-``。"""
    return _trace_id_var.get()


def new_trace_id() -> str:
    return uuid.uuid4().hex


class TraceIdFilter(logging.Filter):
    """把当前 contextvar 的 trace_id 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


_LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(trace_id)s] %(name)s: %(message)s"


def setup_logging(level: int | str | None = None) -> None:
    """配置根日志：INFO 起、单行格式、stdout、UTF-8 友好。

    放在模块 import 与 lifespan 里各调一次：模块导入先让本地/测试生效，
    lifespan 再覆盖 uvicorn 启动时的默认配置（uvicorn 默认会重置 logging）。
    """
    level = os.getenv("LOG_LEVEL", "INFO") if level is None else level
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 —— 清理失败不影响后续配置
            pass

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": _LOG_FORMAT,
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "filters": {
            "trace_id": {"()": TraceIdFilter},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "filters": ["trace_id"],
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            # server.* 日志在祖先 logger 上挂 filter：即使 handler 在 root，
            # 记录也先经过这里注入 trace_id（caplog/测试同样能拿到）。
            "server": {
                "level": level,
                "filters": ["trace_id"],
                "propagate": True,
            },
            # uvicorn 自身日志统一走同一 console handler，避免一套日志一套格式。
            "uvicorn": {
                "level": level,
                "handlers": ["console"],
                "filters": ["trace_id"],
                "propagate": False,
            },
            # uvicorn 默认会给 access/error 配独立 handler；显式接管避免重复输出。
            "uvicorn.error": {
                "level": level,
                "handlers": ["console"],
                "filters": ["trace_id"],
                "propagate": False,
            },
            "uvicorn.access": {
                "level": level,
                "handlers": ["console"],
                "filters": ["trace_id"],
                "propagate": False,
            },
            # 第三方 HTTP 客户端日志降噪（级别可单独用 LOG_LEVEL 覆盖）。
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        },
        "root": {
            "level": level,
            "handlers": ["console"],
        },
    })


class TraceIdMiddleware:
    """ASGI 中间件：为每个请求生成/透传 trace_id 并写入响应头。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        incoming = Headers(raw=scope.get("headers") or [])
        trace_id = incoming.get(TRACE_ID_HEADER) or new_trace_id()
        token = _trace_id_var.set(trace_id)
        try:
            async def send_with_trace_id(message):
                if message["type"] == "http.response.start":
                    # 原地改 header，避免重建整个响应消息
                    MutableHeaders(scope=message).setdefault(TRACE_ID_HEADER, trace_id)
                await send(message)

            await self.app(scope, receive, send_with_trace_id)
        finally:
            _trace_id_var.reset(token)
