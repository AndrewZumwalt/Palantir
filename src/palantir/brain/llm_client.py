"""LLM client for Palantir's reasoning engine.

Supports two providers, selected automatically based on which API key is
set at startup:

  - **Anthropic** (preferred): uses the `anthropic` SDK with Claude Haiku /
    Sonnet. Full prompt caching + high-quality reasoning.
  - **Groq** (fallback): uses the `groq` SDK (OpenAI-compatible) with
    Llama 3.x models. Free tier is generous, so this is handy for local
    development when you don't want to pay for Anthropic tokens.

If both keys are set, Anthropic wins. If neither is set, the client is
inert and callers fall back to the offline responder.

The public surface (``chat``, ``is_available``, ``is_degraded``,
``breaker_state``) is identical across providers so the rest of the code
doesn't need to know which backend is live.
"""

from __future__ import annotations

from typing import Any

import structlog

from palantir.config import LLMConfig
from palantir.resilience import CircuitBreaker

logger = structlog.get_logger()

try:
    import anthropic

    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    import groq

    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False


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
    """Client for chat completions with smart model routing.

    Accepts both an Anthropic key and a Groq key — whichever is set first
    (Anthropic wins on a tie) is used as the provider. The rest of the
    brain service doesn't need to care which one answered.
    """

    def __init__(
        self,
        config: LLMConfig,
        anthropic_api_key: str = "",
        groq_api_key: str = "",
        # Back-compat shim: older callers passed a single `api_key` meaning
        # the Anthropic key. Accept it if given.
        api_key: str | None = None,
    ):
        self._config = config
        self._client: Any = None
        self._provider: str = "none"
        self._breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=30.0,
            name="llm_api",
        )

        if api_key and not anthropic_api_key:
            anthropic_api_key = api_key

        # Preference order: Anthropic first, Groq second.
        if anthropic_api_key:
            if not _ANTHROPIC_AVAILABLE:
                logger.warning(
                    "anthropic_key_set_but_sdk_missing",
                    hint="pip install anthropic",
                )
            else:
                self._client = anthropic.Anthropic(
                    api_key=anthropic_api_key, timeout=15.0
                )
                self._provider = "anthropic"
                logger.info(
                    "llm_client_initialized",
                    provider="anthropic",
                    default_model=config.default_model,
                    complex_model=config.complex_model,
                )
                return

        if groq_api_key:
            if not _GROQ_AVAILABLE:
                logger.warning(
                    "groq_key_set_but_sdk_missing",
                    hint="pip install groq",
                )
            else:
                self._client = groq.Groq(api_key=groq_api_key, timeout=15.0)
                self._provider = "groq"
                logger.info(
                    "llm_client_initialized",
                    provider="groq",
                    default_model=config.groq_default_model,
                    complex_model=config.groq_complex_model,
                )
                return

        logger.warning(
            "llm_api_key_missing",
            hint="set ANTHROPIC_API_KEY or GROQ_API_KEY",
        )

    @property
    def provider(self) -> str:
        """Which backend is live: 'anthropic', 'groq', or 'none'."""
        return self._provider

    @property
    def breaker_state(self) -> str:
        return self._breaker.state.value

    @property
    def is_degraded(self) -> bool:
        """True when the circuit is open (API is failing)."""
        return self._breaker.state.value == "open"

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def chat(
        self,
        user_message: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        use_complex_model: bool = False,
    ) -> str | None:
        """Send a message and get a response.

        Args:
            user_message: The user's transcribed speech.
            context: Structured context about the room state.
            conversation_history: Recent conversation turns.
            use_complex_model: Use the more powerful model for complex queries.

        Returns:
            The model's response text, or None on failure.
        """
        if not self._client:
            return None  # caller will use offline_responder

        # Circuit breaker: fail fast if API is known to be down
        if not self._breaker.allow_request():
            logger.info("llm_circuit_open_skipping", provider=self._provider)
            return None

        # Build system prompt with context (same for both providers)
        system_parts = [SYSTEM_PROMPT]
        if context:
            system_parts.append(f"\n\nCurrent classroom state:\n{context}")
        system = "\n".join(system_parts)

        # Build message history (same shape for both providers)
        messages: list[dict] = []
        if conversation_history:
            for turn in conversation_history[-10:]:  # Last 10 turns
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user_message})

        try:
            if self._provider == "anthropic":
                model = (
                    self._config.complex_model
                    if use_complex_model
                    else self._config.default_model
                )
                response = self._client.messages.create(
                    model=model,
                    max_tokens=500,
                    temperature=self._config.temperature,
                    system=system,
                    messages=messages,
                )
                text = response.content[0].text
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
            else:  # groq (OpenAI-compatible chat completions API)
                model = (
                    self._config.groq_complex_model
                    if use_complex_model
                    else self._config.groq_default_model
                )
                # Groq wants system as the first message, not a separate field.
                groq_messages = [{"role": "system", "content": system}] + messages
                response = self._client.chat.completions.create(
                    model=model,
                    max_tokens=500,
                    temperature=self._config.temperature,
                    messages=groq_messages,
                )
                text = response.choices[0].message.content or ""
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens

            self._breaker.record_success()
            logger.info(
                "llm_response",
                provider=self._provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                text_preview=text[:80],
            )
            return text

        except Exception:
            self._breaker.record_failure()
            logger.exception("llm_request_failed", provider=self._provider)
            return None
