# ──────────────────────────────────────────────────────────────────────────────
# esef-data-fetcher  —  European ESEF financial data downloader
# ──────────────────────────────────────────────────────────────────────────────
# Build:  docker build -t esef-data-fetcher:latest .
# Run:    docker run -v /your/data:/esef-output esef-data-fetcher:latest
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.14-slim

# --- Non-root user for security -----------------------------------------------
# The NFS share must allow writes from UID 1000 (appuser).
# On the NFS server: chown 1000:1000 /srv/nfs/gmr
RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

# --- Python dependencies -------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && rm requirements.txt

# --- Application source -------------------------------------------------------
COPY setup.py .
COPY src/ src/

USER appuser

# --- Runtime ------------------------------------------------------------------
# --output-dir  must match the volume mountPath in the CronJob.
# --upload      streams output to NFS host via tar+ssh (requires SSH key mount).
CMD ["python", "setup.py", "--output-dir", "/esef-output", "--max-workers", "8"]
