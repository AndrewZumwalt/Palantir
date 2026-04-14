"""Claude Vision API client for complex scene understanding.

Used on-demand when a user asks a question that requires deeper
visual analysis than YOLO can provide (e.g., "where is the drill?",
"what's written on the whiteboard?", "what am I wearing?").
"""

from __future__ import annotations

import base64
import io

import cv2
import numpy as np
import structlog

logger = structlog.get_logger()

try:
    import anthropic

    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


class CloudVision:
    """Sends camera frames to Claude Vision for analysis."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20250301"):
        self._client: anthropic.Anthropic | None = None
        self._model = model

        if not _ANTHROPIC_AVAILABLE:
            logger.warning("anthropic_not_installed")
            return

        if not api_key:
            logger.warning("anthropic_api_key_missing")
            return

        self._client = anthropic.Anthropic(api_key=api_key)
        logger.info("cloud_vision_initialized", model=model)

    def analyze_frame(
        self,
        frame: np.ndarray,
        question: str,
        context: str = "",
    ) -> str | None:
        """Send a camera frame to Claude Vision with a question.

        Args:
            frame: BGR image from OpenCV.
            question: The user's question about the scene.
            context: Additional context (who's asking, etc.).

        Returns:
            Claude's analysis of the scene, or None on failure.
        """
        if not self._client:
            return None

        # Encode frame as JPEG
        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        image_b64 = base64.standard_b64encode(buffer).decode("utf-8")

        system = (
            "You are the vision system of Palintir, a classroom AI assistant. "
            "You are looking at the classroom through a camera mounted on the wall. "
            "Describe what you see concisely and helpfully. "
            "When describing locations, use relative terms like 'on the left side of the desk', "
            "'near the window', 'on the back table'. "
            "Keep responses to 1-3 sentences unless more detail is needed."
        )

        if context:
            system += f"\n\nAdditional context:\n{context}"

        try:
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
                            {
                                "type": "text",
                                "text": question,
                            },
                        ],
                    }
                ],
            )

            text = response.content[0].text
            logger.info(
                "cloud_vision_response",
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                preview=text[:80],
            )
            return text

        except Exception:
            logger.exception("cloud_vision_error")
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

    @property
    def is_available(self) -> bool:
        return self._client is not None
