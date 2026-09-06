"""登录认证 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server.auth.accounts import AuthError, AuthUser
from server.auth.dependencies import get_current_user
from server.auth.tokens import create_access_token

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.post("/auth/login")
async def login(req: LoginRequest) -> dict:
    from server.auth.accounts import authenticate

    try:
        user = authenticate(req.username, req.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {
        "token": create_access_token(user),
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "display_name": user.display_name,
        },
    }


@router.get("/auth/me")
async def me(user: AuthUser = Depends(get_current_user)) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "display_name": user.display_name,
    }
