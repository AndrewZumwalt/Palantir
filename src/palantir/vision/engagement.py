"""Student engagement classification using YOLO pose estimation + heuristics.

Classifies each visible person's engagement state based on body pose
keypoints and object detection context. Uses temporal smoothing to
prevent flickering between states.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import structlog

from palantir.models import BoundingBox, EngagementState, PersonEngagement

logger = structlog.get_logger()

try:
    from ultralytics import YOLO

    _YOLO_POSE_AVAILABLE = True
except ImportError:
    _YOLO_POSE_AVAILABLE = False


# COCO keypoint indices
NOSE = 0
LEFT_EYE = 1
RIGHT_EYE = 2
LEFT_EAR = 3
RIGHT_EAR = 4
LEFT_SHOULDER = 5
RIGHT_SHOULDER = 6
LEFT_ELBOW = 7
RIGHT_ELBOW = 8
LEFT_WRIST = 9
RIGHT_WRIST = 10
LEFT_HIP = 11
RIGHT_HIP = 12


@dataclass
class PersonState:
    """Tracks engagement state for a single person over time."""
    person_id: str
    states: deque = field(default_factory=lambda: deque(maxlen=30))
    last_movement_time: float = field(default_factory=time.monotonic)
    last_head_y: float | None = None


class EngagementClassifier:
    """Classifies engagement state per person using pose estimation.

    States:
    - WORKING: head oriented toward desk, hands at desk level
    - COLLABORATING: facing peers (multiple faces oriented toward each other)
    - PHONE: characteristic phone-use posture (head down, hands near face)
    - SLEEPING: head on desk, no movement for 30+ seconds
    - DISENGAGED: looking away from front of room
    """

    def __init__(
        self,
        smoothing_window: int = 30,
        phone_threshold: float = 0.6,
        sleep_stillness_seconds: float = 30.0,
        frame_height: int = 480,
    ):
        self._smoothing_window = smoothing_window
        self._phone_threshold = phone_threshold
        self._sleep_stillness = sleep_stillness_seconds
        self._frame_height = frame_height
        self._model: YOLO | None = None

        # Per-person state tracking: person_id -> PersonState
        self._person_states: dict[str, PersonState] = {}

        if not _YOLO_POSE_AVAILABLE:
            logger.warning("yolo_pose_not_available", hint='pip install -e ".[objects]"')
            return

        try:
            self._model = YOLO("yolo11n-pose.pt")
            logger.info("engagement_classifier_loaded")
        except Exception:
            logger.exception("engagement_classifier_init_failed")

    def classify_frame(
        self,
        frame: np.ndarray,
        known_persons: dict[str, BoundingBox] | None = None,
    ) -> list[PersonEngagement]:
        """Classify engagement for all visible people in a frame.

        Args:
            frame: BGR image from OpenCV.
            known_persons: Dict mapping person_id to their face bounding box
                          (from face recognition). Used to associate poses with
                          identified people.

        Returns:
            List of PersonEngagement results with smoothed states.
        """
        if not self._model:
            return []

        try:
            results = self._model(frame, verbose=False)
        except Exception:
            logger.exception("pose_estimation_error")
            return []

        engagements = []
        person_bboxes = known_persons or {}

        for result in results:
            if result.keypoints is None:
                continue

            for i, kpts in enumerate(result.keypoints.data):
                # kpts shape: (17, 3) — x, y, confidence per keypoint
                kpts_np = kpts.cpu().numpy()

                # Match this pose to a known person by bbox overlap
                person_id = self._match_to_person(result, i, person_bboxes)
                if not person_id:
                    person_id = f"unknown_{i}"

                # Classify raw state
                raw_state = self._classify_pose(kpts_np, person_id)

                # Get or create person state tracker
                if person_id not in self._person_states:
                    self._person_states[person_id] = PersonState(person_id=person_id)

                pstate = self._person_states[person_id]
                pstate.states.append(raw_state)

                # Smoothed state = majority vote over window
                smoothed = self._smooth_state(pstate)

                engagements.append(PersonEngagement(
                    person_id=person_id,
                    state=smoothed,
                    confidence=self._state_confidence(pstate, smoothed),
                ))

        return engagements

    def _classify_pose(self, keypoints: np.ndarray, person_id: str) -> EngagementState:
        """Classify a single person's engagement from their pose keypoints."""
        nose = keypoints[NOSE]
        left_wrist = keypoints[LEFT_WRIST]
        right_wrist = keypoints[RIGHT_WRIST]
        left_shoulder = keypoints[LEFT_SHOULDER]
        right_shoulder = keypoints[RIGHT_SHOULDER]

        nose_conf = nose[2]
        if nose_conf < 0.3:
            return EngagementState.UNKNOWN

        nose_y = nose[1]
        shoulders_visible = left_shoulder[2] > 0.3 and right_shoulder[2] > 0.3
        shoulder_y = (
            (left_shoulder[1] + right_shoulder[1]) / 2
            if shoulders_visible
            else None
        )

        # --- SLEEPING: head at or below shoulder level, no movement ---
        pstate = self._person_states.get(person_id)
        if pstate and shoulder_y:
            head_near_desk = nose_y > shoulder_y + 30  # Head dropped significantly
            if head_near_desk:
                if pstate.last_head_y and abs(nose_y - pstate.last_head_y) < 5:
                    # Head hasn't moved
                    if time.monotonic() - pstate.last_movement_time > self._sleep_stillness:
                        return EngagementState.SLEEPING
                else:
                    pstate.last_movement_time = time.monotonic()
            pstate.last_head_y = nose_y

        # --- PHONE: hands near face, head tilted down ---
        wrist_near_face = False
        for wrist in [left_wrist, right_wrist]:
            if wrist[2] > 0.3 and nose_conf > 0.3:
                dist = np.sqrt((wrist[0] - nose[0]) ** 2 + (wrist[1] - nose[1]) ** 2)
                if dist < 80:  # Wrist close to face
                    wrist_near_face = True
                    break

        head_tilted_down = shoulder_y and nose_y > shoulder_y - 10
        if wrist_near_face and head_tilted_down:
            return EngagementState.PHONE

        # --- WORKING: head oriented downward (toward desk), hands at desk level ---
        if shoulder_y and nose_y > shoulder_y - 30:
            wrists_low = True
            for wrist in [left_wrist, right_wrist]:
                if wrist[2] > 0.3 and wrist[1] < shoulder_y:
                    wrists_low = False
            if wrists_low:
                return EngagementState.WORKING

        # --- DISENGAGED: head oriented far to the side ---
        left_ear = keypoints[LEFT_EAR]
        right_ear = keypoints[RIGHT_EAR]
        if left_ear[2] > 0.3 and right_ear[2] < 0.2:
            return EngagementState.DISENGAGED  # Looking hard right
        if right_ear[2] > 0.3 and left_ear[2] < 0.2:
            return EngagementState.DISENGAGED  # Looking hard left

        # Default: WORKING (benefit of the doubt)
        return EngagementState.WORKING

    def _match_to_person(
        self, result, pose_idx: int, person_bboxes: dict[str, BoundingBox]
    ) -> str | None:
        """Match a detected pose to a known person by bounding box overlap."""
        if not person_bboxes or result.boxes is None:
            return None

        if pose_idx >= len(result.boxes):
            return None

        box = result.boxes[pose_idx]
        xyxy = box.xyxy[0].cpu().numpy().astype(int)
        pose_cx = (xyxy[0] + xyxy[2]) / 2
        pose_cy = (xyxy[1] + xyxy[3]) / 2

        best_id = None
        best_dist = float("inf")

        for person_id, bbox in person_bboxes.items():
            face_cx = bbox.x + bbox.width / 2
            face_cy = bbox.y + bbox.height / 2
            dist = np.sqrt((pose_cx - face_cx) ** 2 + (pose_cy - face_cy) ** 2)
            if dist < best_dist and dist < 150:
                best_dist = dist
                best_id = person_id

        return best_id

    def _smooth_state(self, pstate: PersonState) -> EngagementState:
        """Return the majority engagement state over the smoothing window."""
        if not pstate.states:
            return EngagementState.UNKNOWN

        counts: dict[EngagementState, int] = {}
        for s in pstate.states:
            counts[s] = counts.get(s, 0) + 1

        return max(counts, key=counts.get)

    def _state_confidence(self, pstate: PersonState, state: EngagementState) -> float:
        """Confidence = fraction of window matching the smoothed state."""
        if not pstate.states:
            return 0.0
        count = sum(1 for s in pstate.states if s == state)
        return round(count / len(pstate.states), 3)

    @property
    def is_available(self) -> bool:
        return self._model is not None
