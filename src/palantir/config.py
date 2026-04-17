"""Configuration loader for Palantir services.

Loads configuration from TOML files with layered overrides:
  default.toml -> {environment}.toml -> environment variables -> .env file
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Project root: two levels up from this file (src/palantir/config.py -> Palantir/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base dict."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file, returning empty dict if it doesn't exist."""
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


@dataclass
class CameraConfig:
    device: int = 0
    width: int = 640
    height: int = 480
    fps: int = 15
    face_detection_interval: int = 1
    engagement_interval: int = 5
    object_detection_interval: int = 30


@dataclass
class AudioConfig:
    device: str = "default"
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 30
    wake_word_threshold: float = 0.7
    stt_model: str = "base.en"
    stt_compute_type: str = "int8"
    stt_beam_size: int = 1
    max_utterance_seconds: int = 30
    silence_timeout_ms: int = 1500


@dataclass
class IdentityConfig:
    face_match_threshold: float = 0.4
    voice_match_threshold: float = 0.65
    enrollment_face_samples: int = 10
    enrollment_voice_samples: int = 5
    identity_staleness_seconds: int = 10


@dataclass
class LLMConfig:
    default_model: str = "claude-haiku-4-5-20250301"
    complex_model: str = "claude-sonnet-4-6-20250514"
    max_context_tokens: int = 4096
    enable_prompt_caching: bool = True
    temperature: float = 0.7


@dataclass
class TTSConfig:
    engine: str = "piper"
    voice: str = "en_US-lessac-medium"
    sample_rate: int = 22050


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    # TLS: if cert_file and key_file are set and readable, uvicorn starts
    # with HTTPS. Leave unset for plain HTTP (e.g. behind a reverse proxy).
    tls_cert_file: str = ""
    tls_key_file: str = ""


@dataclass
class PrivacyConfig:
    data_retention_days: int = 90
    auto_delete_on_unenroll: bool = True


@dataclass
class RedisConfig:
    url: str = "unix:///var/run/redis/redis.sock"
    fallback_url: str = "redis://localhost:6379/0"


@dataclass
class EngagementConfig:
    scoring_interval_seconds: int = 10
    smoothing_window_seconds: int = 30
    phone_confidence_threshold: float = 0.6
    sleep_stillness_seconds: int = 30
    cloud_validation_interval_seconds: int = 300


@dataclass
class AttendanceConfig:
    exit_timeout_seconds: int = 300


@dataclass
class AutomationConfig:
    enabled: bool = True
    allow_shell_commands: bool = False  # Dangerous; off by default


@dataclass
class BackupConfig:
    enabled: bool = True
    directory: str = "/var/lib/palantir/backups"
    keep_last_n: int = 14  # ~2 weeks of daily backups
    compress: bool = True


@dataclass
class PalantirConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    web: WebConfig = field(default_factory=WebConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    engagement: EngagementConfig = field(default_factory=EngagementConfig)
    attendance: AttendanceConfig = field(default_factory=AttendanceConfig)
    automation: AutomationConfig = field(default_factory=AutomationConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)

    # Non-TOML config loaded from environment
    anthropic_api_key: str = ""
    auth_token: str = ""
    db_path: str = "/var/lib/palantir/palantir.db"
    enrollment_path: str = "/var/lib/palantir/enrollments"


def _apply_dict_to_dataclass(dc: Any, data: dict[str, Any]) -> None:
    """Apply a flat dict of values to a dataclass instance."""
    for key, value in data.items():
        if hasattr(dc, key):
            expected_type = type(getattr(dc, key))
            # Coerce types for values loaded from TOML (which are already typed)
            if expected_type is bool and isinstance(value, bool):
                setattr(dc, key, value)
            elif expected_type is int and not isinstance(value, int):
                setattr(dc, key, int(value))
            elif expected_type is float and not isinstance(value, float):
                setattr(dc, key, float(value))
            else:
                setattr(dc, key, value)


def load_config(environment: str | None = None) -> PalantirConfig:
    """Load and return the full Palantir configuration.

    Args:
        environment: Override environment name (default: from PALANTIR_ENV or "development").
    """
    # Load .env file
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)

    if environment is None:
        environment = os.environ.get("PALANTIR_ENV", "development")

    # Layer TOML configs
    raw = _load_toml(CONFIG_DIR / "default.toml")
    env_overrides = _load_toml(CONFIG_DIR / f"{environment}.toml")
    raw = _deep_merge(raw, env_overrides)

    # Build config object
    config = PalantirConfig()

    section_map = {
        "camera": config.camera,
        "audio": config.audio,
        "identity": config.identity,
        "llm": config.llm,
        "tts": config.tts,
        "web": config.web,
        "privacy": config.privacy,
        "redis": config.redis,
        "engagement": config.engagement,
        "attendance": config.attendance,
        "automation": config.automation,
        "backup": config.backup,
    }

    for section_name, section_dc in section_map.items():
        if section_name in raw:
            _apply_dict_to_dataclass(section_dc, raw[section_name])

    # Load from environment variables
    config.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    config.auth_token = os.environ.get("PALANTIR_AUTH_TOKEN", "")
    config.db_path = os.environ.get("PALANTIR_DB_PATH", config.db_path)
    config.enrollment_path = os.environ.get("PALANTIR_ENROLLMENT_PATH", config.enrollment_path)

    # Redis URL from env overrides TOML
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        config.redis.url = redis_url

    return config
