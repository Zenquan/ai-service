"""评测指标埋点层测试（MemoryEvaluationRecorder，pymysql MySQL 不接）。"""
from __future__ import annotations

import pytest

from server.services.metrics import (
    MemoryEvaluationRecorder,
    build_default_recorder,
)
from server.services.redactor import reload_rules


@pytest.fixture(autouse=True)
def _redactor_enabled(monkeypatch):
    monkeypatch.setenv("REDACTOR_ENABLED", "1")
    reload_rules()
    yield
    monkeypatch.delenv("REDACTOR_ENABLED", raising=False)
    reload_rules()


def test_record_event_appends_with_redaction():
    recorder = MemoryEvaluationRecorder()
    recorder.record_event({
        "conversation_id": "c-1",
        "intent": "order_query",
        "response_mode": "answer",
        "citation_valid": True,
        "content": "用户原文 13812345678",
        "latency_ms": 320,
    })
    recorder.record_event({
        "conversation_id": "c-1",
        "intent": "unknown",
        "response_mode": "clarify",
        "clarify_reason": "请补充 13812345678 订单",
    })
    events = recorder.events
    assert len(events) == 2
    # content 字段被白名单脱敏
    assert "138****5678" in events[0]["content"]
    assert "138****5678" in events[1]["clarify_reason"]
    # 元数据原样保留
    assert events[0]["intent"] == "order_query"
    assert events[0]["latency_ms"] == 320
    # created_at 已自动填充
    assert events[0]["created_at"]


def test_clear_resets_events():
    recorder = MemoryEvaluationRecorder()
    recorder.record_event({"intent": "order_query"})
    recorder.clear()
    assert recorder.events == []


def test_aggregate_buckets():
    recorder = MemoryEvaluationRecorder()
    for e in [
        {"intent": "order_query", "response_mode": "answer", "tool_status": "ok"},
        {"intent": "order_query", "response_mode": "answer", "tool_status": "ok"},
        {"intent": "knowledge_question", "response_mode": "answer", "tool_status": None},
        {"intent": "after_sale", "response_mode": "handoff", "tool_status": None},
    ]:
        recorder.record_event(e)
    agg = recorder.aggregate()
    assert agg["intent"]["order_query"] == 2
    assert agg["intent"]["knowledge_question"] == 1
    assert agg["intent"]["after_sale"] == 1
    assert agg["response_mode"]["answer"] == 3
    assert agg["response_mode"]["handoff"] == 1
    # tool_status 把 None 也算进 unknown
    assert agg["tool_status"]["ok"] == 2
    assert agg["tool_status"]["unknown"] == 2


def test_build_default_recorder_falls_back_to_memory_without_mysql(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    recorder = build_default_recorder()
    assert isinstance(recorder, MemoryEvaluationRecorder)


def test_build_default_recorder_falls_back_on_bad_mysql(monkeypatch):
    # 故意配错端口，触发连接失败
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "1")
    recorder = build_default_recorder()
    assert isinstance(recorder, MemoryEvaluationRecorder)


def test_record_event_disabled_redactor_passes_through(monkeypatch):
    monkeypatch.setenv("REDACTOR_ENABLED", "0")
    reload_rules()
    recorder = MemoryEvaluationRecorder()
    recorder.record_event({"content": "我的手机 13812345678"})
    assert recorder.events[0]["content"] == "我的手机 13812345678"