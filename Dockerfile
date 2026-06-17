# ────────────────────────────────────────────────────────────────────────────
# Riyadh Dictionary Image Reviewer — Hugging Face Spaces Docker build
# Multi-stage: Node builds frontend → Python serves everything on port 7860
#
# Required HF Space secrets (Settings → Variables and secrets):
#   MONGO_URI               mongodb+srv://... (Atlas)
#   MONGO_DB_NAME           riyadh_dictionary
#   SUPABASE_URL            https://xxx.supabase.co
#   SUPABASE_SERVICE_KEY    ...
#   SUPABASE_BUCKET         dictionary-images
#   OPENAI_API_KEY          sk-...
#   API_KEY                 auth key for the review UI
#   VITE_API_KEY            same value as API_KEY (baked into frontend at build time)
#   CORS_ORIGINS            https://morooj234-riyadh-dict-images.hf.space
# ────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Build React/Vite frontend ───────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend

# Install deps (cached if package.json unchanged)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build-time secrets injected by HF Spaces as Docker build ARGs
ARG VITE_API_KEY=""
ARG VITE_API_URL=""

# Copy source and build
COPY frontend/ ./
RUN VITE_API_URL="${VITE_API_URL}" VITE_API_KEY="${VITE_API_KEY}" npm run build

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

# Copy built frontend from Stage 1 (no Node.js needed at runtime)
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# ── Runtime ───────────────────────────────────────────────────────────────
# HF Spaces requires listening on 0.0.0.0:7860
WORKDIR /app/backend
EXPOSE 7860
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
