"""Speaker identification using SpeechBrain ECAPA-TDNN.

Extracts voice embeddings from audio and matches them against
enrolled speaker profiles for identification.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import structlog

logger = structlog.get_logger()

try:
    import torch  # noqa: F401  # required runtime dep for speechbrain
    import torchaudio  # noqa: F401  # required runtime dep for speechbrain
    from speechbrain.inference.speaker import EncoderClassifier

    _SPEECHBRAIN_AVAILABLE = True
except ImportError:
    _SPEECHBRAIN_AVAILABLE = False

VOICE_EMBEDDING_DIM = 192  # ECAPA-TDNN output dimension


@dataclass
class SpeakerMatch:
    """Result of matching a voice against enrolled profiles."""
    person_id: str | None = None
    name: str | None = None
    confidence: float = 0.0
    matched: bool = False


def voice_embedding_to_blob(embedding: np.ndarray) -> bytes:
    """Serialize a voice embedding to bytes for SQLite."""
    return embedding.astype(np.float32).tobytes()


def blob_to_voice_embedding(blob: bytes) -> np.ndarray:
    """Deserialize bytes from SQLite to a voice embedding."""
    return np.frombuffer(blob, dtype=np.float32)


class SpeakerIdentifier:
    """Identifies speakers by voice using ECAPA-TDNN embeddings.

    Uses cosine similarity to match extracted voice embeddings against
    enrolled speaker profiles stored in the database.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        match_threshold: float = 0.65,
    ):
        self._db = db
        self._threshold = match_threshold
        self._model: EncoderClassifier | None = None

        # Cached enrolled profiles
        self._profiles: list[dict] = []
        self._embeddings: np.ndarray | None = None

        if not _SPEECHBRAIN_AVAILABLE:
            logger.warning("speechbrain_not_installed", hint='pip install -e ".[speaker]"')
            return

        try:
            self._model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                run_opts={"device": "cpu"},
            )
            logger.info("speaker_id_model_loaded")
        except Exception:
            logger.exception("speaker_id_init_failed")

        self._load_profiles()

    def _load_profiles(self) -> None:
        """Load enrolled voice profiles from the database."""
        rows = self._db.execute(
            "SELECT id, name, role, voice_embedding FROM persons "
            "WHERE active = 1 AND voice_embedding IS NOT NULL"
        ).fetchall()

        self._profiles = []
        embeddings = []

        for row in rows:
            embedding = blob_to_voice_embedding(row["voice_embedding"])
            self._profiles.append({
                "person_id": row["id"],
                "name": row["name"],
                "role": row["role"],
            })
            embeddings.append(embedding)

        if embeddings:
            self._embeddings = np.stack(embeddings)
        else:
            self._embeddings = None

        logger.info("voice_profiles_loaded", count=len(self._profiles))

    def reload_profiles(self) -> None:
        """Reload profiles after enrollment changes."""
        self._load_profiles()

    def extract_embedding(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray | None:
        """Extract a voice embedding from an audio clip.

        Args:
            audio: Audio samples as int16 numpy array.
            sample_rate: Sample rate (should be 16000).

        Returns:
            192-D float32 numpy embedding, or None on failure.
        """
        if not self._model:
            return None

        try:
            # Convert int16 to float32 tensor
            audio_float = audio.astype(np.float32) / 32768.0
            waveform = torch.from_numpy(audio_float).unsqueeze(0)  # Shape: (1, samples)

            # Extract embedding
            embedding = self._model.encode_batch(waveform)
            # Shape: (1, 1, 192) -> (192,)
            emb_np = embedding.squeeze().cpu().numpy()

            return emb_np

        except Exception:
            logger.exception("speaker_embedding_error")
            return None

    def identify(self, embedding: np.ndarray) -> SpeakerMatch:
        """Match a voice embedding against enrolled profiles.

        Args:
            embedding: 192-D voice embedding.

        Returns:
            SpeakerMatch with best match or unmatched result.
        """
        if self._embeddings is None or len(self._profiles) == 0:
            return SpeakerMatch()

        # Cosine similarity
        query = embedding / (np.linalg.norm(embedding) + 1e-10)
        db_normed = self._embeddings / (
            np.linalg.norm(self._embeddings, axis=1, keepdims=True) + 1e-10
        )

        similarities = db_normed @ query
        best_idx = int(np.argmax(similarities))
        best_score = float(similarities[best_idx])

        if best_score >= self._threshold:
            profile = self._profiles[best_idx]
            return SpeakerMatch(
                person_id=profile["person_id"],
                name=profile["name"],
                confidence=round(best_score, 4),
                matched=True,
            )

        return SpeakerMatch(confidence=round(best_score, 4))

    def enroll_voice(
        self,
        person_id: str,
        embeddings: list[np.ndarray],
    ) -> np.ndarray:
        """Compute and store the mean voice embedding for a person.

        Args:
            person_id: The person's database ID.
            embeddings: List of voice embeddings from enrollment utterances.

        Returns:
            The mean embedding that was stored.
        """
        mean_embedding = np.mean(embeddings, axis=0).astype(np.float32)
        mean_embedding = mean_embedding / (np.linalg.norm(mean_embedding) + 1e-10)

        blob = voice_embedding_to_blob(mean_embedding)
        self._db.execute(
            "UPDATE persons SET voice_embedding = ? WHERE id = ?",
            (blob, person_id),
        )
        self._db.commit()
        self._load_profiles()

        logger.info("voice_enrolled", person_id=person_id, num_samples=len(embeddings))
        return mean_embedding

    @property
    def is_available(self) -> bool:
        return self._model is not None

    @property
    def enrolled_count(self) -> int:
        return len(self._profiles)
