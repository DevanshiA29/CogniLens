from __future__ import annotations

import shutil
import logging
import os
from pathlib import Path
from typing import Any

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from store_intel.agents.memory_store import MemoryEventStoreAgent
from store_intel.agents.metrics_agent import IntelligenceMetricsAgent
from store_intel.agents.query_agent import TimestampQueryAgent
from store_intel.agents.score_agent import AgentScoreAgent
from store_intel.pipeline import StoreIntelligencePipeline
from store_intel.schemas import EventBatch


def create_app(db_path: str | Path = "data/store_intel.db") -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = FastAPI(title="Agentic Store Intelligence", version="0.1.0")
    static_dir = Path(__file__).resolve().parents[1] / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    store = MemoryEventStoreAgent(db_path)
    metrics = IntelligenceMetricsAgent(store)
    query = TimestampQueryAgent(store)
    scorer = AgentScoreAgent(store)

    @app.middleware("http")
    async def log_requests(request, call_next):
        logging.info("api.request", extra={"method": request.method, "path": request.url.path})
        try:
            response = await call_next(request)
        except Exception:
            logging.exception("api.error", extra={"method": request.method, "path": request.url.path})
            raise
        logging.info("api.response", extra={"method": request.method, "path": request.url.path, "status": response.status_code})
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.get("/")
    def dashboard() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "database": str(store.db_path),
            "events": store.count("events"),
            "agents": [
                "InputAgent",
                "FrameAnalyzerAgent",
                "EventGeneratorAgent",
                "MemoryEventStoreAgent",
                "TimestampQueryAgent",
                "IntelligenceMetricsAgent",
                "DashboardAgent",
                "StaffClassifier",
                "GroupDetector",
                "AgentScoreAgent",
            ],
        }

    @app.post("/events/ingest")
    def ingest_events(batch: EventBatch) -> dict[str, int]:
        return {"inserted": store.ingest_events(batch.events), "received": len(batch.events)}

    @app.post("/videos/upload")
    async def upload_video(
        file: UploadFile = File(...),
        store_id: str = Form("STORE_BLR_002"),
        camera_id: str = Form("CAM_ENTRY_01"),
    ) -> dict[str, Any]:
        if not file.filename or Path(file.filename).suffix.lower() != ".mp4":
            raise HTTPException(status_code=400, detail="Please upload a valid MP4 video.")
        upload_dir = Path(os.getenv("STORE_INTEL_UPLOAD_DIR", "uploads"))
        upload_dir.mkdir(exist_ok=True)
        target = upload_dir / Path(file.filename).name
        with target.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        pipeline = StoreIntelligencePipeline(store.db_path)
        try:
            return pipeline.process_video(target, store_id, camera_id, replace_store=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/videos/local")
    def process_local(payload: dict[str, Any]) -> dict[str, Any]:
        pipeline = StoreIntelligencePipeline(store.db_path)
        path = payload["path"]
        store_id = payload.get("store_id", "STORE_BLR_002")
        camera_id = payload.get("camera_id", "CAM_ENTRY_01")
        try:
            if Path(path).is_dir():
                store.clear_store(store_id)
                return {"results": pipeline.process_folder(path, store_id, payload.get("layout_path"), payload.get("pos_path"))}
            return pipeline.process_video(
                path,
                store_id,
                camera_id,
                payload.get("layout_path"),
                payload.get("pos_path"),
                replace_store=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/demo/run")
    def run_demo(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        pipeline = StoreIntelligencePipeline(store.db_path)
        store.clear_store(payload.get("store_id", "STORE_BLR_002"))
        return pipeline.run_demo(
            store_id=payload.get("store_id", "STORE_BLR_002"),
            camera_id=payload.get("camera_id", "CAM_ENTRY_01"),
            duration_sec=int(payload.get("duration_sec", 8)),
            fps=int(payload.get("fps", 10)),
        )

    @app.get("/stores/{store_id}/metrics")
    def store_metrics(store_id: str) -> dict[str, Any]:
        return metrics.metrics(store_id)

    @app.get("/metrics")
    def global_metrics(store_id: str = "STORE_BLR_002") -> dict[str, Any]:
        return metrics.metrics(store_id)

    @app.get("/stores/{store_id}/funnel")
    def store_funnel(store_id: str) -> dict[str, Any]:
        return metrics.funnel(store_id)

    @app.get("/funnel")
    def global_funnel(store_id: str = "STORE_BLR_002") -> dict[str, Any]:
        return metrics.funnel(store_id)

    @app.get("/stores/{store_id}/heatmap")
    def store_heatmap(store_id: str) -> dict[str, Any]:
        return metrics.heatmap(store_id)

    @app.get("/zones")
    def global_zones(store_id: str = "STORE_BLR_002") -> dict[str, Any]:
        return metrics.zones(store_id)

    @app.get("/stores/{store_id}/anomalies")
    def store_anomalies(store_id: str) -> dict[str, Any]:
        return metrics.anomalies(store_id)

    @app.get("/anomalies")
    def global_anomalies(store_id: str = "STORE_BLR_002") -> dict[str, Any]:
        return metrics.anomalies(store_id)

    @app.get("/visitor/{visitor_id}/timeline")
    def visitor_timeline(visitor_id: str, store_id: str = "STORE_BLR_002") -> dict[str, Any]:
        return metrics.visitor_timeline(store_id, visitor_id)

    @app.get("/stores/{store_id}/agent-score")
    def store_agent_score(store_id: str) -> dict[str, Any]:
        return scorer.score(store_id)

    @app.get("/score")
    def score(store_id: str = "STORE_BLR_002") -> dict[str, Any]:
        return scorer.score(store_id)

    @app.get("/stores/{store_id}/timeline")
    def store_timeline(store_id: str, timestamp: str) -> dict[str, Any]:
        return query.at_timestamp(store_id, timestamp)

    @app.get("/stores/{store_id}/timeline/range")
    def store_timeline_range(store_id: str) -> dict[str, Any]:
        return query.range_for_store(store_id)

    @app.get("/stores/{store_id}/video/current")
    def current_video(store_id: str) -> dict[str, Any]:
        video = store.current_video(store_id)
        if not video or not Path(video["video_path"]).exists():
            raise HTTPException(status_code=404, detail="No processed video is available for this store.")
        capture = cv2.VideoCapture(video["video_path"])
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        capture.release()
        return {
            "store_id": store_id,
            "camera_id": video["camera_id"],
            "duration_sec": video["duration_sec"],
            "fps": video["fps"],
            "width": width,
            "height": height,
            "updated_at": video["updated_at"],
            "video_url": f"/stores/{store_id}/video/stream",
            "poster_url": f"/stores/{store_id}/video/poster",
            "frame_url": f"/stores/{store_id}/video/frame",
        }

    @app.get("/stores/{store_id}/video/stream")
    def stream_video(store_id: str) -> FileResponse:
        video = store.current_video(store_id)
        if not video or not Path(video["video_path"]).exists():
            raise HTTPException(status_code=404, detail="No processed video is available for this store.")
        return FileResponse(video["video_path"], media_type="video/mp4")

    @app.get("/stores/{store_id}/video/poster")
    def video_poster(store_id: str) -> Response:
        return _video_frame_response(store_id, 0)

    @app.get("/stores/{store_id}/video/frame")
    def video_frame(store_id: str, second: float = 0) -> Response:
        return _video_frame_response(store_id, second)

    def _video_frame_response(store_id: str, second: float) -> Response:
        video = store.current_video(store_id)
        if not video or not Path(video["video_path"]).exists():
            raise HTTPException(status_code=404, detail="No processed video is available for this store.")
        capture = cv2.VideoCapture(video["video_path"])
        fps = capture.get(cv2.CAP_PROP_FPS) or float(video.get("fps") or 1)
        total_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 1
        max_second = max((total_frames - 1) / max(fps, 1), 0)
        safe_second = min(max(float(second or 0), 0), max_second)
        capture.set(cv2.CAP_PROP_POS_MSEC, safe_second * 1000)
        ok, frame = capture.read()
        capture.release()
        if not ok:
            raise HTTPException(status_code=404, detail="Unable to read a preview frame from the video.")
        encoded, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not encoded:
            raise HTTPException(status_code=500, detail="Unable to encode video preview frame.")
        return Response(content=buffer.tobytes(), media_type="image/jpeg")

    return app


app = create_app(os.getenv("STORE_INTEL_DB_PATH", "data/store_intel.db"))
