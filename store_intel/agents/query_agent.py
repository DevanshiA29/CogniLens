from __future__ import annotations

from datetime import timedelta
from typing import Any

from store_intel.agents.memory_store import MemoryEventStoreAgent
from store_intel.schemas import normalize_timestamp, parse_timestamp


class TimestampQueryAgent:
    """Powers slider timestamp retrieval and natural summaries."""

    def __init__(self, store: MemoryEventStoreAgent) -> None:
        self.store = store

    def at_timestamp(self, store_id: str, timestamp: str) -> dict[str, Any]:
        normalized = normalize_timestamp(timestamp)
        active_events = self._active_events_near_timestamp(store_id, normalized)
        events = self.store.rows(
            """
            SELECT * FROM events
            WHERE store_id = ? AND timestamp = ?
            ORDER BY camera_id, visitor_id
            """,
            (store_id, normalized),
        )
        zone_counts = self._current_zone_counts(active_events)
        customer_zone_counts = self._current_zone_counts([event for event in active_events if not event["is_staff"]])
        active_customers = {event["visitor_id"] for event in active_events if not event["is_staff"]}
        active_staff = {event["visitor_id"] for event in active_events if event["is_staff"]}
        queue_depth = sum(1 for event in events if event["event_type"] == "BILLING_QUEUE_JOIN")
        visitor_states = self._visitor_states(active_events)
        summary = self._summary(active_events or events, len(active_customers), len(active_staff), queue_depth, customer_zone_counts)
        return {
            "timestamp": normalized,
            "summary": summary,
            "active_visitors": len(active_customers),
            "staff_detected": len(active_staff),
            "zone_activity": zone_counts,
            "events": events,
            "active_events": active_events,
            "visitor_states": visitor_states,
            "display_events": [self._display_state(state) for state in visitor_states],
            "raw_display_events": [self._display_event(event) for event in events],
        }

    def range_for_store(self, store_id: str) -> dict[str, Any]:
        rows = self.store.rows(
            """
            SELECT MIN(timestamp) AS start_timestamp, MAX(timestamp) AS end_timestamp, COUNT(*) AS event_count
            FROM events
            WHERE store_id = ?
            """,
            (store_id,),
        )
        row = rows[0]
        if not row["start_timestamp"]:
            return {
                "store_id": store_id,
                "start_timestamp": None,
                "end_timestamp": None,
                "duration_sec": 0,
                "event_count": 0,
            }
        start = normalize_timestamp(row["start_timestamp"])
        end = normalize_timestamp(row["end_timestamp"])
        duration_sec = int((parse_timestamp(end) - parse_timestamp(start)).total_seconds())
        return {
            "store_id": store_id,
            "start_timestamp": start,
            "end_timestamp": end,
            "duration_sec": duration_sec,
            "event_count": int(row["event_count"]),
        }

    @staticmethod
    def _summary(
        events: list[dict[str, Any]],
        active_customers: int,
        active_staff: int,
        queue_depth: int,
        zone_counts: dict[str, int],
    ) -> str:
        entries = sum(1 for event in events if event["event_type"] in {"ENTRY", "REENTRY"} and not event["is_staff"])
        exits = sum(1 for event in events if event["event_type"] == "EXIT" and not event["is_staff"])
        parts: list[str] = []
        if active_customers and zone_counts:
            zone_order = {"ENTRY": 0, "WALL_PRODUCTS": 1, "PRODUCT_AISLE": 2, "CENTER_DISPLAY": 3, "BILLING": 4, "PMU": 5}
            zone_parts = [
                f"{count} in {TimestampQueryAgent._zone_label(zone_id)}"
                for zone_id, count in sorted(zone_counts.items(), key=lambda item: zone_order.get(item[0], 99))
            ]
            noun = "customer" if active_customers == 1 else "customers"
            parts.append(f"{active_customers} {noun} visible: {', '.join(zone_parts)}")
        elif entries:
            noun = "customer" if entries == 1 else "customers"
            parts.append(f"{entries} {noun} entered the store")
        if exits:
            noun = "customer" if exits == 1 else "customers"
            parts.append(f"{exits} {noun} exited")
        if active_staff:
            noun = "staff member" if active_staff == 1 else "staff members"
            parts.append(f"{active_staff} {noun} detected")
        if queue_depth:
            parts.append(f"Queue depth is {queue_depth}")
        if not parts:
            parts.append(f"{active_customers} active customers observed")
        return ". ".join(parts) + "."

    @staticmethod
    def _visitor_states(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        state_events: dict[str, dict[str, Any]] = {}
        priority = {"ZONE_DWELL": 4, "BILLING_QUEUE_JOIN": 3, "ZONE_ENTER": 2, "ENTRY": 1, "REENTRY": 1}
        state_priority: dict[str, int] = {}
        for event in events:
            event_type = event["event_type"]
            if event_type not in priority or not event["zone_id"]:
                continue
            visitor_id = event["visitor_id"]
            event_priority = priority[event_type]
            if event_priority >= state_priority.get(visitor_id, 0):
                state_events[visitor_id] = event
                state_priority[visitor_id] = event_priority
        return [
            {
                "visitor_id": event["visitor_id"],
                "visitor": TimestampQueryAgent._visitor_label(event["visitor_id"], bool(event["is_staff"])),
                "zone_id": event["zone_id"],
                "zone": TimestampQueryAgent._zone_label(event["zone_id"]),
                "is_staff": bool(event["is_staff"]),
                "confidence_pct": round(float(event["confidence"]) * 100),
                "event_type": event["event_type"],
            }
            for event in sorted(state_events.values(), key=lambda item: item["visitor_id"])
        ]

    def _active_events_near_timestamp(self, store_id: str, normalized: str) -> list[dict[str, Any]]:
        target = parse_timestamp(normalized)
        start = normalize_timestamp(target - timedelta(seconds=3))
        rows = self.store.rows(
            """
            SELECT * FROM events
            WHERE store_id = ?
              AND timestamp >= ?
              AND timestamp <= ?
            ORDER BY timestamp, event_id
            """,
            (store_id, start, normalized),
        )
        state_events: dict[str, dict[str, Any]] = {}
        priority = {
            "ZONE_DWELL": 6,
            "CHECKOUT_VISIT": 5,
            "BILLING_QUEUE_JOIN": 5,
            "PRODUCT_INTERACTION": 4,
            "ZONE_ENTER": 3,
            "REENTRY": 2,
            "ENTRY": 1,
        }
        for event in rows:
            visitor_id = event["visitor_id"]
            event_type = event["event_type"]
            if event_type in {"EXIT", "BILLING_QUEUE_ABANDON"}:
                state_events.pop(visitor_id, None)
                continue
            if event_type not in priority or not event["zone_id"]:
                continue
            existing = state_events.get(visitor_id)
            if not existing:
                state_events[visitor_id] = event
                continue
            existing_time = parse_timestamp(existing["timestamp"])
            event_time = parse_timestamp(event["timestamp"])
            if event_time > existing_time or (
                event_time == existing_time and priority[event_type] >= priority.get(existing["event_type"], 0)
            ):
                state_events[visitor_id] = event
        return sorted(state_events.values(), key=lambda event: (event["camera_id"], event["visitor_id"]))

    @staticmethod
    def _display_state(state: dict[str, Any]) -> dict[str, Any]:
        role = "Employee" if state["is_staff"] else "Customer"
        return {
            "event_id": state["visitor_id"],
            "headline": f"Currently in {state['zone']}",
            "visitor": state["visitor"],
            "zone": state["zone"],
            "confidence_pct": state["confidence_pct"],
            "detail": f"{role} currently in {state['zone']} · {state['confidence_pct']}% confidence",
        }

    @staticmethod
    def _current_zone_counts(events: list[dict[str, Any]]) -> dict[str, int]:
        visitor_zones: dict[str, str] = {}
        priority = {"ZONE_DWELL": 3, "ZONE_ENTER": 2, "ENTRY": 1, "REENTRY": 1}
        visitor_priority: dict[str, int] = {}
        for event in events:
            event_type = event["event_type"]
            if event_type not in priority or not event["zone_id"]:
                continue
            visitor_id = event["visitor_id"]
            event_priority = priority[event_type]
            if event_priority >= visitor_priority.get(visitor_id, 0):
                visitor_zones[visitor_id] = event["zone_id"]
                visitor_priority[visitor_id] = event_priority

        counts: dict[str, int] = {}
        for zone_id in visitor_zones.values():
            counts[zone_id] = counts.get(zone_id, 0) + 1
        return counts

    @staticmethod
    def _display_event(event: dict[str, Any]) -> dict[str, Any]:
        zone = TimestampQueryAgent._zone_label(event["zone_id"])
        visitor = TimestampQueryAgent._visitor_label(event["visitor_id"], bool(event["is_staff"]))
        event_type = event["event_type"]
        headline_by_type = {
            "ENTRY": "Customer entered store",
            "REENTRY": "Customer re-entered store",
            "EXIT": "Customer exited store",
            "ZONE_ENTER": f"Moved into {zone}",
            "ZONE_EXIT": f"Moved out of {zone}",
            "ZONE_DWELL": f"Dwelling in {zone}",
            "BILLING_QUEUE_JOIN": "Joined billing queue",
            "BILLING_QUEUE_ABANDON": "Left billing queue",
        }
        headline = headline_by_type.get(event_type, event_type.replace("_", " ").title())
        confidence_pct = round(float(event["confidence"]) * 100)
        return {
            "event_id": event["event_id"],
            "headline": headline,
            "visitor": visitor,
            "zone": zone,
            "confidence_pct": confidence_pct,
            "detail": f"{visitor} · {zone} · {confidence_pct}% confidence",
        }

    @staticmethod
    def _zone_label(zone_id: str | None) -> str:
        labels = {
            "ENTRY": "Entrance",
            "EXIT": "Exit",
            "AISLE_A": "Aisle A",
            "WALL_PRODUCTS": "Wall Products",
            "PRODUCT_AISLE": "Product Aisle",
            "CENTER_DISPLAY": "Center Display",
            "BILLING": "Checkout",
            "PMU": "PMU Service",
            "UNKNOWN": "Store Floor",
        }
        if not zone_id:
            return "Store Floor"
        return labels.get(zone_id, zone_id.replace("_", " ").title())

    @staticmethod
    def _visitor_label(visitor_id: str, is_staff: bool) -> str:
        role = "Employee" if is_staff else "Customer"
        suffix = visitor_id.rsplit("_", 1)[-1]
        return f"{role} {suffix}"
