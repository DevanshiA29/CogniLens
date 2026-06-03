from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from store_intel.agents.memory_store import MemoryEventStoreAgent
from store_intel.agents.metrics_agent import IntelligenceMetricsAgent


class AgentScoreAgent:
    """Evidence-based self-evaluation for the Store Intelligence rubric."""

    weights = {"detection": 30, "api": 35, "production": 20, "thinking": 15}

    def __init__(self, store: MemoryEventStoreAgent, project_root: str | Path | None = None) -> None:
        self.store = store
        self.metrics = IntelligenceMetricsAgent(store)
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]

    def score(self, store_id: str) -> dict[str, Any]:
        evidence = self._evidence(store_id)
        detection = self._detection_score(evidence)
        api = self._api_score(evidence)
        production = self._production_score(evidence)
        thinking = self._thinking_score(evidence)
        total = detection + api + production + thinking
        return {
            "store_id": store_id,
            "label": "Self-Evaluation Based on Rubric",
            "total": round(total, 2),
            "detection": round(detection, 2),
            "api": round(api, 2),
            "production": round(production, 2),
            "thinking": round(thinking, 2),
            "weights": self.weights,
            "evidence": evidence,
        }

    def _evidence(self, store_id: str) -> dict[str, Any]:
        event_counts = self._event_counts(store_id)
        metrics_result, metrics_ok = self._safe_api("metrics", lambda: self.metrics.metrics(store_id))
        funnel_result, funnel_ok = self._safe_api("funnel", lambda: self.metrics.funnel(store_id))
        zones_result, zones_ok = self._safe_api("zones", lambda: self.metrics.zones(store_id))
        anomalies_result, anomalies_ok = self._safe_api("anomalies", lambda: self.metrics.anomalies(store_id))
        logical_consistency = self._logical_consistency(event_counts, metrics_result, funnel_result)
        api_checks = {
            "metrics": metrics_ok,
            "funnel": funnel_ok,
            "zones": zones_ok,
            "anomalies": anomalies_ok,
            "logical_consistency": logical_consistency,
        }
        docs = self._doc_checks()
        production = self._production_checks()
        return {
            "events_generated": event_counts["events_generated"],
            "entry_events": event_counts["entry_events"],
            "exit_events": event_counts["exit_events"],
            "unique_visitors": event_counts["unique_visitors"],
            "visitor_id_events": event_counts["visitor_id_events"],
            "reentries_handled": event_counts["reentries_handled"],
            "staff_excluded": event_counts["staff_excluded"],
            "groups_detected": event_counts["groups_detected"],
            "apis_passing": sum(1 for passed in api_checks.values() if passed),
            "apis_total": len(api_checks),
            "api_checks": api_checks,
            "docs_present": docs["docs_present"],
            "design_doc_present": docs["design_doc_present"],
            "choices_doc_present": docs["choices_doc_present"],
            "docs_non_empty": docs["docs_non_empty"],
            "docs_mention_tradeoffs_assumptions": docs["docs_mention_tradeoffs_assumptions"],
            "health_endpoint_works": production["health_endpoint_works"],
            "logs_available": production["logs_available"],
            "tests_exist": production["tests_exist"],
            "docker_files_exist": production["docker_files_exist"],
        }

    def _event_counts(self, store_id: str) -> dict[str, int]:
        row = self.store.rows(
            """
            SELECT
              COUNT(*) AS events_generated,
              SUM(CASE WHEN event_type = 'ENTRY' THEN 1 ELSE 0 END) AS entry_events,
              SUM(CASE WHEN event_type = 'EXIT' THEN 1 ELSE 0 END) AS exit_events,
              COUNT(DISTINCT visitor_id) AS unique_visitors,
              SUM(CASE WHEN visitor_id IS NOT NULL AND visitor_id != '' THEN 1 ELSE 0 END) AS visitor_id_events,
              SUM(CASE WHEN event_type = 'REENTRY' THEN 1 ELSE 0 END) AS reentry_events,
              SUM(CASE WHEN is_staff = 1 OR role = 'staff' THEN 1 ELSE 0 END) AS staff_events,
              COUNT(DISTINCT CASE WHEN group_id IS NOT NULL AND group_id != '' THEN group_id END) AS groups_detected
            FROM events
            WHERE store_id = ?
            """,
            (store_id,),
        )[0]
        session_row = self.store.rows(
            """
            SELECT
              COALESCE(SUM(reentry_count), 0) AS session_reentries,
              SUM(CASE WHEN is_staff = 1 THEN 1 ELSE 0 END) AS staff_sessions
            FROM sessions
            WHERE store_id = ?
            """,
            (store_id,),
        )[0]
        return {
            "events_generated": int(row["events_generated"] or 0),
            "entry_events": int(row["entry_events"] or 0),
            "exit_events": int(row["exit_events"] or 0),
            "unique_visitors": int(row["unique_visitors"] or 0),
            "visitor_id_events": int(row["visitor_id_events"] or 0),
            "reentries_handled": int(row["reentry_events"] or 0) + int(session_row["session_reentries"] or 0),
            "staff_excluded": int(row["staff_events"] or 0) + int(session_row["staff_sessions"] or 0),
            "groups_detected": int(row["groups_detected"] or 0),
        }

    def _safe_api(self, name: str, call) -> tuple[dict[str, Any], bool]:
        try:
            result = call()
            return result, isinstance(result, dict)
        except Exception:
            logging.exception("score.api_check_failed", extra={"api": name})
            return {}, False

    @staticmethod
    def _logical_consistency(event_counts: dict[str, int], metrics: dict[str, Any], funnel: dict[str, Any]) -> bool:
        if not metrics or not funnel:
            return False
        conversion = metrics.get("conversion_rate", 0)
        queue_depth = metrics.get("queue_depth", 0)
        visitors = metrics.get("unique_visitors", 0)
        entered = funnel.get("entered_store", 0)
        checkout = funnel.get("checkout_visit", 0)
        product = funnel.get("visited_product_zone", 0)
        events_match = metrics.get("events", 0) == event_counts["events_generated"]
        return all(
            [
                isinstance(conversion, (int, float)) and 0 <= conversion <= 1,
                isinstance(queue_depth, int) and queue_depth >= 0,
                entered >= checkout,
                entered >= product,
                visitors <= max(entered + event_counts["reentries_handled"], event_counts["unique_visitors"]),
                events_match,
            ]
        )

    def _production_checks(self) -> dict[str, bool]:
        health_endpoint_works = False
        try:
            health_endpoint_works = self.store.count("events") >= 0 and self.store.db_path.exists()
        except Exception:
            logging.exception("score.health_check_failed")
        return {
            "health_endpoint_works": health_endpoint_works,
            "logs_available": bool(logging.getLogger().handlers),
            "tests_exist": any((self.project_root / "tests").glob("test_*.py")),
            "docker_files_exist": (self.project_root / "Dockerfile").exists()
            and (self.project_root / "docker-compose.yml").exists(),
        }

    def _doc_checks(self) -> dict[str, bool]:
        design = self.project_root / "DESIGN.md"
        choices = self.project_root / "CHOICES.md"
        design_text = design.read_text(encoding="utf-8") if design.exists() else ""
        choices_text = choices.read_text(encoding="utf-8") if choices.exists() else ""
        combined = f"{design_text}\n{choices_text}".lower()
        return {
            "design_doc_present": design.exists(),
            "choices_doc_present": choices.exists(),
            "docs_present": design.exists() and choices.exists(),
            "docs_non_empty": bool(design_text.strip()) and bool(choices_text.strip()),
            "docs_mention_tradeoffs_assumptions": (
                "trade-off" in combined or "tradeoff" in combined or "trade-offs" in combined
            )
            and "assumption" in combined,
        }

    def _detection_score(self, evidence: dict[str, Any]) -> float:
        score = 0.0
        score += 6 if evidence["events_generated"] > 0 else 0
        score += 3 if evidence["entry_events"] > 0 else 0
        score += 3 if evidence["exit_events"] > 0 else 0
        score += 6 if evidence["visitor_id_events"] == evidence["events_generated"] and evidence["events_generated"] > 0 else 0
        score += 4 if evidence["staff_excluded"] > 0 else 0
        score += 4 if evidence["reentries_handled"] > 0 else 0
        score += 4 if evidence["groups_detected"] > 0 else 0
        return min(score, self.weights["detection"])

    def _api_score(self, evidence: dict[str, Any]) -> float:
        return (evidence["apis_passing"] / max(evidence["apis_total"], 1)) * self.weights["api"]

    def _production_score(self, evidence: dict[str, Any]) -> float:
        checks = [
            evidence["health_endpoint_works"],
            evidence["logs_available"],
            evidence["tests_exist"],
            evidence["docker_files_exist"],
        ]
        return (sum(1 for passed in checks if passed) / len(checks)) * self.weights["production"]

    def _thinking_score(self, evidence: dict[str, Any]) -> float:
        checks = [
            evidence["design_doc_present"],
            evidence["choices_doc_present"],
            evidence["docs_non_empty"],
            evidence["docs_mention_tradeoffs_assumptions"],
        ]
        return (sum(1 for passed in checks if passed) / len(checks)) * self.weights["thinking"]
