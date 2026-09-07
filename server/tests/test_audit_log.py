"""审计日志测试：内存实现 + PII 脱敏 + 工厂回退。"""
from __future__ import annotations

import pytest

from server.services.audit_log import (
    MemoryAuditLogger,
    build_default_audit_logger,
)
from server.services.redactor import reload_rules


@pytest.fixture(autouse=True)
def _redactor_enabled(monkeypatch):
    monkeypatch.setenv("REDACTOR_ENABLED", "1")
    reload_rules()
    yield
    monkeypatch.delenv("REDACTOR_ENABLED", raising=False)
    reload_rules()


def test_log_appends_action_and_fields():
    logger = MemoryAuditLogger()
    logger.log("message_received", conversation_id="c-1", message_id="m-1", user_id="alice")
    entry = logger.entries[0]
    assert entry["action"] == "message_received"
    assert entry["conversation_id"] == "c-1"
    assert entry["message_id"] == "m-1"
    assert entry["user_id"] == "alice"
    assert entry["created_at"]


def test_log_redacts_pii_fields():
    logger = MemoryAuditLogger()
    logger.log(
        "assistant_replied",
        conversation_id="c-1",
        detail="订单 A00001 手机 13812345678",
        handoff_reason="转人工",
    )
    entry = logger.entries[0]
    # detail 含 PII，按白名单脱敏
    assert "138****5678" in entry["detail"]
    assert "A***01" in entry["detail"]
    # 非 PII 元数据原样保留
    assert entry["conversation_id"] == "c-1"


def test_clear_resets_entries():
    logger = MemoryAuditLogger()
    logger.log("x")
    logger.clear()
    assert logger.entries == []


def test_build_falls_back_to_memory_without_mysql(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    assert isinstance(build_default_audit_logger(), MemoryAuditLogger)


def test_build_falls_back_on_bad_mysql(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "1")
    assert isinstance(build_default_audit_logger(), MemoryAuditLogger)
