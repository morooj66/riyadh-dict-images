"""Entry listing, review actions, and image selection."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import Collections
from app.utils import (
    arabic_flexible_regex,
    oid,
    resolve_public_image_url,
    serialize_entry_summary,
    serialize_image,
    strip_arabic_diacritics,
)


async def get_stats(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    total_entries = await db[Collections.ENTRIES].count_documents({})
    total_images = await db[Collections.IMAGES].count_documents({})
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    by_status: dict[str, int] = {}
    async for row in db[Collections.ENTRIES].aggregate(pipeline):
        key = row["_id"] or "unknown"
        by_status[key] = row["count"]
    return {
        "total_entries": total_entries,
        "total_images": total_images,
        "by_status": by_status,
    }


async def list_entries(
    db: AsyncIOMotorDatabase,
    *,
    page: int = 1,
    page_size: int = 25,
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    category: Optional[str] = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if status_filter:
        query["status"] = status_filter
    if category:
        query["category"] = category
    if search:
        plain = strip_arabic_diacritics(search.strip())
        if plain:
            query["word"] = {"$regex": arabic_flexible_regex(plain), "$options": "i"}

    total = await db[Collections.ENTRIES].count_documents(query)
    skip = (page - 1) * page_size
    cursor = (
        db[Collections.ENTRIES]
        .find(query, {"word": 1, "definition": 1, "category": 1, "status": 1,
                      "prompt_family": 1, "current_image_id": 1, "updated_at": 1, "created_at": 1})
        .sort([("updated_at", -1), ("_id", -1)])
        .skip(skip)
        .limit(page_size)
    )
    docs = [doc async for doc in cursor]
    image_counts: dict[ObjectId, int] = {}
    if docs:
        entry_ids = [doc["_id"] for doc in docs]
        pipeline = [
            {"$match": {"entry_id": {"$in": entry_ids}}},
            {"$group": {"_id": "$entry_id", "count": {"$sum": 1}}},
        ]
        async for row in db[Collections.IMAGES].aggregate(pipeline):
            image_counts[row["_id"]] = row["count"]
    items = [
        serialize_entry_summary(doc, image_count=image_counts.get(doc["_id"], 0))
        for doc in docs
    ]
    total_pages = max(1, math.ceil(total / page_size)) if page_size else 1
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


async def get_entry(db: AsyncIOMotorDatabase, entry_id: str) -> dict[str, Any]:
    doc = await db[Collections.ENTRIES].find_one({"_id": oid(entry_id)})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    entry_oid = doc["_id"]
    image_count = await db[Collections.IMAGES].count_documents({"entry_id": entry_oid})

    current_image = None
    current_image_id = doc.get("current_image_id")
    if current_image_id:
        img = await db[Collections.IMAGES].find_one({"_id": current_image_id})
        if img:
            current_image = serialize_image(img, selected_id=doc.get("selected_image_id"))

    return {
        "id": str(doc["_id"]),
        "word": doc["word"],
        "definition": doc.get("definition"),
        "category": doc["category"],
        "status": doc.get("status", "pending"),
        "prompt_family": doc.get("prompt_family"),
        "rejection_reason": doc.get("rejection_reason"),
        "reviewer_vision": doc.get("reviewer_vision"),
        "current_image_id": str(current_image_id) if current_image_id else None,
        "selected_image_id": str(doc["selected_image_id"]) if doc.get("selected_image_id") else None,
        "current_image": current_image,
        "notes": doc.get("notes"),
        "object_description": doc.get("object_description"),
        "base_prompt": doc.get("base_prompt"),
        "image_count": image_count,
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def get_queue_entry(
    db: AsyncIOMotorDatabase,
    *,
    status_filter: str,
    current_id: str | None = None,
    direction: str = "next",
) -> dict[str, Any] | None:
    query: dict[str, Any] = {"status": status_filter}
    sort_field = "updated_at"
    sort_dir = 1 if direction == "next" else -1

    if current_id:
        current = await db[Collections.ENTRIES].find_one({"_id": oid(current_id)})
        if current:
            op = "$gt" if direction == "next" else "$lt"
            query["$or"] = [
                {sort_field: {op: current.get(sort_field)}},
                {sort_field: current.get(sort_field), "_id": {op: current["_id"]}},
            ]

    doc = await db[Collections.ENTRIES].find_one(query, sort=[(sort_field, sort_dir), ("_id", sort_dir)])
    if doc:
        return await get_entry(db, str(doc["_id"]))

    if current_id:
        doc = await db[Collections.ENTRIES].find_one(
            {"status": status_filter},
            sort=[(sort_field, sort_dir), ("_id", sort_dir)],
        )
        if doc:
            return await get_entry(db, str(doc["_id"]))
    return None


async def list_entry_images(db: AsyncIOMotorDatabase, entry_id: str) -> list[dict[str, Any]]:
    entry = await db[Collections.ENTRIES].find_one({"_id": oid(entry_id)}, {"selected_image_id": 1})
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    selected_id = entry.get("selected_image_id")
    cursor = (
        db[Collections.IMAGES]
        .find({"entry_id": oid(entry_id)})
        .sort("created_at", -1)
    )
    return [serialize_image(doc, selected_id=selected_id) async for doc in cursor]


async def _log_review(
    db: AsyncIOMotorDatabase,
    *,
    entry_id: ObjectId,
    action: str,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    now = datetime.now(timezone.utc)
    await db[Collections.REVIEWS].insert_one({
        "entry_id": entry_id,
        "action": action,
        "payload": payload or {},
        "created_at": now,
    })


async def reject_entry(
    db: AsyncIOMotorDatabase,
    entry_id: str,
    rejection_reason: str,
    *,
    reviewer_vision: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    entry_oid = oid(entry_id)
    update: dict[str, Any] = {
        "status": "rejected",
        "rejection_reason": rejection_reason.strip(),
        "updated_at": datetime.now(timezone.utc),
    }
    if reviewer_vision:
        update["reviewer_vision"] = reviewer_vision.strip()
    if notes:
        update["notes"] = notes.strip()

    result = await db[Collections.ENTRIES].update_one({"_id": entry_oid}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    await _log_review(
        db,
        entry_id=entry_oid,
        action="reject",
        payload={"rejection_reason": rejection_reason, "reviewer_vision": reviewer_vision},
    )
    return {"ok": True, "entry_id": entry_id, "message": "Entry rejected"}


async def _image_public_url(db: AsyncIOMotorDatabase, image_id: ObjectId) -> str:
    doc = await db[Collections.IMAGES].find_one({"_id": image_id})
    if not doc:
        return ""
    drive_id = doc.get("drive_file_id")
    if not drive_id:
        meta = doc.get("generation_meta") or {}
        drive_id = meta.get("original_drive_file_id")
    return resolve_public_image_url(doc.get("public_url"), drive_id) or doc.get("public_url", "") or ""


async def select_image(
    db: AsyncIOMotorDatabase,
    entry_id: str,
    image_id: str,
) -> dict[str, Any]:
    entry_oid = oid(entry_id)
    image_oid = oid(image_id)

    entry = await db[Collections.ENTRIES].find_one({"_id": entry_oid})
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    image = await db[Collections.IMAGES].find_one({"_id": image_oid, "entry_id": entry_oid})
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found for this entry")

    await db[Collections.ENTRIES].update_one(
        {"_id": entry_oid},
        {"$set": {
            "selected_image_id": image_oid,
            "status": "needs_selection",
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    await db[Collections.IMAGES].update_one(
        {"_id": image_oid},
        {"$set": {"review_status": "selected"}},
    )
    await _log_review(db, entry_id=entry_oid, action="select_image", payload={"image_id": image_id})
    return {"ok": True, "entry_id": entry_id, "message": "Image selected", "data": {"selected_image_id": image_id}}


async def approve_entry(db: AsyncIOMotorDatabase, entry_id: str) -> dict[str, Any]:
    entry_oid = oid(entry_id)
    entry = await db[Collections.ENTRIES].find_one({"_id": entry_oid})
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    selected_id = entry.get("selected_image_id") or entry.get("current_image_id")
    if not selected_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No image selected or current — select an image first",
        )

    image = await db[Collections.IMAGES].find_one({"_id": selected_id, "entry_id": entry_oid})
    if not image:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected image not found")

    now = datetime.now(timezone.utc)
    old_current_id = entry.get("current_image_id")
    approved_url = await _image_public_url(db, selected_id)

    entry_update: dict[str, Any] = {
        "status": "approved",
        "review_decision": "approved",
        "reviewed_at": now,
        "current_image_id": selected_id,
        "selected_image_id": selected_id,
        "approved_image_url": approved_url,
        "updated_at": now,
    }

    original_url = entry.get("original_image_url")
    if not original_url and old_current_id:
        original_url = await _image_public_url(db, old_current_id)
    if not original_url:
        original_url = approved_url
    if original_url:
        entry_update["original_image_url"] = original_url

    if old_current_id and old_current_id != selected_id:
        previous_url = await _image_public_url(db, old_current_id)
        if previous_url:
            entry_update["previous_image_url"] = previous_url

    await db[Collections.IMAGES].update_many(
        {"entry_id": entry_oid, "is_current": True},
        {"$set": {"is_current": False}},
    )
    await db[Collections.IMAGES].update_one(
        {"_id": selected_id},
        {"$set": {"is_current": True, "review_status": "approved"}},
    )
    if old_current_id and old_current_id != selected_id:
        await db[Collections.IMAGES].update_one(
            {"_id": old_current_id},
            {"$set": {"is_current": False, "review_status": "previous"}},
        )

    await db[Collections.ENTRIES].update_one({"_id": entry_oid}, {"$set": entry_update})
    await _log_review(
        db,
        entry_id=entry_oid,
        action="approve",
        payload={
            "image_id": str(selected_id),
            "previous_image_id": str(old_current_id) if old_current_id else None,
            "approved_image_url": approved_url,
        },
    )
    return {
        "ok": True,
        "entry_id": entry_id,
        "message": "Entry approved",
        "data": {
            "current_image_id": str(selected_id),
            "approved_image_url": approved_url,
            "previous_image_url": entry_update.get("previous_image_url"),
        },
    }
