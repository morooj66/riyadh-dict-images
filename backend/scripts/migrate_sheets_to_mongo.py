"""
============================================================================
Riyadh Dictionary — Sheets → MongoDB + Supabase migration
============================================================================

What this does, in order, for each row in your existing Google Sheet:

    1. Read row data (word, definition, drive_file_id, prompt, etc.)
    2. Check if entry already exists in MongoDB → SKIP (idempotent)
    3. Download image bytes from Google Drive (if drive_file_id present)
    4. Upload to Supabase Storage at a stable path
    5. Insert `entries` document
    6. Insert `images` document, link as current
    7. Save checkpoint (so we can resume on failure)

Safety guarantees:
    - Idempotent: re-running won't duplicate anything (uses upserts + checks)
    - Resumable: --resume picks up from the last successful checkpoint
    - Non-destructive: never modifies your Google Sheet
    - Dry-run mode: --dry-run prints what WOULD happen, writes nothing
    - Per-row error handling: a bad row doesn't kill the whole migration

Usage:
    # 1. Preview first (recommended)
    python -m scripts.migrate_sheets_to_mongo --dry-run --limit 10

    # 2. Real migration with small batch to verify
    python -m scripts.migrate_sheets_to_mongo --limit 20

    # 3. Full migration
    python -m scripts.migrate_sheets_to_mongo

    # 4. Resume if interrupted
    python -m scripts.migrate_sheets_to_mongo --resume

Before running:
    - Run setup_mongo_indexes.py first
    - Confirm column mapping in COLUMN_MAP below matches your sheet
    - Make sure your service account has Viewer access on the Drive folder
"""
from __future__ import annotations

import argparse
import io
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import gspread
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from rich.console import Console
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn, MofNCompleteColumn,
)
from rich.table import Table
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.db import get_sync_db, Collections
from app.statuses import normalize_entry_status
from app.storage import SupabaseStorage


console = Console()
MIGRATION_NAME = "sheets_to_mongo_v1"

# ============================================================================
# COLUMN MAPPING — ADJUST THESE TO MATCH YOUR ACTUAL SHEET HEADERS
# ============================================================================
# The keys are the canonical fields we'll use in MongoDB.
# The values are the exact column headers as they appear in your Google Sheet.
# Any missing column is just treated as None — won't crash the migration.
COLUMN_MAP: dict[str, str] = {
    "word":              "word",
    "definition":        "definition",
    "category":          "category",
    "object_description": "object_description",
    "image_prompt":      "image_prompt",
    "drive_file_id":     "drive_file_id",
    "image_url":         "image_url",
    "status":            "status",
    "rejection_reason":  "rejection_reason",
    "reviewer_note":     "reviewer_note",
}

# Fallback category if not present in the sheet
DEFAULT_CATEGORY = "اسم آلة"

# ============================================================================
# Data structures
# ============================================================================
@dataclass
class MigrationStats:
    total_rows: int = 0
    skipped_existing: int = 0
    created_entries: int = 0
    images_uploaded: int = 0
    images_skipped_no_drive_id: int = 0
    errors: list[dict] = field(default_factory=list)

    def add_error(self, row_index: int, word: str, reason: str) -> None:
        self.errors.append({
            "row_index": row_index,
            "word": word,
            "reason": reason,
        })


# ============================================================================
# Google clients
# ============================================================================
def _google_scopes() -> list[str]:
    return [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]


def get_sheets_client():
    settings = get_settings()
    creds = service_account.Credentials.from_service_account_file(
        settings.google_sa_keyfile, scopes=_google_scopes(),
    )
    return gspread.authorize(creds)


def get_drive_service():
    settings = get_settings()
    creds = service_account.Credentials.from_service_account_file(
        settings.google_sa_keyfile, scopes=_google_scopes(),
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ============================================================================
# Drive download
# ============================================================================
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def download_drive_image(drive_service, file_id: str) -> tuple[bytes, str]:
    """
    Download image bytes from Drive. Returns (data, mime_type).
    Retries on transient failures.
    """
    meta = drive_service.files().get(fileId=file_id, fields="mimeType,name").execute()
    mime = meta.get("mimeType", "image/png")

    request = drive_service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    return buf.getvalue(), mime


# ============================================================================
# Row → documents
# ============================================================================
def normalize_row(raw: dict[str, Any], row_index: int) -> dict[str, Any]:
    """Translate raw sheet columns → canonical fields using COLUMN_MAP."""
    out: dict[str, Any] = {}
    for canonical, sheet_col in COLUMN_MAP.items():
        val = raw.get(sheet_col, "")
        if isinstance(val, str):
            val = val.strip()
        out[canonical] = val or None
    out["sheets_row_index"] = row_index
    out["category"] = out.get("category") or DEFAULT_CATEGORY
    return out


def build_entry_doc(row: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    has_image = bool(row.get("drive_file_id") or row.get("image_url"))
    return {
        "word": row["word"],
        "definition": row.get("definition"),
        "category": row["category"],
        "object_description": row.get("object_description"),
        "status": normalize_entry_status(row.get("status"), has_image=has_image),
        "current_image_id": None,
        "sheets_row_index": row["sheets_row_index"],
        "notes": row.get("reviewer_note"),
        "rejection_reason": row.get("rejection_reason"),
        "source": "sheets_migration",
        "created_at": now,
        "updated_at": now,
    }


def build_image_doc(entry_id, row: dict[str, Any], upload) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "prompt": row.get("image_prompt"),
        "storage_path": upload.storage_path,
        "public_url": upload.public_url,
        "generated_by": "colab",
        "generation_meta": {
            "object_description": row.get("object_description"),
            "source": "sheets_migration",
            "original_drive_file_id": row.get("drive_file_id"),
            "original_image_url": row.get("image_url"),
        },
        "is_current": True,
        "size_bytes": upload.size_bytes,
        "created_at": datetime.now(timezone.utc),
    }


def _import_from_image_url(storage: SupabaseStorage, entry_id, image_url: str):
    """Upload remote image URL to Supabase, or reference existing Supabase public URL."""
    import httpx
    from app.storage import UploadResult

    bucket = storage._bucket
    marker = f"/object/public/{bucket}/"
    if marker in image_url:
        storage_path = image_url.split(marker, 1)[1]
        return UploadResult(storage_path=storage_path, public_url=image_url, size_bytes=0)

    with httpx.Client(timeout=30) as client:
        resp = client.get(image_url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/png")
        ext = "png" if "png" in content_type else "jpg"
        storage_path = f"entries/{entry_id}_v1.{ext}"
        return storage.upload_bytes(resp.content, storage_path, content_type=content_type)


# ============================================================================
# Checkpoint helpers
# ============================================================================
def load_checkpoint(db) -> int:
    """Return the last successfully processed row_index (0 if none)."""
    doc = db[Collections.MIGRATION_CHECKPOINTS].find_one({"migration_name": MIGRATION_NAME})
    return doc["last_row_index"] if doc else 0


def save_checkpoint(db, last_row_index: int) -> None:
    db[Collections.MIGRATION_CHECKPOINTS].find_one_and_update(
        {"migration_name": MIGRATION_NAME},
        {
            "$set": {
                "migration_name": MIGRATION_NAME,
                "last_row_index": last_row_index,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


# ============================================================================
# Core processing of one row
# ============================================================================
def process_row(
    db,
    storage: SupabaseStorage,
    drive_service,
    raw_row: dict[str, Any],
    row_index: int,
    *,
    dry_run: bool,
    stats: MigrationStats,
) -> None:
    row = normalize_row(raw_row, row_index)

    word = row.get("word")
    if not word:
        stats.add_error(row_index, "(empty)", "Missing word")
        return

    # Idempotency check: does this entry already exist?
    existing = db[Collections.ENTRIES].find_one({
        "word": word,
        "category": row["category"],
    })
    if existing:
        stats.skipped_existing += 1
        return

    if dry_run:
        stats.created_entries += 1
        if row.get("drive_file_id") or row.get("image_url"):
            stats.images_uploaded += 1
        else:
            stats.images_skipped_no_drive_id += 1
        return

    # 1. Insert entry doc first
    entry_doc = build_entry_doc(row)
    try:
        entry_result = db[Collections.ENTRIES].insert_one(entry_doc)
    except DuplicateKeyError:
        # Race condition / unique index hit - treat as existing
        stats.skipped_existing += 1
        return
    entry_id = entry_result.inserted_id
    stats.created_entries += 1

    # 2. Handle image (optional - some rows may not have one yet)
    drive_id = row.get("drive_file_id")
    image_url = row.get("image_url")

    if not drive_id and not image_url:
        stats.images_skipped_no_drive_id += 1
        return

    try:
        if drive_id:
            if drive_service is None:
                stats.add_error(row_index, word, "drive_file_id requires Google Drive access")
                return
            data, mime = download_drive_image(drive_service, drive_id)
            ext = "png" if "png" in mime else "jpg" if "jpeg" in mime or "jpg" in mime else "png"
            storage_path = f"entries/{entry_id}_v1.{ext}"
            upload = storage.upload_bytes(data, storage_path, content_type=mime)
        else:
            upload = _import_from_image_url(storage, entry_id, image_url)

        image_doc = build_image_doc(entry_id, row, upload)
        image_result = db[Collections.IMAGES].insert_one(image_doc)

        db[Collections.ENTRIES].update_one(
            {"_id": entry_id},
            {"$set": {
                "current_image_id": image_result.inserted_id,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        stats.images_uploaded += 1

    except Exception as e:
        stats.add_error(row_index, word, f"Image fail: {type(e).__name__}: {e}")


# ============================================================================
# Main driver
# ============================================================================
def run(*, dry_run: bool, limit: Optional[int], resume: bool, batch_checkpoint: int) -> int:
    settings = get_settings()
    if not settings.sheets_spreadsheet_id or not settings.sheets_worksheet_name:
        console.print("[bold red]SHEETS_SPREADSHEET_ID and SHEETS_WORKSHEET_NAME must be set in .env[/bold red]")
        return 1

    console.print(f"[bold cyan]Migration: {MIGRATION_NAME}[/bold cyan]")
    console.print(f"  dry_run = {dry_run}  |  resume = {resume}  |  limit = {limit}")
    console.print(f"  source: spreadsheet {settings.sheets_spreadsheet_id} / {settings.sheets_worksheet_name}")
    console.print(f"  target: mongo db = {settings.mongo_db_name}, supabase bucket = {settings.supabase_bucket}\n")

    # --- Open all connections ---
    db = get_sync_db()
    storage = SupabaseStorage()
    if not dry_run:
        storage.ensure_bucket(public=True)
    drive_service = get_drive_service()
    gc = get_sheets_client()
    sh = gc.open_by_key(settings.sheets_spreadsheet_id)
    ws = sh.worksheet(settings.sheets_worksheet_name)

    # --- Fetch all rows ---
    console.print("[cyan]Fetching rows from Sheets…[/cyan]")
    all_rows = ws.get_all_records()  # list of dicts keyed by header row
    stats = MigrationStats(total_rows=len(all_rows))
    console.print(f"[dim]Found {len(all_rows)} rows.[/dim]\n")

    start_idx = load_checkpoint(db) if resume else 0
    if start_idx > 0:
        console.print(f"[yellow]Resuming from row index {start_idx}[/yellow]\n")

    rows_to_process = list(enumerate(all_rows, start=1))[start_idx:]
    if limit is not None:
        rows_to_process = rows_to_process[:limit]

    # --- Process with progress bar ---
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[cyan]{task.fields[word]}"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Migrating", total=len(rows_to_process), word="")

        for row_index, raw_row in rows_to_process:
            word_preview = (raw_row.get(COLUMN_MAP["word"]) or "?")[:20]
            progress.update(task, word=word_preview)

            try:
                process_row(
                    db, storage, drive_service, raw_row, row_index,
                    dry_run=dry_run, stats=stats,
                )
            except Exception as e:
                stats.add_error(row_index, word_preview, f"Row crash: {type(e).__name__}: {e}")
                if "--verbose" in sys.argv:
                    traceback.print_exc()

            # Checkpoint every N rows (only in real runs)
            if not dry_run and row_index % batch_checkpoint == 0:
                save_checkpoint(db, row_index)

            progress.advance(task)

    # Final checkpoint
    if not dry_run and rows_to_process:
        last_idx = rows_to_process[-1][0]
        save_checkpoint(db, last_idx)

    # --- Summary ---
    _print_summary(stats, dry_run=dry_run)
    return 0 if not stats.errors else 2


def _print_summary(stats: MigrationStats, *, dry_run: bool) -> None:
    table = Table(title=f"Migration summary {'(DRY RUN)' if dry_run else ''}", show_lines=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")

    table.add_row("Total rows examined", str(stats.total_rows))
    table.add_row("Entries created", str(stats.created_entries))
    table.add_row("Entries skipped (already existed)", str(stats.skipped_existing))
    table.add_row("Images uploaded", str(stats.images_uploaded))
    table.add_row("Rows without drive_file_id", str(stats.images_skipped_no_drive_id))
    table.add_row("Errors", f"[red]{len(stats.errors)}[/red]" if stats.errors else "0")

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
        console.print(f"[dim]({len(stats.errors)} total — full list available by inspecting MigrationStats)[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Sheets → MongoDB + Supabase")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing anything")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only the first N rows (good for testing)")
    parser.add_argument("--resume", action="store_true",
                        help="Continue from the last saved checkpoint")
    parser.add_argument("--checkpoint-every", type=int, default=25,
                        help="Save checkpoint every N rows (default: 25)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    sys.exit(run(
        dry_run=args.dry_run,
        limit=args.limit,
        resume=args.resume,
        batch_checkpoint=args.checkpoint_every,
    ))


if __name__ == "__main__":
    main()
