"""
Create MongoDB indexes.

Run ONCE before any data loads, then re-run anytime you add new query patterns.
Indexes are idempotent in MongoDB — creating an existing index is a no-op.

These indexes are the difference between:
  - Fast queries on 2k entries AND 200k entries (with indexes)
  - Slow queries that get slower the more data you add (without indexes)

Usage:
    cd backend
    python -m scripts.setup_mongo_indexes
"""
from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, TEXT
from rich.console import Console
from rich.table import Table

from app.db import get_sync_db, Collections

console = Console()


def setup_entries_indexes(db) -> list[str]:
    """Indexes for the `entries` collection."""
    col = db[Collections.ENTRIES]
    created = []

    # Filter by status (used on every dashboard query)
    created.append(col.create_index([("status", ASCENDING)], name="idx_status"))

    # Filter by category (اسم آلة / اسم ذات / ...)
    created.append(col.create_index([("category", ASCENDING)], name="idx_category"))

    # Compound: status + category (most common dashboard combo)
    created.append(col.create_index(
        [("status", ASCENDING), ("category", ASCENDING)],
        name="idx_status_category",
    ))

    # Sync mapping back to Sheets - MUST be unique to prevent duplicate rows
    # Sparse: only entries that came from sheets have this field
    created.append(col.create_index(
        [("sheets_row_index", ASCENDING)],
        name="idx_sheets_row",
        unique=True,
        sparse=True,
    ))

    # The word itself - unique so we never duplicate an entry
    created.append(col.create_index(
        [("word", ASCENDING), ("category", ASCENDING)],
        name="idx_word_category",
        unique=True,
    ))

    # Text search on Arabic word + definition (for reviewer search box)
    created.append(col.create_index(
        [("word", TEXT), ("definition", TEXT)],
        name="idx_text_search",
        default_language="none",   # Arabic isn't in default stemmers; "none" = exact tokens
    ))

    # For "show me newest first" pagination
    created.append(col.create_index([("updated_at", DESCENDING)], name="idx_updated_desc"))

    return created


def setup_images_indexes(db) -> list[str]:
    """Indexes for the `images` collection."""
    col = db[Collections.IMAGES]
    created = []

    # Find all images for an entry (history view)
    created.append(col.create_index([("entry_id", ASCENDING)], name="idx_entry_id"))

    # The critical hot-path index: "give me the current image for this entry"
    created.append(col.create_index(
        [("entry_id", ASCENDING), ("is_current", ASCENDING)],
        name="idx_entry_current",
    ))

    # For analytics: "how many images did Colab vs FastAPI generate?"
    created.append(col.create_index([("generated_by", ASCENDING)], name="idx_generated_by"))

    # Sorted history
    created.append(col.create_index([("created_at", DESCENDING)], name="idx_created_desc"))

    return created


def setup_reviews_indexes(db) -> list[str]:
    col = db[Collections.REVIEWS]
    created = []
    created.append(col.create_index([("entry_id", ASCENDING)], name="idx_entry_id"))
    created.append(col.create_index([("created_at", DESCENDING)], name="idx_created_desc"))
    created.append(col.create_index([("action", ASCENDING)], name="idx_action"))
    return created


def setup_sync_log_indexes(db) -> list[str]:
    col = db[Collections.SYNC_LOG]
    created = []
    created.append(col.create_index([("run_at", DESCENDING)], name="idx_run_desc"))
    # Auto-delete old sync logs after 90 days to keep collection small
    created.append(col.create_index(
        [("run_at", ASCENDING)],
        name="idx_ttl_90d",
        expireAfterSeconds=90 * 24 * 60 * 60,
    ))
    return created


def setup_migration_checkpoint_indexes(db) -> list[str]:
    col = db[Collections.MIGRATION_CHECKPOINTS]
    created = []
    created.append(col.create_index(
        [("migration_name", ASCENDING)],
        name="idx_migration_name",
        unique=True,
    ))
    return created


def main() -> None:
    db = get_sync_db()
    console.print(f"[bold cyan]Setting up indexes on database:[/bold cyan] {db.name}\n")

    targets = [
        (Collections.ENTRIES, setup_entries_indexes),
        (Collections.IMAGES, setup_images_indexes),
        (Collections.REVIEWS, setup_reviews_indexes),
        (Collections.SYNC_LOG, setup_sync_log_indexes),
        (Collections.MIGRATION_CHECKPOINTS, setup_migration_checkpoint_indexes),
    ]

    table = Table(title="Index setup results")
    table.add_column("Collection", style="cyan")
    table.add_column("Indexes created/verified", style="green")

    for col_name, setup_fn in targets:
        names = setup_fn(db)
        table.add_row(col_name, ", ".join(names))

    console.print(table)
    console.print("\n[bold green]✓ All indexes ready.[/bold green]")


if __name__ == "__main__":
    main()
