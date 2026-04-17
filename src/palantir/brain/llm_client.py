"""Claude API client for Palantir's reasoning engine.

Handles conversation with the Claude API, including prompt caching
and model selection (Haiku for fast responses, Sonnet for complex queries).
Includes circuit-breaker for graceful degradation on outages.
"""

from __future__ import annotations

import structlog

from palantir.config import LLMConfig
from palantir.resilience import CircuitBreaker

logger = structlog.get_logger()

try:
    import anthropic

    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


SYSTEM_PROMPT = """You are Palantir, an AI classroom assistant mounted on the wall of a classroom. You observe the room through a camera and listen via microphone.

Your personality:
- Helpful and friendly, but concise — you're speaking aloud, so keep responses brief and natural
- Aware of the classroom context (who's present, what's happening)
- Respectful of student privacy
- You address people by name when you know who's talking

Your capabilities:
- You can see the room through your camera
- You know who is present (via facial recognition)
- You can detect and locate objects in the room
- You remember past conversations

Keep responses SHORT (1-3 sentences) unless asked to explain something in detail. You're speaking out loud to a room — long responses are awkward."""


class LLMClient:
    """Client for the Claude API with smart model routing."""

    def __init__(self, api_key: str, config: LLMConfig):
        self._config = config
        self._client: anthropic.Anthropic | None = None
        self._breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=30.0,
            name="claude_api",
        )

        if not _ANTHROPIC_AVAILABLE:
            logger.warning("anthropic_not_installed", hint="pip install anthropic")
            return

        if not api_key:
            logger.warning("anthropic_api_key_missing")
            return

        self._client = anthropic.Anthropic(api_key=api_key, timeout=15.0)
        logger.info("llm_client_initialized", default_model=config.default_model)

    @property
    def breaker_state(self) -> str:
        return self._breaker.state.value

    @property
    def is_degraded(self) -> bool:
        """True when the circuit is open (API is failing)."""
        return self._breaker.state.value == "open"

    def chat(
        self,
        user_message: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        use_complex_model: bool = False,
    ) -> str | None:
        """Send a message to Claude and get a response.

        Args:
            user_message: The user's transcribed speech.
            context: Structured context about the room state.
            conversation_history: Recent conversation turns.
            use_complex_model: Use Sonnet instead of Haiku for complex queries.

        Returns:
            Claude's response text, or None on failure.
        """
        if not self._client:
            return None  # caller will use offline_responder

        # Circuit breaker: fail fast if API is known to be down
        if not self._breaker.allow_request():
            logger.info("llm_circuit_open_skipping", model=self._config.default_model)
            return None

        model = self._config.complex_model if use_complex_model else self._config.default_model

        # Build system prompt with context
        system_parts = [SYSTEM_PROMPT]
        if context:
            system_parts.append(f"\n\nCurrent classroom state:\n{context}")

        system = "\n".join(system_parts)

        # Build message history
        messages = []
        if conversation_history:
            for turn in conversation_history[-10:]:  # Last 10 turns
                messages.append({"role": turn["role"], "content": turn["content"]})

        messages.append({"role": "user", "content": user_message})

        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=500,
                temperature=self._config.temperature,
                system=system,
                messages=messages,
            )

            text = response.content[0].text
            self._breaker.record_success()
            logger.info(
                "llm_response",
                model=model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                text_preview=text[:80],
            )
            return text

        except Exception:
            self._breaker.record_failure()
            logger.exception("llm_request_failed")
            return None

    @property
    def is_available(self) -> bool:
        return self._client is not None
