"""客服任务评测指标的 Prometheus 暴露层。

迭代「客服任务评测体系」的指标出口：把 ``evaluation_events``（内存或 MySQL）
聚合出的业务指标，以 Prometheus 文本格式暴露给抓取端（Prometheus / Grafana）。

设计要点：
- **不手写 Prometheus 协议**：直接使用官方 ``prometheus_client`` 库的
  ``CollectorRegistry`` / ``Counter`` / ``Histogram`` / ``Gauge``，由
  ``generate_latest()`` 生成标准 exposition 文本。
- 指标分为两类：
  1. **计数型（Counter）**：按 ``intent`` / ``response_mode`` / ``tool_status``
     等维度累加的总量，用于计算比率（一次解决率 / 转人工率 / 工具成功率）。
  2. **分布型（Histogram）**：``latency_ms`` 的响应时延分布。
- 聚合源：``EvaluationRecorder`` 的内存事件列表（``MemoryEvaluationRecorder.events``）。
  MySQL 模式下事件在 DB 侧，抓取端应直接以 SQL 聚合；本模块聚焦进程内实时视图，
  同时提供 ``PrometheusEvaluator.refresh()`` 从 recorder 拉取最新事件重算。

指标清单（前缀 ``cs_eval_``）：
- ``cs_eval_requests_total{intent,response_mode}``       请求总量
- ``cs_eval_resolved_total{...}``                         一次解决（未追问/未转人工且无错误）
- ``cs_eval_human_handoff_total{}``                       转人工次数
- ``cs_eval_clarification_total{}``                       触发追问次数
- ``cs_eval_tool_calls_total{tool_status}``               工具调用次数（按成功/失败/未调用）
- ``cs_eval_citation_valid_total{valid}``                 引用校验（有效/无效）
- ``cs_eval_errors_total{}``                              出错次数
- ``cs_eval_latency_seconds``（Histogram）                响应时延
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

# 默认关闭 *_created 时间戳序列（仅当用户未显式设置时才关闭，保持指标干净）。
# 须在 import prometheus_client 之前设置。
os.environ.setdefault("PROMETHEUS_DISABLE_CREATED_SERIES", "true")

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

from server.services.metrics import EvaluationRecorder

logger = logging.getLogger(__name__)

_METRIC_PREFIX = "cs_eval"


class PrometheusEvaluator:
    """把 recorder 的评测事件聚合为 Prometheus 指标。

    一个进程内通常只有一个 recorder（``CustomerServiceService._recorder``），
    本评估器通过 ``refresh()`` 拉取其事件做窗口聚合后写入 registry。
    """

    def __init__(self, recorder: EvaluationRecorder, registry: CollectorRegistry | None = None) -> None:
        self._recorder = recorder
        self._registry = registry or CollectorRegistry()
        self._lock = threading.Lock()

        self.requests_total = Counter(
            f"{_METRIC_PREFIX}_requests_total",
            "客服请求总量（按意图与响应模式）",
            ["intent", "response_mode"],
            registry=self._registry,
        )
        self.resolved_total = Counter(
            f"{_METRIC_PREFIX}_resolved_total",
            "一次解决次数（无追问、未转人工、无错误）",
            ["intent"],
            registry=self._registry,
        )
        self.human_handoff_total = Counter(
            f"{_METRIC_PREFIX}_human_handoff_total",
            "转人工次数",
            ["intent"],
            registry=self._registry,
        )
        self.clarification_total = Counter(
            f"{_METRIC_PREFIX}_clarification_total",
            "触发自适应追问次数",
            ["intent"],
            registry=self._registry,
        )
        self.tool_calls_total = Counter(
            f"{_METRIC_PREFIX}_tool_calls_total",
            "工具调用次数（按结果状态）",
            ["tool_status"],
            registry=self._registry,
        )
        self.citation_valid_total = Counter(
            f"{_METRIC_PREFIX}_citation_valid_total",
            "引用校验结果（有效/无效）",
            ["valid"],
            registry=self._registry,
        )
        self.errors_total = Counter(
            f"{_METRIC_PREFIX}_errors_total",
            "出错次数",
            ["intent"],
            registry=self._registry,
        )
        self.latency_seconds = Histogram(
            f"{_METRIC_PREFIX}_latency_seconds",
            "客服响应时延（秒）",
            buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
            registry=self._registry,
        )

        self._seen = 0  # 已聚合的事件下标，避免重复累加

    def set_recorder(self, recorder: EvaluationRecorder) -> None:
        """切换数据源（测试 monkeypatch / 运行期替换 recorder 时使用）。"""
        with self._lock:
            self._recorder = recorder
            self._seen = 0  # 换源后游标归零，重新聚合新源事件

    def refresh(self) -> None:
        """从 recorder 拉取事件，增量聚合到指标。重复调用幂等（按游标去重）。"""
        events = self._collect_events()
        with self._lock:
            new_events = events[self._seen:]
            self._seen = len(events)
        for event in new_events:
            self._apply(event)

    def _collect_events(self) -> list[dict[str, Any]]:
        # MemoryEvaluationRecorder 暴露 ``events``；MySQL 实现无此属性时返回空。
        getter = getattr(self._recorder, "events", None)
        if callable(getter):
            return getter()
        if isinstance(getter, list):
            return getter
        return []

    def _apply(self, event: dict[str, Any]) -> None:
        intent = str(event.get("intent") or "unknown")
        response_mode = str(event.get("response_mode") or "unknown")
        tool_status = str(event.get("tool_status") or "none")
        citation_valid = bool(event.get("citation_valid"))
        needs_human = bool(event.get("needs_human"))
        needs_clarification = bool(event.get("needs_clarification"))
        has_error = bool(event.get("error"))
        latency_ms = event.get("latency_ms")

        self.requests_total.labels(intent=intent, response_mode=response_mode).inc()
        if needs_human:
            self.human_handoff_total.labels(intent=intent).inc()
        if needs_clarification:
            self.clarification_total.labels(intent=intent).inc()
        if has_error:
            self.errors_total.labels(intent=intent).inc()
        # 一次解决：未转人工、未追问、无错误
        if not (needs_human or needs_clarification or has_error):
            self.resolved_total.labels(intent=intent).inc()
        self.tool_calls_total.labels(tool_status=tool_status).inc()
        self.citation_valid_total.labels(valid="1" if citation_valid else "0").inc()
        if latency_ms is not None:
            try:
                self.latency_seconds.observe(float(latency_ms) / 1000.0)
            except (TypeError, ValueError):
                pass

    def render(self) -> bytes:
        """生成 Prometheus exposition 文本（先增量刷新，再序列化 registry）。"""
        self.refresh()
        return generate_latest(self._registry)

    def render_text(self) -> str:
        return self.render().decode("utf-8")


__all__ = [
    "PrometheusEvaluator",
    "generate_latest",
    "CollectorRegistry",
]
