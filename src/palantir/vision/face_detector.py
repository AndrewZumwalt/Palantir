"""Face detection using InsightFace SCRFD.

Detects faces in camera frames and extracts bounding boxes.
The buffalo_s model bundle is optimized for edge/mobile deployment.
"""

from __future__ import annotations

import numpy as np
import structlog

from palantir.models import BoundingBox

logger = structlog.get_logger()

try:
    from insightface.app import FaceAnalysis

    _INSIGHTFACE_AVAILABLE = True
except ImportError:
    _INSIGHTFACE_AVAILABLE = False


class FaceDetection:
    """A detected face with bounding box and embedding."""

    def __init__(
        self,
        bbox: BoundingBox,
        embedding: np.ndarray | None = None,
        landmarks: np.ndarray | None = None,
        det_score: float = 0.0,
    ):
        self.bbox = bbox
        self.embedding = embedding
        self.landmarks = landmarks
        self.det_score = det_score


class FaceDetector:
    """Detects and analyzes faces using InsightFace.

    Uses the buffalo_s model bundle (SCRFD for detection, ArcFace for
    recognition) which is optimized for edge deployment.
    """

    def __init__(
        self,
        model_name: str = "buffalo_s",
        det_size: tuple[int, int] = (640, 640),
        det_thresh: float = 0.5,
    ):
        self._model: FaceAnalysis | None = None
        self._det_size = det_size

        if not _INSIGHTFACE_AVAILABLE:
            logger.warning("insightface_not_installed", hint='pip install -e ".[face]"')
            return

        try:
            self._model = FaceAnalysis(
                name=model_name,
                providers=["CPUExecutionProvider"],
            )
            self._model.prepare(ctx_id=0, det_size=det_size, det_thresh=det_thresh)
            logger.info("face_detector_loaded", model=model_name, det_size=det_size)
        except Exception:
            logger.exception("face_detector_init_failed")

    def detect(self, frame: np.ndarray) -> list[FaceDetection]:
        """Detect all faces in a BGR frame.

        Args:
            frame: BGR image as numpy array (from OpenCV).

        Returns:
            List of FaceDetection objects with bounding boxes and embeddings.
        """
        if not self._model:
            return []

        try:
            faces = self._model.get(frame)
        except Exception:
            logger.exception("face_detection_error")
            return []

        detections = []
        for face in faces:
            bbox_raw = face.bbox.astype(int)
            bbox = BoundingBox(
                x=int(bbox_raw[0]),
                y=int(bbox_raw[1]),
                width=int(bbox_raw[2] - bbox_raw[0]),
                height=int(bbox_raw[3] - bbox_raw[1]),
            )

            detection = FaceDetection(
                bbox=bbox,
                embedding=face.normed_embedding if hasattr(face, "normed_embedding") else None,
                landmarks=face.landmark_2d_106 if hasattr(face, "landmark_2d_106") else None,
                det_score=float(face.det_score) if hasattr(face, "det_score") else 0.0,
            )
            detections.append(detection)

        return detections

    @property
    def is_available(self) -> bool:
        return self._model is not None
