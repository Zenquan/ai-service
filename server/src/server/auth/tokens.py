"""无第三方依赖的 JWT 风格签名 token（HMAC-SHA256，演示/自包含）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from server.auth.accounts import AUTH_JWT_SECRET, AuthError, AuthUser

_TOKEN_TTL_SECONDS = int(os.getenv("AUTH_TOKEN_TTL_HOURS", "24")) * 3600


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_access_token(user: AuthUser) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + _TOKEN_TTL_SECONDS,
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(
        AUTH_JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64url(signature)}"


def decode_access_token(token: str) -> dict:
    try:
        encoded_header, encoded_payload, signature = token.split(".")
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected = hmac.new(
            AUTH_JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _unb64url(signature)):
            raise AuthError("token 签名无效")
        payload = json.loads(_unb64url(encoded_payload))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise AuthError("登录已过期，请重新登录")
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
        raise AuthError("token 无效") from exc
