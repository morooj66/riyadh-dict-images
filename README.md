---
title: Riyadh Dictionary Image Reviewer
emoji: 📚
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# Riyadh Dictionary — Image Pipeline (v2)

End-to-end image generation and review system for the Riyadh Dictionary entries (اسم آلة category and beyond).

## Stack

| Layer | Tech |
|---|---|
| **Generation (batch)** | Google Colab + `dictionary_prompts` package |
| **Backend / regenerate** | FastAPI on Railway |
| **Frontend / reviewer** | React + Vite on Vercel |
| **Image storage** | Supabase Storage (stateless, public bucket) |
| **Metadata** | MongoDB Atlas |
| **Backup** | Google Sheets (one-way sync from MongoDB) |

## Repository layout

```
packages/dictionary_prompts/   ← shared prompt-building logic (Colab + FastAPI)
backend/                       ← FastAPI app + scripts (migration, indexes, sync)
frontend/                      ← React reviewer (next phase)
notebooks/                     ← Colab generation notebook
MIGRATION_GUIDE.md             ← step-by-step data migration walkthrough
```

## Build order (current phase: 1 — Migration)

1. ✅ **Shared prompt package** — `packages/dictionary_prompts/`
2. ✅ **MongoDB schema + indexes** — `backend/scripts/setup_mongo_indexes.py`
3. ✅ **Migration script** — `backend/scripts/migrate_sheets_to_mongo.py`
4. ⬜ **FastAPI backend** — entries / review / regenerate endpoints
5. ⬜ **Google Sheets one-way sync** — periodic backup from Mongo
6. ⬜ **React reviewer** — replaces Streamlit
7. ⬜ **Updated Colab notebook** — writes directly to Mongo + Supabase

## Quick start (current phase)

See [`MIGRATION_GUIDE.md`](./MIGRATION_GUIDE.md) for the full walkthrough.

TL;DR:
```bash
cd backend
cp .env.example .env                            # fill in real values
pip install -r requirements.txt
python3 -m scripts.setup_mongo_indexes          # one-time
python3 -m scripts.migrate_sheets_to_mongo --dry-run --limit 10   # preview
python3 -m scripts.migrate_sheets_to_mongo                          # for real
```
