"""迭代 1（日志 + trace_id）测试：响应头透传、SSE 场景日志携带同一 trace_id。"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.observability import setup_logging
from server.services.customer_service import customer_service


@pytest.fixture(autouse=True)
def clear_conversations():
    for store in (customer_service._store, customer_service._fallback_store):
        store.clear()
    customer_service._graph_checked = True
    customer_service._graph_agent = None
    yield
    for store in (customer_service._store, customer_service._fallback_store):
        store.clear()


@pytest.fixture()
def client():
    return TestClient(app)


def test_response_generates_trace_id_and_echoes_custom(client):
    first = client.get("/api/v1/health")
    assert first.headers.get("x-request-id")

    echoed = client.get("/api/v1/health", headers={"X-Request-ID": "custom-trace-001"})
    assert echoed.headers["x-request-id"] == "custom-trace-001"


def test_sse_response_header_carries_trace_id(client):
    with client.stream(
        "POST",
        "/api/v1/conversations/c-trace-sse/messages/stream",
        json={"message": "帮我查一下订单物流"},
    ) as response:
        assert response.status_code == 200
        assert response.headers.get("x-request-id")
        list(response.iter_text())


def test_request_logs_carry_same_trace_id(client, caplog):
    caplog.set_level(logging.INFO)

    with client.stream(
        "POST",
        "/api/v1/conversations/c-trace-log/messages/stream",
        json={"message": "我要投诉，转人工"},
    ) as response:
        trace_id = response.headers.get("x-request-id")
        list(response.iter_text())

    assert trace_id
    matching = [
        record
        for record in caplog.records
        if getattr(record, "trace_id", None) == trace_id
    ]
    messages = [record.getMessage() for record in matching]
    assert any("客服流式消息收到" in text for text in messages)
    assert any("客服转人工" in text for text in messages)


def test_setup_logging_makes_info_visible_with_trace_id(capsys):
    """INFO 日志可见，且格式带 trace_id 占位（无请求场景显示 -）。"""
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    try:
        setup_logging()
        logger = logging.getLogger("server.services.customer_service")
        logger.info("日志链路自检")
        output = capsys.readouterr().out
        assert "日志链路自检" in output
        assert "[-]" in output
    finally:
        root.handlers.clear()
        for handler in previous_handlers:
            root.addHandler(handler)
