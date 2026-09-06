"""PII / 敏感字段脱敏器测试（纯正则 + dict 递归 + 消息结构）。"""
from __future__ import annotations

import os

import pytest

from server.services.redactor import (
    REDACTOR_ENABLED,
    redact,
    redact_messages,
    redact_text,
    reload_rules,
)


@pytest.fixture(autouse=True)
def _ensure_enabled(monkeypatch):
    monkeypatch.setenv("REDACTOR_ENABLED", "1")
    reload_rules()
    yield
    monkeypatch.delenv("REDACTOR_ENABLED", raising=False)
    reload_rules()


def test_redact_text_handles_each_pii_type():
    cases = {
        "我的手机是13812345678，请尽快联系": "我的手机是138****5678，请尽快联系",
        "身份证 11010519491231002X 看一下": "身份证 1101**********002X 看一下",
        "邮箱 alice.li@example.com 收到": "邮箱 a***@example.com 收到",
        "卡号 6222021234567890123 查到": "卡号 6222********0123 查到",
        "订单 A00001 还在路上": "订单 A***01 还在路上",
    }
    for raw, expected in cases.items():
        assert redact_text(raw) == expected, f"failed for {raw!r}"


def test_redact_text_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("REDACTOR_ENABLED", "0")
    reload_rules()
    raw = "我的手机是13812345678"
    assert redact_text(raw) == raw


def test_redact_does_not_mutate_input_dict():
    payload = {"user_id": "u-1", "note": "13812345678"}
    snapshot = dict(payload)
    redact(payload)
    assert payload == snapshot  # 原 dict 不被改动


def test_redact_recurses_into_nested_structures():
    payload = {
        "user": {"name": "李雷", "phone": "13812345678"},
        "orders": [{"order_no": "A00001"}, {"order_no": "B00002"}],
        "tickets": ({"no": "6222021234567890123"},),
    }
    cleaned = redact(payload)
    assert cleaned["user"]["phone"] == "138****5678"
    assert cleaned["orders"][0]["order_no"] == "A***01"
    assert cleaned["orders"][1]["order_no"] == "B***02"
    assert cleaned["tickets"][0]["no"] == "6222********0123"
    # tuple 外层类型保持
    assert isinstance(cleaned["tickets"], tuple)


def test_redact_leaves_non_pii_strings_intact():
    assert redact_text("AI 智能客服") == "AI 智能客服"
    assert redact_text("什么是 RAG？") == "什么是 RAG？"
    assert redact(42) == 42
    assert redact(None) is None
    assert redact(True) is True


def test_redact_messages_handles_plain_string_content():
    messages = [
        {"role": "user", "content": "我的手机 13812345678"},
        {"role": "assistant", "content": "好的，已记录 A00001"},
    ]
    cleaned = redact_messages(messages)
    assert cleaned[0]["content"] == "我的手机 138****5678"
    assert cleaned[1]["content"] == "好的，已记录 A***01"
    # 原列表不被修改
    assert messages[0]["content"] == "我的手机 13812345678"


def test_redact_messages_handles_multimodal_content():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "卡号 6222021234567890123"},
                {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
            ],
        }
    ]
    cleaned = redact_messages(messages)
    parts = cleaned[0]["content"]
    assert parts[0]["text"] == "卡号 6222********0123"
    # image_url 等非文本字段不被动
    assert parts[1]["image_url"] == {"url": "https://x/y.png"}


def test_redact_handles_order_no_without_false_positive():
    # 普通英文+数字不会误命中订单号规则（需要先字母后 5 位数字，且是孤立的 token）
    assert "2026" in redact_text("今天是 2026 年")
    assert "v1.5" in redact_text("模型版本 v1.5")


def test_custom_rules_via_env(monkeypatch):
    """环境变量 ``REDACTOR_RULES`` 可注入自定义 JSON 规则。"""
    import json

    custom = json.dumps([
        {"name": "project_code", "pattern": r"PRJ-(\d{4})", "replacement": "PRJ-XXXX"}
    ])
    monkeypatch.setenv("REDACTOR_RULES", custom)
    reload_rules()
    assert redact_text("项目编号 PRJ-1234") == "项目编号 PRJ-XXXX"


def test_redact_messages_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("REDACTOR_ENABLED", "0")
    reload_rules()
    messages = [{"role": "user", "content": "我的手机 13812345678"}]
    assert redact_messages(messages)[0]["content"] == "我的手机 13812345678"


def test_module_default_redactor_enabled_flag():
    # 模块默认 REDACTOR_ENABLED=1（除非显式关闭环境变量）
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("REDACTOR_ENABLED", raising=False)
    reload_rules()
    from server.services import redactor

    try:
        assert redactor.REDACTOR_ENABLED is True
    finally:
        monkeypatch.undo()