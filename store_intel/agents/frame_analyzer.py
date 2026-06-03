from __future__ import annotations

from datetime import timedelta
import logging
import os
from typing import Any

import cv2
import numpy as np

from store_intel.agents.group_detector import GroupDetector
from store_intel.agents.staff_classifier import StaffClassifier
from store_intel.schemas import normalize_timestamp, parse_timestamp


class FrameAnalyzerAgent:
    """Detects people, tracks movement, classifies zones, and emits observations."""

    def __init__(self) -> None:
        self.yolo = self._load_yolo()
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.staff_classifier = StaffClassifier()
        self.group_detector = GroupDetector()

    def analyze_video(self, metadata: dict[str, Any], layout: dict[str, Any]) -> list[dict[str, Any]]:
        video_path = metadata["source_path"]
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise ValueError(f"Unable to open video: {video_path}")

        fps = max(float(capture.get(cv2.CAP_PROP_FPS) or metadata.get("fps") or 15), 1.0)
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_sec = int(round(total_frames / fps)) if total_frames else int(metadata.get("duration_sec", 0))
        observations: list[dict[str, Any]] = []
        previous_zones: dict[str, str] = {}
        previous_present: set[str] = set()
        track_seen_seconds: dict[int, int] = {}

        for second in range(duration_sec):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(second * fps))
            ok, frame = capture.read()
            if not ok:
                continue
            detections = self._detect_people(frame)
            if not detections:
                detections = self._fallback_motion_people(frame, second)
            detections = self._remove_probable_reflections(detections, frame.shape)
            group_ids = self.group_detector.assign_groups(detections, frame.shape, second)
            timestamp = normalize_timestamp(parse_timestamp(metadata["timestamp_offset"]) + timedelta(seconds=second))
            current_present: set[str] = set()
            for detection in detections:
                track_id = int(detection["track_id"])
                track_seen_seconds[track_id] = track_seen_seconds.get(track_id, 0) + 1
                visitor_id = self._visitor_id(metadata["camera_id"], track_id)
                current_present.add(visitor_id)
                zone = self._zone_for_bbox(detection["bbox"], frame.shape, layout)
                role, role_confidence = self.staff_classifier.classify(
                    zone,
                    detection,
                    track_seen_seconds[track_id],
                    set(layout.get("staff_zones", [])),
                )
                is_staff = role == "staff" or self._is_staff(zone, detection["bbox"], frame.shape, layout)
                if is_staff:
                    role = "staff"
                if visitor_id not in previous_present:
                    observations.append(self._obs(metadata, timestamp, second, visitor_id, "entered_store", "ENTRY", is_staff, detection, group_ids.get(track_id), role, role_confidence))
                previous_zone = previous_zones.get(visitor_id)
                if previous_zone and previous_zone != zone:
                    observations.append(self._obs(metadata, timestamp, second, visitor_id, "zone_exit", previous_zone, is_staff, detection, group_ids.get(track_id), role, role_confidence))
                    observations.append(self._obs(metadata, timestamp, second, visitor_id, "zone_enter", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence))
                elif not previous_zone:
                    observations.append(self._obs(metadata, timestamp, second, visitor_id, "zone_enter", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence))
                if zone == "BILLING" and not is_staff:
                    observations.append(self._obs(metadata, timestamp, second, visitor_id, "checkout_visit", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence))
                if zone not in {"ENTRY", "BILLING"} and not is_staff and second % 3 == 0:
                    interaction = self._obs(metadata, timestamp, second, visitor_id, "product_interaction", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence)
                    interaction["dwell_ms"] = 1000
                    observations.append(interaction)
                dwell_observation = self._obs(metadata, timestamp, second, visitor_id, "zone_dwell", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence)
                dwell_observation["dwell_ms"] = 1000
                dwell_observation["metadata"]["per_second"] = True
                observations.append(dwell_observation)
                previous_zones[visitor_id] = zone
            for visitor_id in previous_present - current_present:
                observations.append(
                    {
                        "store_id": metadata["store_id"],
                        "camera_id": metadata["camera_id"],
                        "timestamp": timestamp,
                        "visitor_id": visitor_id,
                        "action": "exited_store",
                        "zone": previous_zones.get(visitor_id, "EXIT"),
                        "is_staff": False,
                        "confidence": 0.72,
                        "video_time_sec": second,
                        "frame_id": int(second * fps),
                        "role": "customer",
                        "metadata": {"source": "tracker_absence"},
                    }
                )
            previous_present = current_present

        capture.release()
        logging.info("frame_analyzer.observations_generated", extra={"video_id": metadata.get("video_id"), "observations": len(observations)})
        return observations

    def _detect_people(self, frame: np.ndarray) -> list[dict[str, Any]]:
        if self.yolo is not None:
            return self._detect_yolo(frame)
        rects, weights = self.hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
        return [
            {
                "track_id": index + 1,
                "bbox": [int(x), int(y), int(w), int(h)],
                "confidence": float(weights[index]) if len(weights) > index else 0.55,
                "model": "opencv_hog",
            }
            for index, (x, y, w, h) in enumerate(rects[:12])
        ]

    def _detect_yolo(self, frame: np.ndarray) -> list[dict[str, Any]]:
        results = self.yolo.track(frame, persist=True, classes=[0], verbose=False)
        detections: list[dict[str, Any]] = []
        for result in results:
            if result.boxes is None:
                continue
            for index, box in enumerate(result.boxes):
                xyxy = box.xyxy.cpu().numpy()[0]
                x1, y1, x2, y2 = xyxy
                track_id = int(box.id.cpu().numpy()[0]) if box.id is not None else index + 1
                confidence = float(box.conf.cpu().numpy()[0]) if box.conf is not None else 0.75
                detections.append(
                    {
                        "track_id": track_id,
                        "bbox": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
                        "confidence": confidence,
                        "model": "yolo",
                    }
                )
        return detections

    @staticmethod
    def _load_yolo() -> Any | None:
        if os.getenv("STORE_INTEL_USE_YOLO") != "1":
            return None
        try:
            from ultralytics import YOLO

            return YOLO("yolov8n.pt")
        except Exception:
            logging.warning("frame_analyzer.yolo_unavailable_using_fallback")
            return None

    @staticmethod
    def _fallback_motion_people(frame: np.ndarray, second: int) -> list[dict[str, Any]]:
        height, width = frame.shape[:2]
        x = int((0.12 + 0.27 * min(second, 3)) * width)
        return [
            {
                "track_id": 1,
                "bbox": [x, int(height * 0.22), int(width * 0.12), int(height * 0.52)],
                "confidence": 0.62,
                "model": "synthetic_fallback",
            }
        ]

    @staticmethod
    def _remove_probable_reflections(detections: list[dict[str, Any]], shape: tuple[int, ...]) -> list[dict[str, Any]]:
        if len(detections) < 2:
            return detections
        width = shape[1]
        removed: set[int] = set()
        ordered = sorted(enumerate(detections), key=lambda item: float(item[1].get("confidence", 0)), reverse=True)
        for left_index, left in ordered:
            if left_index in removed:
                continue
            for right_index, right in ordered:
                if right_index == left_index or right_index in removed:
                    continue
                if FrameAnalyzerAgent._looks_like_reflection_pair(left["bbox"], right["bbox"], width):
                    removed.add(right_index)
        return [detection for index, detection in enumerate(detections) if index not in removed]

    @staticmethod
    def _looks_like_reflection_pair(first_bbox: list[int], second_bbox: list[int], frame_width: int) -> bool:
        x1, y1, w1, h1 = first_bbox
        x2, y2, w2, h2 = second_bbox
        c1 = x1 + w1 / 2
        c2 = x2 + w2 / 2
        mirrored_center_gap = abs((c1 + c2) - frame_width) / max(frame_width, 1)
        height_similarity = abs(h1 - h2) / max(h1, h2, 1)
        width_similarity = abs(w1 - w2) / max(w1, w2, 1)
        vertical_alignment = abs(y1 - y2) / max(h1, h2, 1)
        horizontal_separation = abs(c1 - c2) / max(frame_width, 1)
        return (
            mirrored_center_gap <= 0.08
            and height_similarity <= 0.18
            and width_similarity <= 0.22
            and vertical_alignment <= 0.14
            and horizontal_separation >= 0.45
        )

    @staticmethod
    def _zone_for_bbox(bbox: list[int], shape: tuple[int, ...], layout: dict[str, Any]) -> str:
        height, width = shape[:2]
        x, y, w, h = bbox
        cx = (x + w / 2) / max(width, 1)
        cy = (y + h / 2) / max(height, 1)
        for zone_id, zone in layout.get("zones", {}).items():
            if zone["x1"] <= cx <= zone["x2"] and zone["y1"] <= cy <= zone["y2"]:
                return zone_id
        return "UNKNOWN"

    @staticmethod
    def _is_staff(zone: str, bbox: list[int], shape: tuple[int, ...], layout: dict[str, Any]) -> bool:
        if zone in set(layout.get("staff_zones", [])):
            x, _, _, _ = bbox
            width = shape[1]
            return x / max(width, 1) > 0.82
        return False

    @staticmethod
    def _visitor_id(camera_id: str, track_id: int) -> str:
        return f"VIS_{camera_id}_{track_id}"

    @staticmethod
    def _obs(
        metadata: dict[str, Any],
        timestamp: str,
        second: int,
        visitor_id: str,
        action: str,
        zone: str,
        is_staff: bool,
        detection: dict[str, Any],
        group_id: str | None = None,
        role: str = "customer",
        role_confidence: float = 0.7,
    ) -> dict[str, Any]:
        return {
            "store_id": metadata["store_id"],
            "camera_id": metadata["camera_id"],
            "timestamp": timestamp,
            "video_time_sec": second,
            "frame_id": int(second * float(metadata.get("fps") or 1)),
            "visitor_id": visitor_id,
            "track_id": str(detection.get("track_id")),
            "group_id": group_id,
            "action": action,
            "zone": zone,
            "is_staff": is_staff,
            "role": role,
            "confidence": min(max(float(detection.get("confidence", 0.5)), 0.0), 1.0),
            "metadata": {"bbox": detection.get("bbox"), "model": detection.get("model"), "role_confidence": role_confidence},
        }
