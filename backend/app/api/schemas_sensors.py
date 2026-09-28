"""Citizen-provided sensor reading contracts; these are not trusted stations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CitizenSensorReadingIn(BaseModel):
    client_submission_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    pm25_ugm3: float = Field(ge=0, le=2000)
    device_label: str = Field(min_length=1, max_length=80)
    measured_at: datetime
    consent: bool

    @field_validator("measured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measured_at must include a timezone")
        return value

    @field_validator("device_label")
    @classmethod
    def validate_device_label(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("device_label must be readable text")
        return value

    @field_validator("consent")
    @classmethod
    def require_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("consent is required to store a citizen reading")
        return value


class CitizenSensorReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    latitude: float
    longitude: float
    pm25_ugm3: float
    device_label: str
    measured_at: datetime
    submitted_at: datetime
    status: Literal["pending_review", "verified", "rejected"]
    reviewed_at: datetime | None = None


class CitizenSensorReviewIn(BaseModel):
    status: Literal["verified", "rejected"]
