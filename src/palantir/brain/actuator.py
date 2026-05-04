"""Actuator: executes automation trigger actions.

Supported actions:
  - gpio:        {"pin": 17, "state": "high"|"low"|"toggle", "duration_ms": <optional>}
  - tts:         {"text": "..."}
  - notification: {"message": "..."}  (logged + broadcast to web clients)
  - command:     {"shell": "..."}  (sandboxed, must be explicitly allowed)
"""

from __future__ import annotations

import asyncio
import shlex
from typing import TYPE_CHECKING

import structlog

from palantir.models import AssistantResponse, AutomationTrigger, Event, EventType
from palantir.redis_client import Channels, publish

if TYPE_CHECKING:
    from palantir.hardware.gpio import HardwareController

logger = structlog.get_logger()


class Actuator:
    """Executes automation actions.

    Command execution is disabled unless explicitly allowed via ``allow_shell``.
    """

    def __init__(
        self,
        redis,
        hardware: "HardwareController | None" = None,
        allow_shell: bool = False,
    ):
        self._redis = redis
        self._hardware = hardware
        self._allow_shell = allow_shell
        self._relay_states: dict[int, bool] = {}

    async def execute(self, trigger: AutomationTrigger) -> bool:
        """Execute a trigger's action. Returns True on success."""
        action = trigger.action
        params = trigger.params or {}

        try:
            if action == "gpio":
                return await self._do_gpio(params)
            if action == "tts":
                return await self._do_tts(params)
            if action == "notification":
                return await self._do_notification(trigger, params)
            if action == "command":
                return await self._do_command(params)
            logger.warning("actuator_unknown_action", action=action)
            return False
        except Exception:
            logger.exception("actuator_error", action=action)
            return False

    async def _do_gpio(self, params: dict) -> bool:
        pin = int(params.get("pin", 0))
        if pin <= 0:
            return False

        state_str = str(params.get("state", "high")).lower()
        if state_str == "toggle":
            if self._hardware:
                relay = self._hardware.get_relay(pin)
                target = not relay.value
            else:
                target = not self._relay_states.get(pin, False)
        else:
            target = state_str in ("high", "on", "true", "1")

        await self._set_gpio(pin, target)

        duration_ms = int(params.get("duration_ms", 0))
        if duration_ms > 0:
            # Schedule the reverse state after duration
            async def _reverse() -> None:
                await asyncio.sleep(duration_ms / 1000.0)
                await self._set_gpio(pin, not target)

            asyncio.create_task(_reverse())

        return True

    async def _set_gpio(self, pin: int, state: bool) -> None:
        """Set a GPIO relay locally or publish to the Pi relay owner."""
        self._relay_states[pin] = state
        if self._hardware:
            self._hardware.set_relay(pin, state)
            return

        await publish(
            self._redis,
            Channels.RELAY_HARDWARE_CMD,
            {"kind": "relay", "pin": pin, "state": state},
        )

    async def _do_tts(self, params: dict) -> bool:
        text = params.get("text", "").strip()
        if not text:
            return False
        response = AssistantResponse(text=text, target_person_id=None)
        await publish(self._redis, Channels.BRAIN_RESPONSE, response)
        return True

    async def _do_notification(
        self, trigger: AutomationTrigger, params: dict
    ) -> bool:
        message = params.get("message", "").strip()
        if not message:
            return False
        event = Event(
            type=EventType.AUTOMATION_TRIGGERED,
            person_id=trigger.person_id,
            data={
                "rule_id": trigger.rule_id,
                "message": message,
                "type": "notification",
            },
        )
        await publish(self._redis, Channels.EVENTS_LOG, event)
        return True

    async def _do_command(self, params: dict) -> bool:
        if not self._allow_shell:
            logger.warning("actuator_command_blocked", reason="shell disabled")
            return False

        cmd = params.get("shell", "").strip()
        if not cmd:
            return False

        logger.info("actuator_running_command", cmd=cmd[:100])
        try:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(cmd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode != 0:
                logger.warning(
                    "actuator_command_failed",
                    returncode=proc.returncode,
                    stderr=stderr.decode("utf-8", errors="replace")[:200],
                )
                return False
            return True
        except asyncio.TimeoutError:
            logger.warning("actuator_command_timeout")
            return False
