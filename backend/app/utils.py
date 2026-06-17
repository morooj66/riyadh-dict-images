"""Shared helpers."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from bson import ObjectId

_ARABIC_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")


def strip_arabic_diacritics(text: str) -> str:
    return _ARABIC_DIACRITICS_RE.sub("", text)


# Arabic diacritics character class — regular string so \u escapes become actual Unicode chars
# MongoDB PCRE does not understand \u notation; it needs the literal Unicode characters.
_ARABIC_DIACRITICS_OPT = "[\u064B-\u065F\u0670\u06D6-\u06ED]*"


def arabic_flexible_regex(plain: str) -> str:
    """Build a PCRE-compatible regex that matches Arabic text with optional diacritics."""
    parts: list[str] = []
    for ch in plain:
        if "\u0600" <= ch <= "\u06FF":
            parts.append(re.escape(ch) + _ARABIC_DIACRITICS_OPT)
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


_DRIVE_ID_PATTERNS = (
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
    re.compile(r"/file/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"/d/([a-zA-Z0-9_-]+)"),
)


def extract_drive_file_id(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    for pattern in _DRIVE_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return text if len(text) >= 20 and "/" not in text else None


def resolve_public_image_url(
    public_url: str | None,
    drive_file_id: str | None = None,
) -> str | None:
    """Return a browser-friendly image URL (Drive → export=view)."""
    file_id = drive_file_id or extract_drive_file_id(public_url)
    if file_id:
        return f"https://drive.google.com/uc?export=view&id={file_id}"
    if public_url:
        return public_url.strip() or None
    return None


def oid(value: str | ObjectId) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    return ObjectId(value)


def oid_str(value: ObjectId | str | None) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def serialize_image(doc: dict[str, Any], *, selected_id: ObjectId | None = None) -> dict[str, Any]:
    drive_id = doc.get("drive_file_id")
    if not drive_id:
        meta = doc.get("generation_meta") or {}
        drive_id = meta.get("original_drive_file_id")
    return {
        "id": str(doc["_id"]),
        "public_url": resolve_public_image_url(doc.get("public_url"), drive_id) or doc.get("public_url", ""),
        "drive_file_id": drive_id,
        "prompt": doc.get("prompt"),
        "generated_by": doc.get("generated_by"),
        "is_current": bool(doc.get("is_current")),
        "is_selected": selected_id is not None and doc["_id"] == selected_id,
        "created_at": doc["created_at"],
        "generation_attempt": doc.get("generation_attempt"),
        "generation_label": doc.get("generation_label"),
        "image_role": doc.get("image_role"),
        "source": doc.get("source"),
    }


def serialize_entry_summary(doc: dict[str, Any], *, image_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "word": doc["word"],
        "definition": doc.get("definition"),
        "category": doc["category"],
        "status": doc.get("status", "pending"),
        "prompt_family": doc.get("prompt_family"),
        "has_image": bool(doc.get("current_image_id")),
        "image_count": image_count,
        "updated_at": doc.get("updated_at") or doc.get("created_at"),
    }
