"""FastAPI 中间件：API 响应自动 PII 脱敏。

行为：
- 仅作用于 ``/api/v1/`` 前缀下的 JSON 响应；
- 例外路径（默认不脱敏）：``/api/v1/alerts/*``（告警 payload 是系统指标，不含 PII）、
  ``/metrics``（Prometheus 文本格式，跳过）、``/health``（存活检查，不含敏感字段）；
- 错误响应（4xx/5xx 的 JSON body）同样会被脱敏，避免错误消息里的回显 PII 泄漏；
- 通过 ``REDACTOR_ENABLED=0`` 可全局关闭（用于调试与压测）。

实现要点：
- 读取响应 body（``response.body_iterator`` 已被消费）；使用 ``Response.body`` 直接设置；
- ``Content-Type`` 必须是 ``application/json``（含 ``application/json; charset=utf-8``）。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Awaitable, Callable

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from server.services.audit_log import get_audit_logger
from server.services.rate_limit import RateLimiter, get_rate_limiter
from server.services.redactor import REDACTOR_ENABLED, redact

logger = logging.getLogger(__name__)


_DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    r"^/api/v1/alerts/.*",
    r"^/metrics$",
    r"^/health$",
    r"^/health/.*",
)


def _is_excluded(path: str, patterns: tuple[str, ...]) -> bool:
    for pat in patterns:
        if re.match(pat, path):
            return True
    return False


class RedactResponseMiddleware(BaseHTTPMiddleware):
    """对 JSON 响应做 PII 脱敏的中间件。"""

    def __init__(
        self,
        app,
        api_prefix: str = "/api/v1/",
        exclude_patterns: tuple[str, ...] = _DEFAULT_EXCLUDE_PATTERNS,
    ) -> None:
        super().__init__(app)
        self._api_prefix = api_prefix
        self._exclude_patterns = tuple(exclude_patterns)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        if not REDACTOR_ENABLED:
            return response
        if not request.url.path.startswith(self._api_prefix):
            return response
        if _is_excluded(request.url.path, self._exclude_patterns):
            return response
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        try:
            raw = b""
            async for chunk in response.body_iterator:
                raw += chunk
            payload = json.loads(raw.decode("utf-8")) if raw else None
            if payload is None:
                return response
            cleaned = redact(payload)
            new_body = json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.debug("response redactor skip non-JSON body: %s", exc)
            return response
        except Exception:  # noqa: BLE001 —— 脱敏失败不能阻塞业务响应
            logger.exception("response redactor failed; returning original body")
            return response

        new_headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        new_headers["content-length"] = str(len(new_body))
        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=new_headers,
            media_type="application/json",
            background=response.background,
        )


_RATE_LIMIT_PATH_RE = re.compile(r"^/api/v1/conversations/[^/]+/messages(?:/stream)?$")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端 IP 对客服消息写接口限流（MySQL 计数，超限返回 429）。

    - 仅作用于 ``POST /api/v1/conversations/{id}/messages``（含 ``/stream``）。
    - 维度：客户端 IP（优先 ``X-Forwarded-For`` 首跳，回退 ``request.client.host``）。
    - 阈值：``RATE_LIMIT_PER_MINUTE``（默认 30 次 / 60 秒）；``RATE_LIMIT_ENABLED=0`` 全局关闭。
    - 超限时打一条 ``rate_limited`` 审计并返回 429 + ``Retry-After``。
    """

    def __init__(
        self,
        app,
        limiter: RateLimiter | None = None,
        limit: int | None = None,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._limit = limit if limit is not None else int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
        self._window_seconds = window_seconds
        self._enabled = (
            os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._enabled or not self._should_limit(request):
            return await call_next(request)
        key = self._client_key(request)
        limiter = self._limiter or get_rate_limiter()
        # 同步的 MySQL/内存计数放线程池，避免阻塞事件循环。
        allowed = await run_in_threadpool(
            limiter.check, key, self._limit, self._window_seconds
        )
        if allowed:
            return await call_next(request)
        try:
            get_audit_logger().log(
                "rate_limited",
                conversation_id=self._conversation_id(request),
                detail=f"key={key} limit={self._limit}/{self._window_seconds}s",
            )
        except Exception:  # noqa: BLE001 —— 审计失败不影响限流响应
            logger.debug("rate-limit audit skipped", exc_info=True)
        return JSONResponse(
            status_code=429,
            content={"detail": "请求过于频繁，请稍后再试。"},
            headers={"Retry-After": str(self._window_seconds)},
        )

    @staticmethod
    def _should_limit(request: Request) -> bool:
        return request.method == "POST" and bool(_RATE_LIMIT_PATH_RE.match(request.url.path))

    @staticmethod
    def _client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"
        return f"ip:{ip}"

    @staticmethod
    def _conversation_id(request: Request) -> str | None:
        parts = [p for p in request.url.path.split("/") if p]
        # /api/v1/conversations/{id}/messages[/stream]
        if len(parts) >= 4 and parts[0] == "api" and parts[2] == "conversations":
            return parts[3]
        return None


__all__ = ["RedactResponseMiddleware", "RateLimitMiddleware"]