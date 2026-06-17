"""Canonical entry status values stored in MongoDB."""
from __future__ import annotations

VALID_ENTRY_STATUSES = frozenset({
    "pending",
    "needs_review",
    "rejected",
    "generating",
    "needs_selection",
    "approved",
    "generation_failed",
})


def normalize_entry_status(raw: str | None, *, has_image: bool = False) -> str:
    if not raw:
        return "needs_review" if has_image else "pending"
    value = raw.strip().lower()
    aliases = {
        "new": "pending",
        "candidate": "needs_selection",
        "selected": "needs_selection",
        "current": "needs_review",
        "regeneration_requested": "generating",
        "uploaded": "needs_review",
        "approved": "approved",
    }
    value = aliases.get(value, value)
    if value in VALID_ENTRY_STATUSES:
        return value
    return "needs_review" if has_image else "pending"
