"""Shared Pydantic models for inter-service communication via Redis."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# --- Identity ---

class PersonRole(str, Enum):
    TEACHER = "teacher"
    STUDENT = "student"
    ADMIN = "admin"
    GUEST = "guest"


class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int


class DetectedFace(BaseModel):
    person_id: str | None = None  # None if unrecognized
    name: str | None = None
    confidence: float = 0.0
    bbox: BoundingBox
    timestamp: datetime = Field(default_factory=datetime.now)


class VisiblePerson(BaseModel):
    person_id: str
    name: str
    role: PersonRole
    bbox: BoundingBox
    last_seen: datetime = Field(default_factory=datetime.now)


# --- Audio ---

class WakeWordEvent(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    confidence: float


class Utterance(BaseModel):
    text: str
    speaker_embedding: list[float] | None = None
    duration_seconds: float
    timestamp: datetime = Field(default_factory=datetime.now)


class SpeakerIdentification(BaseModel):
    person_id: str | None = None
    name: str | None = None
    confidence: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


# --- Vision ---

class DetectedObject(BaseModel):
    label: str
    confidence: float
    bbox: BoundingBox
    location_description: str | None = None  # e.g., "on the desk near the window"


class VisionFrame(BaseModel):
    faces: list[DetectedFace] = Field(default_factory=list)
    objects: list[DetectedObject] = Field(default_factory=list)
    frame_number: int = 0
    timestamp: datetime = Field(default_factory=datetime.now)


# --- Engagement ---

class EngagementState(str, Enum):
    WORKING = "working"
    COLLABORATING = "collaborating"
    PHONE = "phone"
    SLEEPING = "sleeping"
    DISENGAGED = "disengaged"
    UNKNOWN = "unknown"


class PersonEngagement(BaseModel):
    person_id: str
    name: str | None = None
    state: EngagementState
    confidence: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


# --- Brain ---

class AssistantResponse(BaseModel):
    text: str
    target_person_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class AutomationTrigger(BaseModel):
    rule_id: str
    person_id: str | None = None
    action: str
    params: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


# --- System ---

class PrivacyModeEvent(BaseModel):
    enabled: bool
    source: str  # "gpio", "voice", "web"
    timestamp: datetime = Field(default_factory=datetime.now)


class ServiceStatus(BaseModel):
    name: str
    healthy: bool
    uptime_seconds: float = 0.0
    details: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


# --- Events ---

class EventType(str, Enum):
    PERSON_ENTERED = "person_entered"
    PERSON_EXITED = "person_exited"
    UTTERANCE = "utterance"
    RESPONSE = "response"
    OBJECT_DETECTED = "object_detected"
    ENGAGEMENT_CHANGE = "engagement_change"
    AUTOMATION_TRIGGERED = "automation_triggered"
    PRIVACY_TOGGLED = "privacy_toggled"
    SYSTEM_ERROR = "system_error"


class Event(BaseModel):
    type: EventType
    person_id: str | None = None
    data: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)
