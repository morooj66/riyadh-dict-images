#!/usr/bin/env python3
"""
Export-only JSON backup of MongoDB collections.
Does NOT import, migrate, or modify any data.
Output: backend/data/mongo_backup/{entries,images,generation_jobs}.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(ROOT / ".env")

MONGO_URI: str = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
DB_NAME: str = os.environ.get("MONGO_DB_NAME", "riyadh_dictionary")

BACKUP_DIR = ROOT / "data" / "mongo_backup"


class _BSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return {"$oid": str(obj)}
        if isinstance(obj, datetime):
            return {"$date": obj.isoformat()}
        return super().default(obj)


def export_collection(db, name: str, path: Path) -> int:
    docs = list(db[name].find({}))
    with path.open("w", encoding="utf-8") as f:
        json.dump(docs, f, cls=_BSONEncoder, ensure_ascii=False, indent=2)
    return len(docs)


def main() -> None:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]

    try:
        db.command("ping")
    except Exception as exc:
        print(f"[ERROR] Cannot connect to MongoDB: {exc}")
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = BACKUP_DIR / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    collections = ["entries", "images", "generation_jobs"]
    print(f"Backup directory: {run_dir}")
    for col in collections:
        if col not in db.list_collection_names():
            print(f"  {col}: collection not found — skipping")
            continue
        out = run_dir / f"{col}.json"
        count = export_collection(db, col, out)
        print(f"  {col}: {count} documents → {out.name}")

    print("Backup complete. No data was modified.")


if __name__ == "__main__":
    main()
