# ────────────────────────────────────────────────────────────────────────────
# Riyadh Dictionary Image Reviewer — Hugging Face Spaces Docker build
#
# Required HF Space secrets (set in Space Settings → Variables and secrets):
#   MONGO_URI               mongodb+srv://... (Atlas)
#   MONGO_DB_NAME           riyadh_dictionary
#   SUPABASE_URL            https://xxx.supabase.co
#   SUPABASE_SERVICE_KEY    ...
#   SUPABASE_BUCKET         dictionary-images
#   OPENAI_API_KEY          sk-...
#   API_KEY                 random secret (review auth key)
#   VITE_API_KEY            same value as API_KEY (baked into frontend at build time)
#   CORS_ORIGINS            https://morooj234-riyadh-dict-images.hf.space
# ────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# ── System: Node.js 20 (build-time only) ─────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python: shared package ────────────────────────────────────────────────
COPY packages/ /app/packages/

# ── Python: backend deps ──────────────────────────────────────────────────
COPY backend/requirements.txt /app/backend/requirements.txt
WORKDIR /app/backend
RUN pip install --no-cache-dir -r requirements.txt

# ── Frontend: install npm deps ────────────────────────────────────────────
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# ── Frontend: build-time secrets ─────────────────────────────────────────
# HF Spaces injects matching secret names as Docker build ARGs automatically.
ARG VITE_API_KEY=""
ARG VITE_API_URL=""

# ── Frontend: build ───────────────────────────────────────────────────────
COPY frontend/ /app/frontend/
RUN VITE_API_URL="${VITE_API_URL}" VITE_API_KEY="${VITE_API_KEY}" npm run build

# ── Backend: application code ─────────────────────────────────────────────
WORKDIR /app
COPY backend/app/ /app/backend/app/

# ── Runtime ───────────────────────────────────────────────────────────────
# HF Spaces requires listening on 0.0.0.0:7860
WORKDIR /app/backend
EXPOSE 7860
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
