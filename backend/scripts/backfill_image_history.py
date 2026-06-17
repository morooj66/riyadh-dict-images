"""
Backfill missing generation_attempt / generation_label fields on existing image records.

Usage (dry-run, default — no writes):
    python3 scripts/backfill_image_history.py

Actually apply changes:
    python3 scripts/backfill_image_history.py --apply

Logic:
  • An image WITHOUT generated_by="fastapi" is treated as the original
    (image_role=original, generation_attempt=0, generation_label="original",
     source=csv_import, storage_provider=google_drive).
  • An image WITH generated_by="fastapi" is a regeneration.
    They are sorted by created_at and assigned attempt numbers 1, 2, 3, …
  • If a field already exists on the document, it is NOT overwritten.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running as: python3 scripts/backfill_image_history.py
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from pymongo import MongoClient, UpdateOne

MONGO_URI = os.environ["MONGO_URI"]
MONGO_DB = os.environ.get("MONGO_DB_NAME", "riyadh_dictionary")


def main(apply: bool) -> None:
    mode_label = "APPLY" if apply else "DRY-RUN"
    print("=" * 60)
    print(f"Backfill image history — {mode_label}")
    print(f"Database : {MONGO_DB}")
    print("=" * 60)

    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    db = client[MONGO_DB]
    images_col = db["images"]

    # Load all images
    all_images: list[dict[str, Any]] = list(images_col.find({}, {
        "_id": 1, "entry_id": 1, "generated_by": 1,
        "generation_attempt": 1, "generation_label": 1, "image_role": 1,
        "source": 1, "storage_provider": 1, "public_url": 1, "created_at": 1,
    }))

    print(f"\nTotal images scanned : {len(all_images)}")

    # Group by entry_id
    by_entry: dict[str, list[dict]] = defaultdict(list)
    for img in all_images:
        by_entry[str(img["entry_id"])].append(img)

    stats = {
        "entries_scanned": 0,
        "images_scanned": len(all_images),
        "images_needing_backfill": 0,
        "original_labels_added": 0,
        "regenerate_labels_added": 0,
        "skipped_already_set": 0,
        "conflicts": 0,
    }

    bulk_ops: list[UpdateOne] = []

    for entry_id, imgs in by_entry.items():
        stats["entries_scanned"] += 1

        # Separate originals from regenerations
        originals = [i for i in imgs if i.get("generated_by") != "fastapi"]
        regen_imgs = sorted(
            [i for i in imgs if i.get("generated_by") == "fastapi"],
            key=lambda x: x.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
        )

        # Flag conflict if multiple originals
        if len(originals) > 1:
            stats["conflicts"] += 1
            print(f"  CONFLICT: entry {entry_id} has {len(originals)} non-fastapi images — "
                  "assigning all as originals (attempt=0)")

        # Build updates for original images
        for img in originals:
            _id = img["_id"]
            update: dict[str, Any] = {}

            if "generation_attempt" not in img or img.get("generation_attempt") is None:
                update["generation_attempt"] = 0
                stats["original_labels_added"] += 1
            else:
                stats["skipped_already_set"] += 1

            if "generation_label" not in img or img.get("generation_label") is None:
                update["generation_label"] = "original"

            if "image_role" not in img or img.get("image_role") is None:
                update["image_role"] = "original"

            if "source" not in img or img.get("source") is None:
                update["source"] = "csv_import"

            if "storage_provider" not in img or img.get("storage_provider") is None:
                # Detect Google Drive URLs
                url = img.get("public_url", "")
                if "drive.google.com" in (url or "") or "drive.google" in (url or ""):
                    update["storage_provider"] = "google_drive"
                else:
                    update["storage_provider"] = "unknown"

            if update:
                stats["images_needing_backfill"] += 1
                bulk_ops.append(UpdateOne({"_id": _id}, {"$set": update}))
                if not apply:
                    print(f"  [DRY] original  entry={entry_id}  id={_id}  → {update}")

        # Build updates for regenerated images
        for attempt_idx, img in enumerate(regen_imgs, start=1):
            _id = img["_id"]
            update = {}

            if "generation_attempt" not in img or img.get("generation_attempt") is None:
                update["generation_attempt"] = attempt_idx
                stats["regenerate_labels_added"] += 1
            else:
                stats["skipped_already_set"] += 1

            if "generation_label" not in img or img.get("generation_label") is None:
                update["generation_label"] = f"regenerate_{attempt_idx}"

            if "image_role" not in img or img.get("image_role") is None:
                update["image_role"] = "candidate"

            if "source" not in img or img.get("source") is None:
                update["source"] = "openai_regenerate"

            if "storage_provider" not in img or img.get("storage_provider") is None:
                update["storage_provider"] = "supabase"

            if update:
                stats["images_needing_backfill"] += 1
                bulk_ops.append(UpdateOne({"_id": _id}, {"$set": update}))
                if not apply:
                    print(f"  [DRY] regen     entry={entry_id}  id={_id}  attempt={attempt_idx}  → {update}")

    print("\n--- Report ---")
    print(f"  Entries scanned          : {stats['entries_scanned']}")
    print(f"  Images scanned           : {stats['images_scanned']}")
    print(f"  Images needing backfill  : {stats['images_needing_backfill']}")
    print(f"  Original labels to add   : {stats['original_labels_added']}")
    print(f"  Regenerate labels to add : {stats['regenerate_labels_added']}")
    print(f"  Already set (skipped)    : {stats['skipped_already_set']}")
    print(f"  Conflicts (multi-orig)   : {stats['conflicts']}")
    print(f"  Bulk write ops           : {len(bulk_ops)}")

    if not bulk_ops:
        print("\nNothing to backfill. All images already have required fields.")
        return

    if not apply:
        print(f"\nDRY-RUN complete — no changes were written.")
        print(f"Re-run with --apply to apply {len(bulk_ops)} update(s):")
        print(f"  python3 scripts/backfill_image_history.py --apply")
        return

    # Actually apply
    result = images_col.bulk_write(bulk_ops, ordered=False)
    print(f"\nAPPLY complete.")
    print(f"  Modified : {result.modified_count}")
    print(f"  Matched  : {result.matched_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill image history fields.")
    parser.add_argument("--apply", action="store_true", help="Write changes to MongoDB")
    args = parser.parse_args()
    main(apply=args.apply)
