# ────────────────────────────────────────────────────────────────────────────
# Riyadh Dictionary Image Reviewer — Hugging Face Spaces Docker build
#
# This Dockerfile:
#   1. Installs Python backend dependencies
#   2. Installs the shared dictionary_prompts package
#   3. Builds the React/Vite frontend (bakes VITE_API_KEY at build time)
#   4. Runs FastAPI on port 7860 (HF Spaces default)
#      FastAPI serves both the API and the built frontend (SPA)
#
# Required HF Space secrets / environment variables:
#   MONGO_URI               mongodb+srv://... (Atlas)
#   MONGO_DB_NAME           riyadh_dictionary
#   SUPABASE_URL            https://xxx.supabase.co
#   SUPABASE_SERVICE_KEY    ...
#   SUPABASE_BUCKET         dictionary-images
#   OPENAI_API_KEY          sk-...
#   API_KEY                 random secret (review auth key)
#   VITE_API_KEY            same value as API_KEY (needed at build time for frontend)
#   CORS_ORIGINS            https://<your-space>.hf.space
# ────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Install Node.js 20 (needed only at build time for the frontend)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python: install shared package first (cached layer) ──────────────────
COPY packages/ /app/packages/

# ── Python: install backend dependencies ──────────────────────────────────
COPY backend/requirements.txt /app/backend/requirements.txt
# Run from backend/ so the relative path -e ../packages/... resolves correctly
WORKDIR /app/backend
RUN pip install --no-cache-dir -r requirements.txt

# ── Frontend: install npm deps (cached layer — only re-runs if package.json changes) ──
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --prefer-offline

# ── Frontend: declare build-time secrets ──────────────────────────────────
# HF Spaces injects Space secrets into ARGs when they share the same name.
# VITE_API_KEY must match API_KEY (used by the React client to authenticate).
# VITE_API_URL is intentionally empty: frontend uses same-origin relative paths.
ARG VITE_API_KEY
ARG VITE_API_URL=""

# ── Frontend: build ────────────────────────────────────────────────────────
COPY frontend/ /app/frontend/
RUN VITE_API_URL="${VITE_API_URL}" VITE_API_KEY="${VITE_API_KEY}" npm run build

# ── Backend: copy application code ────────────────────────────────────────
WORKDIR /app
COPY backend/app/ /app/backend/app/

# ── Runtime ────────────────────────────────────────────────────────────────
# HF Spaces requires the app to listen on 0.0.0.0:7860
WORKDIR /app/backend
EXPOSE 7860
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
