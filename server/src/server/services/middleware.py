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
import re
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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


__all__ = ["RedactResponseMiddleware"]