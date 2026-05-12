"""Tests for post-wake voice activity detection."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("torch")

from palantir.audio.vad import VoiceActivityDetector  # noqa: E402


class FakeVadModel:
    def __init__(self, score: float):
        self.score = score
        self.calls: list[int] = []
        self.reset_count = 0

    def eval(self):
        return None

    def reset_states(self) -> None:
        self.reset_count += 1

    def __call__(self, window, sample_rate: int):
        self.calls.append(int(window.numel()))
        return SimpleNamespace(item=lambda: self.score)


def _make_detector(model: FakeVadModel) -> VoiceActivityDetector:
    detector = object.__new__(VoiceActivityDetector)
    detector._sample_rate = 16000
    detector._silence_timeout_samples = 16000
    detector._speech_threshold = 0.5
    detector._max_samples = 16000 * 30
    detector._model = model
    detector._utils = None
    detector._recording = False
    detector._audio_buffer = []
    detector._total_samples = 0
    detector._silence_samples = 0
    detector._speech_detected = False
    detector._vad_remainder = np.empty(0, dtype=np.int16)
    return detector


def test_vad_buffers_480_sample_capture_chunks_into_512_sample_windows():
    model = FakeVadModel(score=0.9)
    detector = _make_detector(model)
    detector.start_recording()

    assert detector.process_audio(np.ones(480, dtype=np.int16)) is None
    assert model.calls == []
    assert detector.speech_detected is False

    assert detector.process_audio(np.ones(480, dtype=np.int16)) is None

    assert model.calls == [512]
    assert detector.speech_detected is True
    assert len(detector._vad_remainder) == 448
