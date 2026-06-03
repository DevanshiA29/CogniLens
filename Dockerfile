FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV STORE_INTEL_DB_PATH=/app/data/store_intel.db
ENV STORE_INTEL_USE_YOLO=0

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    ffmpeg \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY store_intel ./store_intel
COPY store_layout.json pos_transactions.csv ./

RUN pip install --no-cache-dir -e .

COPY samples ./samples

RUN mkdir -p /app/data /app/uploads /app/samples

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1

CMD ["uvicorn", "store_intel.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
