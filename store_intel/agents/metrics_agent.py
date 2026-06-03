from __future__ import annotations

from collections import Counter
import json
from typing import Any

from store_intel.agents.memory_store import MemoryEventStoreAgent


class IntelligenceMetricsAgent:
    """Computes retail KPIs from the event database."""

    def __init__(self, store: MemoryEventStoreAgent) -> None:
        self.store = store

    def metrics(self, store_id: str) -> dict[str, Any]:
        return self.session_metrics(store_id)

    def session_metrics(self, store_id: str) -> dict[str, Any]:
        events = self._events(store_id)
        sessions = self._sessions(store_id)
        customer_sessions = [session for session in sessions if not session["is_staff"]]
        staff_sessions = [session for session in sessions if session["is_staff"]]
        visitors = {session["visitor_id"] for session in customer_sessions}
        entries = [event for event in events if event["event_type"] == "ENTRY" and not event["is_staff"]]
        reentries = sum(int(session["reentry_count"] or 0) for session in customer_sessions)
        checkout = {event["visitor_id"] for event in events if event["event_type"] in {"BILLING_QUEUE_JOIN", "CHECKOUT_VISIT"} and not event["is_staff"]}
        exits = [event for event in events if event["event_type"] == "EXIT" and not event["is_staff"]]
        abandons = [event for event in events if event["event_type"] == "BILLING_QUEUE_ABANDON"]
        dwell = self.store.zone_dwell(store_id)
        average_dwell = {
            zone: round(values["total_dwell_ms"] / max(values["visits"], 1), 2)
            for zone, values in dwell.items()
        }
        return {
            "store_id": store_id,
            "unique_visitors": len(visitors),
            "total_entries": len({event["visitor_id"] for event in entries}),
            "entries": len({event["visitor_id"] for event in entries}),
            "exits": len(exits),
            "staff_count": len(staff_sessions),
            "reentries": reentries,
            "groups_detected": len({session["group_id"] for session in customer_sessions if session["group_id"]}),
            "conversion_rate": round(len(checkout) / max(len(visitors), 1), 4),
            "average_dwell_ms_by_zone": average_dwell,
            "queue_depth": self.current_queue_depth(store_id),
            "abandonment_rate": round(len(abandons) / max(len(checkout) + len(abandons), 1), 4),
            "events": len(events),
        }

    def funnel(self, store_id: str) -> dict[str, Any]:
        events = self._events(store_id)
        all_sessions = self._sessions(store_id)
        staff_visitors = {session["visitor_id"] for session in all_sessions if session["is_staff"]}
        sessions = [session for session in all_sessions if session["visitor_id"] not in staff_visitors]
        customers = [event for event in events if not event["is_staff"] and event["visitor_id"] not in staff_visitors]
        entered = {event["visitor_id"] for event in customers if event["event_type"] in {"ENTRY", "REENTRY"}}
        product_zone = {
            event["visitor_id"]
            for event in customers
            if event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL", "PRODUCT_INTERACTION"} and event["zone_id"] not in {"ENTRY", "BILLING", "EXIT", None}
        }
        product_interaction = {event["visitor_id"] for event in customers if event["event_type"] == "PRODUCT_INTERACTION"}
        checkout = {event["visitor_id"] for event in customers if event["event_type"] in {"CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"}}
        exited = {event["visitor_id"] for event in customers if event["event_type"] == "EXIT"}
        stages = {
            "entered_store": len(entered),
            "visited_product_zone": len(product_zone),
            "product_interaction": len(product_interaction),
            "billing_counter": len(checkout),
            "exited": len(exited),
        }
        dropoffs = {}
        ordered = list(stages.items())
        for (previous_name, previous_value), (name, value) in zip(ordered, ordered[1:]):
            dropoffs[f"{previous_name}_to_{name}"] = (
                0.0 if previous_value == 0 else round(max(previous_value - value, 0) / previous_value * 100, 2)
            )
        attention_scores = self._attention_scores(sessions, customers)
        return {
            "store_id": store_id,
            "flow": [
                {"key": "entered_store", "label": "Entered Store", "count": stages["entered_store"]},
                {"key": "visited_product_zone", "label": "Visited Product Zone", "count": stages["visited_product_zone"]},
                {"key": "product_interaction", "label": "Product Interaction", "count": stages["product_interaction"]},
                {"key": "billing_counter", "label": "Billing Counter", "count": stages["billing_counter"]},
                {"key": "exited", "label": "Exit", "count": stages["exited"]},
            ],
            **stages,
            "dropoff_percentages": dropoffs,
            "attention_scores": attention_scores,
            # Backward-compatible dashboard fields.
            "entry": stages["entered_store"],
            "zone_enter": stages["visited_product_zone"],
            "checkout_visit": stages["billing_counter"],
            "billing_queue_join": stages["billing_counter"],
            "exit": stages["exited"],
        }

    def heatmap(self, store_id: str) -> dict[str, Any]:
        dwell = self.store.zone_dwell(store_id)
        zones = {zone: values["total_dwell_ms"] for zone, values in dwell.items()}
        activity_rows = self.store.rows(
            """
            SELECT zone_id, COUNT(*) AS events
            FROM events
            WHERE store_id = ? AND zone_id IS NOT NULL
            GROUP BY zone_id
            """,
            (store_id,),
        )
        return {
            "store_id": store_id,
            "zones": zones,
            "activity": {row["zone_id"]: row["events"] for row in activity_rows},
        }

    def anomalies(self, store_id: str) -> dict[str, Any]:
        rows = self.store.rows(
            "SELECT * FROM anomalies WHERE store_id = ? ORDER BY timestamp DESC",
            (store_id,),
        )
        anomalies = [self._format_anomaly(row) for row in rows]
        return {"store_id": store_id, "anomalies": anomalies}

    def zones(self, store_id: str) -> dict[str, Any]:
        dwell = self.store.zone_dwell(store_id)
        return {"store_id": store_id, "zones": dwell}

    def visitor_timeline(self, store_id: str, visitor_id: str) -> dict[str, Any]:
        events = self.store.rows(
            "SELECT * FROM events WHERE store_id = ? AND visitor_id = ? ORDER BY timestamp, event_id",
            (store_id, visitor_id),
        )
        session = self.store.get_session(store_id, visitor_id) or {}
        dwell_by_zone = json.loads(session.get("dwell_time_by_zone") or "{}") if session else {}
        total_dwell = sum(int(value) for value in dwell_by_zone.values())
        most_visited_zone = max(dwell_by_zone.items(), key=lambda item: item[1])[0] if dwell_by_zone else None
        converted = any(event["event_type"] in {"CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"} for event in events)
        product_hits = sum(1 for event in events if event["event_type"] == "PRODUCT_INTERACTION")
        purchase_intent_score = min(100, product_hits * 25 + (35 if converted else 0) + min(total_dwell // 1000, 40))
        return {
            "store_id": store_id,
            "visitor_id": visitor_id,
            "events": [
                {
                    "timestamp": event["timestamp"],
                    "event_type": event["event_type"].lower(),
                    "zone": event["zone_id"],
                    "confidence": event["confidence"],
                    "metadata": json.loads(event["metadata"] or "{}"),
                }
                for event in events
            ],
            "total_dwell_time": total_dwell,
            "most_visited_zone": most_visited_zone,
            "purchase_intent_score": purchase_intent_score,
            "converted": converted,
        }

    def current_queue_depth(self, store_id: str) -> int:
        joins = self.store.rows(
            "SELECT COUNT(*) AS n FROM events WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'",
            (store_id,),
        )[0]["n"]
        exits = self.store.rows(
            "SELECT COUNT(*) AS n FROM events WHERE store_id = ? AND event_type IN ('EXIT', 'BILLING_QUEUE_ABANDON')",
            (store_id,),
        )[0]["n"]
        return max(int(joins) - int(exits), 0)

    def _events(self, store_id: str) -> list[dict[str, Any]]:
        return self.store.rows("SELECT * FROM events WHERE store_id = ? ORDER BY timestamp", (store_id,))

    def _sessions(self, store_id: str) -> list[dict[str, Any]]:
        return self.store.rows("SELECT * FROM sessions WHERE store_id = ?", (store_id,))

    @staticmethod
    def _attention_scores(sessions: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events_by_visitor: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            events_by_visitor.setdefault(event["visitor_id"], []).append(event)
        scores: list[dict[str, Any]] = []
        for session in sessions:
            visitor_id = session["visitor_id"]
            dwell = json.loads(session.get("dwell_time_by_zone") or "{}")
            product_dwell_ms = sum(
                int(value or 0)
                for zone, value in dwell.items()
                if zone not in {"ENTRY", "BILLING", "EXIT", "UNKNOWN"}
            )
            visitor_events = events_by_visitor.get(visitor_id, [])
            product_hits = sum(1 for event in visitor_events if event["event_type"] == "PRODUCT_INTERACTION")
            shelf_engagement = sum(
                1
                for event in visitor_events
                if event["event_type"] in {"ZONE_DWELL", "PRODUCT_INTERACTION"}
                and event["zone_id"] not in {"ENTRY", "BILLING", "EXIT", None}
            )
            checkout_bonus = 15 if any(event["event_type"] in {"CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"} for event in visitor_events) else 0
            score = min(
                100,
                round(
                    min(product_dwell_ms / 1000, 30) * 1.4
                    + min(product_hits, 4) * 14
                    + min(shelf_engagement, 8) * 4
                    + checkout_bonus
                ),
            )
            scores.append(
                {
                    "visitor": f"V{visitor_id.rsplit('_', 1)[-1]}",
                    "attention_score": score,
                    "dwell_ms": product_dwell_ms,
                    "product_interactions": product_hits,
                    "shelf_engagement": shelf_engagement,
                }
            )
        return sorted(scores, key=lambda item: item["attention_score"], reverse=True)

    @staticmethod
    def _format_anomaly(row: dict[str, Any]) -> dict[str, Any]:
        metadata = json.loads(row.get("metadata") or "{}")
        measured_value = metadata.get("measured_value")
        threshold = metadata.get("threshold")
        unit = metadata.get("unit")
        if measured_value is None:
            measured_value = metadata.get("reentry_count") or metadata.get("queue_depth") or metadata.get("people") or metadata.get("dwell_ms")
        if threshold is None:
            threshold = {
                "REPEATED_ENTRY_EXIT": 2,
                "QUEUE_SPIKE": 5,
                "CROWDING": 5,
                "EXCESSIVE_DWELL": 900000,
            }.get(row["anomaly_type"])
        if unit is None:
            unit = {
                "REPEATED_ENTRY_EXIT": "reentries",
                "QUEUE_SPIKE": "people",
                "CROWDING": "people",
                "EXCESSIVE_DWELL": "milliseconds",
            }.get(row["anomaly_type"])
        proof = {
            "timestamp": row["timestamp"],
            "rule": metadata.get("rule", row["anomaly_type"]),
            "measured_value": measured_value,
            "threshold": threshold,
            "unit": unit,
            "visitor_id": metadata.get("visitor_id"),
            "zone": metadata.get("zone"),
            "video_time_sec": metadata.get("video_time_sec"),
            "frame_id": metadata.get("frame_id"),
        }
        proof = {key: value for key, value in proof.items() if value is not None}
        return {
            "anomaly_id": row["anomaly_id"],
            "store_id": row["store_id"],
            "timestamp": row["timestamp"],
            "anomaly_type": row["anomaly_type"],
            "severity": row["severity"],
            "message": row["message"],
            "confidence": metadata.get("confidence", 0.5),
            "proof": proof,
            "metadata": metadata,
        }
