# ────────────────────────────────────────────────────────────────────────────
# Riyadh Dictionary Image Reviewer — Hugging Face Spaces Docker build
#
# Frontend is pre-built locally (frontend/dist/ committed to repo).
# Docker only needs Python to run the FastAPI backend, which serves the
# pre-built React app as static files on port 7860.
#
# Required HF Space secrets (Settings → Variables and secrets):
#   MONGO_URI               mongodb+srv://... (Atlas)
#   MONGO_DB_NAME           riyadh_dictionary
#   SUPABASE_URL            https://xxx.supabase.co
#   SUPABASE_SERVICE_KEY    ...
#   SUPABASE_BUCKET         dictionary-images
#   OPENAI_API_KEY          sk-...
#   API_KEY                 auth key (must match VITE_API_KEY baked into dist)
#   CORS_ORIGINS            https://morooj234-riyadh-dict-images.hf.space
# ────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

WORKDIR /app

# Ensure up-to-date CA certificates (required for TLS connections to Atlas)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Shared prompt package
COPY packages/ /app/packages/

# Backend Python deps
COPY backend/requirements.txt /app/backend/requirements.txt
WORKDIR /app/backend
RUN pip install --no-cache-dir -r requirements.txt

# Backend application code
COPY backend/app/ /app/backend/app/

# Pre-built React frontend (committed to repo, no Node.js needed)
COPY frontend/dist/ /app/frontend/dist/

# HF Spaces: listen on 0.0.0.0:7860
EXPOSE 7860
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
