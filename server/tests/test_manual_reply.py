"""人工接管后坐席直接回复的测试。"""
from __future__ import annotations

import pytest

from server.services.customer_service import CustomerServiceService


@pytest.fixture()
def service():
    s = CustomerServiceService()
    s._graph_checked = True
    s._graph_agent = None
    return s


def test_manual_reply_allowed_after_handoff(service):
    list(service.stream_message("manual-1", "我要投诉，转人工"))
    assert service.get_conversation("manual-1")["status"] == "handoff"

    result = service.send_manual_reply("manual-1", "您好，已收到您的反馈，客服专员稍后与您联系。")

    assert result["response_mode"] == "manual"
    assert service.get_conversation("manual-1")["status"] == "manual"
    messages = service.get_messages("manual-1")
    manual = [m for m in messages if m.get("response_mode") == "manual"]
    assert len(manual) == 1
    assert "客服专员" in manual[0]["content"]


def test_manual_reply_allows_second_manual_message(service):
    list(service.stream_message("manual-2", "我要投诉，转人工"))
    service.send_manual_reply("manual-2", "第一次人工回复")

    second = service.send_manual_reply("manual-2", "第二次人工回复")

    assert second["response_mode"] == "manual"
    count = sum(
        1 for m in service.get_messages("manual-2") if m.get("response_mode") == "manual"
    )
    assert count == 2


def test_manual_reply_rejected_on_open_conversation(service):
    service.get_conversation("manual-open")

    with pytest.raises(ValueError, match="只有转人工后可人工回复"):
        service.send_manual_reply("manual-open", "不应该能发")


def test_customer_followup_in_manual_conversation_stays_in_human_queue(service, monkeypatch):
    def unexpected_ask(*_args, **_kwargs):
        raise AssertionError("人工处理中的客户消息不应进入 AI")

    import server.services.rag as rag_service

    monkeypatch.setattr(rag_service, "ask", unexpected_ask)
    list(service.stream_message("manual-3", "我要投诉", user_id="demo-user"))
    service.send_manual_reply("manual-3", "您好，人工已处理。")
    assert service.get_conversation("manual-3")["status"] == "manual"

    followup = service.handle_message("manual-3", "还有补充", user_id="demo-user")

    assert followup["response_mode"] == "handoff"
    assert followup["needs_human"] is True
    assert service.get_conversation("manual-3")["status"] == "handoff"
