"""Enrollment workflow API endpoints.

Handles the registration of new people: creating their profile,
capturing face photos, extracting embeddings, and storing them.
"""

from __future__ import annotations

import base64
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from palintir.config import PalintirConfig
from palintir.vision.face_detector import FaceDetector
from palintir.vision.face_recognizer import FaceRecognizer, embedding_to_blob
from palintir.web.dependencies import get_config, get_db, verify_auth

router = APIRouter(prefix="/api/enrollment", tags=["enrollment"], dependencies=[Depends(verify_auth)])

# Lazy-initialized shared instances
_face_detector: FaceDetector | None = None
_face_recognizer: FaceRecognizer | None = None


def _get_face_detector() -> FaceDetector:
    global _face_detector
    if _face_detector is None:
        _face_detector = FaceDetector()
    return _face_detector


def _get_face_recognizer(db: sqlite3.Connection, config: PalintirConfig) -> FaceRecognizer:
    global _face_recognizer
    if _face_recognizer is None:
        _face_recognizer = FaceRecognizer(db, match_threshold=config.identity.face_match_threshold)
    return _face_recognizer


class CreatePersonRequest(BaseModel):
    name: str
    role: str = "student"  # student, teacher, admin


class FacePhotoRequest(BaseModel):
    """Base64-encoded JPEG image from the camera."""
    image_base64: str


class ConsentRequest(BaseModel):
    consent_text: str


class EnrollmentStatus(BaseModel):
    person_id: str
    name: str
    role: str
    face_samples: int
    required_samples: int
    complete: bool


@router.get("/persons")
async def list_persons(db: sqlite3.Connection = Depends(get_db)):
    """List all enrolled persons."""
    rows = db.execute(
        "SELECT id, name, role, enrolled_at, "
        "CASE WHEN face_embedding IS NOT NULL THEN 1 ELSE 0 END as has_face, "
        "CASE WHEN voice_embedding IS NOT NULL THEN 1 ELSE 0 END as has_voice "
        "FROM persons WHERE active = 1 ORDER BY name"
    ).fetchall()
    return {"persons": [dict(row) for row in rows]}


@router.post("/persons")
async def create_person(
    req: CreatePersonRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """Create a new person profile (step 1 of enrollment)."""
    person_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO persons (id, name, role) VALUES (?, ?, ?)",
        (person_id, req.name, req.role),
    )
    db.commit()
    return {"person_id": person_id, "name": req.name, "role": req.role}


@router.post("/persons/{person_id}/consent")
async def record_consent(
    person_id: str,
    req: ConsentRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """Record consent for biometric data collection (step 2)."""
    row = db.execute("SELECT id FROM persons WHERE id = ?", (person_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    db.execute(
        "UPDATE persons SET consent_given_at = ?, consent_text = ? WHERE id = ?",
        (datetime.now().isoformat(), req.consent_text, person_id),
    )
    db.commit()
    return {"status": "consent_recorded"}


@router.post("/persons/{person_id}/face")
async def capture_face(
    person_id: str,
    req: FacePhotoRequest,
    db: sqlite3.Connection = Depends(get_db),
    config: PalintirConfig = Depends(get_config),
):
    """Submit a face photo for enrollment (step 3, repeat for required samples).

    Accepts a base64-encoded JPEG image, detects the face, extracts the
    embedding, and saves it. Returns the detection status.
    """
    row = db.execute("SELECT id, name FROM persons WHERE id = ?", (person_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    # Decode base64 image
    try:
        image_bytes = base64.b64decode(req.image_base64)
        np_arr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Failed to decode image")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data")

    # Detect face
    detector = _get_face_detector()
    if not detector.is_available:
        raise HTTPException(status_code=503, detail="Face detector not available")

    detections = detector.detect(frame)
    if not detections:
        raise HTTPException(status_code=422, detail="No face detected in image")
    if len(detections) > 1:
        raise HTTPException(status_code=422, detail="Multiple faces detected. Please ensure only one person is in frame.")

    face = detections[0]
    if face.embedding is None:
        raise HTTPException(status_code=422, detail="Could not extract face embedding")

    # Save enrollment photo
    enrollment_dir = Path(config.enrollment_path) / person_id
    enrollment_dir.mkdir(parents=True, exist_ok=True)
    existing = list(enrollment_dir.glob("face_*.jpg"))
    photo_idx = len(existing)
    photo_path = enrollment_dir / f"face_{photo_idx:03d}.jpg"
    cv2.imwrite(str(photo_path), frame)

    # Save embedding temporarily (we'll compute mean after all samples)
    emb_path = enrollment_dir / f"emb_{photo_idx:03d}.npy"
    np.save(str(emb_path), face.embedding)

    total_samples = photo_idx + 1
    required = config.identity.enrollment_face_samples
    complete = total_samples >= required

    # If we have enough samples, compute mean embedding and store
    if complete:
        embeddings = []
        for emb_file in sorted(enrollment_dir.glob("emb_*.npy")):
            embeddings.append(np.load(str(emb_file)))

        recognizer = _get_face_recognizer(db, config)
        recognizer.enroll_face(person_id, embeddings)

    return EnrollmentStatus(
        person_id=person_id,
        name=row["name"],
        role="",
        face_samples=total_samples,
        required_samples=required,
        complete=complete,
    )


class VoiceSampleRequest(BaseModel):
    """Base64-encoded WAV audio from the browser microphone."""
    audio_base64: str
    sample_rate: int = 16000


class VoiceEnrollmentStatus(BaseModel):
    person_id: str
    voice_samples: int
    required_samples: int
    complete: bool


# Lazy speaker identifier
_speaker_identifier = None


def _get_speaker_identifier(db: sqlite3.Connection, config: PalintirConfig):
    global _speaker_identifier
    if _speaker_identifier is None:
        try:
            from palintir.audio.speaker_id import SpeakerIdentifier
            _speaker_identifier = SpeakerIdentifier(
                db, match_threshold=config.identity.voice_match_threshold
            )
        except ImportError:
            pass
    return _speaker_identifier


@router.post("/persons/{person_id}/voice")
async def capture_voice(
    person_id: str,
    req: VoiceSampleRequest,
    db: sqlite3.Connection = Depends(get_db),
    config: PalintirConfig = Depends(get_config),
):
    """Submit a voice sample for enrollment.

    Accepts base64-encoded audio, extracts a speaker embedding,
    and stores it. After enough samples, computes the mean embedding.
    """
    row = db.execute("SELECT id, name FROM persons WHERE id = ?", (person_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    identifier = _get_speaker_identifier(db, config)
    if not identifier or not identifier.is_available:
        raise HTTPException(status_code=503, detail="Speaker ID model not available")

    # Decode base64 audio
    try:
        audio_bytes = base64.b64decode(req.audio_base64)
        audio = np.frombuffer(audio_bytes, dtype=np.int16)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid audio data")

    if len(audio) < req.sample_rate:  # Less than 1 second
        raise HTTPException(status_code=422, detail="Audio too short. Please speak for at least 2 seconds.")

    # Extract voice embedding
    embedding = identifier.extract_embedding(audio, req.sample_rate)
    if embedding is None:
        raise HTTPException(status_code=422, detail="Could not extract voice embedding")

    # Save embedding
    enrollment_dir = Path(config.enrollment_path) / person_id
    enrollment_dir.mkdir(parents=True, exist_ok=True)
    existing = list(enrollment_dir.glob("voice_emb_*.npy"))
    idx = len(existing)
    np.save(str(enrollment_dir / f"voice_emb_{idx:03d}.npy"), embedding)

    total_samples = idx + 1
    required = config.identity.enrollment_voice_samples
    complete = total_samples >= required

    # If enough samples, compute mean and store
    if complete:
        embeddings = []
        for emb_file in sorted(enrollment_dir.glob("voice_emb_*.npy")):
            embeddings.append(np.load(str(emb_file)))
        identifier.enroll_voice(person_id, embeddings)

    return VoiceEnrollmentStatus(
        person_id=person_id,
        voice_samples=total_samples,
        required_samples=required,
        complete=complete,
    )


@router.get("/persons/{person_id}/status")
async def enrollment_status(
    person_id: str,
    db: sqlite3.Connection = Depends(get_db),
    config: PalintirConfig = Depends(get_config),
):
    """Get enrollment status for a person."""
    row = db.execute(
        "SELECT id, name, role, "
        "face_embedding IS NOT NULL as has_face, "
        "voice_embedding IS NOT NULL as has_voice "
        "FROM persons WHERE id = ?",
        (person_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    enrollment_dir = Path(config.enrollment_path) / person_id
    face_samples = len(list(enrollment_dir.glob("face_*.jpg"))) if enrollment_dir.exists() else 0
    voice_samples = len(list(enrollment_dir.glob("voice_emb_*.npy"))) if enrollment_dir.exists() else 0

    return {
        "person_id": person_id,
        "name": row["name"],
        "role": row["role"],
        "face_samples": face_samples,
        "face_required": config.identity.enrollment_face_samples,
        "face_complete": bool(row["has_face"]),
        "voice_samples": voice_samples,
        "voice_required": config.identity.enrollment_voice_samples,
        "voice_complete": bool(row["has_voice"]),
    }


@router.delete("/persons/{person_id}")
async def unenroll_person(
    person_id: str,
    db: sqlite3.Connection = Depends(get_db),
    config: PalintirConfig = Depends(get_config),
):
    """Remove a person and all their data."""
    import shutil

    row = db.execute("SELECT id FROM persons WHERE id = ?", (person_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")

    # Delete enrollment files
    enrollment_dir = Path(config.enrollment_path) / person_id
    if enrollment_dir.exists():
        shutil.rmtree(enrollment_dir)

    if config.privacy.auto_delete_on_unenroll:
        db.execute("DELETE FROM conversations WHERE person_id = ?", (person_id,))
        db.execute("DELETE FROM memory WHERE person_id = ?", (person_id,))
        db.execute("DELETE FROM engagement_samples WHERE person_id = ?", (person_id,))
        db.execute("DELETE FROM events WHERE person_id = ?", (person_id,))

    db.execute("DELETE FROM attendance_records WHERE person_id = ?", (person_id,))
    db.execute("DELETE FROM persons WHERE id = ?", (person_id,))
    db.commit()

    # Reload recognizer cache
    global _face_recognizer
    _face_recognizer = None

    return {"status": "deleted", "person_id": person_id}
