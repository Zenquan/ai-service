"""客服任务离线评测集（docs/customer-service-plan.md §11 客服任务质量）。

与检索离线评测（``core.main.evaluate``，Recall@K/MRR）互补：本模块评测客服图链路的
**任务级正确性** —— 意图识别、槽位提取、工具调用、转人工判断、越权拦截，
而非检索命中率。

设计约束：
- 标注集每条样例标注（见 plan §11）：
  ``question`` / ``intent`` / ``expected_tools`` / ``required_slots`` /
  ``must_handoff`` / ``forbidden_actions``，可选 ``user_id`` / ``clarify_expected``。
- 离线评测默认 **mock 业务工具**（retriever / generator / order_tool 全部注入假实现），
  禁止连接生产写接口；默认不接 LLM（走确定性规则分类器），保证可复现。
- 输出聚合指标 + 逐题明细，可直接对接 CI 与「下一步迭代」的评测联动。

聚合指标：
- ``intent_accuracy``      意图识别准确率
- ``tool_accuracy``        工具调用正确率（调用集合 == expected_tools，且无越权调用）
- ``forbidden_blocked_rate`` 越权拦截率（标注 forbidden_actions 的样例未被调用）
- ``handoff_accuracy``     转人工判断准确率（must_handoff ↔ needs_human）
- ``slot_completion_rate`` 槽位收集完成率（required_slots 全部提取）
- ``first_resolution_rate`` 一次解决率（未转人工且未澄清）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.graph.customer_service import create_customer_service_agent

# 标注集可覆盖的意图（与 state.Intent 对齐）
_INTENTS = {
    "greeting", "knowledge_question", "order_query", "after_sale", "complaint", "unknown",
}


def _make_retriever(contexts: list[dict] | None = None):
    contexts = contexts or []

    def search(query: str, top_k: int = 5) -> list[dict]:
        return contexts[:top_k]

    return search


def _make_generator(answer: str = "根据资料[1]"):
    def generate(question: str, contexts: list[dict], history: list) -> tuple[str, list[int]]:
        # 有上下文时引用第 1 条，否则空引用（触发引用校验转人工，符合 mock 语义）。
        citations = [1] if contexts else []
        return answer, citations

    return generate


def _make_order_tool(orders: dict[str, dict] | None = None):
    """mock 订单工具：orders 为 {order_no: {status, carrier, user_id, ...}}，按归属校验。"""
    orders = orders or {
        "A00001": {"order_no": "A00001", "status": "运输中", "carrier": "顺丰速运",
                   "tracking_no": "SF1", "updated_at": "2026-09-06 14:20:00",
                   "timeline": [], "user_id": "demo-user"},
        "B00001": {"order_no": "B00001", "status": "待发货", "carrier": "",
                   "tracking_no": "", "updated_at": "2026-09-06 10:00:00",
                   "timeline": [], "user_id": "alice"},
    }

    def query(raw_params: dict, requester_user_id: str) -> dict:
        order_no = (raw_params or {}).get("order_no", "")
        order = orders.get(order_no.upper())
        if order is None:
            return {"status": "not_found", "message": f"未查询到订单 {order_no}"}
        if order.get("user_id") and order["user_id"] != requester_user_id:
            return {"status": "forbidden", "message": "该订单不属于当前会话用户"}
        return {"status": "ok", "ok": True, "message": "订单查询成功", "data": order}

    return query


def _tools_called(state: dict) -> set[str]:
    """从 tool_calls 提取实际调用的工具名集合。"""
    return {call.get("tool") for call in state.get("tool_calls", []) if call.get("tool")}


def _slots_collected(state: dict, required: list[str]) -> bool:
    slots = state.get("slots") or {}
    return all(slots.get(key) for key in required)


def _evaluate_case(case: dict, agent, index: int) -> dict:
    question = case["question"]
    user_id = case.get("user_id") or "demo-user"
    result = agent.ask(question, conversation_id=f"eval-{index}", user_id=user_id)

    expected_intent = case.get("intent")
    expected_tools = set(case.get("expected_tools") or [])
    forbidden_actions = set(case.get("forbidden_actions") or [])
    required_slots = case.get("required_slots") or []
    must_handoff = bool(case.get("must_handoff"))

    actual_intent = result.get("intent")
    called = _tools_called(result)

    intent_ok = expected_intent is None or actual_intent == expected_intent
    # 越权：实际调用了任何被禁止的动作即失败
    forbidden_violated = bool(called & forbidden_actions)
    # 工具正确：调用集合 == 期望集合，且无越权调用
    tool_ok = (called == expected_tools) and not forbidden_violated
    # 越权拦截：标注了禁止动作时，实际未调用即视为拦截成功
    forbidden_blocked = (not forbidden_actions) or not forbidden_violated
    # 转人工判断：must_handoff ↔ needs_human
    handoff_ok = (bool(result.get("needs_human")) == must_handoff)
    # 槽位收集：required_slots 全部提取到
    slots_ok = _slots_collected(result, required_slots)
    # 一次解决：未转人工且未澄清
    resolved = (not result.get("needs_human")) and (not result.get("needs_clarification"))

    return {
        "question": question,
        "expected_intent": expected_intent,
        "actual_intent": actual_intent,
        "intent_ok": intent_ok,
        "expected_tools": sorted(expected_tools),
        "called_tools": sorted(called),
        "tool_ok": tool_ok,
        "forbidden_blocked": forbidden_blocked,
        "handoff_ok": handoff_ok,
        "must_handoff": must_handoff,
        "needs_human": bool(result.get("needs_human")),
        "slots_ok": slots_ok,
        "resolved": resolved,
        "response_mode": result.get("response_mode"),
        "handoff_reason": result.get("handoff_reason"),
    }


def evaluate_customer_service(
    cases: list[dict] | None = None,
    cases_path: Path | None = None,
) -> dict:
    """跑客服任务离线评测，返回聚合指标 + 逐题明细。

    ``cases`` 直接传标注集；否则读 ``cases_path`` JSON；都没有则用内置样例。
    """
    if cases is None:
        cases = []
        if cases_path and cases_path.exists():
            cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not cases:
        cases = DEFAULT_CASES

    agent = create_customer_service_agent(
        retriever=_make_retriever([{"text": "客服知识依据", "doc": "kb.md", "seq": 0}]),
        generator=_make_generator("根据知识库，为您解答[1]"),
        # order_tool 通过 build_customer_service_graph 的默认分支走 DemoOrderStore；
        # 这里显式注入 mock，保证不依赖演示订单源的 user_id 细节。
    )
    # 注入 mock 订单工具（覆盖默认 DemoOrderStore，用标注集自带的归属语义）
    from server.graph.customer_service.graph import build_customer_service_graph

    agent.graph = build_customer_service_graph(
        retriever=_make_retriever([{"text": "客服知识依据", "doc": "kb.md", "seq": 0}]),
        generator=_make_generator("根据知识库，为您解答[1]"),
        order_tool=_make_order_tool(),
    )

    detail = [_evaluate_case(case, agent, i) for i, case in enumerate(cases)]
    total = len(detail)

    def _rate(pred) -> float:
        return sum(1 for d in detail if pred(d)) / total if total else 0.0

    return {
        "total": total,
        "intent_accuracy": _rate(lambda d: d["intent_ok"]),
        "tool_accuracy": _rate(lambda d: d["tool_ok"]),
        "forbidden_blocked_rate": _rate(lambda d: d["forbidden_blocked"]),
        "handoff_accuracy": _rate(lambda d: d["handoff_ok"]),
        "slot_completion_rate": _rate(lambda d: d["slots_ok"]),
        "first_resolution_rate": _rate(lambda d: d["resolved"]),
        "cases": detail,
    }


# ── 内置标注集样例（覆盖 plan §11 各维度）──
DEFAULT_CASES: list[dict] = [
    # 问候：intent 命中，不转人工
    {"question": "你好，在吗？", "intent": "greeting", "must_handoff": False},

    # 知识问答：走 RAG，不调工具，不转人工
    {"question": "什么是 AI Agent？", "intent": "knowledge_question",
     "expected_tools": [], "must_handoff": False},

    # 订单查询（本人）：正确意图 + 调用工具 + 提取订单号，不转人工
    {"question": "帮我查一下订单 A00001 的物流", "intent": "order_query",
     "expected_tools": ["query_order_status"], "required_slots": ["order_no"],
     "must_handoff": False, "user_id": "demo-user"},

    # 越权拦截（查他人订单）：调用工具但被归属校验拒绝 → 转人工
    {"question": "帮我查一下订单 A00001 的物流", "intent": "order_query",
     "expected_tools": ["query_order_status"], "must_handoff": True, "user_id": "alice"},

    # 缺单号澄清：正确意图，但不调工具（槽位不齐），不转人工；不标注 required_slots（期望即澄清）
    {"question": "帮我查订单物流", "intent": "order_query", "expected_tools": [],
     "must_handoff": False},

    # 售后：高风险意图直接转人工，不调工具
    {"question": "我要退款", "intent": "after_sale", "expected_tools": [], "must_handoff": True},

    # 投诉：高风险意图直接转人工
    {"question": "我要投诉你们，太坑了", "intent": "complaint", "expected_tools": [], "must_handoff": True},

    # 禁止动作：不明意图（过短短文本）不应触发订单工具（forbidden_actions 校验）
    {"question": "嗯", "intent": "unknown", "expected_tools": [],
     "forbidden_actions": ["query_order_status"], "must_handoff": False},
]

# 指标名 → 中文标签（供 CLI / CI 报告展示）
_METRIC_LABELS = {
    "intent_accuracy": "意图准确率",
    "tool_accuracy": "工具调用正确率",
    "forbidden_blocked_rate": "越权拦截率",
    "handoff_accuracy": "转人工判断准确率",
    "slot_completion_rate": "槽位收集完成率",
    "first_resolution_rate": "一次解决率",
}


def check_thresholds(result: dict, thresholds: dict[str, float]) -> list[str]:
    """按阈值校验聚合指标，返回未达标项描述（空列表 = 全部达标）。

    ``thresholds`` 形如 ``{"intent_accuracy": 0.9, "handoff_accuracy": 0.9}``，
    用于 CI 判定 pass/fail：任一指标低于阈值即失败。
    """
    failures: list[str] = []
    for metric, minimum in thresholds.items():
        actual = result.get(metric)
        if actual is None:
            continue
        if actual < minimum:
            label = _METRIC_LABELS.get(metric, metric)
            failures.append(f"{label}({metric})={actual:.1%} < {minimum:.0%}")
    return failures


__all__ = [
    "evaluate_customer_service",
    "check_thresholds",
    "DEFAULT_CASES",
]
