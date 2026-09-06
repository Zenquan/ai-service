"""认证单元测试（不依赖 MySQL/Qdrant）。"""
from __future__ import annotations

import pytest

from server.auth.accounts import AuthError, authenticate
from server.auth.tokens import create_access_token, decode_access_token


def test_operator_and_customer_demo_accounts():
    operator = authenticate("zenquan", "zenquan123")
    customer = authenticate("alice", "alice123")
    assert operator.role == "operator"
    assert operator.id == "zenquan"
    assert customer.role == "customer"
    assert customer.id == "demo-user"


def test_wrong_password_rejected():
    with pytest.raises(AuthError):
        authenticate("zenquan", "wrong-password")


def test_access_token_roundtrip():
    user = authenticate("zenquan", "zenquan123")
    token = create_access_token(user)
    payload = decode_access_token(token)
    assert payload["sub"] == "zenquan"
    assert payload["role"] == "operator"
