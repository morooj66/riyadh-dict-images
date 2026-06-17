"""
============================================================================
Riyadh Dictionary — Safe CSV import (additive only)
============================================================================

What this script does:

    Reads `backend/data/import.csv` (or a path given by --csv) and either:

      * INSERTS new entries (if the row has no match in MongoDB), or
      * FILLS IN missing safe fields on existing entries (never overwriting
        anything that already has a value).

What this script will NEVER do:

    * Delete any entry.
    * Merge or archive any entry.
    * Overwrite `original_image_url`, `previous_image_url`, or
      `approved_image_url` on an existing entry.
    * Change an entry whose status is `approved` or `needs_selection`.
    * Touch Supabase Storage. CSV import doesn't upload images — it stores
      the URL or drive_file_id as metadata only. The next regenerate-cycle
      handles real uploads.
    * Print any secret.

CSV columns (all optional except `word`):

    word              — Arabic word (required)
    meaning_ar        — Arabic definition (mapped to `definition`)
    category          — entry category, e.g. "اسم آلة" (falls back to default)
    image_url         — direct URL to the original image (e.g. Drive view URL)
    drive_file_id     — Google Drive file id (used to reconstruct URL if
                        image_url is missing)
    status            — one of the canonical statuses (English) OR left empty
                        to default to "بانتظار مراجعة" (i.e. needs_review)
    reviewer_note     — free-text note (mapped to `notes` + `reviewer_vision`)
    rejection_reason  — free-text rejection reason

Matching rule:
    Existing entry = same normalized word (diacritics stripped) AND same
    category. We never match on `_id` because the CSV doesn't carry one.

Usage (from backend/):

    # 1. Always start with a dry run. Writes nothing.
    python -m scripts.import_entries_csv --dry-run

    # 2. Then for real.
    python -m scripts.import_entries_csv

    # Custom path / size limit:
    python -m scripts.import_entries_csv --csv data/import.csv --limit 50

Exit codes:
    0 success
    1 CSV not found / bad arguments
    2 import finished with row-level errors (none of them were silent)
    3 MongoDB connection error
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pymongo.errors import PyMongoError
from rich.console import Console
from rich.table import Table

from app.config import get_settings
from app.db import Collections, get_sync_client, get_sync_db
from app.statuses import normalize_entry_status


console = Console()

DEFAULT_CSV = Path("data/import.csv")
DEFAULT_CATEGORY = "اسم آلة"
IMPORT_SOURCE_TAG = "csv_safe_import_v1"

# ============================================================================
# Field protection rules
# ============================================================================
# Fields that the importer must NEVER overwrite if they already have a value
# on an existing entry. These hold human-curated outcomes (approval results,
# reviewer trail) and losing them silently would be catastrophic.
PROTECTED_FIELDS: frozenset[str] = frozenset({
    "original_image_url",
    "previous_image_url",
    "approved_image_url",
})

# Statuses that mean "human work has been done — do not let an import
# undo it." Any entry in one of these states is fill-in-blanks only.
TERMINAL_STATUSES: frozenset[str] = frozenset({
    "approved",
    "needs_selection",
})

# Fields that are safe to fill in on existing entries IF they are currently
# empty/missing. We never overwrite a non-empty value.
SAFE_FILLABLE_FIELDS: tuple[str, ...] = (
    "definition",
    "category",
    "notes",
    "reviewer_vision",
    "rejection_reason",
    "object_description",
    "base_prompt",
)


# ============================================================================
# Helpers
# ============================================================================
_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")


def _strip_diacritics(text: str) -> str:
    if not text:
        return ""
    return _DIACRITICS_RE.sub("", text)


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Pre-built lowercase header lookup for column name matching that is
# robust to casing differences but never alters the original key.
# The first alias listed for each canonical name is the brief-specified one;
# the rest are fall-backs that match the extended dictionary export schema
# (the format actually present in data/import.csv at the time of writing).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "word": (
        "word",
        "lemma.formRepresentations[0].form",
        "lemma",
        "nonDiacriticsLemma",
    ),
    "meaning_ar": (
        "meaning_ar",
        "definition",
        "meaning",
        "senses.definition.textRepresentations[0].form",
    ),
    "category": (
        "category",
        "senses.pos",
        "pos",
    ),
    "image_url": ("image_url",),
    "drive_file_id": ("drive_file_id", "driveFileId", "drive_id"),
    "status": ("status", "review_status"),
    "reviewer_note": (
        "reviewer_note",
        "reviewer_visual_note",
        "note",
        "regeneration_note",
    ),
    "rejection_reason": ("rejection_reason",),
}


def _pick_column(raw: dict[str, Any], canonical: str) -> Optional[str]:
    """Return the first non-empty value found among any alias of `canonical`."""
    aliases = _COLUMN_ALIASES.get(canonical, (canonical,))
    for key in aliases:
        if key in raw:
            v = _clean(raw[key])
            if v is not None:
                return v
    # Case-insensitive sweep as last resort.
    lower_lookup = {k.lower(): k for k in raw.keys()}
    for alias in aliases:
        actual = lower_lookup.get(alias.lower())
        if actual:
            v = _clean(raw[actual])
            if v is not None:
                return v
    return None


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def extract_row(raw: dict[str, Any], row_index: int) -> dict[str, Any]:
    word = _pick_column(raw, "word")
    meaning = _pick_column(raw, "meaning_ar")
    category = _pick_column(raw, "category") or DEFAULT_CATEGORY
    image_url = _pick_column(raw, "image_url")
    drive_file_id = _pick_column(raw, "drive_file_id")
    status_raw = _pick_column(raw, "status")
    reviewer_note = _pick_column(raw, "reviewer_note")
    rejection_reason = _pick_column(raw, "rejection_reason")

    return {
        "_csv_row_index": row_index,
        "word": word,
        "normalized_word": _strip_diacritics(word).strip() if word else "",
        "definition": meaning,
        "category": category,
        "image_url": image_url,
        "drive_file_id": drive_file_id,
        "status_raw": status_raw,
        "reviewer_note": reviewer_note,
        "rejection_reason": rejection_reason,
    }


def derive_status(row: dict[str, Any]) -> str:
    """
    Decide the status for a NEW entry. Existing entries are never restatused
    by this script.

    Default per brief: "بانتظار مراجعة" which is the Arabic label for the
    canonical `needs_review` status used everywhere in the codebase.
    """
    has_image = bool(row.get("image_url") or row.get("drive_file_id"))
    raw = row.get("status_raw")
    # If CSV provided a value, normalize it through the project's official
    # alias table. If it's empty / unknown, fall back per has_image.
    return normalize_entry_status(raw, has_image=has_image)


def resolve_public_url(row: dict[str, Any]) -> Optional[str]:
    """
    Build a browser-friendly URL for the original image, without uploading.
    Mirrors app.utils.resolve_public_image_url for Drive ids.
    """
    if row.get("image_url"):
        return row["image_url"]
    drive_id = row.get("drive_file_id")
    if drive_id:
        return f"https://drive.google.com/uc?export=view&id={drive_id}"
    return None


def count_available_image_refs(row: dict[str, Any]) -> int:
    """Number of distinct image references this row brings (0, 1, or 2)."""
    return sum(1 for k in ("image_url", "drive_file_id") if row.get(k))


# ============================================================================
# Document builders
# ============================================================================
def build_new_entry_doc(row: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    public_url = resolve_public_url(row)
    # `image_count` is NOT a stored field in the live schema — it is computed
    # at runtime from the `images` collection. We do not introduce a new
    # schema field; instead, creating one matching `images` doc below makes
    # the runtime image_count reflect "1" naturally.
    doc: dict[str, Any] = {
        "word": row["word"],
        "definition": row.get("definition"),
        "category": row["category"],
        "status": derive_status(row),
        "current_image_id": None,
        "source": IMPORT_SOURCE_TAG,
        "csv_row_index": row["_csv_row_index"],
        "created_at": now,
        "updated_at": now,
    }
    if row.get("reviewer_note"):
        doc["notes"] = row["reviewer_note"]
        doc["reviewer_vision"] = row["reviewer_note"]
    if row.get("rejection_reason"):
        doc["rejection_reason"] = row["rejection_reason"]
    if public_url:
        # Keep the original URL on the entry. We never overwrite this field
        # on existing entries; on a brand-new entry it is safe to set.
        doc["original_image_url"] = public_url
    return doc


def build_image_doc_for_new_entry(
    entry_id: Any,
    row: dict[str, Any],
    public_url: str,
) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "public_url": public_url,
        "drive_file_id": row.get("drive_file_id"),
        "prompt": None,
        "storage_path": None,
        "generated_by": IMPORT_SOURCE_TAG,
        "is_current": True,
        "review_status": "current",
        "size_bytes": 0,
        "generation_meta": {
            "source": IMPORT_SOURCE_TAG,
            "csv_row_index": row["_csv_row_index"],
            "original_image_url": row.get("image_url"),
            "original_drive_file_id": row.get("drive_file_id"),
        },
        "created_at": datetime.now(timezone.utc),
    }


def compute_fillable_updates(
    existing: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    """
    Return ONLY the fields that:
      - the row has a value for,
      - the existing entry is missing or has an empty value for,
      - are listed in SAFE_FILLABLE_FIELDS (so we never touch protected ones).
    """
    update: dict[str, Any] = {}

    incoming = {
        "definition": row.get("definition"),
        "category": row.get("category"),
        "notes": row.get("reviewer_note"),
        "reviewer_vision": row.get("reviewer_note"),
        "rejection_reason": row.get("rejection_reason"),
    }
    for field_name in SAFE_FILLABLE_FIELDS:
        new_val = incoming.get(field_name)
        if new_val in (None, ""):
            continue
        cur_val = existing.get(field_name)
        if cur_val in (None, ""):
            update[field_name] = new_val
    return update


# ============================================================================
# Stats
# ============================================================================
@dataclass
class ImportStats:
    total_rows: int = 0
    missing_word: int = 0
    missing_image_url: int = 0
    rows_without_any_image_ref: int = 0
    csv_duplicates: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_existing_no_change: int = 0
    skipped_protected_status: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add_error(self, row_index: int, word: str, reason: str) -> None:
        self.errors.append({"row_index": row_index, "word": word, "reason": reason})


# ============================================================================
# Core processing
# ============================================================================
def process_row(
    db,
    entries_col,
    images_col,
    row: dict[str, Any],
    *,
    dry_run: bool,
    stats: ImportStats,
) -> None:
    word = row.get("word")
    if not word:
        stats.missing_word += 1
        return

    # Match on (normalized_word, category). We never use the row's _id and
    # we never look at any other field, to avoid surprising overlaps.
    normalized = row["normalized_word"]
    category = row["category"]

    # Match strategy: pre-filter with a regex that ignores diacritics in the
    # stored `word`, then verify the normalized form matches exactly. This
    # avoids loading the whole collection while still being safe against
    # diacritic variations.
    existing: Optional[dict[str, Any]] = None
    candidates = entries_col.find(
        {"category": category, "word": {"$regex": _escape_with_diacritics(normalized)}},
        {
            "_id": 1, "word": 1, "category": 1, "status": 1,
            "definition": 1, "notes": 1, "reviewer_vision": 1,
            "rejection_reason": 1, "object_description": 1, "base_prompt": 1,
            "original_image_url": 1, "previous_image_url": 1, "approved_image_url": 1,
            "current_image_id": 1,
        },
    )
    for cand in candidates:
        if _strip_diacritics(cand.get("word") or "").strip() == normalized:
            existing = cand
            break

    if existing is None:
        _insert_new(db, entries_col, images_col, row, dry_run=dry_run, stats=stats)
    else:
        _update_existing(entries_col, existing, row, dry_run=dry_run, stats=stats)


def _escape_with_diacritics(plain: str) -> str:
    """
    Build a regex like:  ق[\u064B-\u065F]*ن[\u064B-\u065F]*ا[\u064B-\u065F]*ل
    which matches the same Arabic letters with optional diacritics between.
    Mirrors app.utils.arabic_flexible_regex.
    """
    parts: list[str] = []
    for ch in plain:
        if "\u0600" <= ch <= "\u06FF":
            parts.append(re.escape(ch) + r"[\u064B-\u065F\u0670\u06D6-\u06ED]*")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _insert_new(
    db,
    entries_col,
    images_col,
    row: dict[str, Any],
    *,
    dry_run: bool,
    stats: ImportStats,
) -> None:
    if dry_run:
        stats.inserted += 1
        return

    entry_doc = build_new_entry_doc(row)
    try:
        entry_result = entries_col.insert_one(entry_doc)
    except PyMongoError as exc:
        stats.add_error(
            row["_csv_row_index"],
            row.get("word") or "?",
            f"{type(exc).__name__}: insert_one failed",
        )
        return
    entry_id = entry_result.inserted_id
    stats.inserted += 1

    public_url = resolve_public_url(row)
    if not public_url:
        return

    image_doc = build_image_doc_for_new_entry(entry_id, row, public_url)
    try:
        image_result = images_col.insert_one(image_doc)
    except PyMongoError as exc:
        # Image insert failed but entry was created — surface it as an
        # error but leave the entry; the next run can repair it without
        # creating duplicates (we match on word + category).
        stats.add_error(
            row["_csv_row_index"],
            row.get("word") or "?",
            f"{type(exc).__name__}: image insert_one failed",
        )
        return
    image_id = image_result.inserted_id

    entries_col.update_one(
        {"_id": entry_id},
        {"$set": {
            "current_image_id": image_id,
            "updated_at": datetime.now(timezone.utc),
        }},
    )


def _update_existing(
    entries_col,
    existing: dict[str, Any],
    row: dict[str, Any],
    *,
    dry_run: bool,
    stats: ImportStats,
) -> None:
    cur_status = (existing.get("status") or "").strip().lower()
    if cur_status in TERMINAL_STATUSES:
        # Brief: "do not reset approved or needs_selection status." We go
        # further and skip the whole row — touching a finished entry is
        # exactly the kind of silent mutation this importer must avoid.
        stats.skipped_protected_status += 1
        return

    updates = compute_fillable_updates(existing, row)

    # Hard guard: never touch a protected field even if some refactor adds
    # one to SAFE_FILLABLE_FIELDS by mistake.
    for protected in PROTECTED_FIELDS:
        updates.pop(protected, None)

    if not updates:
        stats.skipped_existing_no_change += 1
        return

    if dry_run:
        stats.updated += 1
        return

    updates["updated_at"] = datetime.now(timezone.utc)
    entries_col.update_one({"_id": existing["_id"]}, {"$set": updates})
    stats.updated += 1


# ============================================================================
# Driver
# ============================================================================
def run(*, csv_path: Path, dry_run: bool, limit: Optional[int]) -> int:
    if not csv_path.exists():
        console.print(f"[bold red]CSV not found:[/bold red] {csv_path}")
        return 1

    # Validate env before opening any connection.
    settings = get_settings()  # noqa: F841 — forces pydantic validation

    # Touch the DB only if we're really going to write; for dry-run we still
    # need it to detect existing entries.
    try:
        client = get_sync_client()
        client.admin.command("ping")
    except PyMongoError as exc:
        console.print(f"[bold red]MongoDB ping failed:[/bold red] {type(exc).__name__}")
        return 3

    db = get_sync_db()
    entries_col = db[Collections.ENTRIES]
    images_col = db[Collections.IMAGES]

    rows_raw = read_csv_rows(csv_path)
    normalized: list[dict[str, Any]] = []
    for i, raw in enumerate(rows_raw, start=1):
        normalized.append(extract_row(raw, i))

    if limit is not None:
        normalized = normalized[:limit]

    stats = ImportStats(total_rows=len(normalized))

    # Pre-pass counts
    for r in normalized:
        if not r.get("word"):
            stats.missing_word += 1
        if not r.get("image_url"):
            stats.missing_image_url += 1
        if count_available_image_refs(r) == 0:
            stats.rows_without_any_image_ref += 1

    # CSV-side duplicate detection (informational; we do not skip them — the
    # second occurrence will simply hit the "existing" branch and update
    # blanks, which is safe).
    keys = [
        (r["normalized_word"], r["category"])
        for r in normalized
        if r.get("word")
    ]
    counts = Counter(keys)
    stats.csv_duplicates = sum(1 for c in counts.values() if c > 1)

    # Process
    for r in normalized:
        try:
            process_row(db, entries_col, images_col, r, dry_run=dry_run, stats=stats)
        except PyMongoError as exc:
            stats.add_error(
                r["_csv_row_index"],
                r.get("word") or "?",
                f"{type(exc).__name__}: read failed",
            )
        except Exception as exc:
            stats.add_error(
                r["_csv_row_index"],
                r.get("word") or "?",
                f"{type(exc).__name__}: {exc}",
            )

    print_summary(stats, dry_run=dry_run)
    return 0 if not stats.errors else 2


def print_summary(stats: ImportStats, *, dry_run: bool) -> None:
    title = "CSV import summary" + (" (DRY RUN — nothing written)" if dry_run else "")
    t = Table(title=title, show_lines=True)
    t.add_column("Metric", style="cyan")
    t.add_column("Count", justify="right", style="green")
    t.add_row("Total rows", str(stats.total_rows))
    t.add_row("Inserted (new entries)", str(stats.inserted))
    t.add_row("Updated (filled blanks)", str(stats.updated))
    t.add_row("Skipped — existing, no blanks", str(stats.skipped_existing_no_change))
    t.add_row("Skipped — approved / needs_selection", str(stats.skipped_protected_status))
    t.add_row("Duplicate keys inside CSV", str(stats.csv_duplicates))
    t.add_row("Rows missing word", str(stats.missing_word))
    t.add_row("Rows missing image_url", str(stats.missing_image_url))
    t.add_row("Rows with no image reference at all", str(stats.rows_without_any_image_ref))
    t.add_row(
        "Errors",
        f"[red]{len(stats.errors)}[/red]" if stats.errors else "0",
    )
    console.print(t)

    if stats.errors:
        et = Table(title="First 10 errors")
        et.add_column("CSV row", style="cyan")
        et.add_column("Word")
        et.add_column("Reason", overflow="fold", style="red")
        for e in stats.errors[:10]:
            et.add_row(str(e["row_index"]), str(e["word"]), e["reason"])
        console.print(et)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safely import dictionary entries from CSV into MongoDB.",
    )
    parser.add_argument("--csv", default=str(DEFAULT_CSV),
                        help=f"CSV path (default: {DEFAULT_CSV})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only — nothing is written to MongoDB.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only the first N rows (good for smoke tests).")
    args = parser.parse_args()

    sys.exit(run(csv_path=Path(args.csv), dry_run=args.dry_run, limit=args.limit))


if __name__ == "__main__":
    main()
