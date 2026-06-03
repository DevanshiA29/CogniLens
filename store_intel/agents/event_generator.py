from __future__ import annotations

import hashlib
from typing import Any

from store_intel.schemas import EventType, StoreEvent, normalize_timestamp


ACTION_TO_EVENT = {
    "entered_store": EventType.ENTRY,
    "exited_store": EventType.EXIT,
    "zone_enter": EventType.ZONE_ENTER,
    "zone_exit": EventType.ZONE_EXIT,
    "zone_dwell": EventType.ZONE_DWELL,
    "billing_queue_join": EventType.BILLING_QUEUE_JOIN,
    "billing_queue_abandon": EventType.BILLING_QUEUE_ABANDON,
    "reentry": EventType.REENTRY,
    "product_interaction": EventType.PRODUCT_INTERACTION,
    "checkout_visit": EventType.CHECKOUT_VISIT,
    "anomaly": EventType.ANOMALY,
}


class EventGeneratorAgent:
    """Converts analyzer observations into the required event schema."""

    def from_observation(self, observation: dict[str, Any]) -> StoreEvent:
        event_type = ACTION_TO_EVENT.get(str(observation["action"]))
        if event_type is None:
            raise ValueError(f"Unsupported action: {observation['action']}")

        timestamp = normalize_timestamp(observation["timestamp"])
        zone_id = observation.get("zone") or observation.get("zone_id")
        dwell_ms = observation.get("dwell_ms")
        event_id = observation.get("event_id") or self._stable_event_id(
            observation["store_id"],
            observation["camera_id"],
            observation["visitor_id"],
            event_type,
            timestamp,
            zone_id,
            dwell_ms,
        )
        return StoreEvent(
            event_id=event_id,
            store_id=observation["store_id"],
            camera_id=observation["camera_id"],
            visitor_id=observation["visitor_id"],
            video_time_sec=observation.get("video_time_sec"),
            frame_id=observation.get("frame_id"),
            track_id=str(observation.get("track_id")) if observation.get("track_id") is not None else None,
            group_id=observation.get("group_id"),
            role=observation.get("role") or ("staff" if observation.get("is_staff") else "customer"),
            event_type=event_type,
            timestamp=timestamp,
            zone_id=zone_id,
            zone=zone_id,
            dwell_ms=dwell_ms,
            is_staff=bool(observation.get("is_staff", False)),
            confidence=float(observation.get("confidence", 0.5)),
            metadata=dict(observation.get("metadata", {})),
        )

    def from_observations(self, observations: list[dict[str, Any]]) -> list[StoreEvent]:
        return [self.from_observation(observation) for observation in observations]

    @staticmethod
    def _stable_event_id(*parts: Any) -> str:
        digest = hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:12]
        return f"EVT_{digest}"
