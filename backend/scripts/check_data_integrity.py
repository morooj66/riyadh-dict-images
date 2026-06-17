"""
============================================================================
Riyadh Dictionary — Read-only data integrity check
============================================================================

This script ONLY reads from MongoDB. It NEVER modifies, deletes, merges, or
archives anything. It is safe to run anytime in any environment.

What it reports:

  1. Connection sanity
       - Confirms MONGO_URI / MONGO_DB_NAME resolved from env.
       - Pings MongoDB. Does NOT print the URI or credentials.

  2. Entry counts
       - Total entries
       - Entries with current_image_id
       - Entries without any image at all
       - Counts by status

  3. Duplicate detection (NOT a fix — only a report)
       - Groups entries by (normalized word + category)
       - Counts how many groups have more than one entry.

  4. Sidebar visibility prediction
       - Mirrors frontend `prepareSidebarEntries`:
            * dedupe by (normalized word + category) → pick highest-scored
            * keep only entries with current_image_id OR image_count > 0
       - Reports:
            * entries_after_dedupe (the count the sidebar would show)
            * sidebar_visible      (after the image filter)
            * hidden_no_image      (truly without images)

  5. Image link health
       - Entries with current_image_id pointing to a non-existent image
       - Orphan images whose entry_id does not match any entry

  6. Sample issues
       - Up to 10 examples of each issue category, for human review.

Usage (from backend/):
    python -m scripts.check_data_integrity
    python -m scripts.check_data_integrity --json   # machine-readable output

Exit code:
    0 if no issues detected
    2 if any issues detected (broken links, orphan images, duplicates)

This script does not write to MongoDB under any circumstance.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo.errors import PyMongoError, ServerSelectionTimeoutError
from rich.console import Console
from rich.table import Table

from app.config import get_settings
from app.db import Collections, get_sync_client, get_sync_db


console = Console()

# Mirror the frontend's STATUS_RANK (entryDedupe.ts) so the sidebar prediction
# matches what the reviewer actually sees in the UI.
STATUS_RANK: dict[str, int] = {
    "needs_selection": 5,
    "needs_review": 4,
    "approved": 3,
    "generation_failed": 2,
    "rejected": 1,
    "generating": 0,
    "pending": 0,
}

# Same diacritics range used by app.utils.strip_arabic_diacritics and by the
# frontend's stripArabicDiacritics. Keep these in sync.
_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")


def _strip_diacritics(text: str) -> str:
    if not text:
        return ""
    return _DIACRITICS_RE.sub("", text)


def _entry_score(
    entry: dict[str, Any],
    image_count: int,
) -> float:
    """Mirror frontend entryScore() so the same winner is picked per group."""
    status = STATUS_RANK.get(entry.get("status") or "", 0)
    has_image = 1 if entry.get("current_image_id") else 0
    updated = entry.get("updated_at")
    if isinstance(updated, datetime):
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        updated_ms = updated.timestamp() * 1000
    else:
        updated_ms = 0
    return status * 1e15 + has_image * 1e12 + image_count * 1e9 + updated_ms


def _has_reviewable_image(entry: dict[str, Any], image_count: int) -> bool:
    return entry.get("current_image_id") is not None or image_count > 0


def _safe_ping(client) -> tuple[bool, Optional[str]]:
    """Ping MongoDB without leaking the URI on failure."""
    try:
        client.admin.command("ping")
        return True, None
    except ServerSelectionTimeoutError:
        return False, "MongoDB server selection timed out (check network / Atlas IP allowlist)."
    except PyMongoError as exc:
        # Sanitize message: never include the URI. PyMongo error messages can
        # sometimes include host info, but `type(exc).__name__` plus a short
        # category is enough for diagnostics.
        return False, f"{type(exc).__name__}: connection failed"


def collect_report(db) -> dict[str, Any]:
    """Build the full integrity report dict. Pure read-only."""
    entries_col = db[Collections.ENTRIES]
    images_col = db[Collections.IMAGES]

    report: dict[str, Any] = {
        "db_name": db.name,
        "collections_seen": db.list_collection_names(),
        "totals": {},
        "duplicates": {},
        "sidebar": {},
        "broken_links": {},
        "samples": {
            "duplicate_groups": [],
            "broken_current_image_id": [],
            "orphan_images": [],
            "hidden_no_image": [],
        },
    }

    total_entries = entries_col.count_documents({})
    total_images = images_col.count_documents({})
    entries_with_current_image = entries_col.count_documents({"current_image_id": {"$ne": None}})
    entries_without_current_image = entries_col.count_documents(
        {"$or": [{"current_image_id": None}, {"current_image_id": {"$exists": False}}]}
    )

    # Status breakdown via aggregation (efficient on large collections).
    by_status: dict[str, int] = {}
    for row in entries_col.aggregate([
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]):
        key = row["_id"] if row["_id"] else "(none)"
        by_status[str(key)] = row["count"]

    report["totals"] = {
        "total_entries": total_entries,
        "total_images": total_images,
        "entries_with_current_image": entries_with_current_image,
        "entries_without_current_image": entries_without_current_image,
        "by_status": by_status,
    }

    # ------------------------------------------------------------------
    # Build per-entry image counts (one aggregation pass).
    # ------------------------------------------------------------------
    image_count_by_entry: dict[Any, int] = defaultdict(int)
    for row in images_col.aggregate([
        {"$group": {"_id": "$entry_id", "count": {"$sum": 1}}},
    ]):
        image_count_by_entry[row["_id"]] = row["count"]

    # ------------------------------------------------------------------
    # Stream entries with only the fields we need (keeps memory low even
    # if entries grows large).
    # ------------------------------------------------------------------
    needed_fields = {
        "_id": 1,
        "word": 1,
        "category": 1,
        "status": 1,
        "current_image_id": 1,
        "updated_at": 1,
    }
    cursor = entries_col.find({}, needed_fields)

    # group key -> list of (entry, image_count)
    groups: dict[tuple[str, str], list[tuple[dict[str, Any], int]]] = defaultdict(list)

    for entry in cursor:
        word = entry.get("word") or ""
        category = entry.get("category") or ""
        key = (_strip_diacritics(word).strip(), category.strip())
        ic = image_count_by_entry.get(entry["_id"], 0)
        groups[key].append((entry, ic))

    # Duplicates
    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    duplicate_entry_count = sum(len(v) for v in duplicate_groups.values())

    report["duplicates"] = {
        "duplicate_groups": len(duplicate_groups),
        "entries_in_duplicate_groups": duplicate_entry_count,
        "unique_groups": len(groups),
    }

    # Sample first 10 duplicate groups
    for (norm_word, category), members in list(duplicate_groups.items())[:10]:
        report["samples"]["duplicate_groups"].append({
            "normalized_word": norm_word,
            "category": category,
            "count": len(members),
            "ids": [str(m[0]["_id"]) for m in members[:5]],
        })

    # ------------------------------------------------------------------
    # Sidebar prediction: dedupe + keep entries with a reviewable image.
    # ------------------------------------------------------------------
    sidebar_visible = 0
    hidden_no_image = 0
    hidden_samples: list[dict[str, Any]] = []
    for key, members in groups.items():
        # Pick winner via same scoring as the frontend.
        winner_entry, winner_ic = max(members, key=lambda m: _entry_score(m[0], m[1]))
        if _has_reviewable_image(winner_entry, winner_ic):
            sidebar_visible += 1
        else:
            hidden_no_image += 1
            if len(hidden_samples) < 10:
                hidden_samples.append({
                    "id": str(winner_entry["_id"]),
                    "word": winner_entry.get("word"),
                    "category": winner_entry.get("category"),
                    "status": winner_entry.get("status"),
                })

    report["sidebar"] = {
        "entries_after_dedupe": len(groups),
        "sidebar_visible": sidebar_visible,
        "hidden_no_image": hidden_no_image,
    }
    report["samples"]["hidden_no_image"] = hidden_samples

    # ------------------------------------------------------------------
    # Broken current_image_id (entry points at an image that doesn't exist).
    # ------------------------------------------------------------------
    broken_current = 0
    broken_samples: list[dict[str, Any]] = []
    cursor = entries_col.find(
        {"current_image_id": {"$ne": None}},
        {"_id": 1, "word": 1, "category": 1, "current_image_id": 1},
    )
    for entry in cursor:
        img_id = entry["current_image_id"]
        if not images_col.find_one({"_id": img_id}, {"_id": 1}):
            broken_current += 1
            if len(broken_samples) < 10:
                broken_samples.append({
                    "entry_id": str(entry["_id"]),
                    "word": entry.get("word"),
                    "category": entry.get("category"),
                    "missing_image_id": str(img_id),
                })

    report["broken_links"] = {"broken_current_image_id": broken_current}
    report["samples"]["broken_current_image_id"] = broken_samples

    # ------------------------------------------------------------------
    # Orphan images (images.entry_id has no matching entry).
    # ------------------------------------------------------------------
    orphan_images = 0
    orphan_samples: list[dict[str, Any]] = []
    if "images" in report["collections_seen"]:
        cursor = images_col.find({}, {"_id": 1, "entry_id": 1})
        for img in cursor:
            entry_id = img.get("entry_id")
            if not entry_id:
                # Image with no entry_id at all
                orphan_images += 1
                if len(orphan_samples) < 10:
                    orphan_samples.append({
                        "image_id": str(img["_id"]),
                        "entry_id": None,
                    })
                continue
            if not entries_col.find_one({"_id": entry_id}, {"_id": 1}):
                orphan_images += 1
                if len(orphan_samples) < 10:
                    orphan_samples.append({
                        "image_id": str(img["_id"]),
                        "entry_id": str(entry_id),
                    })

    report["broken_links"]["orphan_images"] = orphan_images
    report["samples"]["orphan_images"] = orphan_samples

    return report


def print_human_report(report: dict[str, Any]) -> None:
    console.print(f"\n[bold cyan]Database:[/bold cyan] {report['db_name']}")
    console.print(f"[dim]Collections: {', '.join(report['collections_seen'])}[/dim]\n")

    t = Table(title="Entry totals", show_lines=False)
    t.add_column("Metric", style="cyan")
    t.add_column("Count", justify="right", style="green")
    for key, val in report["totals"].items():
        if key == "by_status":
            continue
        t.add_row(key, str(val))
    console.print(t)

    if report["totals"]["by_status"]:
        st = Table(title="Entries by status")
        st.add_column("status", style="cyan")
        st.add_column("count", justify="right", style="green")
        for s, c in sorted(
            report["totals"]["by_status"].items(),
            key=lambda kv: kv[1],
            reverse=True,
        ):
            st.add_row(s, str(c))
        console.print(st)

    dup = Table(title="Duplicate detection (normalized word + category)")
    dup.add_column("Metric", style="cyan")
    dup.add_column("Count", justify="right", style="green")
    dup.add_row("Unique groups", str(report["duplicates"]["unique_groups"]))
    dup.add_row("Duplicate groups", str(report["duplicates"]["duplicate_groups"]))
    dup.add_row("Entries in duplicate groups", str(report["duplicates"]["entries_in_duplicate_groups"]))
    console.print(dup)

    sb = Table(title="Sidebar visibility prediction")
    sb.add_column("Metric", style="cyan")
    sb.add_column("Count", justify="right", style="green")
    sb.add_row("Entries after dedupe", str(report["sidebar"]["entries_after_dedupe"]))
    sb.add_row("Sidebar visible (has image)", str(report["sidebar"]["sidebar_visible"]))
    sb.add_row("Hidden (no image at all)", str(report["sidebar"]["hidden_no_image"]))
    console.print(sb)

    bl = Table(title="Broken links")
    bl.add_column("Metric", style="cyan")
    bl.add_column("Count", justify="right", style="red")
    bl.add_row("Broken current_image_id", str(report["broken_links"]["broken_current_image_id"]))
    bl.add_row("Orphan images", str(report["broken_links"].get("orphan_images", 0)))
    console.print(bl)

    # Samples
    for label, key in [
        ("Sample duplicate groups", "duplicate_groups"),
        ("Sample broken current_image_id", "broken_current_image_id"),
        ("Sample orphan images", "orphan_images"),
        ("Sample hidden entries (no image)", "hidden_no_image"),
    ]:
        items = report["samples"].get(key, [])
        if not items:
            continue
        console.print(f"\n[bold]{label}[/bold] (up to 10):")
        for i, item in enumerate(items, 1):
            console.print(f"  {i}. {item}")


def has_issues(report: dict[str, Any]) -> bool:
    return (
        report["duplicates"]["duplicate_groups"] > 0
        or report["broken_links"]["broken_current_image_id"] > 0
        or report["broken_links"].get("orphan_images", 0) > 0
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only MongoDB data integrity check (never modifies data).",
    )
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON to stdout (no rich formatting).")
    args = parser.parse_args()

    # Touch settings so MONGO_URI / MONGO_DB_NAME are validated by pydantic.
    settings = get_settings()  # noqa: F841 — accessing forces env validation

    client = get_sync_client()
    ok, err = _safe_ping(client)
    if not ok:
        console.print(f"[bold red]MongoDB connection failed:[/bold red] {err}")
        sys.exit(3)

    db = get_sync_db()
    try:
        report = collect_report(db)
    except PyMongoError as exc:
        # Never include the URI / credentials.
        console.print(f"[bold red]Read failed:[/bold red] {type(exc).__name__}")
        sys.exit(3)

    if args.json:
        # Default JSON encoder can't handle datetime / ObjectId; everything
        # we put in the report is already string-converted, but stay safe.
        print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    else:
        print_human_report(report)

    sys.exit(2 if has_issues(report) else 0)


if __name__ == "__main__":
    main()
