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
        detected_duration_sec = int(round(total_frames / fps)) if total_frames else int(metadata.get("duration_sec", 0))
        requested_duration_sec = int(metadata.get("analysis_duration_sec") or metadata.get("duration_sec") or detected_duration_sec or 0)
        duration_sec = min(detected_duration_sec or requested_duration_sec, requested_duration_sec)
        sample_seconds = max(int(os.getenv("STORE_INTEL_FRAME_SAMPLE_SECONDS", "1")), 1)
        analysis_chunks = metadata.get("analysis_chunks") or [
            {"start_sec": 0, "end_sec": duration_sec, "duration_sec": duration_sec}
        ]
        observations: list[dict[str, Any]] = []
        previous_zones: dict[str, str] = {}
        previous_present: set[str] = set()
        track_seen_seconds: dict[int, int] = {}
        track_zone_streak: dict[int, tuple[str, int]] = {}
        track_last_detection: dict[str, dict[str, Any]] = {}
        track_last_role: dict[str, tuple[str, bool]] = {}
        track_first_zone: dict[int, str] = {}
        track_first_center: dict[int, tuple[float, float]] = {}

        for chunk_index, chunk in enumerate(analysis_chunks, start=1):
            start_second = min(max(int(chunk.get("start_sec", 0)), 0), duration_sec)
            end_second = min(max(int(chunk.get("end_sec", start_second)), start_second), duration_sec)
            print(
                f"[CHUNK {chunk_index}/{len(analysis_chunks)}] FrameAnalyzerAgent analyzing {start_second}-{end_second}s",
                flush=True,
            )
            logging.info(
                "frame_analyzer.chunk_start",
                extra={"video_id": metadata.get("video_id"), "chunk": chunk_index, "start_sec": start_second, "end_sec": end_second},
            )
            for second in range(start_second, end_second, sample_seconds):
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(second * fps))
                ok, frame = capture.read()
                if not ok:
                    continue
                analysis_frame, scale_x, scale_y = self._analysis_frame(frame)
                detections = self._detect_people(analysis_frame)
                if not detections:
                    detections = self._fallback_motion_people(analysis_frame, second)
                detections = self._add_service_zone_people(detections, analysis_frame, layout)
                detections = self._filter_person_like_detections(detections, analysis_frame)
                detections = self._remove_mirror_zone_detections(detections, analysis_frame.shape, layout, second=second)
                detections = self._remove_probable_reflections(detections, analysis_frame.shape)
                detections = self._scale_detections(detections, scale_x, scale_y)
                group_ids = self.group_detector.assign_groups(detections, frame.shape, second)
                timestamp = normalize_timestamp(parse_timestamp(metadata["timestamp_offset"]) + timedelta(seconds=second))
                current_present: set[str] = set()
                for detection in detections:
                    track_id = int(detection["track_id"])
                    track_seen_seconds[track_id] = track_seen_seconds.get(track_id, 0) + 1
                    visitor_id = self._visitor_id(metadata["camera_id"], track_id)
                    current_present.add(visitor_id)
                    zone = self._zone_for_bbox(detection["bbox"], frame.shape, layout)
                    center = self._normalized_center(detection["bbox"], frame.shape)
                    track_first_zone.setdefault(track_id, zone)
                    track_first_center.setdefault(track_id, center)
                    movement_ratio = self._center_distance(track_first_center[track_id], center)
                    role, role_confidence = self.staff_classifier.classify(
                        zone,
                        detection,
                        track_seen_seconds[track_id],
                        set(layout.get("staff_service_zones") or layout.get("staff_zones", [])),
                        track_first_zone.get(track_id),
                        movement_ratio,
                    )
                    is_staff = role == "staff" or self._is_staff(zone, detection["bbox"], frame.shape, layout)
                    if is_staff:
                        role = "staff"
                    track_last_detection[visitor_id] = detection
                    track_last_role[visitor_id] = (role, is_staff)
                    previous_streak_zone, previous_streak_count = track_zone_streak.get(track_id, (zone, 0))
                    zone_streak = previous_streak_count + 1 if previous_streak_zone == zone else 1
                    track_zone_streak[track_id] = (zone, zone_streak)
                    entry_zone = self._entry_zone(zone, layout)
                    if visitor_id not in previous_present:
                        observations.append(self._obs(metadata, timestamp, second, visitor_id, "entered_store", entry_zone, is_staff, detection, group_ids.get(track_id), role, role_confidence))
                    previous_zone = previous_zones.get(visitor_id)
                    if previous_zone and previous_zone != zone:
                        observations.append(self._obs(metadata, timestamp, second, visitor_id, "zone_exit", previous_zone, is_staff, detection, group_ids.get(track_id), role, role_confidence))
                        observations.append(self._obs(metadata, timestamp, second, visitor_id, "zone_enter", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence))
                    elif not previous_zone:
                        observations.append(self._obs(metadata, timestamp, second, visitor_id, "zone_enter", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence))
                    if zone in set(layout.get("checkout_zones", ["BILLING"])) and not is_staff:
                        observations.append(self._obs(metadata, timestamp, second, visitor_id, "checkout_visit", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence))
                    if zone in set(layout.get("product_zones", [])) and not is_staff and (zone_streak == 1 or zone_streak % 2 == 0):
                        interaction = self._obs(metadata, timestamp, second, visitor_id, "product_interaction", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence)
                        interaction["dwell_ms"] = zone_streak * 1000
                        interaction["metadata"]["evidence"] = {
                            "rule": "mapped_product_zone_presence",
                            "zone_streak_sec": zone_streak,
                            "video_time_sec": second,
                            "frame_sampling": f"1 frame every {sample_seconds}s",
                        }
                        observations.append(interaction)
                    dwell_observation = self._obs(metadata, timestamp, second, visitor_id, "zone_dwell", zone, is_staff, detection, group_ids.get(track_id), role, role_confidence)
                    dwell_observation["dwell_ms"] = 1000
                    dwell_observation["metadata"]["per_second"] = True
                    observations.append(dwell_observation)
                    previous_zones[visitor_id] = zone
                for visitor_id in previous_present - current_present:
                    exit_role, exit_is_staff = track_last_role.get(visitor_id, ("customer", False))
                    last_zone = previous_zones.get(visitor_id)
                    if not self._should_emit_exit(last_zone, layout):
                        continue
                    exit_metadata = {"source": "tracker_absence"}
                    if visitor_id in track_last_detection:
                        exit_metadata["bbox"] = track_last_detection[visitor_id].get("bbox")
                        exit_metadata["last_seen_bbox"] = track_last_detection[visitor_id].get("bbox")
                    observations.append(
                        {
                            "store_id": metadata["store_id"],
                            "camera_id": metadata["camera_id"],
                            "timestamp": timestamp,
                            "visitor_id": visitor_id,
                            "action": "exited_store",
                            "zone": self._exit_zone(previous_zones.get(visitor_id), layout),
                            "is_staff": exit_is_staff,
                            "confidence": 0.72,
                            "video_time_sec": second,
                            "frame_id": int(second * fps),
                            "role": exit_role,
                            "metadata": exit_metadata,
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

    @staticmethod
    def _analysis_frame(frame: np.ndarray) -> tuple[np.ndarray, float, float]:
        max_width = int(os.getenv("STORE_INTEL_ANALYSIS_WIDTH", "960"))
        if max_width <= 0:
            return frame, 1.0, 1.0
        height, width = frame.shape[:2]
        if width <= max_width:
            return frame, 1.0, 1.0
        ratio = max_width / max(width, 1)
        resized_height = max(1, int(round(height * ratio)))
        resized = cv2.resize(frame, (max_width, resized_height), interpolation=cv2.INTER_AREA)
        return resized, width / max_width, height / resized_height

    @staticmethod
    def _scale_detections(detections: list[dict[str, Any]], scale_x: float, scale_y: float) -> list[dict[str, Any]]:
        if scale_x == 1.0 and scale_y == 1.0:
            return detections
        scaled: list[dict[str, Any]] = []
        for detection in detections:
            x, y, w, h = detection.get("bbox") or [0, 0, 0, 0]
            updated = dict(detection)
            updated["bbox"] = [
                int(round(x * scale_x)),
                int(round(y * scale_y)),
                int(round(w * scale_x)),
                int(round(h * scale_y)),
            ]
            updated["analysis_scaled_from"] = detection.get("bbox")
            scaled.append(updated)
        return scaled

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
    def _filter_person_like_detections(detections: list[dict[str, Any]], frame: np.ndarray) -> list[dict[str, Any]]:
        height, width = frame.shape[:2]
        filtered: list[dict[str, Any]] = []
        for detection in detections:
            bbox = detection.get("bbox") or [0, 0, 0, 0]
            if not FrameAnalyzerAgent._is_plausible_customer_bbox(bbox, (height, width, frame.shape[2] if len(frame.shape) > 2 else 1)):
                continue
            if FrameAnalyzerAgent._looks_like_flat_display(frame, bbox):
                continue
            filtered.append(detection)
        return filtered

    @staticmethod
    def _is_plausible_customer_bbox(bbox: list[int], shape: tuple[int, ...]) -> bool:
        height, width = shape[:2]
        x, y, w, h = bbox
        if w <= 0 or h <= 0:
            return False
        frame_area = max(width * height, 1)
        area_ratio = (w * h) / frame_area
        aspect = h / max(w, 1)
        lower_edge = (y + h) / max(height, 1)
        width_ratio = w / max(width, 1)
        if area_ratio < 0.012 or area_ratio > 0.42:
            return False
        if aspect < 1.25 or aspect > 5.2:
            return False
        if width_ratio > 0.42:
            return False
        # Posters, faces on walls, and TV ads usually occupy the upper wall and
        # do not extend toward the floor like a real shopper track.
        if lower_edge < 0.48:
            return False
        return True

    @staticmethod
    def _looks_like_flat_display(frame: np.ndarray, bbox: list[int]) -> bool:
        x, y, w, h = bbox
        height, width = frame.shape[:2]
        x1, y1 = max(int(x), 0), max(int(y), 0)
        x2, y2 = min(int(x + w), width), min(int(y + h), height)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return True
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        edge_density = float(np.mean(cv2.Canny(gray, 60, 140) > 0))
        color_std = float(np.mean(np.std(crop.reshape(-1, crop.shape[-1]), axis=0))) if len(crop.shape) == 3 else float(np.std(gray))
        lower_edge = (y + h) / max(height, 1)
        aspect = h / max(w, 1)
        if lower_edge < 0.52 and edge_density > 0.12:
            return True
        return edge_density > 0.18 and color_std > 54 and aspect < 2.15

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
    def _add_service_zone_people(detections: list[dict[str, Any]], frame: np.ndarray, layout: dict[str, Any]) -> list[dict[str, Any]]:
        height, width = frame.shape[:2]
        additions: list[dict[str, Any]] = []
        service_zones = layout.get("staff_service_zones") or layout.get("staff_zones", [])
        for index, zone_id in enumerate(service_zones, start=1):
            zone = layout.get("zones", {}).get(zone_id)
            if not zone:
                continue
            x1 = max(0, int(zone["x1"] * width))
            y1 = max(0, int(zone["y1"] * height))
            x2 = min(width, int(zone["x2"] * width))
            y2 = min(height, int(zone["y2"] * height))
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 45, 120)
            kernel = np.ones((7, 5), np.uint8)
            mask = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:4]:
                cx, cy, cw, ch = cv2.boundingRect(contour)
                if cw <= 0 or ch <= 0:
                    continue
                area_ratio = (cw * ch) / max((x2 - x1) * (y2 - y1), 1)
                aspect = ch / max(cw, 1)
                lower_edge = (cy + ch) / max((y2 - y1), 1)
                if area_ratio < 0.035 or area_ratio > 0.65 or aspect < 1.15 or lower_edge < 0.35:
                    continue
                bbox = [x1 + cx, y1 + cy, cw, ch]
                if any(FrameAnalyzerAgent._bbox_overlap_ratio(bbox, existing["bbox"]) > 0.25 for existing in detections + additions):
                    continue
                additions.append(
                    {
                        "track_id": 900 + index,
                        "bbox": bbox,
                        "confidence": 0.7,
                        "model": f"service_zone_fallback:{zone_id}",
                    }
                )
                break
        return detections + additions

    @staticmethod
    def _bbox_overlap_ratio(first: list[int], second: list[int]) -> float:
        ax, ay, aw, ah = first
        bx, by, bw, bh = second
        ix1, iy1 = max(ax, bx), max(ay, by)
        ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        intersection = (ix2 - ix1) * (iy2 - iy1)
        return intersection / max(min(aw * ah, bw * bh), 1)

    @staticmethod
    def _remove_mirror_zone_detections(
        detections: list[dict[str, Any]],
        shape: tuple[int, ...],
        layout: dict[str, Any],
        second: int = 0,
    ) -> list[dict[str, Any]]:
        mirror_polygons = FrameAnalyzerAgent._mirror_zone_polygons(layout, shape)
        if not mirror_polygons:
            return detections
        threshold = float(layout.get("mirror_overlap_threshold", 0.45))
        kept: list[dict[str, Any]] = []
        for detection in detections:
            bbox = detection.get("bbox") or [0, 0, 0, 0]
            match = FrameAnalyzerAgent._matching_mirror_zone(bbox, mirror_polygons, shape, threshold)
            if match is None:
                kept.append(detection)
                continue
            zone_id, overlap_ratio = match
            logging.info(
                "frame_analyzer.mirror_detection_ignored",
                extra={
                    "zone_id": zone_id,
                    "bbox": bbox,
                    "overlap_ratio": round(overlap_ratio, 3),
                    "frame_second": second,
                    "track_id": detection.get("track_id"),
                },
            )
        return kept

    @staticmethod
    def _mirror_zone_polygons(layout: dict[str, Any], shape: tuple[int, ...]) -> list[tuple[str, np.ndarray]]:
        height, width = shape[:2]
        polygons: list[tuple[str, np.ndarray]] = []
        for zone_id, zone in (layout.get("mirror_zones") or {}).items():
            if "points" in zone:
                points = zone["points"]
            elif {"x1", "y1", "x2", "y2"}.issubset(zone):
                points = [
                    [zone["x1"], zone["y1"]],
                    [zone["x2"], zone["y1"]],
                    [zone["x2"], zone["y2"]],
                    [zone["x1"], zone["y2"]],
                ]
            else:
                continue
            polygon = np.array(
                [[int(round(float(px) * width)), int(round(float(py) * height))] for px, py in points],
                dtype=np.int32,
            )
            if len(polygon) >= 3:
                polygons.append((zone_id, polygon))
        return polygons

    @staticmethod
    def _matching_mirror_zone(
        bbox: list[int],
        mirror_polygons: list[tuple[str, np.ndarray]],
        shape: tuple[int, ...],
        threshold: float,
    ) -> tuple[str, float] | None:
        x, y, w, h = [int(value) for value in bbox]
        if w <= 0 or h <= 0:
            return None
        center = (float(x + w / 2), float(y + h / 2))
        best_match: tuple[str, float] | None = None
        for zone_id, polygon in mirror_polygons:
            overlap_ratio = FrameAnalyzerAgent._bbox_polygon_overlap_ratio(bbox, polygon, shape)
            center_inside = cv2.pointPolygonTest(polygon, center, False) >= 0
            if center_inside or overlap_ratio >= threshold:
                if best_match is None or overlap_ratio > best_match[1]:
                    best_match = (zone_id, overlap_ratio)
        return best_match

    @staticmethod
    def _bbox_polygon_overlap_ratio(bbox: list[int], polygon: np.ndarray, shape: tuple[int, ...]) -> float:
        frame_height, frame_width = shape[:2]
        x, y, w, h = [int(value) for value in bbox]
        x1, y1 = max(x, 0), max(y, 0)
        x2, y2 = min(x + w, frame_width), min(y + h, frame_height)
        if x2 <= x1 or y2 <= y1:
            return 0.0
        crop_width, crop_height = x2 - x1, y2 - y1
        shifted = polygon.copy()
        shifted[:, 0] -= x1
        shifted[:, 1] -= y1
        mask = np.zeros((crop_height, crop_width), dtype=np.uint8)
        cv2.fillPoly(mask, [shifted], 255)
        return float(np.count_nonzero(mask)) / max(crop_width * crop_height, 1)

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
        for zone_id, zone in FrameAnalyzerAgent._ordered_zones(layout):
            if zone["x1"] <= cx <= zone["x2"] and zone["y1"] <= cy <= zone["y2"]:
                return zone_id
        return "UNKNOWN"

    @staticmethod
    def _ordered_zones(layout: dict[str, Any]) -> list[tuple[str, dict[str, float]]]:
        zones = layout.get("zones", {})
        priority_names = []
        priority_names.extend(layout.get("staff_service_zones") or layout.get("staff_zones", []))
        priority_names.extend(layout.get("checkout_zones", []))
        priority_names.extend(layout.get("entry_zones", ["ENTRY"]))
        priority_names.extend(layout.get("exit_zones", ["EXIT"]))
        priority_names.extend(layout.get("product_zones", []))
        seen: set[str] = set()
        ordered: list[tuple[str, dict[str, float]]] = []
        for zone_id in priority_names:
            if zone_id in zones and zone_id not in seen:
                ordered.append((zone_id, zones[zone_id]))
                seen.add(zone_id)
        ordered.extend((zone_id, zone) for zone_id, zone in zones.items() if zone_id not in seen)
        return ordered

    @staticmethod
    def _normalized_center(bbox: list[int], shape: tuple[int, ...]) -> tuple[float, float]:
        height, width = shape[:2]
        x, y, w, h = bbox
        return ((x + w / 2) / max(width, 1), (y + h / 2) / max(height, 1))

    @staticmethod
    def _center_distance(first: tuple[float, float], current: tuple[float, float]) -> float:
        return ((first[0] - current[0]) ** 2 + (first[1] - current[1]) ** 2) ** 0.5

    @staticmethod
    def _entry_zone(zone: str, layout: dict[str, Any]) -> str:
        entry_zones = set(layout.get("entry_zones", ["ENTRY"]))
        return zone if zone in entry_zones else "ENTRY"

    @staticmethod
    def _exit_zone(previous_zone: str | None, layout: dict[str, Any]) -> str:
        exit_zones = list(layout.get("exit_zones", ["EXIT"]))
        if previous_zone in set(exit_zones):
            return previous_zone
        return exit_zones[0] if exit_zones else "EXIT"

    @staticmethod
    def _should_emit_exit(previous_zone: str | None, layout: dict[str, Any]) -> bool:
        doorway_zones = set(layout.get("exit_zones", ["EXIT"])) | set(layout.get("entry_zones", ["ENTRY"]))
        return previous_zone in doorway_zones

    @staticmethod
    def _is_staff(zone: str, bbox: list[int], shape: tuple[int, ...], layout: dict[str, Any]) -> bool:
        if zone in set(layout.get("staff_service_zones") or layout.get("staff_zones", [])):
            return True
        if zone in set(layout.get("staff_zones", [])):
            x, _, _, _ = bbox
            width = shape[1]
            return x / max(width, 1) > 0.78
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
            "metadata": {
                "bbox": detection.get("bbox"),
                "model": detection.get("model"),
                "role_confidence": role_confidence,
                "annotation_policy": "person_floor_track_not_wall_poster_or_tv",
                "frame_analysis": {
                    "sampled_at_video_second": second,
                    "method": "one_representative_frame_per_second",
                    "purpose": "fast_business_impact_annotation",
                },
            },
        }
