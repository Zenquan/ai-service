"""FastAPI 响应脱敏中间件测试。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.services.middleware import RedactResponseMiddleware
from server.services.redactor import reload_rules


@pytest.fixture(autouse=True)
def _redactor_enabled(monkeypatch):
    monkeypatch.setenv("REDACTOR_ENABLED", "1")
    reload_rules()
    yield
    monkeypatch.delenv("REDACTOR_ENABLED", raising=False)
    reload_rules()


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RedactResponseMiddleware)

    @app.get("/api/v1/echo")
    def echo():
        return {"user": {"phone": "13812345678", "name": "李雷"}, "ok": True}

    @app.get("/api/v1/list")
    def list_():
        return [{"order_no": "A00001"}, {"order_no": "B00002"}]

    @app.get("/api/v1/alerts/test")
    def alerts():
        # 例外路径，应保持原样（即使含 PII）
        return {"phone": "13812345678"}

    @app.get("/health")
    def health():
        return {"phone": "13812345678", "status": "ok"}

    @app.get("/metrics")
    def metrics():
        # 非 JSON，Content-Length 也不动
        return Response_PlainText("cs_total 42\n")

    @app.get("/not-api/users")
    def not_api():
        return {"phone": "13812345678"}

    return app


class Response_PlainText:
    pass  # placeholder


def test_redact_middleware_redacts_json_response():
    app = _build_app()
    client = TestClient(app)
    r = client.get("/api/v1/echo")
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["phone"] == "138****5678"
    assert body["user"]["name"] == "李雷"  # 不在规则内
    assert body["ok"] is True


def test_redact_middleware_redacts_nested_list():
    app = _build_app()
    client = TestClient(app)
    r = client.get("/api/v1/list")
    body = r.json()
    assert body[0]["order_no"] == "A***01"
    assert body[1]["order_no"] == "B***02"


def test_redact_middleware_skips_alerts_path():
    app = _build_app()
    client = TestClient(app)
    r = client.get("/api/v1/alerts/test")
    body = r.json()
    # 例外路径，不脱敏
    assert body["phone"] == "13812345678"


def test_redact_middleware_skips_health_path():
    app = _build_app()
    client = TestClient(app)
    r = client.get("/health")
    body = r.json()
    assert body["phone"] == "13812345678"


def test_redact_middleware_skips_non_api_path():
    app = _build_app()
    client = TestClient(app)
    r = client.get("/not-api/users")
    body = r.json()
    assert body["phone"] == "13812345678"


def test_redact_middleware_respects_disabled_flag(monkeypatch):
    monkeypatch.setenv("REDACTOR_ENABLED", "0")
    reload_rules()
    app = _build_app()
    client = TestClient(app)
    r = client.get("/api/v1/echo")
    body = r.json()
    assert body["user"]["phone"] == "13812345678"  # 关闭，原样