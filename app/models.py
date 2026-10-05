"""Pydantic schemas for the museum lighting orchestrator."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class SceneKind(str, Enum):
    normal = "normal"
    emergency = "emergency"


def _require_tz_aware(value: datetime) -> datetime:
    """Reject naive datetimes; normalize aware ones to UTC."""
    if value.tzinfo is None:
        raise ValueError(
            "datetime must be timezone-aware ISO 8601 (e.g. 2026-10-05T10:00:00+08:00)"
        )
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Lamps
# ---------------------------------------------------------------------------

class LampCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    channel: str = Field(min_length=1, max_length=40)
    min_brightness: int = Field(default=0, ge=0, le=100)
    max_brightness: int = Field(default=100, ge=0, le=100)
    default_brightness: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def _check_range(self) -> "LampCreate":
        if self.min_brightness > self.max_brightness:
            raise ValueError("min_brightness must be <= max_brightness")
        if not self.min_brightness <= self.default_brightness <= self.max_brightness:
            raise ValueError(
                "default_brightness must be within [min_brightness, max_brightness]"
            )
        return self


class Lamp(LampCreate):
    id: str


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

class SceneChannel(BaseModel):
    channel: str = Field(min_length=1, max_length=40)
    brightness: int = Field(ge=0, le=100)
    fade_seconds: int = Field(default=0, ge=0, le=3600)


class SceneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: SceneKind = SceneKind.normal
    start: datetime
    end: Optional[datetime] = None
    channels: List[SceneChannel] = Field(min_length=1)

    @field_validator("start", "end")
    @classmethod
    def _tz_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        return _require_tz_aware(v)

    @model_validator(mode="after")
    def _check_scene(self) -> "SceneCreate":
        if self.end is not None and self.end <= self.start:
            raise ValueError("end must be after start")
        if self.kind == SceneKind.emergency and self.end is None:
            raise ValueError("emergency scenes must define an end time")
        seen: set[str] = set()
        for ch in self.channels:
            if ch.channel in seen:
                raise ValueError(f"duplicate channel '{ch.channel}' in scene")
            seen.add(ch.channel)
        return self

    @property
    def channel_map(self) -> Dict[str, SceneChannel]:
        return {ch.channel: ch for ch in self.channels}


class Scene(SceneCreate):
    id: str


# ---------------------------------------------------------------------------
# Validation conflicts (business-rule violations -> HTTP 409)
# ---------------------------------------------------------------------------

class ConflictInterval(BaseModel):
    start: datetime
    end: datetime


class Conflict(BaseModel):
    channel: str
    reason: str  # lamp_not_found | brightness_out_of_range | fade_interval_overlap | emergency_window_overlap
    detail: str
    interval: Optional[ConflictInterval] = None


class SceneRejected(BaseModel):
    error: str = "scene_rejected"
    conflicts: List[Conflict]


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

class TimelineEntry(BaseModel):
    time: datetime
    channel: str
    brightness: int
    fade_seconds: int
    kind: str  # scene | emergency | restore
    scene_id: Optional[str] = None
    scene_name: Optional[str] = None


class Preemption(BaseModel):
    emergency_scene_id: str
    emergency_scene_name: str
    channel: str
    window: ConflictInterval
    preempted_scene_id: Optional[str] = None
    restored_scene_id: Optional[str] = None
    restored_brightness: int


class Timeline(BaseModel):
    generated_at: datetime
    entries: List[TimelineEntry]
    preemptions: List[Preemption]
