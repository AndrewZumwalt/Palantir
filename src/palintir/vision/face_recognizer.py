"""Face recognition: matches detected face embeddings against enrolled profiles.

Manages the enrolled face database and performs cosine similarity
matching to identify known people.
"""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass

import numpy as np
import structlog

logger = structlog.get_logger()

EMBEDDING_DIM = 512  # ArcFace embedding dimension


@dataclass
class RecognitionResult:
    """Result of matching a face against the enrolled database."""

    person_id: str | None = None
    name: str | None = None
    role: str | None = None
    confidence: float = 0.0
    matched: bool = False


def embedding_to_blob(embedding: np.ndarray) -> bytes:
    """Serialize a float32 numpy embedding to bytes for SQLite storage."""
    return embedding.astype(np.float32).tobytes()


def blob_to_embedding(blob: bytes) -> np.ndarray:
    """Deserialize bytes from SQLite back to a float32 numpy embedding."""
    return np.frombuffer(blob, dtype=np.float32)


class FaceRecognizer:
    """Matches face embeddings against enrolled profiles using cosine similarity."""

    def __init__(self, db: sqlite3.Connection, match_threshold: float = 0.4):
        self._db = db
        self._threshold = match_threshold

        # Cache enrolled embeddings in memory for fast matching
        self._profiles: list[dict] = []
        self._embeddings: np.ndarray | None = None
        self._load_profiles()

    def _load_profiles(self) -> None:
        """Load all enrolled face profiles from the database into memory."""
        rows = self._db.execute(
            "SELECT id, name, role, face_embedding FROM persons "
            "WHERE active = 1 AND face_embedding IS NOT NULL"
        ).fetchall()

        self._profiles = []
        embeddings = []

        for row in rows:
            embedding = blob_to_embedding(row["face_embedding"])
            self._profiles.append({
                "person_id": row["id"],
                "name": row["name"],
                "role": row["role"],
            })
            embeddings.append(embedding)

        if embeddings:
            self._embeddings = np.stack(embeddings)  # Shape: (N, 512)
        else:
            self._embeddings = None

        logger.info("face_profiles_loaded", count=len(self._profiles))

    def reload_profiles(self) -> None:
        """Reload profiles from the database (call after enrollment changes)."""
        self._load_profiles()

    def recognize(self, embedding: np.ndarray) -> RecognitionResult:
        """Match a face embedding against all enrolled profiles.

        Args:
            embedding: 512-D face embedding from InsightFace.

        Returns:
            RecognitionResult with the best match, or unmatched result.
        """
        if self._embeddings is None or len(self._profiles) == 0:
            return RecognitionResult()

        # Cosine similarity: dot product of normalized embeddings
        # InsightFace returns normed embeddings, but normalize again to be safe
        query = embedding / (np.linalg.norm(embedding) + 1e-10)
        db_normed = self._embeddings / (
            np.linalg.norm(self._embeddings, axis=1, keepdims=True) + 1e-10
        )

        similarities = db_normed @ query  # Shape: (N,)
        best_idx = int(np.argmax(similarities))
        best_score = float(similarities[best_idx])

        if best_score >= self._threshold:
            profile = self._profiles[best_idx]
            return RecognitionResult(
                person_id=profile["person_id"],
                name=profile["name"],
                role=profile["role"],
                confidence=round(best_score, 4),
                matched=True,
            )

        return RecognitionResult(confidence=round(best_score, 4))

    def enroll_face(
        self,
        person_id: str,
        embeddings: list[np.ndarray],
    ) -> np.ndarray:
        """Compute and store the mean face embedding for a person.

        Args:
            person_id: The person's database ID.
            embeddings: List of face embeddings from enrollment photos.

        Returns:
            The mean embedding that was stored.
        """
        mean_embedding = np.mean(embeddings, axis=0).astype(np.float32)
        # Normalize the mean embedding
        mean_embedding = mean_embedding / (np.linalg.norm(mean_embedding) + 1e-10)

        blob = embedding_to_blob(mean_embedding)
        self._db.execute(
            "UPDATE persons SET face_embedding = ? WHERE id = ?",
            (blob, person_id),
        )
        self._db.commit()

        # Reload cached profiles
        self._load_profiles()

        logger.info(
            "face_enrolled",
            person_id=person_id,
            num_samples=len(embeddings),
        )
        return mean_embedding

    @property
    def enrolled_count(self) -> int:
        return len(self._profiles)
