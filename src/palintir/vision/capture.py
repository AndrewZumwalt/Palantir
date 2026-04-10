"""Camera capture from USB camera using OpenCV."""

from __future__ import annotations

import asyncio
import threading
import time

import cv2
import numpy as np
import structlog

from palintir.config import CameraConfig

logger = structlog.get_logger()


class CameraCapture:
    """Captures frames from a USB camera in a background thread.

    Provides the latest frame via a thread-safe property. Frames are
    captured continuously and made available to vision processing services.
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
        """Open the camera and start the capture thread."""
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
            device=self._config.device,
            resolution=f"{actual_w}x{actual_h}",
            target_fps=self._config.fps,
        )

    def _capture_loop(self) -> None:
        """Background thread that continuously reads frames."""
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

            # Throttle to target FPS
            time.sleep(max(0, 1.0 / self._config.fps - 0.001))

    def get_frame(self) -> tuple[np.ndarray | None, int]:
        """Get the latest captured frame and its frame number.

        Returns:
            Tuple of (frame as BGR numpy array or None, frame count).
        """
        with self._lock:
            if self._latest_frame is None:
                return None, self._frame_count
            return self._latest_frame.copy(), self._frame_count

    def stop(self) -> None:
        """Stop the capture thread and release the camera."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._cap:
            self._cap.release()
            self._cap = None
        logger.info("camera_capture_stopped")

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def fps(self) -> float:
        return self._fps_actual

    @property
    def is_running(self) -> bool:
        return self._running
