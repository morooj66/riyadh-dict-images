# ────────────────────────────────────────────────────────────────────────────
# Riyadh Dictionary Image Reviewer — Hugging Face Spaces Docker build
# Multi-stage: Node builds frontend → Python serves everything on port 7860
#
# Required HF Space secrets (Settings → Variables and secrets):
#   MONGO_URI, MONGO_DB_NAME, SUPABASE_URL, SUPABASE_SERVICE_KEY,
#   SUPABASE_BUCKET, OPENAI_API_KEY, API_KEY, VITE_API_KEY, CORS_ORIGINS
# ────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Build React/Vite frontend ───────────────────────────────────
FROM node:24-slim AS frontend-builder

# Build-time secrets (HF Spaces injects matching secret names as build ARGs)
ARG VITE_API_KEY=""
ARG VITE_API_URL=""

WORKDIR /app/frontend
# Copy all frontend source at once, then install + build in one layer
COPY frontend/ ./
RUN npm install && \
    VITE_API_URL="${VITE_API_URL}" VITE_API_KEY="${VITE_API_KEY}" \
    ./node_modules/.bin/vite build

# ── Stage 2: Python backend (runtime image) ───────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Shared prompt package
COPY packages/ /app/packages/

# Backend Python deps
COPY backend/requirements.txt /app/backend/requirements.txt
WORKDIR /app/backend
RUN pip install --no-cache-dir -r requirements.txt

# Backend application code
COPY backend/app/ /app/backend/app/

# Copy built frontend from Stage 1
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# ── Runtime ───────────────────────────────────────────────────────────────
WORKDIR /app/backend
EXPOSE 7860
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
