#!/usr/bin/env python3
"""
Safe import of local MongoDB backup → MongoDB Atlas.

Usage:
    # Dry-run (default — safe, no writes):
    python3 scripts/import_to_atlas.py

    # Actual import (requires explicit flag):
    python3 scripts/import_to_atlas.py --confirm

Requirements:
    - Set ATLAS_MONGO_URI in backend/.env  (a NEW variable, separate from MONGO_URI)
    - Run backup_mongo_json.py first to create a backup
    - The most recent backup under backend/data/mongo_backup/ is used automatically

Rules:
    - Does NOT delete data from local MongoDB.
    - Does NOT overwrite existing documents in Atlas (skips by _id).
    - Dry-run shows exactly what would be inserted without doing it.
    - Actual import only runs with --confirm flag.
    - Never prints ATLAS_MONGO_URI or any secret.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bson import ObjectId
from dotenv import load_dotenv
import os
from urllib.parse import urlparse, quote, urlunparse

load_dotenv(ROOT / ".env")

BACKUP_DIR = ROOT / "data" / "mongo_backup"
COLLECTIONS = ["entries", "images", "generation_jobs"]


# ── BSON deserialization ────────────────────────────────────────────────────

def _decode_value(val: Any) -> Any:
    if isinstance(val, dict):
        if "$oid" in val:
            return ObjectId(val["$oid"])
        if "$date" in val:
            return datetime.fromisoformat(val["$date"].replace("Z", "+00:00"))
        return {k: _decode_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_decode_value(v) for v in val]
    return val


def load_backup(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [_decode_value(doc) for doc in raw]


# ── Atlas connection helper ─────────────────────────────────────────────────

def _encode_uri_password(uri: str) -> str:
    """URL-encode the password in a mongodb+srv URI if it contains special chars."""
    try:
        parsed = urlparse(uri)
        if parsed.password:
            encoded_pwd = quote(parsed.password, safe="")
            if encoded_pwd != parsed.password:
                # Rebuild URI with encoded password (never printed)
                safe_netloc = parsed.netloc.replace(
                    f":{parsed.password}@", f":{encoded_pwd}@", 1
                )
                uri = urlunparse(parsed._replace(netloc=safe_netloc))
    except Exception:
        pass
    return uri


def get_atlas_uri() -> str:
    uri = os.environ.get("ATLAS_MONGO_URI", "").strip()
    if not uri:
        print(
            "\n[ERROR] ATLAS_MONGO_URI is not set.\n"
            "Add this to backend/.env:\n"
            "  ATLAS_MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/\n"
            "(This is separate from MONGO_URI — keep your local MONGO_URI unchanged.)"
        )
        sys.exit(1)
    if not uri.startswith("mongodb+srv://") and not uri.startswith("mongodb://"):
        print("[ERROR] ATLAS_MONGO_URI must start with mongodb+srv:// or mongodb://")
        sys.exit(1)
    # Auto-encode special characters in password (e.g. !@#$)
    return _encode_uri_password(uri)


def get_db_name() -> str:
    return os.environ.get("MONGO_DB_NAME", "riyadh_dictionary")


# ── Main ────────────────────────────────────────────────────────────────────

def find_latest_backup() -> Path:
    runs = sorted(BACKUP_DIR.iterdir(), reverse=True) if BACKUP_DIR.exists() else []
    runs = [r for r in runs if r.is_dir()]
    if not runs:
        print(
            f"[ERROR] No backup found under {BACKUP_DIR}.\n"
            "Run this first:\n"
            "  python3 scripts/backup_mongo_json.py"
        )
        sys.exit(1)
    return runs[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Import local MongoDB backup to Atlas.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually perform the import. Without this flag, only a dry-run is shown.",
    )
    args = parser.parse_args()
    dry_run = not args.confirm

    backup_path = find_latest_backup()
    print("=" * 60)
    print("MongoDB Atlas Import")
    print("=" * 60)
    print(f"Backup    : {backup_path}")
    print(f"DB name   : {get_db_name()}")
    print(f"Mode      : {'DRY-RUN (no writes)' if dry_run else '⚠️  ACTUAL IMPORT'}")
    print()

    # Load all backup docs
    docs_by_col: dict[str, list[dict]] = {}
    for col in COLLECTIONS:
        path = backup_path / f"{col}.json"
        if not path.exists():
            print(f"  {col}: backup file not found — skipping")
            docs_by_col[col] = []
            continue
        docs = load_backup(path)
        docs_by_col[col] = docs
        print(f"  {col}: {len(docs)} documents in backup")

    print()
    if dry_run:
        print("DRY-RUN complete. Nothing was written.")
        print("Re-run with --confirm to perform the actual import:")
        print("  python3 scripts/import_to_atlas.py --confirm")
        return

    # ── Actual import ──────────────────────────────────────────────────────
    from pymongo import MongoClient

    atlas_uri = get_atlas_uri()
    db_name = get_db_name()

    print("Connecting to Atlas...")
    try:
        client = MongoClient(atlas_uri, serverSelectionTimeoutMS=10000)
        client.admin.command("ping")
    except Exception as exc:
        print(f"[ERROR] Cannot connect to Atlas: {exc}")
        sys.exit(1)

    atlas_db = client[db_name]
    print(f"Connected to Atlas DB: {db_name}")
    print()

    total_inserted = 0
    total_skipped = 0

    for col in COLLECTIONS:
        docs = docs_by_col.get(col, [])
        if not docs:
            print(f"  {col}: no documents — skipping")
            continue

        col_obj = atlas_db[col]

        # Find existing _ids to avoid duplicates
        existing_ids = set(
            doc["_id"] for doc in col_obj.find({}, {"_id": 1})
        )

        to_insert = [d for d in docs if d["_id"] not in existing_ids]
        skipped = len(docs) - len(to_insert)

        print(f"  {col}: {len(docs)} in backup, {skipped} already exist, {len(to_insert)} to insert")

        if to_insert:
            result = col_obj.insert_many(to_insert, ordered=False)
            inserted = len(result.inserted_ids)
            print(f"    → inserted {inserted} documents")
            total_inserted += inserted
        total_skipped += skipped

    print()
    print(f"Import complete: {total_inserted} inserted, {total_skipped} skipped (already existed)")

    # ── Verification ───────────────────────────────────────────────────────
    print()
    print("Verification:")
    for col in COLLECTIONS:
        count = atlas_db[col].count_documents({})
        backup_count = len(docs_by_col.get(col, []))
        status = "✓" if count >= backup_count else "⚠️"
        print(f"  {status} {col}: Atlas={count}  backup={backup_count}")

    # Extra checks for entries
    entries_col = atlas_db["entries"]
    broken = entries_col.count_documents({"current_image_id": 0})
    no_img = entries_col.count_documents({
        "$or": [
            {"current_image_id": {"$exists": False}},
            {"current_image_id": None},
            {"current_image_id": 0},
        ]
    })
    print(f"  broken current_image_id=0 : {broken}")
    print(f"  entries with no image     : {no_img}")

    # Orphan images
    all_entry_ids = set(str(d["_id"]) for d in entries_col.find({}, {"_id": 1}))
    orphans = sum(
        1 for d in atlas_db["images"].find({}, {"entry_id": 1})
        if str(d.get("entry_id", "")) not in all_entry_ids
    )
    print(f"  orphan images             : {orphans}")

    print()
    print("=" * 60)
    print("Next steps:")
    print("  1. Update MONGO_URI in backend/.env to your Atlas URI")
    print("  2. Restart the backend: python3 -m uvicorn app.main:app --reload --port 8000")
    print("  3. Run inspect_mongo.py to verify counts match")
    print("  4. NEVER share MONGO_URI — it contains credentials")
    print("=" * 60)


if __name__ == "__main__":
    main()
