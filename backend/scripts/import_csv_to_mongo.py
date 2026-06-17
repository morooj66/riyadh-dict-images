"""
Import dictionary rows from CSV into MongoDB (no Google Sheets).

Usage (from backend/):
    python -m scripts.import_csv_to_mongo --dry-run
    python -m scripts.import_csv_to_mongo
    python -m scripts.import_csv_to_mongo --csv data/import.csv --limit 10
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError
from rich.console import Console
from rich.table import Table

from app.db import Collections, get_sync_db
from app.statuses import normalize_entry_status

console = Console()
DEFAULT_CSV = Path("data/import.csv")
MIGRATION_NAME = "csv_import_v1"
DEFAULT_CATEGORY = "اسم آلة"

# Canonical field → exact CSV column header
COLUMN_MAP: dict[str, str] = {
    "word": "lemma.formRepresentations[0].form",
    "definition": "senses.definition.textRepresentations[0].form",
    "category": "senses.pos",
    "image_url": "image_url",
    "review_status": "review_status",
    "review_decision": "review_decision",
    "reviewer_name": "reviewer_name",
    "reviewed_at": "reviewed_at",
    "rejection_reason": "rejection_reason",
    "reviewer_note": "reviewer_visual_note",
    "needs_regeneration": "needs_regeneration",
    "regeneration_request_status": "regeneration_request_status",
    "repaired_prompt": "regenerated_prompt",
    "regeneration_note": "regeneration_note",
    "approved_image_url": "approved_image_url",
    "previous_image_url": "previous_image_url",
    "sheet_row_index": "sheet_row_number",
    "image_uid": "image_uid",
    "image_filename": "image_filename",
    "drive_file_id": "drive_file_id",
    "generation_status": "generation_status",
    "english_term": "english_term",
    "object_description": "object_description",
    "base_prompt": "image_prompt",
    "negative_prompt": "negative_prompt",
    "prompt_quality_note": "prompt_quality_note",
    "error_message": "error_message",
    "attempts_count": "regeneration_count",
    "last_regenerated_at": "last_regenerated_at",
    "regeneration_history": "regeneration_history",
    "prompt_repair_status": "prompt_repair_status",
    "prompt_repair_note": "prompt_repair_note",
    "test_regenerated_prompt": "test_regenerated_prompt",
    "test_regenerated_image_url": "test_regenerated_image_url",
    "test_regenerated_at": "test_regenerated_at",
    "test_regeneration_note": "test_regeneration_note",
}

CATEGORY_FALLBACK_COLUMN = "pos"


@dataclass
class ImportStats:
    total_rows: int = 0
    with_word: int = 0
    with_image_url: int = 0
    with_drive_file_id: int = 0
    with_any_image: int = 0
    unique_word_category: int = 0
    duplicate_word_category: int = 0
    skipped_existing: int = 0
    created_entries: int = 0
    created_images: int = 0
    skipped_no_word: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add_error(self, row_index: int, word: str, reason: str) -> None:
        self.errors.append({"row_index": row_index, "word": word, "reason": reason})


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def extract_row(raw: dict[str, Any], row_index: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for canonical, csv_col in COLUMN_MAP.items():
        out[canonical] = _clean(raw.get(csv_col))

    if not out.get("category"):
        out["category"] = _clean(raw.get(CATEGORY_FALLBACK_COLUMN))

    out["category"] = out.get("category") or DEFAULT_CATEGORY

    sheet_idx = out.get("sheet_row_index")
    out["sheets_row_index"] = int(sheet_idx) if sheet_idx and sheet_idx.isdigit() else row_index
    out["_csv_row_index"] = row_index
    return out


def map_entry_status(row: dict[str, Any]) -> str:
    has_image = bool(row.get("image_url") or row.get("drive_file_id"))

    decision = (row.get("review_decision") or "").lower()
    if decision == "rejected":
        return "rejected"
    if decision == "approved":
        return "approved"

    review_status = (row.get("review_status") or "").lower()
    if review_status == "regeneration_requested":
        return "generating"

    needs_regen = (row.get("needs_regeneration") or "").lower()
    if needs_regen in {"yes", "true", "1"}:
        return "generating"

    regen_status = (row.get("regeneration_request_status") or "").lower()
    if regen_status == "failed":
        return "generation_failed"

    gen_status = (row.get("generation_status") or "").lower()
    if gen_status == "failed":
        return "generation_failed"

    for raw in (review_status, gen_status):
        if raw:
            mapped = normalize_entry_status(raw, has_image=has_image)
            if mapped != "pending" or has_image:
                return mapped

    return normalize_entry_status(None, has_image=has_image)


def build_entry_doc(row: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "word": row["word"],
        "definition": row.get("definition"),
        "category": row["category"],
        "object_description": row.get("object_description"),
        "base_prompt": row.get("base_prompt"),
        "status": map_entry_status(row),
        "current_image_id": None,
        "sheets_row_index": row["sheets_row_index"],
        "notes": row.get("reviewer_note"),
        "rejection_reason": row.get("rejection_reason"),
        "reviewer_vision": row.get("reviewer_note"),
        "english_term": row.get("english_term"),
        "source": "csv_import",
        "csv_import": {
            "migration_name": MIGRATION_NAME,
            "csv_row_index": row["_csv_row_index"],
            "review_status": row.get("review_status"),
            "review_decision": row.get("review_decision"),
            "reviewer_name": row.get("reviewer_name"),
            "reviewed_at": row.get("reviewed_at"),
            "needs_regeneration": row.get("needs_regeneration"),
            "regeneration_request_status": row.get("regeneration_request_status"),
            "repaired_prompt": row.get("repaired_prompt"),
            "regeneration_note": row.get("regeneration_note"),
            "approved_image_url": row.get("approved_image_url"),
            "previous_image_url": row.get("previous_image_url"),
            "image_uid": row.get("image_uid"),
            "image_filename": row.get("image_filename"),
            "generation_status": row.get("generation_status"),
            "negative_prompt": row.get("negative_prompt"),
            "prompt_quality_note": row.get("prompt_quality_note"),
            "error_message": row.get("error_message"),
            "attempts_count": row.get("attempts_count"),
            "last_regenerated_at": row.get("last_regenerated_at"),
            "regeneration_history": row.get("regeneration_history"),
            "prompt_repair_status": row.get("prompt_repair_status"),
            "prompt_repair_note": row.get("prompt_repair_note"),
            "test_regenerated_prompt": row.get("test_regenerated_prompt"),
            "test_regenerated_image_url": row.get("test_regenerated_image_url"),
            "test_regenerated_at": row.get("test_regenerated_at"),
            "test_regeneration_note": row.get("test_regeneration_note"),
        },
        "created_at": now,
        "updated_at": now,
    }


def resolve_public_url(row: dict[str, Any]) -> Optional[str]:
    if row.get("image_url"):
        return row["image_url"]
    drive_id = row.get("drive_file_id")
    if drive_id:
        return f"https://drive.google.com/uc?id={drive_id}"
    return None


def build_image_doc(entry_id: Any, row: dict[str, Any], public_url: str) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "public_url": public_url,
        "drive_file_id": row.get("drive_file_id"),
        "prompt": row.get("base_prompt"),
        "storage_path": None,
        "generated_by": "csv_import",
        "is_current": True,
        "review_status": "current",
        "size_bytes": 0,
        "generation_meta": {
            "source": "csv_import",
            "image_uid": row.get("image_uid"),
            "image_filename": row.get("image_filename"),
            "original_image_url": row.get("image_url"),
            "original_drive_file_id": row.get("drive_file_id"),
        },
        "created_at": datetime.now(timezone.utc),
    }


def analyze_rows(rows: list[dict[str, Any]]) -> tuple[ImportStats, list[dict[str, Any]]]:
    stats = ImportStats(total_rows=len(rows))
    normalized: list[dict[str, Any]] = []

    for row_index, raw in enumerate(rows, start=1):
        row = extract_row(raw, row_index)
        normalized.append(row)
        if row.get("word"):
            stats.with_word += 1
        else:
            stats.skipped_no_word += 1
        if row.get("image_url"):
            stats.with_image_url += 1
        if row.get("drive_file_id"):
            stats.with_drive_file_id += 1
        if row.get("image_url") or row.get("drive_file_id"):
            stats.with_any_image += 1

    keys = [(r["word"], r["category"]) for r in normalized if r.get("word")]
    counts = Counter(keys)
    stats.unique_word_category = len(counts)
    stats.duplicate_word_category = sum(1 for c in counts.values() if c > 1)

    return stats, normalized


def print_dry_run_summary(
    csv_path: Path,
    stats: ImportStats,
    normalized: list[dict[str, Any]],
) -> None:
    console.print(f"\n[bold cyan]CSV dry-run summary[/bold cyan]")
    console.print(f"  source: {csv_path}")
    console.print(f"  migration: {MIGRATION_NAME} (CSV only — no Google Sheets)\n")

    table = Table(title="Counts", show_lines=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_row("Total rows", str(stats.total_rows))
    table.add_row("Rows with word", str(stats.with_word))
    table.add_row("Rows with image_url", str(stats.with_image_url))
    table.add_row("Rows with drive_file_id", str(stats.with_drive_file_id))
    table.add_row("Rows with any image ref", str(stats.with_any_image))
    table.add_row("Unique word+category", str(stats.unique_word_category))
    table.add_row("Duplicate word+category keys", str(stats.duplicate_word_category))
    table.add_row("Rows missing word (skipped)", str(stats.skipped_no_word))
    console.print(table)

    first_words = [r["word"] for r in normalized if r.get("word")][:5]
    console.print("\n[bold]First 5 words to import:[/bold]")
    for i, word in enumerate(first_words, 1):
        console.print(f"  {i}. {word}")

    console.print("\n[bold]Final COLUMN_MAP:[/bold]")
    for canonical, csv_col in COLUMN_MAP.items():
        extra = ""
        if canonical == "category":
            extra = f" (fallback: {CATEGORY_FALLBACK_COLUMN})"
        console.print(f"  {canonical} = {csv_col}{extra}")

    console.print("\n[dim]Dry-run only — nothing written to MongoDB.[/dim]")


def process_row(db, row: dict[str, Any], *, dry_run: bool, stats: ImportStats) -> None:
    word = row.get("word")
    if not word:
        stats.skipped_no_word += 1
        return

    existing = db[Collections.ENTRIES].find_one({
        "word": word,
        "category": row["category"],
    })
    if existing:
        stats.skipped_existing += 1
        return

    public_url = resolve_public_url(row)
    if not public_url and not dry_run:
        # Entry without image is still valid
        pass

    if dry_run:
        stats.created_entries += 1
        if public_url:
            stats.created_images += 1
        return

    entry_doc = build_entry_doc(row)
    try:
        entry_result = db[Collections.ENTRIES].insert_one(entry_doc)
    except DuplicateKeyError:
        stats.skipped_existing += 1
        return

    entry_id = entry_result.inserted_id
    stats.created_entries += 1

    if not public_url:
        return

    image_doc = build_image_doc(entry_id, row, public_url)
    image_result = db[Collections.IMAGES].insert_one(image_doc)
    db[Collections.ENTRIES].update_one(
        {"_id": entry_id},
        {"$set": {
            "current_image_id": image_result.inserted_id,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    stats.created_images += 1


def print_import_summary(stats: ImportStats, *, dry_run: bool) -> None:
    table = Table(title=f"Import summary {'(DRY RUN)' if dry_run else ''}", show_lines=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_row("Total rows", str(stats.total_rows))
    table.add_row("Entries created", str(stats.created_entries))
    table.add_row("Images created", str(stats.created_images))
    table.add_row("Skipped (existing)", str(stats.skipped_existing))
    table.add_row("Skipped (no word)", str(stats.skipped_no_word))
    table.add_row("Errors", str(len(stats.errors)))
    console.print()
    console.print(table)

    if stats.errors:
        err_table = Table(title="First 10 errors")
        err_table.add_column("Row")
        err_table.add_column("Word")
        err_table.add_column("Reason", overflow="fold")
        for e in stats.errors[:10]:
            err_table.add_row(str(e["row_index"]), e["word"], e["reason"])
        console.print(err_table)


def run(*, csv_path: Path, dry_run: bool, limit: Optional[int]) -> int:
    if not csv_path.exists():
        console.print(f"[bold red]CSV not found: {csv_path}[/bold red]")
        return 1

    rows = read_csv_rows(csv_path)
    stats, normalized = analyze_rows(rows)

    if dry_run:
        print_dry_run_summary(csv_path, stats, normalized)
        would_create_entries = stats.unique_word_category
        would_create_images = sum(
            1 for r in normalized
            if r.get("word") and (r.get("image_url") or r.get("drive_file_id"))
        )
        # Second pass: only count image for first occurrence of each word+category
        seen: set[tuple[str, str]] = set()
        unique_images = 0
        for r in normalized:
            if not r.get("word"):
                continue
            key = (r["word"], r["category"])
            if key in seen:
                continue
            seen.add(key)
            if r.get("image_url") or r.get("drive_file_id"):
                unique_images += 1
        preview_stats = ImportStats(
            total_rows=stats.total_rows,
            with_word=stats.with_word,
            with_image_url=stats.with_image_url,
            with_drive_file_id=stats.with_drive_file_id,
            with_any_image=stats.with_any_image,
            unique_word_category=stats.unique_word_category,
            duplicate_word_category=stats.duplicate_word_category,
            created_entries=would_create_entries,
            created_images=unique_images,
            skipped_no_word=stats.skipped_no_word,
            skipped_existing=stats.total_rows - would_create_entries - stats.skipped_no_word,
        )
        print_import_summary(preview_stats, dry_run=True)
        return 0

    db = get_sync_db()
    to_process = normalized[:limit] if limit else normalized
    import_stats = ImportStats(total_rows=len(to_process))

    for row in to_process:
        try:
            process_row(db, row, dry_run=False, stats=import_stats)
        except Exception as exc:
            import_stats.add_error(
                row["_csv_row_index"],
                row.get("word") or "?",
                f"{type(exc).__name__}: {exc}",
            )

    print_import_summary(import_stats, dry_run=False)
    return 0 if not import_stats.errors else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Import CSV → MongoDB (no Google Sheets)")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="Path to CSV (default: data/import.csv)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no writes")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N rows")
    args = parser.parse_args()
    sys.exit(run(csv_path=Path(args.csv), dry_run=args.dry_run, limit=args.limit))


if __name__ == "__main__":
    main()
