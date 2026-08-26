from __future__ import annotations

from pathlib import Path
from typing import Any
import logging
import os

import cv2

from store_intel.agents.event_generator import EventGeneratorAgent
from store_intel.agents.frame_analyzer import FrameAnalyzerAgent
from store_intel.agents.input_agent import InputAgent
from store_intel.agents.memory_store import MemoryEventStoreAgent
from store_intel.agents.metrics_agent import IntelligenceMetricsAgent
from store_intel.demo import create_demo_video
from store_intel.orchestration import AgentRunState, OrchestrationLimitError, fallback_summary, json_state, telemetry_step


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
        state = AgentRunState()
        if replace_store:
            self.store.clear_store(store_id)
            logging.info("pipeline.store_cleared", extra={"store_id": store_id})
        total_steps = 6
        try:
            metadata = telemetry_step(
                state,
                step=1,
                total=total_steps,
                agent="InputAgent",
                tool="inspect_video",
                inputs=json_state(video_path=str(video_path), store_id=store_id, camera_id=camera_id),
                run=lambda: self.input_agent.inspect_video(video_path, store_id, camera_id, timestamp_offset),
            )
            metadata = self._prepare_analysis_metadata(metadata)
            layout = telemetry_step(
                state,
                step=2,
                total=total_steps,
                agent="InputAgent",
                tool="load_store_layout",
                inputs=json_state(layout_path=str(layout_path) if layout_path else None),
                run=lambda: self.input_agent.load_store_layout(layout_path),
            )
            observations = telemetry_step(
                state,
                step=3,
                total=total_steps,
                agent="FrameAnalyzerAgent",
                tool="analyze_video",
                inputs=json_state(
                    video_id=metadata["video_id"],
                    duration_sec=metadata["duration_sec"],
                    analysis_duration_sec=metadata.get("analysis_duration_sec", metadata["duration_sec"]),
                    analysis_chunks=metadata.get("analysis_chunks", []),
                    layout_name=layout.get("layout_name"),
                ),
                run=lambda: self.analyzer.analyze_video(metadata, layout),
            )
            events = telemetry_step(
                state,
                step=4,
                total=total_steps,
                agent="EventGeneratorAgent",
                tool="from_observations",
                inputs=json_state(observation_count=len(observations)),
                run=lambda: self.generator.from_observations(observations),
            )
            self._load_pos_transactions(pos_path, store_id)
            inserted = telemetry_step(
                state,
                step=5,
                total=total_steps,
                agent="MemoryEventStoreAgent",
                tool="ingest_events",
                inputs=json_state(store_id=store_id, event_count=len(events)),
                run=lambda: self.store.ingest_events(events),
            )
        except OrchestrationLimitError as exc:
            logging.warning("pipeline.fallback", extra={"reason": str(exc), "store_id": store_id})
            return fallback_summary(state, str(exc))
        logging.info("pipeline.processed_video", extra={"store_id": store_id, "camera_id": camera_id, "observations": len(observations), "events": len(events), "inserted": inserted})
        telemetry_step(
            state,
            step=6,
            total=total_steps,
            agent="MemoryEventStoreAgent",
            tool="set_current_video",
            inputs=json_state(store_id=store_id, video_id=metadata["video_id"]),
            run=lambda: self.store.set_current_video(
                store_id=store_id,
                video_path=str(Path(video_path).resolve()),
                camera_id=camera_id,
                duration_sec=int(metadata["duration_sec"]),
                fps=int(metadata["fps"]),
                updated_at=metadata["timestamp_offset"],
            ),
        )
        return json_state(
            status="ok",
            input=metadata,
            observations=len(observations),
            events_generated=len(events),
            events_inserted=inserted,
            metrics=self.metrics_agent.metrics(store_id),
            orchestration={
                "max_iterations": state.max_iterations,
                "iterations_used": state.iteration,
                "steps_completed": state.steps_completed,
            },
        )

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
        state = AgentRunState()
        for index, video in enumerate(videos, start=1):
            try:
                state.next_iteration("process_folder")
            except OrchestrationLimitError as exc:
                logging.warning("pipeline.folder_iteration_limit", extra={"folder": str(folder_path), "processed": len(results)})
                results.append(fallback_summary(state, str(exc)))
                break
            camera_id = self._camera_id_from_name(video, index)
            results.append(self.process_video(video, store_id, camera_id, layout_path, pos_path))
        return results

    def run_demo(self, store_id: str = "STORE_BLR_002", camera_id: str = "CAM_ENTRY_01", duration_sec: int = 8, fps: int = 10) -> dict[str, Any]:
        video_path = Path("samples") / "demo_cctv.mp4"
        force_synthetic = os.getenv("STORE_INTEL_FORCE_SYNTHETIC_DEMO") == "1"
        if force_synthetic or not self._is_usable_video(video_path):
            if not force_synthetic:
                logging.warning(
                    "pipeline.bundled_demo_unusable_falling_back_to_synthetic",
                    extra={"path": str(video_path)},
                )
            video_path = Path(os.getenv("STORE_INTEL_SYNTHETIC_DEMO_PATH", "runtime/demo_synthetic.mp4"))
            video_path = create_demo_video(video_path, duration_sec=duration_sec, fps=fps)
        return self.process_video(video_path, store_id, camera_id)

    @staticmethod
    def _is_usable_video(path: Path) -> bool:
        """Return False for missing files, empty files, and unresolved Git LFS pointer
        stubs (e.g. when the real binary failed to download), not just zero-byte files."""
        if not path.exists() or path.stat().st_size == 0:
            return False
        # A real video is always far larger than a text LFS pointer, but check the
        # pointer signature directly so this also catches small corrupt files.
        if path.stat().st_size < 4096:
            try:
                head = path.read_bytes()[:200]
                if head.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    return False
            except OSError:
                return False
        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                return False
            frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps_value = capture.get(cv2.CAP_PROP_FPS) or 0
            return frames > 0 and fps_value > 0
        finally:
            capture.release()

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
    def _cap_metadata_duration(metadata: dict[str, Any]) -> dict[str, Any]:
        return StoreIntelligencePipeline._prepare_analysis_metadata(metadata)

    @staticmethod
    def _prepare_analysis_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        max_seconds = int(os.getenv("STORE_INTEL_MAX_ANALYSIS_SECONDS", "0"))
        chunk_seconds = max(int(os.getenv("STORE_INTEL_CHUNK_SECONDS", "300")), 1)
        duration = int(metadata.get("duration_sec") or 0)
        analysis_duration = duration if max_seconds <= 0 else min(duration, max_seconds)
        prepared = dict(metadata)
        prepared["analysis_duration_sec"] = analysis_duration
        prepared["analysis_chunk_sec"] = chunk_seconds
        prepared["analysis_chunks"] = StoreIntelligencePipeline._analysis_chunks(analysis_duration, chunk_seconds)
        prepared["chunks"] = [
            f"{chunk['start_sec']}-{chunk['end_sec']}s"
            for chunk in prepared["analysis_chunks"]
        ]
        if max_seconds <= 0 or duration <= max_seconds:
            return prepared
        prepared["original_duration_sec"] = duration
        prepared["duration_sec"] = max_seconds
        prepared["analysis_capped"] = True
        print(
            f"[STEP 1/6] Agent [InputAgent] capped analysis window from {duration}s to {max_seconds}s",
            flush=True,
        )
        logging.info(
            "pipeline.analysis_duration_capped",
            extra={"video_id": metadata.get("video_id"), "original_duration_sec": duration, "duration_sec": max_seconds},
        )
        return prepared

    @staticmethod
    def _analysis_chunks(duration_sec: int, chunk_seconds: int) -> list[dict[str, int]]:
        if duration_sec <= 0:
            return []
        chunks: list[dict[str, int]] = []
        for start in range(0, duration_sec, chunk_seconds):
            end = min(start + chunk_seconds, duration_sec)
            chunks.append({"start_sec": start, "end_sec": end, "duration_sec": end - start})
        return chunks

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
