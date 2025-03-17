# Sage — single-service container (uvicorn + Node for mermaid validation)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data

WORKDIR /app

# Node.js + npm (bookworm ships Node 18; mermaid 11 needs >=18). The mermaid
# validator (app/services/mermaid.py -> tools/validate_mermaid.mjs) imports
# mermaid + dompurify from node_modules at runtime, so Node must be present.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# JS deps for the mermaid validator (mermaid + dompurify)
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

# Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application
COPY app/ ./app/
COPY tools/validate_mermaid.mjs ./tools/validate_mermaid.mjs
COPY static/ ./static/
COPY .env.example ./

# Bind-mounted data dir (SQLite DB + uploads). Created here so the mount needn't pre-exist.
RUN mkdir -p /data

VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]