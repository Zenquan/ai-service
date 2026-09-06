"""迭代 2 验收：只读订单工具闭环 + 会话归属 + 图 checkpoint 多轮恢复。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.graph.customer_service import create_customer_service_agent
from server.main import app
from server.services import rag as rag_service
from server.services.customer_service import customer_service
from server.tools.orders import (
    DemoOrderStore,
    extract_order_numbers,
    query_order_status,
)


@pytest.fixture(autouse=True)
def clean_state():
    for store in (customer_service._store, customer_service._fallback_store):
        store.clear()
    yield
    for store in (customer_service._store, customer_service._fallback_store):
        store.clear()


@pytest.fixture()
def real_graph():
    """本轮用真实客服图（确定性订单分支，知识问答仍 mock 掉以免连检索）。"""
    customer_service._graph_checked = False
    customer_service._graph_agent = None
    yield
    customer_service._graph_checked = True
    customer_service._graph_agent = None


def test_extract_order_number_from_chinese_message():
    assert extract_order_numbers("帮我查一下订单号 A00001 的物流") == ["A00001"]
    assert extract_order_numbers("没有订单号的普通问题") == []


def test_query_own_order_returns_ok():
    result = query_order_status({"order_no": "a00001"}, "demo-user")
    assert result.status == "ok"
    assert result.data["carrier"] == "顺丰速运"


def test_query_other_users_order_is_forbidden():
    result = query_order_status({"order_no": "A00001"}, "alice")
    assert result.status == "forbidden"
    assert result.data == {"order_no": "A00001"}


def test_query_missing_order_returns_not_found():
    result = query_order_status({"order_no": "NOPE99"}, "demo-user")
    assert result.status == "not_found"


def test_invalid_params_do_not_reach_store():
    def unexpected(*_args, **_kwargs):
        raise AssertionError("非法入参不应触达订单存储")

    result = query_order_status({"order_no": ""}, "demo-user", store=DemoOrderStore())
    assert result.status == "error"
    assert "订单号缺失或格式不正确" in result.message


def test_service_clarifies_then_restores_checkpoint_to_answer(real_graph, monkeypatch):
    monkeypatch.setattr(
        rag_service,
        "ask",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("订单链路不应走知识问答")),
    )

    first = customer_service.handle_message("c-order-1", "帮我查一下订单物流", user_id="demo-user")
    assert first["response_mode"] == "clarify"
    assert first["needs_clarification"] is True
    assert "订单号" in first["answer"]
    assert customer_service.get_conversation("c-order-1")["status"] == "waiting"

    # 第二轮只补订单号（不带“订单”关键词），靠 checkpoint 恢复 order_query intent。
    second = customer_service.handle_message("c-order-1", "A00001", user_id="demo-user")
    assert second["response_mode"] == "answer"
    assert second["needs_human"] is False
    assert "运输中" in second["answer"]
    assert "顺丰速运" in second["answer"]


def test_service_stream_order_closed_loop_events(real_graph, monkeypatch):
    monkeypatch.setattr(
        rag_service,
        "ask_stream",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("订单链路不应走知识问答流")),
    )

    events = list(customer_service.stream_message("c-order-2", "帮我查订单", user_id="demo-user"))
    kinds = [event["event"] for event in events]
    assert kinds == ["meta", "done"]
    meta = events[0]
    assert meta["response_mode"] == "clarify"
    assert "订单号" in meta["answer"]

    second = list(customer_service.stream_message("c-order-2", "A00001", user_id="demo-user"))
    assert [event["event"] for event in second] == ["progress", "meta", "done"]
    meta2 = second[1]
    assert meta2["event"] == "meta"
    assert meta2["response_mode"] == "answer"
    assert meta2["needs_human"] is False
    assert "运输中" in meta2["answer"]
    assert meta2["tool_results"][0]["status"] == "ok"


def test_non_owner_order_handoff_via_graph(real_graph):
    result = create_customer_service_agent().ask(
        "查一下 A00001 物流",
        conversation_id="c-denied",
        user_id="alice",
    )
    assert result["handoff_reason"] == "非本人订单，权限拒绝"
    assert result["tool_results"][0]["status"] == "forbidden"
