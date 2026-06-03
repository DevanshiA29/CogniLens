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
        return json.loads(path.read_text())

    @staticmethod
    def default_layout() -> dict[str, Any]:
        return {
            "zones": {
                "ENTRY": {"x1": 0.0, "y1": 0.0, "x2": 0.35, "y2": 1.0},
                "AISLE_A": {"x1": 0.35, "y1": 0.0, "x2": 0.72, "y2": 1.0},
                "BILLING": {"x1": 0.72, "y1": 0.0, "x2": 1.0, "y2": 1.0},
            },
            "staff_zones": ["BILLING"],
        }

    @staticmethod
    def _video_id(path: Path) -> str:
        digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:8]
        return f"VID_{digest}"
