from __future__ import annotations

from typing import Any


class StaffClassifier:
    """Explainable staff heuristic based on zones and track persistence."""

    def classify(self, zone: str, detection: dict[str, Any], track_seen_seconds: int, restricted_zones: set[str]) -> tuple[str, float]:
        if zone in restricted_zones and track_seen_seconds >= 2:
            return "staff", 0.82
        if zone in restricted_zones and detection.get("bbox", [0])[0] > 0:
            return "staff", 0.68
        return "customer", 0.72
