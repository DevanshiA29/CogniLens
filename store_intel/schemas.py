from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class EventType(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"
    PRODUCT_INTERACTION = "PRODUCT_INTERACTION"
    CHECKOUT_VISIT = "CHECKOUT_VISIT"
    ANOMALY = "ANOMALY"


class Role(StrEnum):
    CUSTOMER = "customer"
    STAFF = "staff"
    UNKNOWN = "unknown"


class StoreEvent(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    video_time_sec: float | None = None
    frame_id: int | None = None
    track_id: str | None = None
    group_id: str | None = None
    role: Role = Role.CUSTOMER
    event_type: EventType
    timestamp: str
    zone_id: str | None = None
    zone: str | None = None
    dwell_ms: int | None = None
    is_staff: bool = False
    confidence: float = Field(ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: str) -> str:
        return normalize_timestamp(value)

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: str | EventType) -> str | EventType:
        if isinstance(value, str):
            return value.upper()
        return value

    @model_validator(mode="after")
    def enforce_binary_person_role(self) -> "StoreEvent":
        if self.is_staff:
            self.role = Role.STAFF
        elif self.role == Role.UNKNOWN:
            self.role = Role.CUSTOMER
        return self


class EventBatch(BaseModel):
    events: list[StoreEvent]


def normalize_timestamp(value: str | datetime) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(normalize_timestamp(value).replace("Z", "+00:00"))
