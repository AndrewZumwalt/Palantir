"""Automation rules engine.

Watches for triggering conditions (person enter/exit, schedules, voice commands)
and publishes AutomationTrigger events for downstream actuators.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable

import structlog

from palintir.models import AutomationTrigger, EventType

logger = structlog.get_logger()


@dataclass
class AutomationRule:
    id: str
    name: str
    description: str
    trigger_type: str
    trigger_config: dict
    action_type: str
    action_config: dict
    enabled: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AutomationRule":
        return cls(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            trigger_type=row["trigger_type"],
            trigger_config=json.loads(row["trigger_config"]),
            action_type=row["action_type"],
            action_config=json.loads(row["action_config"]),
            enabled=bool(row["enabled"]),
        )


class AutomationEngine:
    """Evaluates events against rules and emits triggers."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._rules: list[AutomationRule] = []
        self.reload()

    def reload(self) -> None:
        """Reload rules from the database."""
        rows = self._db.execute(
            "SELECT * FROM automation_rules WHERE enabled = 1"
        ).fetchall()
        self._rules = [AutomationRule.from_row(r) for r in rows]
        logger.info("automation_rules_loaded", count=len(self._rules))

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def evaluate_person_event(
        self,
        event_type: EventType,
        person_id: str,
        role: str | None = None,
    ) -> list[AutomationTrigger]:
        """Match rules for a person_entered or person_exited event."""
        triggers: list[AutomationTrigger] = []
        type_str = event_type.value
        # Map EventType to rule trigger_type
        rule_type_map = {
            "person_entered": "person_enters",
            "person_exited": "person_exits",
        }
        target_trigger = rule_type_map.get(type_str)
        if not target_trigger:
            return triggers

        for rule in self._rules:
            if rule.trigger_type != target_trigger:
                continue

            cfg = rule.trigger_config
            # Match by person_id or role (empty/missing config matches all)
            if "person_id" in cfg and cfg["person_id"] != person_id:
                continue
            if "role" in cfg and role is not None and cfg["role"] != role:
                continue

            triggers.append(
                AutomationTrigger(
                    rule_id=rule.id,
                    person_id=person_id,
                    action=rule.action_type,
                    params=rule.action_config,
                )
            )
            logger.info(
                "automation_rule_matched",
                rule_id=rule.id,
                rule_name=rule.name,
                person_id=person_id,
            )

        return triggers

    def evaluate_voice_command(
        self,
        text: str,
        speaker_id: str | None = None,
    ) -> list[AutomationTrigger]:
        """Match rules for spoken commands."""
        triggers: list[AutomationTrigger] = []
        text_lower = text.lower().strip()

        for rule in self._rules:
            if rule.trigger_type != "voice_command":
                continue

            cfg = rule.trigger_config
            phrase = cfg.get("phrase", "").lower().strip()
            if not phrase:
                continue

            # Simple substring match (could be upgraded to fuzzy later)
            if phrase not in text_lower:
                continue

            # Optional speaker restriction
            if "speaker_id" in cfg and cfg["speaker_id"] != speaker_id:
                continue

            triggers.append(
                AutomationTrigger(
                    rule_id=rule.id,
                    person_id=speaker_id,
                    action=rule.action_type,
                    params=rule.action_config,
                )
            )
            logger.info(
                "automation_voice_matched",
                rule_id=rule.id,
                phrase=phrase,
            )

        return triggers

    def evaluate_schedule(
        self, now: datetime | None = None
    ) -> list[AutomationTrigger]:
        """Match rules whose schedule has arrived.

        Rules with trigger_type='schedule' use trigger_config:
          {"time": "HH:MM", "days": ["mon","tue",...]}  (days optional)
        """
        now = now or datetime.now()
        current_time = now.time().replace(second=0, microsecond=0)
        current_day = now.strftime("%a").lower()[:3]

        triggers: list[AutomationTrigger] = []
        for rule in self._rules:
            if rule.trigger_type != "schedule":
                continue

            cfg = rule.trigger_config
            scheduled_str = cfg.get("time", "")
            if not scheduled_str:
                continue

            try:
                hh, mm = scheduled_str.split(":")
                scheduled = time(int(hh), int(mm))
            except (ValueError, AttributeError):
                continue

            if scheduled != current_time:
                continue

            days = cfg.get("days")
            if days and current_day not in [d.lower()[:3] for d in days]:
                continue

            triggers.append(
                AutomationTrigger(
                    rule_id=rule.id,
                    action=rule.action_type,
                    params=rule.action_config,
                )
            )

        return triggers


# --- Rule CRUD helpers (used by web router) ---

def list_rules(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM automation_rules ORDER BY created_at DESC"
    ).fetchall()
    result = []
    for r in rows:
        rule = AutomationRule.from_row(r)
        result.append(
            {
                "id": rule.id,
                "name": rule.name,
                "description": rule.description,
                "trigger_type": rule.trigger_type,
                "trigger_config": rule.trigger_config,
                "action_type": rule.action_type,
                "action_config": rule.action_config,
                "enabled": rule.enabled,
            }
        )
    return result


def create_rule(db: sqlite3.Connection, data: dict) -> str:
    import uuid

    rule_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO automation_rules "
        "(id, name, description, trigger_type, trigger_config, action_type, action_config, enabled) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            rule_id,
            data["name"],
            data.get("description", ""),
            data["trigger_type"],
            json.dumps(data.get("trigger_config", {})),
            data["action_type"],
            json.dumps(data.get("action_config", {})),
            int(data.get("enabled", True)),
        ),
    )
    db.commit()
    return rule_id


def update_rule(db: sqlite3.Connection, rule_id: str, data: dict) -> bool:
    fields: list[str] = []
    values: list = []
    if "name" in data:
        fields.append("name = ?")
        values.append(data["name"])
    if "description" in data:
        fields.append("description = ?")
        values.append(data["description"])
    if "trigger_type" in data:
        fields.append("trigger_type = ?")
        values.append(data["trigger_type"])
    if "trigger_config" in data:
        fields.append("trigger_config = ?")
        values.append(json.dumps(data["trigger_config"]))
    if "action_type" in data:
        fields.append("action_type = ?")
        values.append(data["action_type"])
    if "action_config" in data:
        fields.append("action_config = ?")
        values.append(json.dumps(data["action_config"]))
    if "enabled" in data:
        fields.append("enabled = ?")
        values.append(int(data["enabled"]))

    if not fields:
        return False

    values.append(rule_id)
    cursor = db.execute(
        f"UPDATE automation_rules SET {', '.join(fields)} WHERE id = ?",
        values,
    )
    db.commit()
    return cursor.rowcount > 0


def delete_rule(db: sqlite3.Connection, rule_id: str) -> bool:
    cursor = db.execute("DELETE FROM automation_rules WHERE id = ?", (rule_id,))
    db.commit()
    return cursor.rowcount > 0
