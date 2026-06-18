from __future__ import annotations

import json
import shutil
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from store_intel.agents.memory_store import MemoryEventStoreAgent
from store_intel.agents.metrics_agent import IntelligenceMetricsAgent
from store_intel.agents.query_agent import TimestampQueryAgent
from store_intel.agents.score_agent import AgentScoreAgent
from store_intel.schemas import EventBatch, StoreEvent


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
    review_cache: dict[str, dict[str, Any]] = {}
    video_jobs: dict[str, dict[str, Any]] = {}
    video_jobs_lock = threading.Lock()

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
            "events": _safe_event_count(store),
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
        async_mode: bool = Form(False),
    ) -> dict[str, Any]:
        if not file.filename or Path(file.filename).suffix.lower() != ".mp4":
            raise HTTPException(status_code=400, detail="Please upload a valid MP4 video.")
        upload_dir = Path(os.getenv("STORE_INTEL_UPLOAD_DIR", "uploads"))
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / Path(file.filename).name
        with target.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        if async_mode:
            job_id = f"JOB_{uuid.uuid4().hex[:12]}"
            _set_video_job(
                video_jobs,
                video_jobs_lock,
                job_id,
                {
                    "job_id": job_id,
                    "status": "queued",
                    "store_id": store_id,
                    "camera_id": camera_id,
                    "filename": target.name,
                    "created_at": _utc_now(),
                    "updated_at": _utc_now(),
                    "message": "CCTV video accepted. Analytics agents are starting.",
                },
            )
            worker = threading.Thread(
                target=_process_video_job,
                args=(video_jobs, video_jobs_lock, job_id, store.db_path, target, store_id, camera_id),
                daemon=True,
            )
            worker.start()
            logging.info("video_job.queued", extra={"job_id": job_id, "store_id": store_id, "camera_id": camera_id})
            return {
                "status": "queued",
                "job_id": job_id,
                "store_id": store_id,
                "camera_id": camera_id,
                "message": "Upload received. Processing continues in the background.",
            }
        return _run_uploaded_video(store.db_path, target, store_id, camera_id)

    @app.get("/videos/jobs/{job_id}")
    def video_job_status(job_id: str) -> dict[str, Any]:
        with video_jobs_lock:
            job = dict(video_jobs.get(job_id) or {})
        if not job:
            raise HTTPException(status_code=404, detail="Video processing job was not found.")
        return job

    @app.post("/videos/local")
    def process_local(payload: dict[str, Any]) -> dict[str, Any]:
        from store_intel.pipeline import StoreIntelligencePipeline

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
        from store_intel.pipeline import StoreIntelligencePipeline

        pipeline = StoreIntelligencePipeline(store.db_path)
        store.clear_store(payload.get("store_id", "STORE_BLR_002"))
        return pipeline.run_demo(
            store_id=payload.get("store_id", "STORE_BLR_002"),
            camera_id=payload.get("camera_id", "CAM_ENTRY_01"),
            duration_sec=int(payload.get("duration_sec", 8)),
            fps=int(payload.get("fps", 10)),
        )

    @app.get("/demo/reviews")
    def saved_demo_reviews(store_id: str = "STORE_BLR_002") -> dict[str, Any]:
        reviews = [
            _review_summary(review)
            for review in review_cache.values()
            if review["store_id"] == store_id and Path(review["video_path"]).exists()
        ]
        reviews.sort(key=lambda review: review["saved_at"], reverse=True)
        return {"store_id": store_id, "reviews": reviews}

    @app.post("/demo/reviews")
    def save_demo_review(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        store_id = payload.get("store_id", "STORE_BLR_002")
        current = store.current_video(store_id)
        if not current or not Path(current["video_path"]).exists():
            raise HTTPException(status_code=404, detail="No processed CCTV video is available to save.")
        title = str(payload.get("title") or f"CCTV Review {current['updated_at']}")
        events = store.rows("SELECT * FROM events WHERE store_id = ? ORDER BY timestamp, event_id", (store_id,))
        if not events:
            raise HTTPException(status_code=400, detail="No analyzed events are available to save.")
        signature = _review_signature(store_id, current, events)
        for review in review_cache.values():
            if review["signature"] == signature:
                return _review_summary(review)
        saved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        review_id = f"REV_{store_id}_{saved_at}".replace(":", "").replace("-", "").replace(".", "")
        review_cache[review_id] = {
            "review_id": review_id,
            "signature": signature,
            "store_id": store_id,
            "title": title,
            "video_path": str(Path(current["video_path"]).resolve()),
            "camera_id": current["camera_id"],
            "duration_sec": int(current["duration_sec"]),
            "fps": int(current["fps"]),
            "updated_at": current["updated_at"],
            "saved_at": saved_at,
            "events": [MemoryEventStoreAgent._event_row_to_payload(event) for event in events],
        }
        return _review_summary(review_cache[review_id])

    @app.post("/demo/reviews/{review_id}/load")
    def load_demo_review(review_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        store_id = payload.get("store_id", "STORE_BLR_002")
        try:
            review = review_cache[review_id]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Saved CCTV review was not found in the temporary cache.") from exc
        if review["store_id"] != store_id or not Path(review["video_path"]).exists():
            raise HTTPException(status_code=404, detail="Saved CCTV review is no longer available in the temporary cache.")
        events = [StoreEvent(**event) for event in review["events"]]
        store.clear_store(store_id)
        inserted = store.ingest_events(events)
        store.set_current_video(
            store_id=store_id,
            video_path=review["video_path"],
            camera_id=review["camera_id"],
            duration_sec=int(review["duration_sec"]),
            fps=int(review["fps"]),
            updated_at=review["updated_at"],
        )
        return {
            "review_id": review_id,
            "store_id": store_id,
            "title": review["title"],
            "events_inserted": inserted,
            "duration_sec": int(review["duration_sec"]),
            "fps": int(review["fps"]),
        }

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
        video_path = Path(video["video_path"]) if video else None
        if not video or not video_path or not video_path.exists():
            raise HTTPException(status_code=404, detail="No processed video is available for this store.")
        import cv2

        stat = video_path.stat()
        capture = cv2.VideoCapture(str(video_path))
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
            "cache_key": f"{stat.st_mtime_ns}-{stat.st_size}",
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
        import cv2

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


def _safe_event_count(store: MemoryEventStoreAgent) -> int:
    try:
        return store.count("events")
    except Exception:
        logging.exception("health.event_count_unavailable")
        return 0


def _run_uploaded_video(db_path: str | Path, target: Path, store_id: str, camera_id: str) -> dict[str, Any]:
    from store_intel.pipeline import StoreIntelligencePipeline

    pipeline = StoreIntelligencePipeline(db_path)
    try:
        return pipeline.process_video(target, store_id, camera_id, replace_store=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _process_video_job(
    video_jobs: dict[str, dict[str, Any]],
    video_jobs_lock: threading.Lock,
    job_id: str,
    db_path: str | Path,
    target: Path,
    store_id: str,
    camera_id: str,
) -> None:
    _set_video_job(
        video_jobs,
        video_jobs_lock,
        job_id,
        {
            "status": "processing",
            "updated_at": _utc_now(),
            "message": "Analyzing CCTV frames and generating retail insights.",
        },
    )
    try:
        result = _run_uploaded_video(db_path, target, store_id, camera_id)
    except Exception as exc:
        logging.exception("video_job.failed", extra={"job_id": job_id, "store_id": store_id})
        _set_video_job(
            video_jobs,
            video_jobs_lock,
            job_id,
            {
                "status": "failed",
                "updated_at": _utc_now(),
                "message": str(exc),
                "error": str(exc),
            },
        )
        return
    _set_video_job(
        video_jobs,
        video_jobs_lock,
        job_id,
        {
            "status": "completed",
            "updated_at": _utc_now(),
            "message": "Processing complete. Retail insights are ready.",
            "result": result,
        },
    )
    logging.info("video_job.completed", extra={"job_id": job_id, "store_id": store_id, "events": result.get("events_inserted")})


def _set_video_job(
    video_jobs: dict[str, dict[str, Any]],
    video_jobs_lock: threading.Lock,
    job_id: str,
    updates: dict[str, Any],
) -> None:
    with video_jobs_lock:
        current = dict(video_jobs.get(job_id) or {"job_id": job_id})
        current.update(updates)
        video_jobs[job_id] = current


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _review_signature(store_id: str, video: dict[str, Any], events: list[dict[str, Any]]) -> str:
    event_ids = [event["event_id"] for event in events]
    payload = {
        "store_id": store_id,
        "video_path": str(Path(video["video_path"]).resolve()),
        "updated_at": video["updated_at"],
        "duration_sec": video["duration_sec"],
        "fps": video["fps"],
        "event_count": len(event_ids),
        "first_event": event_ids[0] if event_ids else "",
        "last_event": event_ids[-1] if event_ids else "",
    }
    return json.dumps(payload, sort_keys=True)


def _review_summary(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_id": review["review_id"],
        "store_id": review["store_id"],
        "title": review["title"],
        "video_path": review["video_path"],
        "camera_id": review["camera_id"],
        "duration_sec": review["duration_sec"],
        "fps": review["fps"],
        "updated_at": review["updated_at"],
        "saved_at": review["saved_at"],
        "events": len(review["events"]),
        "cache": "temporary",
    }


app = create_app(os.getenv("STORE_INTEL_DB_PATH", "data/store_intel.db"))
