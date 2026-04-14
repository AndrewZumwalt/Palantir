"""Automation rules CRUD API."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from palintir.brain.automation import (
    create_rule,
    delete_rule,
    list_rules,
    update_rule,
)
from palintir.web.dependencies import get_db, verify_auth

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


@router.get("")
async def get_rules(db: sqlite3.Connection = Depends(get_db)):
    return {"rules": list_rules(db)}


@router.post("")
async def create_new_rule(
    data: RuleCreate, db: sqlite3.Connection = Depends(get_db)
):
    _validate_types(data.trigger_type, data.action_type)
    rule_id = create_rule(db, data.model_dump())
    return {"id": rule_id}


@router.put("/{rule_id}")
async def update_existing_rule(
    rule_id: str,
    data: RuleUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    _validate_types(data.trigger_type, data.action_type)
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    if not update_rule(db, rule_id, updates):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"updated": True}


@router.delete("/{rule_id}")
async def remove_rule(rule_id: str, db: sqlite3.Connection = Depends(get_db)):
    if not delete_rule(db, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True}


@router.get("/trigger-types")
async def get_trigger_types():
    """Return the list of supported trigger and action types for the UI."""
    return {
        "triggers": sorted(TRIGGER_TYPES),
        "actions": sorted(ACTION_TYPES),
    }
