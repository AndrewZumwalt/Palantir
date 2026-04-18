"""Cloud vision client for complex scene understanding.

Used on-demand when a user asks a question that requires deeper visual
analysis than YOLO can provide (e.g., "where is the drill?", "what's
written on the whiteboard?", "what am I wearing?").

Supports two providers — mirroring ``LLMClient``:

  - **Anthropic Claude Vision** (preferred) via the `anthropic` SDK.
  - **Groq** (fallback) via the `groq` SDK with a Llama vision model.

Whichever API key is set first (Anthropic wins on a tie) is used. The
public ``analyze_frame`` surface is provider-agnostic.
"""

from __future__ import annotations

import base64
from typing import Any

import cv2
import numpy as np
import structlog

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


class CloudVision:
    """Sends camera frames to a vision-capable LLM for analysis."""

    def __init__(
        self,
        anthropic_api_key: str = "",
        groq_api_key: str = "",
        anthropic_model: str = "claude-haiku-4-5-20251001",
        groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
        # Back-compat: older callers passed `api_key` + `model` (Anthropic).
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client: Any = None
        self._provider: str = "none"
        self._model: str = ""
        self._breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=60.0,
            name="cloud_vision",
        )

        # Back-compat shim
        if api_key and not anthropic_api_key:
            anthropic_api_key = api_key
        if model and anthropic_model == "claude-haiku-4-5-20251001":
            anthropic_model = model

        if anthropic_api_key:
            if not _ANTHROPIC_AVAILABLE:
                logger.warning("anthropic_not_installed")
            else:
                self._client = anthropic.Anthropic(
                    api_key=anthropic_api_key, timeout=15.0
                )
                self._provider = "anthropic"
                self._model = anthropic_model
                logger.info(
                    "cloud_vision_initialized",
                    provider="anthropic",
                    model=anthropic_model,
                )
                return

        if groq_api_key:
            if not _GROQ_AVAILABLE:
                logger.warning("groq_not_installed")
            else:
                self._client = groq.Groq(api_key=groq_api_key, timeout=15.0)
                self._provider = "groq"
                self._model = groq_model
                logger.info(
                    "cloud_vision_initialized",
                    provider="groq",
                    model=groq_model,
                )
                return

        logger.warning("cloud_vision_api_key_missing")

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def analyze_frame(
        self,
        frame: np.ndarray,
        question: str,
        context: str = "",
    ) -> str | None:
        """Send a camera frame to the vision API with a question.

        Args:
            frame: BGR image from OpenCV.
            question: The user's question about the scene.
            context: Additional context (who's asking, etc.).

        Returns:
            The model's analysis of the scene, or None on failure.
        """
        if not self._client:
            return None

        if not self._breaker.allow_request():
            logger.info("cloud_vision_circuit_open_skipping")
            return None

        # Encode frame as JPEG
        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        image_b64 = base64.standard_b64encode(buffer).decode("utf-8")

        system = (
            "You are the vision system of Palantir, a classroom AI assistant. "
            "You are looking at the classroom through a camera mounted on the wall. "
            "Describe what you see concisely and helpfully. "
            "When describing locations, use relative terms like 'on the left side of the desk', "
            "'near the window', 'on the back table'. "
            "Keep responses to 1-3 sentences unless more detail is needed."
        )

        if context:
            system += f"\n\nAdditional context:\n{context}"

        try:
            if self._provider == "anthropic":
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=300,
                    system=system,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": image_b64,
                                    },
                                },
                                {"type": "text", "text": question},
                            ],
                        }
                    ],
                )
                text = response.content[0].text
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
            else:  # groq — OpenAI-style chat completions with image_url part
                data_url = f"data:image/jpeg;base64,{image_b64}"
                response = self._client.chat.completions.create(
                    model=self._model,
                    max_tokens=300,
                    messages=[
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": data_url},
                                },
                                {"type": "text", "text": question},
                            ],
                        },
                    ],
                )
                text = response.choices[0].message.content or ""
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens

            self._breaker.record_success()
            logger.info(
                "cloud_vision_response",
                provider=self._provider,
                tokens_in=input_tokens,
                tokens_out=output_tokens,
                preview=text[:80],
            )
            return text

        except Exception:
            self._breaker.record_failure()
            logger.exception("cloud_vision_error", provider=self._provider)
            return None

    def describe_scene(self, frame: np.ndarray) -> str | None:
        """Get a general description of the current scene."""
        return self.analyze_frame(
            frame,
            "Briefly describe what you see in this classroom. "
            "Note any people, their activities, and notable objects.",
        )

    def find_object(self, frame: np.ndarray, object_name: str) -> str | None:
        """Find a specific object in the scene."""
        return self.analyze_frame(
            frame,
            f"Can you see a {object_name} in this image? "
            f"If yes, describe exactly where it is. If no, say you don't see it.",
        )
