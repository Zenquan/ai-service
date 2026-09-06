"""demo 账号与角色（迭代用，后续替换为 MySQL 用户表 + 真实权限）。"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Literal

Role = Literal["operator", "customer"]

# 演示环境 JWT 密钥；生产必须通过 AUTH_JWT_SECRET 注入随机值。
AUTH_JWT_SECRET = os.getenv("AUTH_JWT_SECRET", "dev-insecure-change-me")


class AuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthUser:
    id: str
    username: str
    role: Role
    display_name: str


def _pbkdf2(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), 120_000
    ).hex()
    return digest, salt


def _load_demo_users() -> dict[str, dict]:
    """demo 账号可经环境变量覆盖；密码仅用于本地联调，不写入仓库。"""
    operator_name = os.getenv("AUTH_OPERATOR_USERNAME", "zenquan")
    operator_password = os.getenv("AUTH_OPERATOR_PASSWORD", "zenquan123")
    customer_name = os.getenv("AUTH_CUSTOMER_USERNAME", "alice")
    customer_password = os.getenv("AUTH_CUSTOMER_PASSWORD", "alice123")

    users: dict[str, dict] = {}
    for user_id, username, password, role, display_name in (
        ("zenquan", operator_name, operator_password, "operator", "Zenquan"),
        ("demo-user", customer_name, customer_password, "customer", "普通用户 Alice"),
    ):
        digest, salt = _pbkdf2(password)
        users[user_id] = {
            "id": user_id,
            "username": username,
            "password_hash": digest,
            "password_salt": salt,
            "role": role,
            "display_name": display_name,
        }
    return users


_USERS = _load_demo_users()


def list_users() -> list[AuthUser]:
    return [
        AuthUser(
            id=item["id"],
            username=item["username"],
            role=item["role"],
            display_name=item["display_name"],
        )
        for item in _USERS.values()
    ]


def find_user(user_id: str) -> AuthUser | None:
    item = _USERS.get(user_id)
    if item is None:
        return None
    return AuthUser(
        id=item["id"],
        username=item["username"],
        role=item["role"],
        display_name=item["display_name"],
    )


def authenticate(username: str, password: str) -> AuthUser:
    for item in _USERS.values():
        if hmac.compare_digest(item["username"], username):
            digest, _ = _pbkdf2(password, item["password_salt"])
            if hmac.compare_digest(digest, item["password_hash"]):
                return AuthUser(
                    id=item["id"],
                    username=item["username"],
                    role=item["role"],
                    display_name=item["display_name"],
                )
            break
    raise AuthError("用户名或密码错误")
