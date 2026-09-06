"""只读订单/物流查询工具（迭代 2 第一个业务闭环）。

设计约束（docs/customer-service-plan.md §10）：
- 工具只读，不允许“改写结果”；失败必须显式返回，不能由调用方改写成成功。
- 入参用 Pydantic 校验（缺单号/非法单号在进 Store 前拦截）。
- 带超时与有限重试；归属校验放查询语义内（只能查当前会话用户自己的订单）。
- 统一返回 ``ToolResult``，图节点/日志按 status 分流，不猜。
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

ToolStatus = Literal["ok", "not_found", "forbidden", "timeout", "error"]


class QueryOrderParams(BaseModel):
    """订单查询入参（Pydantic 校验边界）。"""

    order_no: str = Field(min_length=3, max_length=32, description="订单号")


@dataclass(frozen=True)
class ToolResult:
    """业务工具统一返回结构。"""

    status: ToolStatus
    ok: bool = False
    message: str = ""
    data: dict | None = None
    retries: int = 0
    duration_ms: int = 0
    tool: str = "query_order_status"

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "status": self.status,
            "ok": self.ok,
            "message": self.message,
            "data": self.data,
            "retries": self.retries,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class Order:
    """只读订单快照（演示数据；后续替换为业务系统只读接口）。"""

    order_no: str
    user_id: str
    status: str
    carrier: str
    tracking_no: str
    updated_at: str
    timeline: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {
            "order_no": self.order_no,
            "status": self.status,
            "carrier": self.carrier,
            "tracking_no": self.tracking_no,
            "updated_at": self.updated_at,
            "timeline": list(self.timeline),
        }


class OrderStore(Protocol):
    """订单数据源抽象：生产环境替换为带鉴权的只读接口/缓存。"""

    def get_by_order_no(self, order_no: str) -> Order | None: ...


class DemoOrderStore:
    """演示订单源：固定两条订单，便于验证“本人可查 / 非本人拒绝”。"""

    _ORDERS: dict[str, Order] = {
        "A00001": Order(
            order_no="A00001",
            user_id="demo-user",
            status="运输中",
            carrier="顺丰速运",
            tracking_no="SF1409876543210",
            updated_at="2026-09-06 14:20:00",
            timeline=(
                {"time": "2026-09-05 09:00", "event": "商家已发货"},
                {"time": "2026-09-06 14:20", "event": "快件到达上海转运中心"},
            ),
        ),
        "B00001": Order(
            order_no="B00001",
            user_id="alice",
            status="待发货",
            carrier="",
            tracking_no="",
            updated_at="2026-09-06 10:00:00",
            timeline=(
                {"time": "2026-09-06 10:00", "event": "订单已支付，等待仓库发货"},
            ),
        ),
    }

    def get_by_order_no(self, order_no: str) -> Order | None:
        return self._ORDERS.get(order_no.strip().upper())


# 订单号宽松识别：字母前缀 1~4 位 + 数字 4 位以上（A00001、ORD20260901001）
# 带字母前缀避免把年份/纯数字（手机号）误当成订单号。
_ORDER_NO_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,4}\d{4,}(?![A-Za-z0-9])")


def extract_order_numbers(text: str) -> list[str]:
    """从用户消息里提取候选订单号，去重后保序。"""
    seen: set[str] = set()
    result: list[str] = []
    for match in _ORDER_NO_RE.finditer(text.upper()):
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _query_with_retry(
    store: OrderStore,
    order_no: str,
    timeout_seconds: float,
    max_retries: int = 1,
) -> tuple[Order | None, int]:
    last_error: Exception | None = None
    attempts = 0
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(store.get_by_order_no, order_no)
                order = future.result(timeout=timeout_seconds)
            return order, attempts
        except FutureTimeoutError as exc:
            last_error = exc
            logger.warning("订单工具超时 attempt=%d order_no=%s", attempt + 1, order_no)
        except Exception as exc:  # noqa: BLE001 —— 存储/网络异常统一走失败分支
            last_error = exc
            logger.warning("订单工具查询失败 attempt=%d order_no=%s error=%s", attempt + 1, order_no, exc)
        if attempt < max_retries:
            time.sleep(0.05 * (attempt + 1))
    raise last_error  # type: ignore[misc]


def query_order_status(
    raw_params: dict | str,
    requester_user_id: str,
    store: OrderStore | None = None,
    timeout_seconds: float = 2.0,
    max_retries: int = 1,
) -> ToolResult:
    """查询本人订单状态/物流（只读）。

    - 入参非法（缺单号/格式不对）→ ``error``（不落库、不查询）。
    - 非本人订单 → ``forbidden``（权限拒绝，不返回任何业务数据）。
    - 查无此单 → ``not_found``。
    - 超时/存储异常 → ``timeout``/``error``（调用方转人工，不编造成功）。
    """
    store = store or DemoOrderStore()
    started = time.monotonic()
    try:
        params = QueryOrderParams.model_validate(
            raw_params if isinstance(raw_params, dict) else {"order_no": raw_params}
        )
    except ValidationError as exc:
        logger.info("订单工具入参校验失败 params=%s", raw_params)
        return ToolResult(
            status="error",
            ok=False,
            message="订单号缺失或格式不正确，请提供订单号后再试。",
            data={"errors": exc.errors(include_url=False)},
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    order_no = params.order_no.strip().upper()
    try:
        order, attempts = _query_with_retry(
            store,
            order_no,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    except FutureTimeoutError:
        return ToolResult(
            status="timeout",
            message="订单查询超时，请稍后重试或转人工处理。",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("订单工具查询异常 order_no=%s", order_no)
        return ToolResult(
            status="error",
            ok=False,
            message=f"订单服务暂时不可用（{exc}）。",
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    if order is None:
        return ToolResult(
            status="not_found",
            message=f"未查询到订单 {order_no}，请核对订单号或转人工处理。",
            data={"order_no": order_no},
            retries=attempts - 1,
            duration_ms=duration_ms,
        )
    if order.user_id != (requester_user_id or ""):
        logger.warning(
            "订单归属校验拒绝 order_no=%s requester=%s owner=%s",
            order_no,
            requester_user_id,
            order.user_id,
        )
        return ToolResult(
            status="forbidden",
            message="该订单不属于当前会话用户，已停止查询并转人工核实。",
            data={"order_no": order_no},
            retries=attempts - 1,
            duration_ms=duration_ms,
        )

    return ToolResult(
        status="ok",
        ok=True,
        message="订单查询成功",
        data=order.to_dict(),
        retries=attempts - 1,
        duration_ms=duration_ms,
    )
