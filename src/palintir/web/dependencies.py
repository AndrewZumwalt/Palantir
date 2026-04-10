"""FastAPI dependency injection for shared resources."""

from __future__ import annotations

import sqlite3
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from palintir.config import PalintirConfig

security = HTTPBearer(auto_error=False)


def get_config(request: Request) -> PalintirConfig:
    return request.app.state.config


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


async def verify_auth(
    config: PalintirConfig = Depends(get_config),
    credentials: HTTPAuthorizationCredentials | None = Security(security),
) -> None:
    """Verify bearer token authentication."""
    if not config.auth_token:
        # No auth configured (development mode)
        return

    if not credentials or credentials.credentials != config.auth_token:
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")
