"""Input validation helpers for the web API.

Centralizes size limits and character-set checks so that every endpoint
rejects pathological input consistently. Kept deliberately small — pydantic
models handle most shape validation; this module handles sizes, encodings,
and allow-lists.
"""

from __future__ import annotations

import base64
import re

from fastapi import HTTPException

# Hard ceilings. Tuned for classroom use on a Pi: a typical enrollment photo
# is ~100-300KB JPEG, and a 5s voice clip at 16kHz int16 is ~160KB.
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB decoded
MAX_AUDIO_BYTES = 4 * 1024 * 1024  # 4 MB decoded (~2 min at 16kHz int16)
MAX_NAME_LEN = 100
MAX_ROLE_LEN = 20
MAX_CONSENT_LEN = 4000
MAX_RULE_CONFIG_BYTES = 4096

ALLOWED_ROLES = frozenset({"student", "teacher", "admin", "guest"})

# Unicode letters + common name punctuation. Deliberately permissive — we
# serve an international classroom — but blocks control chars and anything
# that looks like injection (<, >, {, }, backticks, null bytes).
_NAME_FORBIDDEN = re.compile(r"[<>{}`\x00-\x1f\x7f]")


def validate_name(name: str) -> str:
    """Normalize and validate a person/rule name."""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if len(name) > MAX_NAME_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Name exceeds maximum length of {MAX_NAME_LEN}",
        )
    if _NAME_FORBIDDEN.search(name):
        raise HTTPException(
            status_code=400,
            detail="Name contains disallowed characters",
        )
    return name


def validate_role(role: str) -> str:
    """Validate a role against the allow-list."""
    role = role.strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Role must be one of {sorted(ALLOWED_ROLES)}",
        )
    return role


def decode_base64_image(encoded: str) -> bytes:
    """Decode and bound-check a base64-encoded image payload."""
    if not encoded:
        raise HTTPException(status_code=400, detail="Image data missing")
    # Reject obvious oversize before decoding (base64 is ~4/3 the raw size)
    if len(encoded) > int(MAX_IMAGE_BYTES * 4 / 3) + 64:
        raise HTTPException(status_code=413, detail="Image payload too large")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid base64 image data")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds maximum size")
    if len(raw) < 100:
        raise HTTPException(status_code=400, detail="Image data is truncated")
    return raw


def decode_base64_audio(encoded: str) -> bytes:
    """Decode and bound-check a base64-encoded audio payload."""
    if not encoded:
        raise HTTPException(status_code=400, detail="Audio data missing")
    if len(encoded) > int(MAX_AUDIO_BYTES * 4 / 3) + 64:
        raise HTTPException(status_code=413, detail="Audio payload too large")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid base64 audio data")
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio exceeds maximum size")
    if len(raw) < 32:
        raise HTTPException(status_code=400, detail="Audio data is truncated")
    return raw


def validate_consent_text(text: str) -> str:
    """Trim and size-limit consent text."""
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Consent text cannot be empty")
    if len(text) > MAX_CONSENT_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Consent text exceeds maximum length of {MAX_CONSENT_LEN}",
        )
    return text


def validate_rule_config(config: dict, field_name: str = "config") -> dict:
    """Size-check a rule's trigger or action config blob.

    We deliberately don't enforce a schema here — each trigger/action type
    has its own config shape. But we do cap the total serialized size so a
    malicious or buggy client can't push multi-MB blobs into SQLite.
    """
    import json

    try:
        serialized = json.dumps(config)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} is not JSON-serializable",
        )
    if len(serialized) > MAX_RULE_CONFIG_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{field_name} exceeds maximum size of {MAX_RULE_CONFIG_BYTES} bytes",
        )
    return config
