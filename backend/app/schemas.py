"""Request/response models for the reviewer API."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class EntryStatus(str, Enum):
    PENDING = "pending"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    GENERATING = "generating"
    NEEDS_SELECTION = "needs_selection"
    APPROVED = "approved"
    GENERATION_FAILED = "generation_failed"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    COMPLETED = "completed"
    FAILED = "failed"


class HealthResponse(BaseModel):
    status: str = "ok"


class StatsResponse(BaseModel):
    total_entries: int
    total_images: int
    by_status: dict[str, int]


class EntrySummary(BaseModel):
    id: str
    word: str
    definition: Optional[str] = None
    category: str
    status: str
    prompt_family: Optional[str] = None
    has_image: bool
    image_count: int = 0
    updated_at: datetime


class ImageSummary(BaseModel):
    id: str
    public_url: str
    drive_file_id: Optional[str] = None
    prompt: Optional[str] = None
    generated_by: Optional[str] = None
    is_current: bool
    is_selected: bool
    created_at: datetime
    generation_attempt: Optional[int] = None
    generation_label: Optional[str] = None
    image_role: Optional[str] = None
    source: Optional[str] = None


class EntryDetail(BaseModel):
    id: str
    word: str
    definition: Optional[str] = None
    category: str
    status: str
    prompt_family: Optional[str] = None
    rejection_reason: Optional[str] = None
    reviewer_vision: Optional[str] = None
    current_image_id: Optional[str] = None
    selected_image_id: Optional[str] = None
    current_image: Optional[ImageSummary] = None
    notes: Optional[str] = None
    object_description: Optional[str] = None
    base_prompt: Optional[str] = None
    image_count: int = 0
    created_at: datetime
    updated_at: datetime


class PaginatedEntries(BaseModel):
    items: list[EntrySummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class RejectRequest(BaseModel):
    rejection_reason: str = ""
    reviewer_vision: Optional[str] = None
    notes: Optional[str] = None
    regenerate: bool = False

    @model_validator(mode="after")
    def validate_reject_fields(self) -> "RejectRequest":
        reason = self.rejection_reason.strip()
        vision = (self.reviewer_vision or "").strip()
        if self.regenerate:
            if not reason and not vision:
                raise ValueError("يجب إدخال سبب الرفض أو تصور المراجع")
        elif not reason:
            raise ValueError("سبب الرفض مطلوب")
        return self


class RegenerateRequest(BaseModel):
    rejection_reason: str = ""
    reviewer_vision: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_regenerate_fields(self) -> "RegenerateRequest":
        reason = self.rejection_reason.strip()
        vision = (self.reviewer_vision or "").strip()
        if not reason and not vision:
            raise ValueError("يجب إدخال سبب الرفض أو تصور المراجع")
        return self


class ApproveRequest(BaseModel):
    pass


class GenerationJobResponse(BaseModel):
    id: str
    entry_id: str
    status: JobStatus
    image_id: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ActionResponse(BaseModel):
    ok: bool = True
    entry_id: str
    message: str
    data: Optional[dict[str, Any]] = None
