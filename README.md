---
title: CogniLens
sdk: docker
app_port: 8000
pinned: false
---

# Agentic Store Intelligence

End-to-end CCTV-to-retail-analytics system with separate agents for input, frame analysis, event generation, event memory, timestamp queries, APIs, and dashboard output.

Dashboard preview: run the app and open `http://localhost:8000` to view the live CogniLens interface. Local CCTV uploads and runtime databases are intentionally excluded from the repository.

## What It Does

- Accepts uploaded videos, local video files, or folders of CCTV clips.
- Extracts metadata, FPS, duration, camera/store IDs, and one-second chunks.
- Analyzes each second with optional YOLO/Ultralytics, otherwise OpenCV plus deterministic demo fallback.
- Emits structured events: `ENTRY`, `EXIT`, `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON`, and `REENTRY`.
- Stores events in SQLite tables: `events`, `sessions`, `visitor_tracks`, `zone_dwell`, `pos_transactions`, and `anomalies`.
- Exposes metrics, funnel, heatmap, anomalies, health, event ingest, video processing, and timestamp timeline APIs.
- Serves a live dashboard with metric cards, slider timeline, zone heatmap, funnel, queue/anomaly panel, and per-second event replay.

## Assumptions

- The bundled demo clip is intentionally small and committed so **Use Demo Video** works immediately after clone.
- Uploaded CCTV videos, generated SQLite data, and local Docker volumes are runtime artifacts and are not committed.
- YOLO/Ultralytics is optional. Docker defaults to deterministic OpenCV/demo analysis with `STORE_INTEL_USE_YOLO=0` so reviewers do not need to download model weights.
- Cross-camera identity uses practical tracking/session heuristics unless stronger appearance embeddings are enabled.

## Run Locally

```bash
python3 -m pip install -e ".[dev]"
python3 -m uvicorn store_intel.api.app:app --reload --port 8000
```

Open `http://127.0.0.1:8000`, then click **Use Demo Video**.

## Run With Docker

This is the reviewer path. After cloning the repository, no database seed, model download, local video copy, or manual frontend build is required:

```bash
git clone <your-repo-url>
cd <your-project>
docker compose up --build
```

Then open:

```text
http://127.0.0.1:8000
```

The container starts the full FastAPI app and serves the dashboard, metrics API, funnel API, video upload/analysis flow, and canvas annotations without any manual setup. Click **Use Demo Video** to process the bundled demo CCTV clip and populate the dashboard.

Docker details:

- Backend entrypoint: `store_intel.api.app:app`
- Exposed port: `8000`
- SQLite database path inside the container: `/app/data/store_intel.db`
- Uploaded videos path inside the container: `/app/uploads`
- Persistent volumes:
  - `store_intel_data` -> `/app/data`
  - `store_intel_uploads` -> `/app/uploads`
- Healthcheck: `GET /health`
- Bundled demo: `samples/demo_cctv.mp4` is copied into the image so the demo button works immediately after clone.
- Default environment:
  - `STORE_INTEL_DB_PATH=/app/data/store_intel.db`
  - `STORE_INTEL_UPLOAD_DIR=/app/uploads`
  - `STORE_INTEL_USE_YOLO=0`
  - `STORE_INTEL_MAX_ANALYSIS_SECONDS=0`
  - `STORE_INTEL_CHUNK_SECONDS=300`
  - `STORE_INTEL_ANALYSIS_WIDTH=960`
  - `STORE_INTEL_FRAME_SAMPLE_SECONDS=1`

Deployment processing guardrails:

- Browser requests abort with a clear message instead of staying in an infinite processing state.
- The backend processes uploaded videos in `STORE_INTEL_CHUNK_SECONDS` windows. The default `300` seconds means long CCTV clips are analyzed in 5-minute fragments and then stitched into one timeline. `STORE_INTEL_MAX_ANALYSIS_SECONDS=0` means process the full video; set it to a positive value only when you intentionally want a safety cap.
- `STORE_INTEL_ANALYSIS_WIDTH=960` keeps hosted CPU processing responsive by analyzing resized frames and mapping observations back onto the original video coordinates.
- `STORE_INTEL_FRAME_SAMPLE_SECONDS=1` samples one representative frame per video second, giving second-by-second annotations without full-frame-rate processing cost.
- Container logs print orchestration checkpoints like `[STEP 3/6] Agent [FrameAnalyzerAgent] initiating tool [analyze_video]`.

Useful checks after startup:

```bash
python3 - <<'PY'
from urllib.request import urlopen
for path in ["health", "metrics", "funnel", "zones", "anomalies", "score"]:
    with urlopen(f"http://127.0.0.1:8000/{path}", timeout=10) as response:
        print(path, response.status)
PY
```

To reset Docker persistence during development:

```bash
docker compose down -v
```

## Deploy

See [DEPLOYMENT.md](DEPLOYMENT.md) for the recommended Render deployment, persistent disk settings, health check, and live endpoint verification commands.

## Process Footage

Use a local file:

```bash
python3 -m store_intel.cli --video /path/to/cctv.mp4 --store-id STORE_BLR_002 --camera-id CAM_ENTRY_01 --layout store_layout.json --pos pos_transactions.csv
```

Use a folder with multiple cameras:

```bash
python3 -m store_intel.cli --folder /path/to/cctv-folder --store-id STORE_BLR_002 --layout store_layout.json --pos pos_transactions.csv
```

Generate and ingest demo footage:

```bash
python3 -m store_intel.cli --demo
```

## Enable YOLO

By default, the analyzer avoids downloading model weights and uses OpenCV/demo fallback. To enable YOLO tracking:

```bash
STORE_INTEL_USE_YOLO=1 python3 -m store_intel.cli --video /path/to/cctv.mp4
```

Install the optional vision dependency first if needed:

```bash
python3 -m pip install -e ".[vision]"
```

## API

- `POST /events/ingest`
- `POST /videos/upload`
- `POST /videos/local`
- `POST /demo/run`
- `GET /stores/{id}/metrics`
- `GET /stores/{id}/funnel`
- `GET /stores/{id}/heatmap`
- `GET /stores/{id}/anomalies`
- `GET /stores/{id}/timeline?timestamp=2026-03-03T14:22:10Z`
- `GET /metrics?store_id=STORE_BLR_002`
- `GET /funnel?store_id=STORE_BLR_002`
- `GET /zones?store_id=STORE_BLR_002`
- `GET /anomalies?store_id=STORE_BLR_002`
- `GET /visitor/{visitor_id}/timeline?store_id=STORE_BLR_002`
- `GET /health`

## Tests

```bash
python3 -m pytest tests
```

The tests cover API JSON validity, event storage/session behavior, normal visitor entry/exit, re-entry handling, staff exclusion, group detection, funnel consistency, and excessive-dwell anomaly generation.
