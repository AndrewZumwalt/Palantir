"""Brain service: LLM orchestration, identity linking, automation.

This is the main entry point for the palantir-brain systemd service.
Subscribes to utterances from the audio service, builds context,
calls Claude API, and publishes responses for TTS.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time

import structlog

from palantir.config import load_config
from palantir.db import init_db
from palantir.logging import setup_logging
from palantir.models import (
    AssistantResponse,
    Event,
    EventType,
    PrivacyModeEvent,
    ServiceStatus,
    Utterance,
)
from palantir.preflight import log_and_check, validate_for
from palantir.redis_client import Channels, Keys, Subscriber, create_redis, publish
from palantir.resilience import NetworkMonitor

from .actuator import Actuator
from .automation import AutomationEngine
from .context_builder import ContextBuilder
from .conversation import ConversationManager
from .identity_linker import IdentityLinker
from .llm_client import LLMClient
from .offline_responder import generate_offline_response

logger = structlog.get_logger()

# Conditional import for cloud vision
try:
    from palantir.vision.cloud_vision import CloudVision

    _CLOUD_VISION_AVAILABLE = True
except ImportError:
    _CLOUD_VISION_AVAILABLE = False


class BrainService:
    """Orchestrates AI reasoning: receives utterances, generates responses."""

    def __init__(self):
        self._config = load_config()
        self._redis = None
        self._subscriber: Subscriber | None = None
        self._db = None
        self._privacy_mode = False
        self._running = False
        self._start_time = time.monotonic()

        # Components
        self._llm: LLMClient | None = None
        self._context_builder: ContextBuilder | None = None
        self._conversation: ConversationManager | None = None
        self._identity_linker: IdentityLinker | None = None
        self._cloud_vision: CloudVision | None = None
        self._automation: AutomationEngine | None = None
        self._actuator: Actuator | None = None
        self._network_monitor: NetworkMonitor | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._automation_triggered = 0

    async def start(self) -> None:
        # Preflight checks — warn loudly about missing deps/config
        preflight = validate_for("brain", self._config)
        if not log_and_check(preflight, fatal_on_error=False):
            raise RuntimeError("brain preflight failed")

        self._loop = asyncio.get_running_loop()
        self._redis = await create_redis(self._config)
        self._db = init_db(self._config)

        privacy = await self._redis.get(Keys.PRIVACY_MODE)
        self._privacy_mode = privacy == "1"

        # Initialize components
        self._llm = LLMClient(
            api_key=self._config.anthropic_api_key,
            config=self._config.llm,
        )
        self._context_builder = ContextBuilder(self._redis, self._db)
        self._conversation = ConversationManager(self._db)
        self._identity_linker = IdentityLinker(
            self._redis,
            staleness_timeout=self._config.identity.identity_staleness_seconds,
        )

        # Initialize cloud vision for object/scene queries
        if _CLOUD_VISION_AVAILABLE and self._config.anthropic_api_key:
            self._cloud_vision = CloudVision(
                api_key=self._config.anthropic_api_key,
                model=self._config.llm.default_model,
            )

        # Start network monitor for offline detection
        self._network_monitor = NetworkMonitor(check_interval_seconds=30.0)
        await self._network_monitor.start()

        # Initialize automation engine and actuator
        self._automation = AutomationEngine(self._db)
        self._actuator = Actuator(
            self._redis,
            hardware=None,  # Brain doesn't own hardware; GPIO actions published for hardware owner
            allow_shell=self._config.automation.allow_shell_commands,
        )

        # Subscribe to events
        self._subscriber = Subscriber(self._redis)
        self._subscriber.on(Channels.SYSTEM_PRIVACY, self._on_privacy_toggle)
        self._subscriber.on(Channels.AUDIO_UTTERANCE, self._on_utterance)
        self._subscriber.on(Channels.EVENTS_LOG, self._on_event_for_automation)
        await self._subscriber.start()

        self._running = True
        logger.info(
            "brain_service_started",
            llm_available=self._llm.is_available,
        )
        await self._publish_status(healthy=True)

    async def _on_utterance(self, data: dict) -> None:
        """Handle a transcribed utterance from the audio service."""
        if self._privacy_mode:
            return

        utterance = Utterance(**data)

        if not utterance.text:
            return

        logger.info("brain_processing", text=utterance.text[:100])

        # Resolve speaker identity via the identity linker
        speaker_name = None
        speaker_id = None
        linked = None

        # Check if audio service identified the speaker
        last_speaker = await self._redis.get("state:last_speaker")
        parsed_speaker: dict | None = None
        if last_speaker:
            try:
                parsed_speaker = json.loads(last_speaker)
            except (json.JSONDecodeError, TypeError):
                logger.warning("last_speaker_parse_failed", raw=str(last_speaker)[:80])

        if parsed_speaker:
            # Link voice identity to visual location
            try:
                conf = float(parsed_speaker.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            linked = await self._identity_linker.link(
                speaker_person_id=parsed_speaker.get("person_id"),
                speaker_name=parsed_speaker.get("name"),
                speaker_confidence=conf,
            )
            speaker_id = linked.person_id
            speaker_name = linked.name
            logger.info(
                "identity_resolved",
                name=speaker_name,
                fully_linked=linked.fully_linked,
                location_source=linked.location_source,
            )
        else:
            # No voice match - try inference from visible faces
            linked = await self._identity_linker.link(None, None, 0.0)
            speaker_id = linked.person_id
            speaker_name = linked.name

        # Check if this is a visual/object question that needs cloud vision
        visual_answer = await self._handle_visual_question(utterance.text, speaker_name)

        # Build context from room state
        context = await self._context_builder.build(
            speaker_name=speaker_name,
            speaker_id=speaker_id,
        )

        # If cloud vision answered, include it in context
        if visual_answer:
            context += f"\n\n[VISUAL ANALYSIS]\n{visual_answer}"

        # Get conversation history
        history = self._conversation.get_recent_turns(person_id=speaker_id, limit=5)

        # Determine if we need the complex model
        use_complex = self._should_use_complex_model(utterance.text)

        # Decide whether to even attempt the cloud call
        network_ok = not self._network_monitor or self._network_monitor.online
        api_degraded = self._llm.is_degraded if self._llm else True

        response_text: str | None = None
        if network_ok and not api_degraded and self._llm and self._llm.is_available:
            # Call Claude API (run in executor since it's synchronous)
            response_text = await self._loop.run_in_executor(
                None,
                lambda: self._llm.chat(
                    user_message=utterance.text,
                    context=context,
                    conversation_history=history,
                    use_complex_model=use_complex,
                ),
            )

        # Offline fallback: generate a deterministic reply from local state
        if not response_text:
            visible_names = await self._get_visible_person_names()
            response_text = generate_offline_response(
                user_text=utterance.text,
                visible_person_names=visible_names,
                speaker_name=speaker_name,
            )
            logger.info(
                "brain_offline_response",
                network_ok=network_ok,
                api_degraded=api_degraded,
            )

        # Save conversation turn
        self._conversation.save_turn(
            user_text=utterance.text,
            assistant_text=response_text,
            person_id=speaker_id,
        )

        # Publish response for TTS
        response = AssistantResponse(
            text=response_text,
            target_person_id=speaker_id,
        )
        await publish(self._redis, Channels.BRAIN_RESPONSE, response)

        # Log the event
        event = Event(
            type=EventType.UTTERANCE,
            person_id=speaker_id,
            data={"user_text": utterance.text, "response_text": response_text},
        )
        await publish(self._redis, Channels.EVENTS_LOG, event)

        logger.info("brain_responded", response=response_text[:100])

        # Check voice command automation rules
        if self._automation:
            voice_triggers = self._automation.evaluate_voice_command(
                utterance.text, speaker_id
            )
            for trigger in voice_triggers:
                await self._fire_trigger(trigger)

    async def _on_event_for_automation(self, data: dict) -> None:
        """Evaluate person enter/exit events against automation rules."""
        if self._privacy_mode or not self._automation:
            return

        try:
            event = Event(**data)
        except Exception:
            return

        if event.type not in (EventType.PERSON_ENTERED, EventType.PERSON_EXITED):
            return

        if not event.person_id:
            return

        role = event.data.get("role")
        triggers = self._automation.evaluate_person_event(
            event.type, event.person_id, role
        )
        for trigger in triggers:
            await self._fire_trigger(trigger)

    async def _fire_trigger(self, trigger) -> None:
        """Publish an automation trigger, execute it, and log it."""
        await publish(self._redis, Channels.BRAIN_ACTION, trigger)

        # Execute via actuator (TTS + notification handled directly; GPIO needs hardware owner)
        if self._actuator:
            await self._actuator.execute(trigger)

        self._automation_triggered += 1

        # Log as event
        event = Event(
            type=EventType.AUTOMATION_TRIGGERED,
            person_id=trigger.person_id,
            data={
                "rule_id": trigger.rule_id,
                "action": trigger.action,
                "params": trigger.params,
            },
        )
        await publish(self._redis, Channels.EVENTS_LOG, event)
        logger.info(
            "automation_fired",
            rule_id=trigger.rule_id,
            action=trigger.action,
        )

    def _should_use_complex_model(self, text: str) -> bool:
        """Decide if a query needs the more powerful (slower) model."""
        complex_triggers = [
            "explain", "why", "how does", "what is the difference",
            "analyze", "compare", "teach me", "help me understand",
        ]
        lower = text.lower()
        return any(trigger in lower for trigger in complex_triggers)

    def _is_visual_question(self, text: str) -> bool:
        """Detect if a question requires looking at the camera feed."""
        visual_triggers = [
            "where is", "where are", "where's", "can you see",
            "do you see", "what am i wearing", "what is he wearing",
            "what is she wearing", "what color", "how many people",
            "what's on the", "look at", "show me", "find the",
            "what does", "look like", "what's in", "is there a",
            "what am i holding", "what am i doing",
        ]
        lower = text.lower()
        return any(trigger in lower for trigger in visual_triggers)

    async def _handle_visual_question(
        self, text: str, speaker_name: str | None
    ) -> str | None:
        """Handle questions that need visual analysis.

        Strategy:
        1. Check YOLO cache for simple object questions
        2. Fall back to Claude Vision for complex questions
        """
        if not self._is_visual_question(text):
            return None

        # Try answering from YOLO cache first
        cached = await self._redis.get(Keys.OBJECT_CACHE)
        if cached:
            import json
            try:
                objects = json.loads(cached)
                lower = text.lower()

                # Check if a specific object is mentioned in the cache
                for obj in objects:
                    if obj["label"].lower() in lower:
                        loc = obj.get("location_description", "visible in frame")
                        return (
                            f"I can see a {obj['label']} {loc} "
                            f"(confidence: {obj['confidence']:.0%})."
                        )
            except (json.JSONDecodeError, KeyError):
                pass

        # Cache miss or complex question - use Claude Vision
        if self._cloud_vision and self._cloud_vision.is_available:
            frame = await self._get_latest_frame_async()
            context = f"The person asking is: {speaker_name or 'unknown'}"
            vision_answer = await self._loop.run_in_executor(
                None,
                lambda: self._cloud_vision.analyze_frame(frame, text, context),
            )
            if vision_answer:
                return vision_answer

        return None

    async def _get_visible_person_names(self) -> list[str]:
        """Return names of currently visible people from Redis."""
        try:
            visible = await self._redis.hgetall(Keys.VISIBLE_PERSONS)
            names: list[str] = []
            for raw in visible.values():
                try:
                    data = json.loads(raw)
                    if "name" in data:
                        names.append(data["name"])
                except (json.JSONDecodeError, KeyError):
                    continue
            return names
        except Exception:
            return []

    async def _get_latest_frame_async(self):
        """Get the latest camera frame from Redis."""
        import cv2
        import numpy as np

        frame_bytes = await self._redis.get(Keys.LATEST_FRAME)
        if frame_bytes:
            np_arr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if frame is not None:
                return frame

        return np.zeros((480, 640, 3), dtype=np.uint8)

    async def _on_privacy_toggle(self, data: dict) -> None:
        event = PrivacyModeEvent(**data)
        self._privacy_mode = event.enabled
        logger.info("brain_privacy_mode", enabled=event.enabled)

    async def _publish_status(self, healthy: bool) -> None:
        status = ServiceStatus(
            name="brain",
            healthy=healthy,
            uptime_seconds=time.monotonic() - self._start_time,
            details={
                "privacy_mode": self._privacy_mode,
                "llm_available": self._llm.is_available if self._llm else False,
                "llm_circuit": self._llm.breaker_state if self._llm else "n/a",
                "online": self._network_monitor.online if self._network_monitor else True,
                "automation_rules": self._automation.rule_count if self._automation else 0,
                "automation_triggered": self._automation_triggered,
            },
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    async def run(self) -> None:
        await self.start()
        reload_counter = 0
        last_schedule_minute = -1
        try:
            while self._running:
                await self._publish_status(healthy=True)

                # Check time-based schedule rules once per minute
                if self._automation:
                    from datetime import datetime
                    now = datetime.now()
                    if now.minute != last_schedule_minute:
                        last_schedule_minute = now.minute
                        triggers = self._automation.evaluate_schedule(now)
                        for trigger in triggers:
                            await self._fire_trigger(trigger)

                await asyncio.sleep(10)

                # Reload automation rules periodically (every ~5 min)
                reload_counter += 1
                if reload_counter >= 30 and self._automation:
                    self._automation.reload()
                    reload_counter = 0
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._subscriber:
            await self._subscriber.stop()
        if self._network_monitor:
            await self._network_monitor.stop()
        if self._db:
            self._db.close()
        if self._redis:
            await self._redis.close()
        logger.info("brain_service_stopped")


def main() -> None:
    setup_logging("brain")
    service = BrainService()
    loop = asyncio.new_event_loop()

    def shutdown(sig: signal.Signals) -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown, sig)

    try:
        loop.run_until_complete(service.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
