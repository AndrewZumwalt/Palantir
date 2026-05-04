"""FastAPI dependency injection for shared resources."""

from __future__ import annotations

import secrets
import sqlite3

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from palantir.config import PalantirConfig

security = HTTPBearer(auto_error=False)


def get_config(request: Request) -> PalantirConfig:
    return request.app.state.config


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


async def verify_auth(
    config: PalantirConfig = Depends(get_config),
    credentials: HTTPAuthorizationCredentials | None = Security(security),
) -> None:
    """Verify bearer token authentication."""
    if not config.auth_token:
        if config.is_production:
            raise HTTPException(
                status_code=503,
                detail="Authentication token is not configured",
            )
        return

    if not credentials or not secrets.compare_digest(
        credentials.credentials, config.auth_token
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")
