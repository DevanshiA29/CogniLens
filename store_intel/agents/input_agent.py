from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import cv2


class InputAgent:
    """Receives long CCTV files, extracts metadata, and creates one-second chunks."""

    def inspect_video(
        self,
        video_path: str | Path,
        store_id: str,
        camera_id: str,
        timestamp_offset: str = "2026-03-03T14:22:10Z",
    ) -> dict[str, Any]:
        path = Path(video_path)
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            logging.error("video_ingest.open_failed", extra={"path": str(path)})
            raise ValueError(f"Unable to open video: {path}")
        fps = capture.get(cv2.CAP_PROP_FPS) or 15
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_sec = int(round(frames / fps)) if frames else 0
        capture.release()
        if duration_sec <= 0:
            logging.error("video_ingest.empty_video", extra={"path": str(path)})
            raise ValueError(f"Video has no readable frames: {path}")
        chunks = [f"{second}-{second + 1}s" for second in range(duration_sec)]
        logging.info("video_ingest.metadata", extra={"path": str(path), "store_id": store_id, "camera_id": camera_id, "fps": fps, "duration_sec": duration_sec})
        return {
            "video_id": self._video_id(path),
            "store_id": store_id,
            "camera_id": camera_id,
            "fps": int(round(fps)),
            "duration_sec": duration_sec,
            "timestamp_offset": timestamp_offset,
            "source_path": str(path),
            "chunks": chunks,
        }

    def load_store_layout(self, layout_path: str | Path | None) -> dict[str, Any]:
        if not layout_path:
            return self.default_layout()
        path = Path(layout_path)
        if not path.exists():
            return self.default_layout()
        if path.suffix.lower() in {".xlsx", ".xls"}:
            return self._layout_from_workbook(path)
        return json.loads(path.read_text())

    @staticmethod
    def default_layout() -> dict[str, Any]:
        return {
            "zones": {
                "ENTRY": {"x1": 0.0, "y1": 0.34, "x2": 0.18, "y2": 0.96},
                "EXIT": {"x1": 0.0, "y1": 0.42, "x2": 0.16, "y2": 1.0},
                "WALL_PRODUCTS": {"x1": 0.1, "y1": 0.0, "x2": 0.88, "y2": 0.22},
                "PRODUCT_AISLE": {"x1": 0.13, "y1": 0.72, "x2": 0.86, "y2": 1.0},
                "CENTER_DISPLAY": {"x1": 0.32, "y1": 0.34, "x2": 0.66, "y2": 0.72},
                "BILLING": {"x1": 0.82, "y1": 0.22, "x2": 0.96, "y2": 0.78},
                "PMU": {"x1": 0.9, "y1": 0.58, "x2": 1.0, "y2": 0.9},
            },
            "mirror_zones": {
                "ENTRY_MIRROR": {
                    "label": "Mirror / Reflection Area",
                    "points": [
                        [0.132, 0.367],
                        [0.183, 0.355],
                        [0.215, 0.385],
                        [0.217, 0.694],
                        [0.138, 0.704],
                        [0.129, 0.404],
                    ],
                },
                "RIGHT_PROMO_MIRROR": {
                    "label": "Mirror / Reflection Area",
                    "points": [
                        [0.612, 0.0],
                        [0.985, 0.0],
                        [0.985, 0.985],
                        [0.71, 0.985],
                        [0.66, 0.82],
                        [0.622, 0.56],
                    ],
                },
            },
            "mirror_overlap_threshold": 0.45,
            "entry_zones": ["ENTRY"],
            "exit_zones": ["EXIT"],
            "product_zones": ["WALL_PRODUCTS", "PRODUCT_AISLE", "CENTER_DISPLAY"],
            "staff_zones": ["BILLING", "PMU"],
            "staff_service_zones": ["BILLING", "PMU"],
            "layout_name": "Brigade Road",
        }

    def _layout_from_workbook(self, path: Path) -> dict[str, Any]:
        # The Brigade Road workbook stores the floor plan as an embedded image,
        # so we map its visible door, product assets, cash counter, and PMU into
        # normalized camera zones.
        layout = self.default_layout()
        layout["source_layout"] = str(path)
        return layout

    @staticmethod
    def _video_id(path: Path) -> str:
        digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:8]
        return f"VID_{digest}"
