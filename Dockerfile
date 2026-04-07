# ── stage 1: build deps ───────────────────────────────────────────────────────
FROM python:3.12-slim AS deps

WORKDIR /app

# gcc + libpq-dev needed to compile psycopg2-binary and Pillow C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libfreetype6-dev \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt


# ── stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Only the shared-library runtime deps (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libfreetype6 \
        libjpeg62-turbo \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from build stage
COPY --from=deps /usr/local/lib/python3.12/site-packages \
                 /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application source
COPY . .

# Non-root user for safety
RUN useradd -m -u 1000 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 7070

ENTRYPOINT ["bash", "docker/entrypoint.sh"]
CMD ["run"]
