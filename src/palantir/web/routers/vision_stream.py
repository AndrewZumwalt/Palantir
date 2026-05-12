"""Live camera feed for the dashboard's troubleshooting view.

Streams whatever JPEG bytes are currently in `Keys.LATEST_FRAME` as a
multipart MJPEG response.  Browsers handle this natively in an `<img>`
tag, so the frontend Camera page is just an `<img>` plus an SVG overlay
fed by the existing VISION_FACES / VISION_OBJECTS / VISION_ENGAGEMENT
WebSocket bridge.

The vision service (or RelayCameraCapture in relay mode) keeps
`LATEST_FRAME` updated at ~15 Hz; we poll at the same cadence so the
stream is fluid without burning CPU on the busy-wait when the source is
idle.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import AsyncIterator

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from palantir.config import PalantirConfig
from palantir.redis_client import Keys

router = APIRouter(prefix="/api/vision", tags=["vision"])

# Multipart boundary -- arbitrary, must match the Content-Type header.
_BOUNDARY = b"--palantir_frame"
_MIME = f"multipart/x-mixed-replace; boundary={_BOUNDARY.decode().lstrip('-')}"

# Poll interval for the latest frame.  Matches vision service's 15 Hz
# write cadence; tighter doesn't help, looser causes visible stutter.
_POLL_SECONDS = 1.0 / 15
# How long to wait without a frame before we send a "still alive" tick
# so the browser doesn't decide the connection died and tear it down.
_KEEPALIVE_SECONDS = 5.0


def _verify_token(request: Request, token: str | None) -> None:
    """Auth check tailored for an MJPEG <img> tag.

    Browsers can't add headers to image requests, so the dashboard puts
    the token in the query string just like /ws does.  Constant-time
    compare to keep the auth flow uniform.
    """
    config: PalantirConfig = request.app.state.config
    if not config.auth_token:
        return  # auth disabled in dev
    candidate = token or ""
    # Also accept a normal Authorization header for tools like curl.
    if not candidate:
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer "):
            candidate = header[len("Bearer "):]
    if not secrets.compare_digest(candidate, config.auth_token):
        raise HTTPException(status_code=401, detail="invalid token")


async def _frame_iterator(redis: aioredis.Redis) -> AsyncIterator[bytes]:
    """Yield multipart JPEG chunks from `Keys.LATEST_FRAME`.

    `redis` MUST be a binary-mode client (created with
    `decode_responses=False`) -- a text-mode client would try to
    utf-8-decode the JPEG bytes and raise.
    """
    last_bytes: bytes | None = None
    last_yield = time.monotonic()
    while True:
        try:
            blob = await redis.get(Keys.LATEST_FRAME)
        except Exception:
            # Redis dropped; pause briefly and try again so the stream
            # recovers without the browser noticing.
            await asyncio.sleep(0.5)
            continue

        now = time.monotonic()
        if blob and blob != last_bytes:
            last_bytes = blob
            last_yield = now
            yield (
                _BOUNDARY
                + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(blob)).encode()
                + b"\r\n\r\n"
                + blob
                + b"\r\n"
            )
        elif now - last_yield >= _KEEPALIVE_SECONDS and last_bytes is not None:
            # Re-emit the most recent frame so the browser keeps the
            # connection considered "live" even if the camera is idle.
            last_yield = now
            yield (
                _BOUNDARY
                + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(last_bytes)).encode()
                + b"\r\n\r\n"
                + last_bytes
                + b"\r\n"
            )

        await asyncio.sleep(_POLL_SECONDS)


@router.get("/stream")
async def stream(request: Request, token: str | None = Query(default=None)):
    """MJPEG live view of whatever the vision pipeline is processing."""
    _verify_token(request, token)
    # Use the binary client so the JPEG payload comes back unmodified.
    redis: aioredis.Redis = request.app.state.binary_redis
    return StreamingResponse(
        _frame_iterator(redis),
        media_type=_MIME,
        headers={
            # MJPEG doesn't play well with intermediaries that buffer.
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Connection": "close",
            "X-Accel-Buffering": "no",
        },
    )
