---
title: Riyadh Dictionary Image Reviewer
emoji: 📚
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# Riyadh Dictionary — Image Review System

End-to-end image generation and review system for the Riyadh Dictionary entries.

Reviewers can browse dictionary words, view generated images, reject with a reason, regenerate using OpenAI, approve, and export results.

## Current Stack

| Layer | Tech |
|---|---|
| **Frontend** | React + Vite (TypeScript) |
| **Backend** | FastAPI (Python) |
| **Deployment** | Hugging Face Spaces — Docker |
| **Database** | MongoDB Atlas |
| **Image Storage** | Supabase Storage (`dictionary-images` bucket) |
| **AI Generation** | OpenAI image generation (`gpt-image-1`) |
| **Prompt Logic** | `packages/dictionary_prompts` (shared package) |

> **Note on original images:** Original imported images may reference Google Drive URLs.
> A migration to Supabase Storage is planned but not yet executed.
> All newly regenerated images are stored directly in Supabase.

## Features

- Browse all dictionary entries in a sidebar
- View current / original image per entry
- Zoom image preview (lightbox)
- Reject an image with a written reason
- Regenerate image using OpenAI (stores result in Supabase)
- View candidate images and select the best one
- Approve a selected image
- View full generation history per entry (attempt number, status, date, prompt, failure reason)
- Display failure reason when generation fails
- Export review results to CSV

## Repository Layout

```
packages/dictionary_prompts/   ← shared prompt-building logic
backend/                       ← FastAPI app + maintenance scripts
  app/                         ← API routers, services, config, storage
  scripts/                     ← data inspection, backup, migration utilities
frontend/                      ← React reviewer UI
  src/                         ← components, pages, API client
  dist/                        ← pre-built assets (used by Docker)
Dockerfile                     ← single-container build for HF Spaces
MIGRATION_GUIDE.md             ← historical data migration notes
```

## Local Development

```bash
# Backend
cd backend
cp .env.example .env        # fill in real values
pip install -r requirements.txt
pip install -e ../packages/dictionary_prompts
uvicorn app.main:app --reload --port 8001

# Frontend
cd frontend
cp .env.example .env        # set VITE_API_URL and VITE_API_KEY
npm install
npm run dev
```

## Environment Variables

All secrets are configured via environment variables and are **not included in this repository**.

See `backend/.env.example` and `frontend/.env.example` for the full list.

Required variables:

| Variable | Description |
|---|---|
| `MONGO_URI` | MongoDB Atlas connection string |
| `MONGO_DB_NAME` | Database name (default: `riyadh_dictionary`) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |
| `SUPABASE_BUCKET` | Storage bucket name (`dictionary-images`) |
| `OPENAI_API_KEY` | OpenAI API key |
| `API_KEY` | Shared secret between frontend and backend |
| `CORS_ORIGINS` | Allowed origins (comma-separated) |

> **Secrets are not included in this repository.**
> Environment variables must be configured separately (locally via `.env`, on Hugging Face via Space Secrets).
