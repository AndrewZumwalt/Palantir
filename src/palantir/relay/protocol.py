"""Wire protocol for the Pi <-> laptop relay.

A single bidirectional WebSocket carries both directions.  Each WebSocket
*message* is one binary frame: byte 0 is the opcode, the rest is the
payload.  The web framework (websockets / FastAPI) already gives us
message boundaries, so we don't need length prefixes.

Payload formats per opcode are documented on the Op enum below.  The
codec is intentionally tiny — no msgpack, no protobuf — so the Pi-side
client has zero compile-time deps beyond `websockets`.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass


class Op(enum.IntEnum):
    """Frame opcodes.

    Pi -> laptop:
        AUDIO_IN     payload = raw int16 little-endian PCM, 16 kHz mono
        VIDEO_FRAME  payload = JPEG bytes
        GPIO_EVENT   payload = UTF-8 JSON {"event": str, ...}
        HELLO        payload = UTF-8 JSON {"version": str, "hostname": str}

    Laptop -> Pi:
        AUDIO_OUT    payload = int16 LE PCM at the negotiated sample rate
        LED          payload = UTF-8 JSON {"r": float, "g": float, "b": float}
        RELAY        payload = UTF-8 JSON {"pin": int, "state": bool}
        PING         payload = empty (keep-alive)

    Either direction:
        ERROR        payload = UTF-8 JSON {"message": str}
    """

    HELLO = 0x00
    AUDIO_IN = 0x01
    VIDEO_FRAME = 0x02
    GPIO_EVENT = 0x03

    AUDIO_OUT = 0x10
    LED = 0x11
    RELAY = 0x12
    PING = 0x1F

    ERROR = 0xFF


# Default sample rate for both directions.  Matches AudioConfig.sample_rate
# and TTSConfig.sample_rate (the Pi resamples on output if needed).
DEFAULT_AUDIO_SR_HZ = 16000


@dataclass
class Frame:
    """Decoded relay frame."""

    op: Op
    payload: bytes

    def encode(self) -> bytes:
        """Serialize to a single binary WebSocket message."""
        return bytes([int(self.op)]) + self.payload

    @classmethod
    def decode(cls, message: bytes) -> "Frame":
        """Parse a binary WebSocket message into a Frame.

        Raises ValueError if the message is empty or has an unknown opcode.
        """
        if not message:
            raise ValueError("empty relay frame")
        op_byte = message[0]
        try:
            op = Op(op_byte)
        except ValueError as e:
            raise ValueError(f"unknown opcode 0x{op_byte:02x}") from e
        return cls(op=op, payload=message[1:])

    # ---------- Convenience builders ----------

    @classmethod
    def hello(cls, version: str, hostname: str) -> "Frame":
        body = json.dumps({"version": version, "hostname": hostname}).encode()
        return cls(Op.HELLO, body)

    @classmethod
    def audio_in(cls, pcm_int16_le: bytes) -> "Frame":
        return cls(Op.AUDIO_IN, pcm_int16_le)

    @classmethod
    def video(cls, jpeg_bytes: bytes) -> "Frame":
        return cls(Op.VIDEO_FRAME, jpeg_bytes)

    @classmethod
    def gpio_event(cls, event: str, **fields: object) -> "Frame":
        body = json.dumps({"event": event, **fields}).encode()
        return cls(Op.GPIO_EVENT, body)

    @classmethod
    def audio_out(cls, pcm_int16_le: bytes) -> "Frame":
        return cls(Op.AUDIO_OUT, pcm_int16_le)

    @classmethod
    def led(cls, r: float, g: float, b: float) -> "Frame":
        body = json.dumps({"r": r, "g": g, "b": b}).encode()
        return cls(Op.LED, body)

    @classmethod
    def relay(cls, pin: int, state: bool) -> "Frame":
        body = json.dumps({"pin": pin, "state": state}).encode()
        return cls(Op.RELAY, body)

    @classmethod
    def ping(cls) -> "Frame":
        return cls(Op.PING, b"")

    @classmethod
    def error(cls, message: str) -> "Frame":
        return cls(Op.ERROR, json.dumps({"message": message}).encode())

    def json(self) -> dict:
        """Decode a JSON payload (for control opcodes).  Raises if non-JSON."""
        return json.loads(self.payload.decode("utf-8"))
