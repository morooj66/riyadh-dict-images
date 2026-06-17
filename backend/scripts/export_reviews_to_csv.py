#!/usr/bin/env python3
"""Export reviewed entries to CSV for downstream sync or reporting."""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.utils import resolve_public_image_url  # noqa: E402

OUTPUT_PATH = ROOT / "data" / "review_results_export.csv"

SUPABASE_PUBLIC_PATH = "/storage/v1/object/public/"


def _supabase_canonical_url(
    storage_path: str,
    *,
    supabase_base: str,
    supabase_bucket: str,
) -> str:
    base = supabase_base.rstrip("/")
    path = storage_path.lstrip("/")
    return f"{base}{SUPABASE_PUBLIC_PATH}{supabase_bucket}/{path}"


def normalize_export_url(
    value: str | None,
    *,
    supabase_base: str = "",
    supabase_bucket: str = "",
    storage_path: str | None = None,
) -> str:
    """Ensure exported URLs are absolute https links (Sheets-safe)."""
    raw = (value or "").strip()

    if storage_path and supabase_base and supabase_bucket:
        canonical = _supabase_canonical_url(
            storage_path,
            supabase_base=supabase_base,
            supabase_bucket=supabase_bucket,
        )
        if not raw or "supabase.co" in raw or raw.startswith("//"):
            raw = canonical

    if not raw:
        return ""

    if raw.startswith("//"):
        raw = f"https:{raw}"
    elif not raw.startswith("http://") and not raw.startswith("https://"):
        if "supabase.co" in raw or "drive.google.com" in raw or "." in raw.split("/")[0]:
            raw = f"https://{raw.lstrip('/')}"

    if raw.startswith("http://"):
        raw = f"https://{raw[len('http://'):]}"

    return raw


def image_url(
    doc: Optional[dict[str, Any]],
    *,
    supabase_base: str,
    supabase_bucket: str,
) -> str:
    if not doc:
        return ""

    public_url = doc.get("public_url")
    storage_path = doc.get("storage_path")

    if storage_path or (public_url and "supabase.co" in public_url):
        return normalize_export_url(
            public_url,
            supabase_base=supabase_base,
            supabase_bucket=supabase_bucket,
            storage_path=storage_path,
        )

    drive_id = doc.get("drive_file_id")
    if not drive_id:
        meta = doc.get("generation_meta") or {}
        drive_id = meta.get("original_drive_file_id")
    resolved = resolve_public_image_url(public_url, drive_id) or public_url or ""
    return normalize_export_url(resolved)


def entry_url(
    value: str | None,
    *,
    supabase_base: str,
    supabase_bucket: str,
) -> str:
    return normalize_export_url(
        value,
        supabase_base=supabase_base,
        supabase_bucket=supabase_bucket,
    )

COLUMNS = [
    "word",
    "definition",
    "category",
    "original_image_url",
    "previous_image_url",
    "approved_image_url",
    "selected_image_url",
    "current_image_url",
    "review_status",
    "review_decision",
    "rejection_reason",
    "reviewer_note",
    "reviewer_vision",
    "reviewed_at",
    "current_image_id",
    "selected_image_id",
    "regeneration_count",
    "latest_generation_label",
    "last_regenerated_at",
    "image_count",
    "candidate_count",
    "all_candidate_urls",
    "all_regenerate_urls",
]


def fmt_dt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_row(
    entry: dict[str, Any],
    images: list[dict[str, Any]],
    *,
    supabase_base: str,
    supabase_bucket: str,
) -> dict[str, str]:
    by_id = {img["_id"]: img for img in images}
    current_id = entry.get("current_image_id")
    selected_id = entry.get("selected_image_id")
    current_img = by_id.get(current_id) if current_id else None
    selected_img = by_id.get(selected_id) if selected_id else None

    candidate_count = sum(
        1 for img in images
        if img.get("review_status") == "candidate" or (
            not img.get("is_current") and img.get("generated_by") == "fastapi"
        )
    )
    regen_imgs = [img for img in images if img.get("generated_by") == "fastapi"]
    regen_imgs_sorted = sorted(
        regen_imgs,
        key=lambda x: x.get("generation_attempt") or 0,
        reverse=True,
    )
    regeneration_count = len(regen_imgs)

    latest_generation_label = ""
    if regen_imgs_sorted:
        latest_generation_label = regen_imgs_sorted[0].get("generation_label") or ""

    last_regenerated_at = ""
    regen_times = [img.get("created_at") for img in regen_imgs]
    if regen_times:
        last_regenerated_at = fmt_dt(max(regen_times))

    candidate_imgs = [
        img for img in images
        if img.get("review_status") == "candidate" or (
            not img.get("is_current") and img.get("generated_by") == "fastapi"
        )
    ]
    all_candidate_urls = "|".join(
        normalize_export_url(image_url(img, supabase_base=supabase_base, supabase_bucket=supabase_bucket))
        for img in candidate_imgs
        if img
    )
    all_regenerate_urls = "|".join(
        normalize_export_url(image_url(img, supabase_base=supabase_base, supabase_bucket=supabase_bucket))
        for img in sorted(regen_imgs, key=lambda x: x.get("generation_attempt") or 0)
        if img
    )

    csv_import = entry.get("csv_import") or {}
    original_url = entry.get("original_image_url") or ""

    return {
        "word": entry.get("word", ""),
        "definition": entry.get("definition") or "",
        "category": entry.get("category", ""),
        "original_image_url": entry_url(
            original_url, supabase_base=supabase_base, supabase_bucket=supabase_bucket
        ),
        "previous_image_url": entry_url(
            entry.get("previous_image_url"), supabase_base=supabase_base, supabase_bucket=supabase_bucket
        ),
        "approved_image_url": entry_url(
            entry.get("approved_image_url"), supabase_base=supabase_base, supabase_bucket=supabase_bucket
        ),
        "selected_image_url": image_url(
            selected_img, supabase_base=supabase_base, supabase_bucket=supabase_bucket
        ),
        "current_image_url": image_url(
            current_img, supabase_base=supabase_base, supabase_bucket=supabase_bucket
        ),
        "review_status": entry.get("status", ""),
        "review_decision": entry.get("review_decision") or csv_import.get("review_decision") or "",
        "rejection_reason": entry.get("rejection_reason") or "",
        "reviewer_note": entry.get("notes") or "",
        "reviewer_vision": entry.get("reviewer_vision") or "",
        "reviewed_at": fmt_dt(entry.get("reviewed_at") or csv_import.get("reviewed_at")),
        "current_image_id": str(current_id) if current_id else "",
        "selected_image_id": str(selected_id) if selected_id else "",
        "regeneration_count": str(regeneration_count),
        "latest_generation_label": latest_generation_label,
        "last_regenerated_at": last_regenerated_at,
        "image_count": str(len(images)),
        "candidate_count": str(candidate_count),
        "all_candidate_urls": all_candidate_urls,
        "all_regenerate_urls": all_regenerate_urls,
    }


def check_supabase_bucket_public(supabase_base: str, supabase_key: str, bucket: str) -> bool:
    from supabase import create_client

    client = create_client(supabase_base, supabase_key)
    buckets = client.storage.list_buckets()
    for item in buckets:
        name = item.name if hasattr(item, "name") else item.get("name")
        if name != bucket:
            continue
        public = item.public if hasattr(item, "public") else item.get("public")
        return bool(public)
    return False


def main() -> None:
    load_dotenv(ROOT / ".env")
    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    db_name = os.environ.get("MONGO_DB_NAME", "riyadh_dictionary")
    supabase_base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supabase_bucket = os.environ.get("SUPABASE_BUCKET", "dictionary-images")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    bucket_public = False
    if supabase_base and supabase_key:
        try:
            bucket_public = check_supabase_bucket_public(supabase_base, supabase_key, supabase_bucket)
        except Exception:
            bucket_public = False

    db = MongoClient(uri)[db_name]

    entries = list(db.entries.find({}).sort("updated_at", -1))
    rows: list[dict[str, str]] = []
    for entry in entries:
        images = list(db.images.find({"entry_id": entry["_id"]}))
        rows.append(
            build_row(
                entry,
                images,
                supabase_base=supabase_base,
                supabase_bucket=supabase_bucket,
            )
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    approved = sum(1 for r in rows if r.get("review_decision") == "approved")
    print(f"exported_rows={len(rows)}")
    print(f"approved_rows={approved}")
    print(f"supabase_bucket={supabase_bucket}")
    print(f"supabase_bucket_public={bucket_public}")
    print(f"output_path={OUTPUT_PATH}")


if __name__ == "__main__":
    main()
