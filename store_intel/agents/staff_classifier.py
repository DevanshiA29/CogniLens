from __future__ import annotations

from typing import Any


class StaffClassifier:
    """Explainable staff heuristic based on zones and track persistence."""

    def classify(
        self,
        zone: str,
        detection: dict[str, Any],
        track_seen_seconds: int,
        restricted_zones: set[str],
        first_zone: str | None = None,
        movement_ratio: float | None = None,
    ) -> tuple[str, float]:
        if zone in restricted_zones:
            return ("staff", 0.9) if track_seen_seconds >= 2 else ("staff", 0.78)
        if first_zone in restricted_zones and track_seen_seconds >= 2:
            return "staff", 0.86
        if track_seen_seconds >= 6 and first_zone not in {"ENTRY", "EXIT"} and (movement_ratio is None or movement_ratio <= 0.18):
            return "staff", 0.74
        return "customer", 0.72
