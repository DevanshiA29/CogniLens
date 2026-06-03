from __future__ import annotations

from pathlib import Path
from typing import Any
import logging

from store_intel.agents.event_generator import EventGeneratorAgent
from store_intel.agents.frame_analyzer import FrameAnalyzerAgent
from store_intel.agents.input_agent import InputAgent
from store_intel.agents.memory_store import MemoryEventStoreAgent
from store_intel.agents.metrics_agent import IntelligenceMetricsAgent
from store_intel.demo import create_demo_video


class StoreIntelligencePipeline:
    """Coordinates the input, analyzer, event, memory, and metrics agents."""

    def __init__(self, db_path: str | Path = "data/store_intel.db") -> None:
        self.store = MemoryEventStoreAgent(db_path)
        self.input_agent = InputAgent()
        self.analyzer = FrameAnalyzerAgent()
        self.generator = EventGeneratorAgent()
        self.metrics_agent = IntelligenceMetricsAgent(self.store)

    def process_video(
        self,
        video_path: str | Path,
        store_id: str,
        camera_id: str,
        layout_path: str | Path | None = None,
        pos_path: str | Path | None = None,
        timestamp_offset: str = "2026-03-03T14:22:10Z",
        replace_store: bool = False,
    ) -> dict[str, Any]:
        if replace_store:
            self.store.clear_store(store_id)
            logging.info("pipeline.store_cleared", extra={"store_id": store_id})
        metadata = self.input_agent.inspect_video(video_path, store_id, camera_id, timestamp_offset)
        layout = self.input_agent.load_store_layout(layout_path)
        observations = self.analyzer.analyze_video(metadata, layout)
        events = self.generator.from_observations(observations)
        self._load_pos_transactions(pos_path, store_id)
        inserted = self.store.ingest_events(events)
        logging.info("pipeline.processed_video", extra={"store_id": store_id, "camera_id": camera_id, "observations": len(observations), "events": len(events), "inserted": inserted})
        self.store.set_current_video(
            store_id=store_id,
            video_path=str(Path(video_path).resolve()),
            camera_id=camera_id,
            duration_sec=int(metadata["duration_sec"]),
            fps=int(metadata["fps"]),
            updated_at=metadata["timestamp_offset"],
        )
        return {
            "input": metadata,
            "observations": len(observations),
            "events_generated": len(events),
            "events_inserted": inserted,
            "metrics": self.metrics_agent.metrics(store_id),
        }

    def process_folder(
        self,
        folder: str | Path,
        store_id: str,
        layout_path: str | Path | None = None,
        pos_path: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        folder_path = Path(folder)
        videos = sorted(
            path for path in folder_path.iterdir() if path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv"}
        )
        results = []
        for index, video in enumerate(videos, start=1):
            camera_id = self._camera_id_from_name(video, index)
            results.append(self.process_video(video, store_id, camera_id, layout_path, pos_path))
        return results

    def run_demo(self, store_id: str = "STORE_BLR_002", camera_id: str = "CAM_ENTRY_01", duration_sec: int = 8, fps: int = 10) -> dict[str, Any]:
        video_path = create_demo_video(Path("samples") / "demo_cctv.mp4", duration_sec=duration_sec, fps=fps)
        return self.process_video(video_path, store_id, camera_id)

    def _load_pos_transactions(self, pos_path: str | Path | None, store_id: str) -> None:
        if not pos_path:
            return
        path = Path(pos_path)
        if not path.exists():
            return
        import pandas as pd

        data = pd.read_csv(path)
        with self.store.connect() as conn:
            for index, row in data.iterrows():
                transaction_id = str(row.get("transaction_id", f"POS_{index}"))
                timestamp = str(row.get("timestamp"))
                amount = float(row.get("amount", 0))
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pos_transactions(transaction_id, store_id, timestamp, amount, metadata)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (transaction_id, store_id, timestamp, amount, "{}"),
                )

    @staticmethod
    def _camera_id_from_name(path: Path, index: int) -> str:
        stem = path.stem.upper()
        if "ENTRY" in stem:
            return "CAM_ENTRY_01"
        if "BILL" in stem or "POS" in stem:
            return "CAM_BILLING_01"
        if "MAIN" in stem or "FLOOR" in stem:
            return "CAM_MAIN_01"
        return f"CAM_{index:02d}"
