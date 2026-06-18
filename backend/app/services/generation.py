"""Image regeneration (sync MVP — no background worker)."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import APIConnectionError, AuthenticationError, OpenAI, RateLimitError

from app.config import get_settings
from app.db import Collections
from app.services.entries import _log_review
from app.storage import SupabaseStorage
from app.utils import oid
from dictionary_prompts import build_regenerate_prompt
from dictionary_prompts.classifier import PromptFamily


def generation_error_message(exc: Exception) -> str:
    import re
    if isinstance(exc, AuthenticationError):
        return "فشل توليد الصورة من OpenAI — تحقق من مفتاح OPENAI_API_KEY"
    if isinstance(exc, APIConnectionError):
        return "فشل الاتصال بخدمة OpenAI"
    if isinstance(exc, RateLimitError):
        return "تجاوز حد طلبات OpenAI — حاول لاحقاً"

    text = str(exc)
    # Strip secrets that might appear in SDK error messages
    safe_text = re.sub(r"(eyJ[A-Za-z0-9_.-]{20,})", "[TOKEN_REDACTED]", text)
    safe_text = re.sub(r"(key|token|secret)[=:\s]+\S+", "[REDACTED]", safe_text, flags=re.I)
    safe_text = safe_text[:500]

    lower = text.lower()
    if "invalid api key" in lower and "supabase" in lower:
        return f"فشل رفع الصورة إلى Supabase — SUPABASE_SERVICE_KEY غير صحيح | {safe_text}"
    if "invalid api key" in lower:
        return "فشل توليد الصورة من OpenAI — تحقق من مفتاح OPENAI_API_KEY"
    if "bucket" in lower and "not found" in lower:
        return f"فشل رفع الصورة — bucket غير موجود في Supabase | {safe_text}"
    if "bucket" in lower or "storage" in lower or "supabase" in lower:
        return f"فشل رفع الصورة إلى Supabase | {safe_text}"
    if "openai" in lower:
        return f"فشل توليد الصورة من OpenAI | {safe_text}"
    return f"فشل توليد الصورة: {safe_text}"


def _normalize_previous_status(raw: Optional[str]) -> str:
    if raw in (None, "generating", "generation_failed"):
        return "needs_review"
    return raw


async def get_generation_job(db: AsyncIOMotorDatabase, job_id: str) -> dict[str, Any]:
    doc = await db[Collections.GENERATION_JOBS].find_one({"_id": oid(job_id)})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {
        "id": str(doc["_id"]),
        "entry_id": str(doc["entry_id"]),
        "status": doc["status"],
        "image_id": str(doc["image_id"]) if doc.get("image_id") else None,
        "error": doc.get("error"),
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
    }


async def regenerate_entry(
    db: AsyncIOMotorDatabase,
    entry_id: str,
    *,
    rejection_reason: str,
    reviewer_vision: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    reason = rejection_reason.strip()
    vision = (reviewer_vision or "").strip()
    if not reason and not vision:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="يجب إدخال سبب الرفض أو تصور المراجع",
        )

    entry_oid = oid(entry_id)
    entry = await db[Collections.ENTRIES].find_one({"_id": entry_oid})
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    previous_status = _normalize_previous_status(entry.get("status"))
    now = datetime.now(timezone.utc)
    settings = get_settings()

    job_result = await db[Collections.GENERATION_JOBS].insert_one({
        "entry_id": entry_oid,
        "status": "running",
        "image_id": None,
        "error": None,
        "previous_entry_status": previous_status,
        "created_at": now,
        "updated_at": now,
    })
    job_id = job_result.inserted_id

    entry_update: dict[str, Any] = {
        "status": "generating",
        "updated_at": now,
    }
    if reason:
        entry_update["rejection_reason"] = reason
    if vision:
        entry_update["reviewer_vision"] = vision
    if notes:
        entry_update["notes"] = notes.strip()

    await db[Collections.ENTRIES].update_one({"_id": entry_oid}, {"$set": entry_update})

    try:
        # Determine the next generation attempt number for this entry
        # Count only AI-generated images (generated_by=fastapi) for the attempt counter
        regen_count = await db[Collections.IMAGES].count_documents(
            {"entry_id": entry_oid, "generated_by": "fastapi"}
        )
        next_attempt = regen_count + 1
        generation_label = f"regenerate_{next_attempt}"

        base_prompt = entry.get("base_prompt")
        current_image_id = entry.get("current_image_id")
        if not base_prompt and current_image_id:
            current_img = await db[Collections.IMAGES].find_one(
                {"_id": current_image_id},
                {"prompt": 1},
            )
            if current_img:
                base_prompt = current_img.get("prompt")

        family = None
        raw_family = entry.get("prompt_family")
        if raw_family:
            try:
                family = PromptFamily(raw_family)
            except ValueError:
                family = None

        prompt, resolved_family = build_regenerate_prompt(
            entry["word"],
            entry.get("definition"),
            base_prompt=base_prompt,
            object_description=entry.get("object_description"),
            rejection_reason=reason,
            reviewer_vision=vision,
            family=family,
        )

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.images.generate(
            model=settings.openai_image_model,
            prompt=prompt,
            size="1024x1024",
        )
        image_b64 = response.data[0].b64_json
        if not image_b64:
            raise RuntimeError("OpenAI returned no image data")
        image_bytes = base64.b64decode(image_b64)

        version = await db[Collections.IMAGES].count_documents({"entry_id": entry_oid}) + 1
        storage_path = f"entries/{entry_id}_v{version}.png"
        storage = SupabaseStorage()
        upload = storage.upload_bytes(image_bytes, storage_path, content_type="image/png")

        image_doc = {
            "entry_id": entry_oid,
            "prompt": prompt,
            "storage_path": upload.storage_path,
            "public_url": upload.public_url,
            "generated_by": "fastapi",
            "review_status": "candidate",
            "generation_attempt": next_attempt,
            "generation_label": generation_label,
            "image_role": "candidate",
            "source": "openai_regenerate",
            "storage_provider": "supabase",
            "rejection_reason": reason or None,
            "reviewer_vision": vision or None,
            "reviewer_note": notes.strip() if notes else None,
            "generation_meta": {
                "family": resolved_family.value if hasattr(resolved_family, "value") else str(resolved_family),
                "job_id": str(job_id),
                "rejection_reason": reason or None,
                "reviewer_vision": vision or None,
                "base_prompt": base_prompt,
            },
            "is_current": False,
            "size_bytes": upload.size_bytes,
            "created_at": now,
        }
        image_result = await db[Collections.IMAGES].insert_one(image_doc)
        image_id = image_result.inserted_id

        await db[Collections.GENERATION_JOBS].update_one(
            {"_id": job_id},
            {"$set": {
                "status": "succeeded",
                "image_id": image_id,
                "generation_attempt": next_attempt,
                "generation_label": generation_label,
                "error": None,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        await db[Collections.ENTRIES].update_one(
            {"_id": entry_oid},
            {"$set": {
                "status": "needs_selection",
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        await _log_review(
            db,
            entry_id=entry_oid,
            action="regenerate",
            payload={
                "job_id": str(job_id),
                "image_id": str(image_id),
                "generation_attempt": next_attempt,
                "generation_label": generation_label,
            },
        )

        return {
            "ok": True,
            "entry_id": entry_id,
            "message": "تم توليد صورة مرشحة جديدة",
            "data": {
                "job_id": str(job_id),
                "image_id": str(image_id),
            },
        }

    except HTTPException:
        raise
    except Exception as exc:
        error_message = generation_error_message(exc)
        await db[Collections.GENERATION_JOBS].update_one(
            {"_id": job_id},
            {"$set": {
                "status": "failed",
                "error": error_message,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        await db[Collections.ENTRIES].update_one(
            {"_id": entry_oid},
            {"$set": {
                "status": "generation_failed",
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_message,
        ) from exc
