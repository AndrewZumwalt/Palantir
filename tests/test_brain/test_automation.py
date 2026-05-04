"""Tests for automation rule matching."""

from __future__ import annotations

from palantir.brain.automation import AutomationEngine, create_rule
from palantir.models import EventType


def test_role_specific_person_rule_requires_known_matching_role(temp_db):
    rule_id = create_rule(
        temp_db,
        {
            "name": "Teacher greeting",
            "trigger_type": "person_enters",
            "trigger_config": {"role": "teacher"},
            "action_type": "tts",
            "action_config": {"text": "Welcome"},
        },
    )
    engine = AutomationEngine(temp_db)

    assert engine.evaluate_person_event(EventType.PERSON_ENTERED, "p1", None) == []
    assert engine.evaluate_person_event(EventType.PERSON_ENTERED, "p1", "student") == []

    triggers = engine.evaluate_person_event(EventType.PERSON_ENTERED, "p1", "teacher")
    assert [t.rule_id for t in triggers] == [rule_id]
