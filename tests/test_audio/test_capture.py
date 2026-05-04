"""Tests for the audio capture queue behavior.

We don't exercise sounddevice here; we just verify the backpressure
logic in the audio callback directly.
"""

from __future__ import annotations

import queue
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock


class _SoundDeviceStub:
    """Minimal sounddevice stub so capture.py imports without PortAudio."""

    CallbackFlags = type("CallbackFlags", (), {})

    def InputStream(self, **kwargs):  # noqa: N802
        return MagicMock()


def _install_sounddevice_stub():
    if "sounddevice" not in sys.modules:
        sys.modules["sounddevice"] = _SoundDeviceStub()


_install_sounddevice_stub()

import numpy as np  # noqa: E402

from palantir.audio.capture import LocalAudioCapture  # noqa: E402
from palantir.config import AudioConfig  # noqa: E402


def _make_capture() -> LocalAudioCapture:
    cfg = AudioConfig()
    cap = LocalAudioCapture(cfg)
    # Shrink queue for faster test
    cap._thread_queue = queue.Queue(maxsize=2)
    return cap


def _fake_frame(marker: float) -> np.ndarray:
    # 2D array as sounddevice would deliver it
    return np.full((10, 1), marker, dtype=np.float32)


def test_audio_callback_drops_oldest_under_backpressure():
    cap = _make_capture()
    status = SimpleNamespace()

    cap._audio_callback(_fake_frame(0.1), 10, {}, status)
    cap._audio_callback(_fake_frame(0.2), 10, {}, status)
    # Queue is now full; next chunk should evict the oldest.
    cap._audio_callback(_fake_frame(0.3), 10, {}, status)

    retained = []
    while True:
        try:
            retained.append(cap._thread_queue.get_nowait())
        except queue.Empty:
            break

    assert len(retained) == 2
    # Oldest (0.1) should have been dropped; 0.2 and 0.3 kept in order.
    assert retained[0][0] != 0
    assert retained[1][0] > retained[0][0]
