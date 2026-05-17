"""Optional Bearer token auth.

If MEMORY_AUTH_TOKEN is set, all routes (except /health) require the matching
Authorization header. If unset, auth is a no-op.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from memory_service.config import get_settings


async def require_auth(request: Request) -> None:
    token = get_settings().memory_auth_token
    if not token:
        return  # auth disabled

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    presented = header.removeprefix("Bearer ").strip()
    if presented != token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
