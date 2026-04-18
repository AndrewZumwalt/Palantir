"""Tests for authentication dependency."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from palantir.config import PalantirConfig
from palantir.web.dependencies import verify_auth


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_verify_auth_noop_when_token_unset():
    cfg = PalantirConfig()
    cfg.auth_token = ""
    # Should not raise even without credentials
    await verify_auth(config=cfg, credentials=None)


async def test_verify_auth_rejects_bad_token():
    cfg = PalantirConfig()
    cfg.auth_token = "expected"
    with pytest.raises(HTTPException) as exc:
        await verify_auth(config=cfg, credentials=_creds("wrong"))
    assert exc.value.status_code == 401


async def test_verify_auth_accepts_good_token():
    cfg = PalantirConfig()
    cfg.auth_token = "expected"
    await verify_auth(config=cfg, credentials=_creds("expected"))


async def test_verify_auth_rejects_missing_credentials():
    cfg = PalantirConfig()
    cfg.auth_token = "expected"
    with pytest.raises(HTTPException) as exc:
        await verify_auth(config=cfg, credentials=None)
    assert exc.value.status_code == 401
