"""
Safe import of previous/old dataset CSV into MongoDB.

Default: dry-run only (no writes).
Actual import requires explicit --apply flag.

Usage:
    # Dry-run (default) — shows what would happen:
    python3 scripts/import_csv_safe.py --csv backend/data/import.csv

    # Apply the import (only after reviewing dry-run report):
    python3 scripts/import_csv_safe.py --csv backend/data/import.csv --apply

Rules:
  - Entry uniqueness: exact word + category combination.
  - If entry already exists:
      • Do NOT overwrite review fields (review_status, rejection_reason, etc.)
      • Do NOT overwrite approved_image_url
      • Do NOT overwrite previous_image_url
      • Do NOT overwrite current_image_id unless it is missing and import provides one
      • Fill empty safe fields (definition, object_description, base_prompt) only
  - If entry does not exist: create it.
  - Original image from CSV:
      image_role=original, generation_attempt=0, generation_label="original",
      source=csv_import, storage_provider=google_drive, is_current=True (if no current exists)
  - Image URLs already in the images collection for that entry are skipped.
  - Nothing is deleted.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from bson import ObjectId
from pymongo import MongoClient

MONGO_URI = os.environ["MONGO_URI"]
MONGO_DB = os.environ.get("MONGO_DB_NAME", "riyadh_dictionary")

# Column aliases: (csv_column, internal_key)
COL_MAP = {
    "_id": "_id",
    "lemma.formRepresentations[0].form": "word",
    "stems[0].formRepresentations[0].form": "stem",
    "senses.definition.textRepresentations[0].form": "definition",
    "senses.pos": "pos",
    "pos": "pos_alt",
    "nonDiacriticsLemma": "word_plain",
    "image_url": "image_url",
    "drive_file_id": "drive_file_id",
    "image_filename": "image_filename",
    "object_description": "object_description",
    "image_prompt": "base_prompt",
    "review_status": "review_status",
    "review_decision": "review_decision",
    "rejection_reason": "rejection_reason",
    "reviewer_visual_note": "reviewer_visual_note",
    "approved_image_url": "approved_image_url",
    "previous_image_url": "previous_image_url",
    "english_term": "english_term",
    "senses._id": "sense_id",
    "sheet_row_number": "sheet_row_number",
    "generation_status": "generation_status",
    "image_uid": "image_uid",
}

ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")


def strip_diacritics(text: str) -> str:
    return ARABIC_DIACRITICS.sub("", text).strip()


def extract_drive_id(url_or_id: str | None) -> str | None:
    if not url_or_id:
        return None
    for pattern in (
        re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
        re.compile(r"/file/d/([a-zA-Z0-9_-]+)"),
        re.compile(r"/d/([a-zA-Z0-9_-]+)"),
    ):
        m = pattern.search(url_or_id)
        if m:
            return m.group(1)
    v = url_or_id.strip()
    return v if len(v) >= 20 and "/" not in v else None


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if u.startswith("//"):
        return "https:" + u
    return u or None


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def map_row(raw: dict[str, str]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for csv_col, key in COL_MAP.items():
        val = raw.get(csv_col, "").strip()
        if val:
            row[key] = val
    return row


def main(csv_path: Path, apply: bool) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    print("=" * 62)
    print(f"Safe CSV Import — {mode}")
    print(f"CSV      : {csv_path}")
    print(f"Database : {MONGO_DB}")
    print("=" * 62)

    if not csv_path.exists():
        print(f"\nERROR: CSV file not found: {csv_path}")
        sys.exit(1)

    rows_raw = read_csv(csv_path)
    print(f"\nRows in CSV : {len(rows_raw)}")

    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    db = client[MONGO_DB]
    entries_col = db["entries"]
    images_col = db["images"]

    # Build lookup: (word_plain, category) → existing entry
    existing_entries: dict[tuple[str, str], dict] = {}
    for doc in entries_col.find({}, {"_id": 1, "word": 1, "category": 1,
                                    "current_image_id": 1, "approved_image_url": 1,
                                    "previous_image_url": 1, "review_status": 1,
                                    "status": 1, "definition": 1,
                                    "object_description": 1, "base_prompt": 1}):
        key = (strip_diacritics(doc.get("word", "")),
               (doc.get("category") or "").strip())
        existing_entries[key] = doc

    # Build lookup: entry_id → set of known image URLs
    existing_image_urls: dict[str, set[str]] = {}
    for img in images_col.find({}, {"entry_id": 1, "public_url": 1}):
        eid = str(img["entry_id"])
        url = normalize_url(img.get("public_url")) or ""
        existing_image_urls.setdefault(eid, set()).add(url)

    stats = {
        "rows_read": len(rows_raw),
        "entries_to_create": 0,
        "entries_to_update": 0,
        "entries_skipped": 0,
        "images_to_create": 0,
        "image_urls_existing": 0,
        "duplicates_in_csv": 0,
        "missing_image_url": 0,
        "already_have_approved": 0,
        "risky": 0,
    }

    seen_keys: set[tuple[str, str]] = set()
    entries_to_create: list[dict] = []
    entries_to_update: list[dict] = []  # (entry_oid, fields_to_set)
    images_to_create: list[dict] = []

    now = datetime.now(timezone.utc)

    for raw in rows_raw:
        row = map_row(raw)
        word = row.get("word") or ""
        if not word:
            stats["entries_skipped"] += 1
            continue

        # Category: use pos or pos_alt, default to "NM"
        category = row.get("pos") or row.get("pos_alt") or "NM"
        key = (strip_diacritics(word), category.strip())

        # Duplicate in CSV
        if key in seen_keys:
            stats["duplicates_in_csv"] += 1
            print(f"  DUP in CSV: word='{word}' category='{category}'")
            continue
        seen_keys.add(key)

        image_url = normalize_url(row.get("image_url"))
        drive_id = extract_drive_id(row.get("drive_file_id")) or extract_drive_id(image_url)
        if image_url and drive_id and "drive.google.com" not in image_url:
            image_url = f"https://drive.google.com/uc?export=view&id={drive_id}"

        existing = existing_entries.get(key)

        if existing is None:
            # NEW entry
            entry_oid = ObjectId()
            entry_doc: dict[str, Any] = {
                "_id": entry_oid,
                "word": word,
                "category": category,
                "definition": row.get("definition"),
                "object_description": row.get("object_description"),
                "base_prompt": row.get("base_prompt"),
                "english_term": row.get("english_term"),
                "status": "pending",
                "review_status": "pending",
                "current_image_id": None,
                "approved_image_url": None,
                "previous_image_url": None,
                "created_at": now,
                "updated_at": now,
            }
            entries_to_create.append(entry_doc)
            stats["entries_to_create"] += 1

            if image_url:
                img_doc: dict[str, Any] = {
                    "_id": ObjectId(),
                    "entry_id": entry_oid,
                    "public_url": image_url,
                    "drive_file_id": drive_id,
                    "image_role": "original",
                    "generation_attempt": 0,
                    "generation_label": "original",
                    "source": "csv_import",
                    "storage_provider": "google_drive",
                    "review_status": "original",
                    "is_current": True,
                    "generated_by": None,
                    "prompt": row.get("base_prompt"),
                    "created_at": now,
                    "updated_at": now,
                }
                images_to_create.append(img_doc)
                stats["images_to_create"] += 1
                # We'll set current_image_id to img_doc["_id"] on apply
            else:
                stats["missing_image_url"] += 1

            if not apply:
                print(f"  [DRY] CREATE entry: '{word}' ({category})"
                      f"  image={'yes' if image_url else 'no'}")

        else:
            # EXISTING entry — only fill safe empty fields
            entry_oid = existing["_id"]
            fields_to_set: dict[str, Any] = {}

            # Safe fields: only fill if currently empty
            if not existing.get("definition") and row.get("definition"):
                fields_to_set["definition"] = row["definition"]
            if not existing.get("object_description") and row.get("object_description"):
                fields_to_set["object_description"] = row["object_description"]
            if not existing.get("base_prompt") and row.get("base_prompt"):
                fields_to_set["base_prompt"] = row["base_prompt"]
            if not existing.get("english_term") and row.get("english_term"):
                fields_to_set["english_term"] = row["english_term"]

            has_approved = bool(existing.get("approved_image_url"))
            if has_approved:
                stats["already_have_approved"] += 1

            # Image logic
            known_urls = existing_image_urls.get(str(entry_oid), set())
            if image_url:
                if image_url in known_urls:
                    stats["image_urls_existing"] += 1
                else:
                    # Check if entry has no current image at all → add as original
                    has_current = bool(existing.get("current_image_id"))
                    img_doc = {
                        "_id": ObjectId(),
                        "entry_id": entry_oid,
                        "public_url": image_url,
                        "drive_file_id": drive_id,
                        "image_role": "original",
                        "generation_attempt": 0,
                        "generation_label": "original",
                        "source": "csv_import",
                        "storage_provider": "google_drive",
                        "review_status": "original",
                        "is_current": not has_current,
                        "generated_by": None,
                        "prompt": row.get("base_prompt"),
                        "created_at": now,
                        "updated_at": now,
                    }
                    images_to_create.append(img_doc)
                    stats["images_to_create"] += 1
                    if not has_current:
                        fields_to_set["current_image_id"] = img_doc["_id"]
            else:
                stats["missing_image_url"] += 1

            if fields_to_set:
                fields_to_set["updated_at"] = now
                entries_to_update.append({"_id": entry_oid, "$set": fields_to_set})
                stats["entries_to_update"] += 1
                if not apply:
                    print(f"  [DRY] UPDATE entry: '{word}' ({category})  fields={list(fields_to_set.keys())}")
            else:
                stats["entries_skipped"] += 1

    print("\n--- Dry-Run Report ---" if not apply else "\n--- Apply Report ---")
    print(f"  Rows read                    : {stats['rows_read']}")
    print(f"  Entries to create            : {stats['entries_to_create']}")
    print(f"  Entries to update (safe)     : {stats['entries_to_update']}")
    print(f"  Entries skipped (unchanged)  : {stats['entries_skipped']}")
    print(f"  Images to create             : {stats['images_to_create']}")
    print(f"  Image URLs already existing  : {stats['image_urls_existing']}")
    print(f"  Duplicates in CSV            : {stats['duplicates_in_csv']}")
    print(f"  Missing image URL in CSV     : {stats['missing_image_url']}")
    print(f"  Entries already have approved: {stats['already_have_approved']}")

    total_writes = stats["entries_to_create"] + stats["entries_to_update"] + stats["images_to_create"]
    if total_writes == 0:
        print("\nNothing to import — all entries and images already exist.")
        return

    if not apply:
        print(f"\nDRY-RUN complete — {total_writes} write(s) pending.")
        print("Re-run with --apply to perform the actual import:")
        print(f"  python3 scripts/import_csv_safe.py --csv {csv_path} --apply")
        return

    # ── APPLY ─────────────────────────────────────────────────
    print(f"\nApplying {total_writes} write(s)…")
    created_entries = 0
    created_images = 0
    updated_entries = 0

    if entries_to_create:
        # For each new entry that has an image, wire current_image_id
        # (already set in img_doc above; entry_doc has current_image_id=None)
        # Update entry docs
        img_by_entry: dict[str, ObjectId] = {
            str(img["entry_id"]): img["_id"]
            for img in images_to_create
            if img.get("is_current")
        }
        for ed in entries_to_create:
            eid_str = str(ed["_id"])
            if eid_str in img_by_entry:
                ed["current_image_id"] = img_by_entry[eid_str]

        db["entries"].insert_many(entries_to_create, ordered=False)
        created_entries = len(entries_to_create)
        print(f"  Created entries : {created_entries}")

    if images_to_create:
        db["images"].insert_many(images_to_create, ordered=False)
        created_images = len(images_to_create)
        print(f"  Created images  : {created_images}")

    if entries_to_update:
        from pymongo import UpdateOne as MU
        ops = [MU({"_id": u["_id"]}, {"$set": u["$set"]}) for u in entries_to_update]
        result = db["entries"].bulk_write(ops, ordered=False)
        updated_entries = result.modified_count
        print(f"  Updated entries : {updated_entries}")

    print("\nImport complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Safe CSV import into MongoDB.")
    parser.add_argument("--csv", required=True, help="Path to the CSV file")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write to MongoDB (default is dry-run)")
    args = parser.parse_args()
    main(csv_path=Path(args.csv), apply=args.apply)
