"""Tests for relay frame safety guards."""

from __future__ import annotations

from palantir.relay.protocol import Frame, Op
from palantir.web.main import (
    MAX_RELAY_AUDIO_PAYLOAD_BYTES,
    MAX_RELAY_VIDEO_FRAMES_PER_SECOND,
    MAX_RELAY_VIDEO_PAYLOAD_BYTES,
    _relay_payload_too_large,
    _RelayFrameLimiter,
)


def test_relay_payload_limits_binary_sensor_frames():
    assert _relay_payload_too_large(
        Frame(Op.AUDIO_IN, b"\0" * (MAX_RELAY_AUDIO_PAYLOAD_BYTES + 1))
    )
    assert _relay_payload_too_large(
        Frame(Op.VIDEO_FRAME, b"\0" * (MAX_RELAY_VIDEO_PAYLOAD_BYTES + 1))
    )
    assert not _relay_payload_too_large(
        Frame(Op.AUDIO_IN, b"\0" * MAX_RELAY_AUDIO_PAYLOAD_BYTES)
    )


def test_relay_frame_limiter_blocks_excess_video_cadence():
    limiter = _RelayFrameLimiter(now=0.0)

    for _ in range(MAX_RELAY_VIDEO_FRAMES_PER_SECOND):
        assert limiter.check(Op.VIDEO_FRAME, now=0.2)

    assert not limiter.check(Op.VIDEO_FRAME, now=0.2)
    assert limiter.check(Op.VIDEO_FRAME, now=1.1)
