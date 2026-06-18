# Deployment Guide

This project is production-ready as a single Docker web service. The dashboard, APIs, video upload flow, demo video, and annotations are all served by the FastAPI app.

## Recommended Platform: Render

Use Render when you want the simplest public URL from a GitHub repository.

1. Push the latest commit to GitHub.
2. In Render, create a new Blueprint or Docker Web Service from the repository.
3. If using the Blueprint, Render reads `render.yaml`.
4. If creating the service manually, use:
   - Runtime: Docker
   - Dockerfile path: `Dockerfile`
   - Health check path: `/health`
   - Public service port: `8000`
   - Environment variables:
     - `PORT=8000`
     - `STORE_INTEL_DB_PATH=/app/runtime/data/store_intel.db`
     - `STORE_INTEL_UPLOAD_DIR=/app/runtime/uploads`
     - `STORE_INTEL_USE_YOLO=0`
     - `STORE_INTEL_MAX_ANALYSIS_SECONDS=0`
     - `STORE_INTEL_CHUNK_SECONDS=300`
     - `STORE_INTEL_ANALYSIS_WIDTH=960`
     - `STORE_INTEL_FRAME_SAMPLE_SECONDS=1`
   - Persistent disk:
     - Mount path: `/app/runtime`
     - Size: `1 GB` or larger

After deployment, open the Render URL and click **Use Demo Video**.

Processing safety on Render:

- Demo processing should complete quickly and return dashboard results.
- Uploaded videos are processed in `STORE_INTEL_CHUNK_SECONDS` windows, so long CCTV files are analyzed in 5-minute fragments and stitched into a single event timeline. `STORE_INTEL_MAX_ANALYSIS_SECONDS=0` processes the full video; set a positive value only when you intentionally want a host safety cap.
- `STORE_INTEL_ANALYSIS_WIDTH=960` downsizes frames internally for CPU analysis, then maps annotations back to the original video so playback remains full quality.
- `STORE_INTEL_FRAME_SAMPLE_SECONDS=1` extracts one representative image per video second, producing quick second-by-second retail annotations instead of processing every video frame.
- Browser requests have explicit timeouts and show **Processing Could Not Complete** instead of remaining in an infinite loading loop.
- Render logs include checkpoints like `[STEP 3/6] Agent [FrameAnalyzerAgent] initiating tool [analyze_video]`.

## Verify The Live App

Replace `https://your-service.onrender.com` with the deployed URL.

```bash
python3 - <<'PY'
from urllib.request import urlopen
import json

base = "https://your-service.onrender.com"
for path in ["health", "metrics", "funnel", "zones", "anomalies", "score"]:
    with urlopen(f"{base}/{path}", timeout=20) as response:
        body = json.loads(response.read())
        print(path, response.status, sorted(body.keys())[:8])
PY
```

Expected:

- Dashboard loads at `/`
- `/health` returns `status: ok`
- `/metrics` and `/funnel` return JSON
- **Use Demo Video** processes the bundled demo clip and updates dashboard metrics
- MP4 uploads are stored under the persistent disk path
- Long uploads do not trap the UI in a permanent processing state

## Keep-Awake Automation

The repository includes `.github/workflows/keep-space-awake.yml`, which pings the deployed `/health` endpoint every 12 hours and can be run manually from GitHub Actions. This prevents long inactivity gaps without running video analysis or consuming heavy compute. Override the target by setting the repository variable `COGNILENS_SPACE_URL`; otherwise it uses `https://conradfishe-cognilens.hf.space`.

## Local Docker Parity

Before or after cloud deployment, verify locally:

```bash
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000
```

## Notes

- Do not deploy user-uploaded CCTV videos from the local `uploads/` folder. They are intentionally ignored by Git.
- Do not commit SQLite database files from `data/`.
- `STORE_INTEL_USE_YOLO=0` keeps deployment deterministic and avoids downloading model weights during review. Set it to `1` only if the host has enough CPU/RAM and you install the optional vision dependency.
- Keep `STORE_INTEL_CHUNK_SECONDS` at `300` for hosted review. The browser polls the background job instead of waiting on one long request, and each chunk emits terminal checkpoints for observability.
- SQLite is acceptable for the hackathon demo and single-service deployment. For multi-user production traffic, migrate the event store to managed PostgreSQL.
