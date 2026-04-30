"""Camera capture: pluggable source (local USB camera vs. network relay)."""

from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod

import cv2
import numpy as np
import structlog

from palantir.config import CameraConfig
from palantir.redis_client import Channels, Keys

logger = structlog.get_logger()


class CameraCapture(ABC):
    """Common interface for both local and relay-sourced camera frames."""

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def get_frame(self) -> tuple[np.ndarray | None, int]: ...

    @property
    @abstractmethod
    def fps(self) -> float: ...

    @property
    @abstractmethod
    def is_running(self) -> bool: ...

    @property
    @abstractmethod
    def frame_count(self) -> int: ...


class LocalCameraCapture(CameraCapture):
    """Captures frames from a USB camera in a background thread.

    Provides the latest frame via a thread-safe property.
    """

    def __init__(self, config: CameraConfig):
        self._config = config
        self._cap: cv2.VideoCapture | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._frame_count = 0
        self._fps_actual = 0.0

    def start(self) -> None:
        self._cap = cv2.VideoCapture(self._config.device)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._config.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._config.height)
        self._cap.set(cv2.CAP_PROP_FPS, self._config.fps)

        if not self._cap.isOpened():
            raise RuntimeError(f"Failed to open camera device {self._config.device}")

        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        logger.info(
            "camera_capture_started",
            source="local",
            device=self._config.device,
            resolution=f"{actual_w}x{actual_h}",
            target_fps=self._config.fps,
        )

    def _capture_loop(self) -> None:
        fps_counter = 0
        fps_timer = time.monotonic()

        while self._running and self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if not ret:
                logger.warning("camera_frame_read_failed")
                time.sleep(0.01)
                continue

            with self._lock:
                self._latest_frame = frame
                self._frame_count += 1

            fps_counter += 1
            elapsed = time.monotonic() - fps_timer
            if elapsed >= 1.0:
                self._fps_actual = fps_counter / elapsed
                fps_counter = 0
                fps_timer = time.monotonic()

            time.sleep(max(0, 1.0 / self._config.fps - 0.001))

    def get_frame(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            if self._latest_frame is None:
                return None, self._frame_count
            return self._latest_frame.copy(), self._frame_count

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._cap:
            self._cap.release()
            self._cap = None
        logger.info("camera_capture_stopped", source="local")

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def fps(self) -> float:
        return self._fps_actual

    @property
    def is_running(self) -> bool:
        return self._running


class RelayCameraCapture(CameraCapture):
    """Pulls JPEG frames from Redis `relay:video:frame` and decodes lazily.

    Frames arrive as JPEG bytes (the web service publishes them after
    the Pi sends a VIDEO_FRAME WebSocket message).  We hold the most
    recent JPEG bytes; `get_frame()` decodes on demand so the network
    listener never blocks on cv2.imdecode.

    Also keeps `Keys.LATEST_FRAME` warm so the brain's cloud-vision
    queries still work without a local camera.
    """

    def __init__(self, config: CameraConfig, redis):
        self._config = config
        self._redis = redis
        self._running = False
        self._lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._frame_count = 0
        self._fps_actual = 0.0
        self._fps_counter = 0
        self._fps_timer = time.monotonic()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._running = True
        # Run subscription as a background task on the running event loop
        # so the service's existing process_frame() poll loop continues to
        # work unchanged.  We schedule it lazily — the first start() call
        # may happen before the loop is running, in which case the service
        # is responsible for invoking _ensure_task() once its loop exists.
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._listen())
        except RuntimeError:
            self._task = None
        logger.info(
            "camera_capture_started",
            source="relay",
            channel=Channels.RELAY_VIDEO_FRAME,
        )

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(Channels.RELAY_VIDEO_FRAME)
        try:
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if not isinstance(data, (bytes, bytearray)):
                    continue
                with self._lock:
                    self._latest_jpeg = bytes(data)
                    self._frame_count += 1
                    self._fps_counter += 1
                    elapsed = time.monotonic() - self._fps_timer
                    if elapsed >= 1.0:
                        self._fps_actual = self._fps_counter / elapsed
                        self._fps_counter = 0
                        self._fps_timer = time.monotonic()
                # Keep LATEST_FRAME refreshed for the brain's cloud-vision path.
                try:
                    await self._redis.set(Keys.LATEST_FRAME, bytes(data), ex=30)
                except Exception:
                    logger.debug("latest_frame_redis_set_failed", exc_info=True)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(Channels.RELAY_VIDEO_FRAME)
                await pubsub.close()
            except Exception:
                logger.debug("relay_video_pubsub_close_failed", exc_info=True)

    def get_frame(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            jpeg = self._latest_jpeg
            count = self._frame_count
        if jpeg is None:
            return None, count
        # Decode JPEG -> BGR numpy array (matches LocalCameraCapture).
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return None, count
        return frame, count

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None
        logger.info("camera_capture_stopped", source="relay")

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def fps(self) -> float:
        return self._fps_actual

    @property
    def is_running(self) -> bool:
        return self._running


def create_camera_capture(
    camera_config: CameraConfig,
    *,
    relay_mode: bool = False,
    binary_redis=None,
) -> CameraCapture:
    """Factory: local USB camera vs. Pi relay over Redis.

    `binary_redis` is required when `relay_mode=True`.  See
    `palantir.audio.capture.create_audio_capture` for why.
    """
    if relay_mode:
        if binary_redis is None:
            raise ValueError(
                "RelayCameraCapture requires a binary Redis client "
                "(create_binary_redis(config))"
            )
        return RelayCameraCapture(camera_config, binary_redis)
    return LocalCameraCapture(camera_config)
