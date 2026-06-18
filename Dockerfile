FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV STORE_INTEL_DB_PATH=/app/data/store_intel.db
ENV STORE_INTEL_UPLOAD_DIR=/app/uploads
ENV STORE_INTEL_USE_YOLO=0
ENV STORE_INTEL_MAX_ANALYSIS_SECONDS=0
ENV STORE_INTEL_CHUNK_SECONDS=300
ENV STORE_INTEL_ANALYSIS_WIDTH=960
ENV STORE_INTEL_FRAME_SAMPLE_SECONDS=1

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    ffmpeg \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md DESIGN.md CHOICES.md Dockerfile docker-compose.yml ./
COPY store_intel ./store_intel
COPY tests ./tests
COPY store_layout.json pos_transactions.csv ./

RUN pip install --no-cache-dir -e .

COPY samples ./samples

RUN mkdir -p /app/data /app/uploads /app/runtime/data /app/runtime/uploads /app/samples

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os; from urllib.request import urlopen; urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8000\")}/health', timeout=3).read()" || exit 1

CMD ["sh", "-c", "uvicorn store_intel.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
