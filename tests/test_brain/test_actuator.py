"""Tests for automation actuator behavior."""

from __future__ import annotations

import json

from palantir.brain.actuator import Actuator
from palantir.models import AutomationTrigger
from palantir.redis_client import Channels


class FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


async def test_gpio_action_publishes_to_relay_when_hardware_missing():
    redis = FakeRedis()
    actuator = Actuator(redis)
    trigger = AutomationTrigger(
        rule_id="rule-1",
        action="gpio",
        params={"pin": 17, "state": "high"},
    )

    assert await actuator.execute(trigger) is True

    assert len(redis.published) == 1
    channel, payload = redis.published[0]
    assert channel == Channels.RELAY_HARDWARE_CMD
    assert json.loads(payload) == {"kind": "relay", "pin": 17, "state": True}


async def test_gpio_toggle_uses_last_published_relay_state():
    redis = FakeRedis()
    actuator = Actuator(redis)
    trigger = AutomationTrigger(
        rule_id="rule-1",
        action="gpio",
        params={"pin": 17, "state": "toggle"},
    )

    assert await actuator.execute(trigger) is True
    assert await actuator.execute(trigger) is True

    states = [json.loads(payload)["state"] for _, payload in redis.published]
    assert states == [True, False]
