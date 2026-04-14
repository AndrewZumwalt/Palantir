"""On-device object detection using YOLO via Ultralytics.

Runs YOLO at low frequency (every 30th frame) to maintain a cached
inventory of visible objects. Uses NCNN export for optimized Pi 5 inference.
"""

from __future__ import annotations

import time

import numpy as np
import structlog

from palintir.models import BoundingBox, DetectedObject

logger = structlog.get_logger()

try:
    from ultralytics import YOLO

    _YOLO_AVAILABLE = True
except ImportError:
    _YOLO_AVAILABLE = False


class ObjectDetector:
    """Detects objects in camera frames using YOLO.

    Maintains a cached list of recently detected objects with
    approximate spatial descriptions.
    """

    def __init__(
        self,
        model_path: str = "yolo11n.pt",
        confidence_threshold: float = 0.4,
        frame_width: int = 640,
        frame_height: int = 480,
    ):
        self._model: YOLO | None = None
        self._conf_threshold = confidence_threshold
        self._frame_width = frame_width
        self._frame_height = frame_height

        if not _YOLO_AVAILABLE:
            logger.warning("ultralytics_not_installed", hint='pip install -e ".[objects]"')
            return

        try:
            self._model = YOLO(model_path)
            logger.info("object_detector_loaded", model=model_path)
        except Exception:
            logger.exception("object_detector_init_failed")

    def detect(self, frame: np.ndarray) -> list[DetectedObject]:
        """Detect objects in a BGR frame.

        Args:
            frame: BGR image from OpenCV.

        Returns:
            List of DetectedObject with labels, bounding boxes, and
            natural language location descriptions.
        """
        if not self._model:
            return []

        try:
            results = self._model(frame, verbose=False, conf=self._conf_threshold)
        except Exception:
            logger.exception("object_detection_error")
            return []

        objects = []
        for result in results:
            if result.boxes is None:
                continue

            for box in result.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                label = result.names.get(cls_id, f"class_{cls_id}")

                xyxy = box.xyxy[0].cpu().numpy().astype(int)
                bbox = BoundingBox(
                    x=int(xyxy[0]),
                    y=int(xyxy[1]),
                    width=int(xyxy[2] - xyxy[0]),
                    height=int(xyxy[3] - xyxy[1]),
                )

                # Generate spatial description
                location = self._describe_location(bbox)

                objects.append(DetectedObject(
                    label=label,
                    confidence=round(conf, 3),
                    bbox=bbox,
                    location_description=location,
                ))

        return objects

    def _describe_location(self, bbox: BoundingBox) -> str:
        """Generate a natural language description of where an object is.

        Divides the frame into a 3x3 grid and describes position.
        """
        center_x = bbox.x + bbox.width / 2
        center_y = bbox.y + bbox.height / 2

        # Horizontal position
        x_ratio = center_x / self._frame_width
        if x_ratio < 0.33:
            h_pos = "on the left"
        elif x_ratio > 0.66:
            h_pos = "on the right"
        else:
            h_pos = "in the center"

        # Vertical position (camera perspective: top = far, bottom = near)
        y_ratio = center_y / self._frame_height
        if y_ratio < 0.33:
            v_pos = "in the back"
        elif y_ratio > 0.66:
            v_pos = "in the front"
        else:
            v_pos = ""

        # Relative size gives rough distance
        area_ratio = (bbox.width * bbox.height) / (self._frame_width * self._frame_height)
        if area_ratio > 0.15:
            size_hint = "nearby"
        elif area_ratio < 0.02:
            size_hint = "far away"
        else:
            size_hint = ""

        parts = [p for p in [v_pos, h_pos, size_hint] if p]
        return ", ".join(parts) if parts else "visible in frame"

    @property
    def is_available(self) -> bool:
        return self._model is not None
