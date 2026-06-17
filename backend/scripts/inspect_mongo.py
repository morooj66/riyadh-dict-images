#!/usr/bin/env python3
"""
Read-only MongoDB inspection script.
Prints collection stats, status counts, data quality summary.
Does NOT print secrets, does NOT modify any data.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(ROOT / ".env")

MONGO_URI: str = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
DB_NAME: str = os.environ.get("MONGO_DB_NAME", "riyadh_dictionary")

_ARABIC_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")


def strip_diacritics(text: str) -> str:
    return _ARABIC_DIACRITICS_RE.sub("", text)


def db_type(uri: str) -> str:
    if uri.startswith("mongodb+srv://"):
        return "atlas"
    return "local"


def main() -> None:
    uri_type = db_type(MONGO_URI)
    print("=" * 60)
    print("MongoDB Inspection Report")
    print("=" * 60)
    print(f"DB type   : {uri_type}")
    print(f"DB name   : {DB_NAME}")

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]

    try:
        db.command("ping")
    except Exception as exc:
        print(f"\n[ERROR] Cannot connect to MongoDB: {exc}")
        sys.exit(1)

    collections = db.list_collection_names()
    print(f"Collections: {sorted(collections)}")

    # ── Entries ───────────────────────────────────────────────────────────────
    entries_count = db.entries.count_documents({})
    print(f"\nEntries count : {entries_count}")

    status_counts = Counter(
        doc["status"] for doc in db.entries.find({}, {"status": 1}) if doc.get("status")
    )
    print("Entry statuses:")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {status:25s} : {count}")

    # ── Images ────────────────────────────────────────────────────────────────
    images_count = db.images.count_documents({})
    print(f"\nImages count  : {images_count}")

    current_count = db.images.count_documents({"is_current": True})
    candidate_count = db.images.count_documents({"review_status": "candidate"})
    previous_count = db.images.count_documents({"review_status": "previous"})
    approved_img_count = db.images.count_documents({"review_status": "approved"})
    print(f"  is_current=True    : {current_count}")
    print(f"  review_status=candidate : {candidate_count}")
    print(f"  review_status=previous  : {previous_count}")
    print(f"  review_status=approved  : {approved_img_count}")

    # ── Generation jobs ───────────────────────────────────────────────────────
    jobs_count = db.generation_jobs.count_documents({}) if "generation_jobs" in collections else 0
    print(f"\nGeneration jobs : {jobs_count}")
    if jobs_count:
        job_status = Counter(
            doc["status"]
            for doc in db.generation_jobs.find({}, {"status": 1})
            if doc.get("status")
        )
        for s, c in sorted(job_status.items(), key=lambda x: -x[1]):
            print(f"  {s:20s} : {c}")

    # ── Data quality ──────────────────────────────────────────────────────────
    no_image_count = db.entries.count_documents({
        "$or": [
            {"current_image_id": {"$exists": False}},
            {"current_image_id": None},
            {"current_image_id": 0},
        ]
    })
    print(f"\nEntries with no valid current_image_id : {no_image_count}")

    approved_entries = status_counts.get("approved", 0)
    needs_review = status_counts.get("needs_review", 0)
    print(f"Approved entries    : {approved_entries}")
    print(f"Needs review        : {needs_review}")

    # Broken current_image_id = 0 (integer zero, not ObjectId)
    broken_count = db.entries.count_documents({"current_image_id": 0})
    print(f"Broken current_image_id=0 : {broken_count}")

    # Orphan images: images whose entry_id has no matching entry
    all_entry_ids = set(str(doc["_id"]) for doc in db.entries.find({}, {"_id": 1}))
    orphan_count = sum(
        1
        for doc in db.images.find({}, {"entry_id": 1})
        if str(doc.get("entry_id", "")) not in all_entry_ids
    )
    print(f"Orphan images       : {orphan_count}")

    # ── Dedupe / sidebar estimate ─────────────────────────────────────────────
    entries_list = list(db.entries.find(
        {},
        {"word": 1, "category": 1, "current_image_id": 1, "status": 1}
    ))

    # Image counts per entry
    pipeline = [{"$group": {"_id": "$entry_id", "count": {"$sum": 1}}}]
    img_counts = {str(row["_id"]): row["count"] for row in db.images.aggregate(pipeline)}

    dedupe_seen: dict[str, dict] = {}
    STATUS_RANK = {
        "needs_selection": 5, "needs_review": 4, "approved": 3,
        "generation_failed": 2, "rejected": 1, "generating": 0, "pending": 0,
    }

    for doc in entries_list:
        key = f"{strip_diacritics(doc.get('word', ''))}\0{doc.get('category', '')}"
        has_img = bool(doc.get("current_image_id"))
        ic = img_counts.get(str(doc["_id"]), 0)
        score = (
            STATUS_RANK.get(doc.get("status", ""), 0) * int(1e6)
            + (1 if has_img else 0) * int(1e4)
            + ic
        )
        current = dedupe_seen.get(key)
        if current is None or score > current["score"]:
            dedupe_seen[key] = {"doc": doc, "score": score, "has_img": has_img, "ic": ic}

    total_deduped = len(dedupe_seen)
    visible = sum(
        1 for v in dedupe_seen.values()
        if v["has_img"] or v["ic"] > 0
    )
    print(f"\nDeduplicated display groups : {total_deduped}")
    print(f"Expected sidebar visible    : {visible}  (has image or image_count > 0)")
    print("=" * 60)


if __name__ == "__main__":
    main()
