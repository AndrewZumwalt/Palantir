"""Automation rules CRUD API."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from palantir.brain.automation import (
    create_rule,
    delete_rule,
    list_rules,
    update_rule,
)
from palantir.web.dependencies import get_db, verify_auth
from palantir.web.rate_limit import rate_limit_read, rate_limit_write
from palantir.web.validation import validate_name, validate_rule_config

router = APIRouter(prefix="/api/automation", tags=["automation"], dependencies=[Depends(verify_auth)])


TRIGGER_TYPES = {"person_enters", "person_exits", "schedule", "voice_command"}
ACTION_TYPES = {"gpio", "tts", "notification", "command"}


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    trigger_type: str
    trigger_config: dict = Field(default_factory=dict)
    action_type: str
    action_config: dict = Field(default_factory=dict)
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    trigger_type: str | None = None
    trigger_config: dict | None = None
    action_type: str | None = None
    action_config: dict | None = None
    enabled: bool | None = None


def _validate_types(trigger_type: str | None, action_type: str | None) -> None:
    if trigger_type is not None and trigger_type not in TRIGGER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trigger_type; must be one of {sorted(TRIGGER_TYPES)}",
        )
    if action_type is not None and action_type not in ACTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action_type; must be one of {sorted(ACTION_TYPES)}",
        )


@router.get("", dependencies=[Depends(rate_limit_read)])
async def get_rules(db: sqlite3.Connection = Depends(get_db)):
    return {"rules": list_rules(db)}


@router.post("", dependencies=[Depends(rate_limit_write)])
async def create_new_rule(
    data: RuleCreate, db: sqlite3.Connection = Depends(get_db)
):
    _validate_types(data.trigger_type, data.action_type)
    payload = data.model_dump()
    payload["name"] = validate_name(payload["name"])
    validate_rule_config(payload["trigger_config"], "trigger_config")
    validate_rule_config(payload["action_config"], "action_config")
    rule_id = create_rule(db, payload)
    return {"id": rule_id}


@router.put("/{rule_id}", dependencies=[Depends(rate_limit_write)])
async def update_existing_rule(
    rule_id: str,
    data: RuleUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    _validate_types(data.trigger_type, data.action_type)
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    if "name" in updates:
        updates["name"] = validate_name(updates["name"])
    if "trigger_config" in updates:
        validate_rule_config(updates["trigger_config"], "trigger_config")
    if "action_config" in updates:
        validate_rule_config(updates["action_config"], "action_config")

    if not update_rule(db, rule_id, updates):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"updated": True}


@router.delete("/{rule_id}", dependencies=[Depends(rate_limit_write)])
async def remove_rule(rule_id: str, db: sqlite3.Connection = Depends(get_db)):
    if not delete_rule(db, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True}


@router.get("/trigger-types", dependencies=[Depends(rate_limit_read)])
async def get_trigger_types():
    """Return the list of supported trigger and action types for the UI."""
    return {
        "triggers": sorted(TRIGGER_TYPES),
        "actions": sorted(ACTION_TYPES),
    }
