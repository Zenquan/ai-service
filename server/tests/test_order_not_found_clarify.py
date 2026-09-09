"""查无此单的分流契约：先澄清核对订单号，连续失败才升级人工。

回归背景：订单工具 ``not_found`` 的文案是「请核对订单号**或**转人工处理」——
给用户的选择权，但旧实现一律置 ``needs_human=True``，等于系统替用户选了转人工，
前端直接渲染「正在为您转人工」。本文件锁定「业务可自纠的错误先澄清」这一语义。
"""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from server.graph.customer_service.graph import build_customer_service_graph


def _no_contexts(query: str, top_k: int = 5) -> list[dict]:
    return []


def _no_answer(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
    return "不应调用模型", []


def _tool(status: str, message: str = "", data: dict | None = None):
    """构造返回固定 status 的订单工具。"""

    def call(raw_params: dict, requester_user_id: str) -> dict:
        return {
            "tool": "query_order_status",
            "status": status,
            "ok": status == "ok",
            "message": message,
            "data": data,
            "retries": 0,
            "duration_ms": 1,
        }

    return call


def _build(order_tool, checkpointer=None):
    return build_customer_service_graph(
        retriever=_no_contexts,
        generator=_no_answer,
        order_tool=order_tool,
        checkpointer=checkpointer,
    )


def _ask(graph, query: str, thread_id: str = "t-1", user_id: str = "demo-user"):
    return graph.invoke(
        {
            "conversation_id": thread_id,
            "user_id": user_id,
            "tenant_id": None,
            "current_query": query,
            "messages": [{"role": "user", "content": query}],
        },
        {"configurable": {"thread_id": thread_id}},
    )


def test_not_found_first_time_asks_clarification_not_handoff():
    """第一次查不到订单：澄清核对，绝不转人工。"""
    graph = _build(_tool("not_found", "未查询到订单 A99999，请核对订单号或转人工处理。"))

    result = _ask(graph, "帮我查订单 A99999 的物流")

    assert result["response_mode"] == "clarify"
    assert result["needs_human"] is False
    assert result["needs_clarification"] is True
    assert result["handoff_reason"] is None
    assert "A99999" in result["final_answer"]
    # 澄清应把错误单号清掉，下一轮重新提取
    assert "order_no" not in (result.get("slots") or {})


def test_not_found_twice_in_a_row_escalates_to_human():
    """连续两次都查不到：不再无限澄清，升级人工（避免澄清死循环）。"""
    graph = _build(
        _tool("not_found", "未查询到订单 A99999，请核对订单号或转人工处理。"),
        checkpointer=InMemorySaver(),
    )

    first = _ask(graph, "帮我查订单 A99999 的物流")
    assert first["response_mode"] == "clarify"

    second = _ask(graph, "帮我查订单 A99999 的物流")
    assert second["response_mode"] == "handoff"
    assert second["needs_human"] is True
    assert second["handoff_reason"] == "多次核对仍未查询到订单"


def test_successful_query_resets_not_found_counter():
    """中途查到订单后再查错：计数应清零，重新从澄清开始。"""
    calls = {"n": 0}

    def flaky(raw_params: dict, requester_user_id: str) -> dict:
        calls["n"] += 1
        if calls["n"] == 2:  # 第二轮命中
            return {
                "tool": "query_order_status",
                "status": "ok",
                "ok": True,
                "message": "订单查询成功",
                "data": {
                    "order_no": "A00001",
                    "status": "已发货",
                    "carrier": "顺丰",
                    "tracking_no": "SF123",
                    "timeline": [{"time": "2026-09-09 10:00", "event": "已发货"}],
                },
                "retries": 0,
                "duration_ms": 1,
            }
        return {
            "tool": "query_order_status",
            "status": "not_found",
            "ok": False,
            "message": "未查询到订单，请核对订单号或转人工处理。",
            "data": None,
            "retries": 0,
            "duration_ms": 1,
        }

    graph = _build(flaky, checkpointer=InMemorySaver())

    _ask(graph, "帮我查订单 A99999 的物流")           # 1st: not_found → clarify
    ok = _ask(graph, "帮我查订单 A00001 的物流")       # 2nd: ok → 计数清零
    assert ok["response_mode"] == "answer"
    assert "order_not_found_count" not in (ok.get("slots") or {})

    third = _ask(graph, "帮我查订单 A88888 的物流")    # 3rd: not_found → 重新澄清
    assert third["response_mode"] == "clarify"
    assert third["needs_human"] is False


def test_forbidden_still_escalates_to_human():
    """越权查询他人订单属安全语义，不澄清，直接转人工。"""
    graph = _build(_tool("forbidden", "该订单不属于当前会话用户，已停止查询并转人工核实。"))

    result = _ask(graph, "帮我查订单 A00001 的物流", user_id="alice")

    assert result["response_mode"] == "handoff"
    assert result["needs_human"] is True
