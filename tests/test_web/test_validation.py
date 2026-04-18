"""Tests for input validation helpers."""

from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException

from palantir.web.validation import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    decode_base64_audio,
    decode_base64_image,
    validate_consent_text,
    validate_name,
    validate_role,
    validate_rule_config,
)


def test_validate_name_trims_and_returns():
    assert validate_name("  Alice  ") == "Alice"


def test_validate_name_rejects_empty():
    with pytest.raises(HTTPException) as exc:
        validate_name("   ")
    assert exc.value.status_code == 400


def test_validate_name_rejects_control_chars():
    with pytest.raises(HTTPException):
        validate_name("bad\x00name")


def test_validate_name_rejects_injection_chars():
    with pytest.raises(HTTPException):
        validate_name("<script>")


def test_validate_role_normalizes_case():
    assert validate_role("  STUDENT ") == "student"


def test_validate_role_rejects_unknown():
    with pytest.raises(HTTPException):
        validate_role("wizard")


def test_decode_base64_image_roundtrip():
    payload = b"x" * 1024  # meets min size
    encoded = base64.b64encode(payload).decode()
    assert decode_base64_image(encoded) == payload


def test_decode_base64_image_rejects_too_small():
    payload = b"x" * 10
    encoded = base64.b64encode(payload).decode()
    with pytest.raises(HTTPException) as exc:
        decode_base64_image(encoded)
    assert exc.value.status_code == 400


def test_decode_base64_image_rejects_oversize_envelope():
    # Too big even before decoding
    big = "A" * (int(MAX_IMAGE_BYTES * 4 / 3) + 1024)
    with pytest.raises(HTTPException) as exc:
        decode_base64_image(big)
    assert exc.value.status_code == 413


def test_decode_base64_image_rejects_invalid_base64():
    with pytest.raises(HTTPException):
        decode_base64_image("!!!not base64!!!")


def test_decode_base64_audio_roundtrip():
    payload = b"\x00\x01" * 64
    encoded = base64.b64encode(payload).decode()
    assert decode_base64_audio(encoded) == payload


def test_decode_base64_audio_rejects_oversize():
    big = "A" * (int(MAX_AUDIO_BYTES * 4 / 3) + 1024)
    with pytest.raises(HTTPException) as exc:
        decode_base64_audio(big)
    assert exc.value.status_code == 413


def test_validate_consent_text_trims():
    assert validate_consent_text("  yes  ") == "yes"


def test_validate_consent_text_rejects_empty():
    with pytest.raises(HTTPException):
        validate_consent_text("   ")


def test_validate_rule_config_ok():
    cfg = {"when": "entry", "target": "light"}
    assert validate_rule_config(cfg) == cfg


def test_validate_rule_config_rejects_non_serializable():
    class NotSerializable:
        pass

    with pytest.raises(HTTPException):
        validate_rule_config({"x": NotSerializable()})
