"""Brain service: LLM orchestration, identity linking, automation.

This is the main entry point for the palintir-brain systemd service.
Subscribes to utterances from the audio service, builds context,
calls Claude API, and publishes responses for TTS.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time

import structlog

from palintir.config import load_config
from palintir.db import init_db
from palintir.logging import setup_logging
from palintir.models import (
    AssistantResponse,
    Event,
    EventType,
    PrivacyModeEvent,
    ServiceStatus,
    Utterance,
)
from palintir.redis_client import Channels, Keys, Subscriber, create_redis, publish

from .context_builder import ContextBuilder
from .conversation import ConversationManager
from .identity_linker import IdentityLinker
from .llm_client import LLMClient

logger = structlog.get_logger()


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
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_event_loop()
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

        # Subscribe to events
        self._subscriber = Subscriber(self._redis)
        self._subscriber.on(Channels.SYSTEM_PRIVACY, self._on_privacy_toggle)
        self._subscriber.on(Channels.AUDIO_UTTERANCE, self._on_utterance)
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
        if last_speaker:
            parts = last_speaker.split(":", 2)
            if len(parts) == 3:
                raw_id, raw_name, raw_conf = parts
                # Link voice identity to visual location
                linked = await self._identity_linker.link(
                    speaker_person_id=raw_id,
                    speaker_name=raw_name,
                    speaker_confidence=float(raw_conf),
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

        # Build context from room state
        context = await self._context_builder.build(
            speaker_name=speaker_name,
            speaker_id=speaker_id,
        )

        # Get conversation history
        history = self._conversation.get_recent_turns(person_id=speaker_id, limit=5)

        # Determine if we need the complex model
        use_complex = self._should_use_complex_model(utterance.text)

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

        if not response_text:
            response_text = "I'm sorry, I couldn't process that. Could you try again?"

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

    def _should_use_complex_model(self, text: str) -> bool:
        """Decide if a query needs the more powerful (slower) model."""
        complex_triggers = [
            "explain", "why", "how does", "what is the difference",
            "analyze", "compare", "teach me", "help me understand",
        ]
        lower = text.lower()
        return any(trigger in lower for trigger in complex_triggers)

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
            },
        )
        await publish(self._redis, Channels.SYSTEM_STATUS, status)

    async def run(self) -> None:
        await self.start()
        try:
            while self._running:
                await self._publish_status(healthy=True)
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._subscriber:
            await self._subscriber.stop()
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
