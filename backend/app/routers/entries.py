from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_async_db
from app.dependencies import verify_api_key
from app.schemas import (
    ActionResponse,
    EntryDetail,
    GenerationJobResponse,
    ImageSummary,
    PaginatedEntries,
    RegenerateRequest,
    RejectRequest,
)
from app.services import entries as entry_service
from app.services import generation as generation_service

router = APIRouter(prefix="/entries", tags=["entries"], dependencies=[Depends(verify_api_key)])


@router.get("", response_model=PaginatedEntries)
async def list_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_async_db),
) -> PaginatedEntries:
    data = await entry_service.list_entries(
        db, page=page, page_size=page_size, search=search, status_filter=status, category=category,
    )
    return PaginatedEntries(**data)


@router.get("/queue/next", response_model=EntryDetail)
async def queue_next_entry(
    status: str = Query(..., alias="status"),
    current_id: Optional[str] = Query(None),
    direction: str = Query("next", pattern="^(next|prev)$"),
    db: AsyncIOMotorDatabase = Depends(get_async_db),
) -> EntryDetail:
    data = await entry_service.get_queue_entry(
        db, status_filter=status, current_id=current_id, direction=direction,
    )
    if not data:
        raise HTTPException(status_code=404, detail="No entries in queue")
    return EntryDetail(**data)


@router.get("/{entry_id}", response_model=EntryDetail)
async def get_entry(
    entry_id: str,
    db: AsyncIOMotorDatabase = Depends(get_async_db),
) -> EntryDetail:
    data = await entry_service.get_entry(db, entry_id)
    return EntryDetail(**data)


@router.get("/{entry_id}/images", response_model=list[ImageSummary])
async def get_entry_images(
    entry_id: str,
    db: AsyncIOMotorDatabase = Depends(get_async_db),
) -> list[ImageSummary]:
    items = await entry_service.list_entry_images(db, entry_id)
    return [ImageSummary(**item) for item in items]


@router.post("/{entry_id}/reject", response_model=ActionResponse)
async def reject_entry(
    entry_id: str,
    body: RejectRequest,
    db: AsyncIOMotorDatabase = Depends(get_async_db),
) -> ActionResponse:
    if body.regenerate:
        data = await generation_service.regenerate_entry(
            db,
            entry_id,
            rejection_reason=body.rejection_reason,
            reviewer_vision=body.reviewer_vision,
            notes=body.notes,
        )
        return ActionResponse(**data)

    data = await entry_service.reject_entry(
        db,
        entry_id,
        body.rejection_reason,
        reviewer_vision=body.reviewer_vision,
        notes=body.notes,
    )
    return ActionResponse(**data)


@router.post("/{entry_id}/regenerate", response_model=ActionResponse)
async def regenerate_entry(
    entry_id: str,
    body: RegenerateRequest,
    db: AsyncIOMotorDatabase = Depends(get_async_db),
) -> ActionResponse:
    data = await generation_service.regenerate_entry(
        db,
        entry_id,
        rejection_reason=body.rejection_reason,
        reviewer_vision=body.reviewer_vision,
        notes=body.notes,
    )
    return ActionResponse(**data)


@router.post("/{entry_id}/select-image/{image_id}", response_model=ActionResponse)
async def select_image(
    entry_id: str,
    image_id: str,
    db: AsyncIOMotorDatabase = Depends(get_async_db),
) -> ActionResponse:
    data = await entry_service.select_image(db, entry_id, image_id)
    return ActionResponse(**data)


@router.post("/{entry_id}/approve", response_model=ActionResponse)
async def approve_entry(
    entry_id: str,
    db: AsyncIOMotorDatabase = Depends(get_async_db),
) -> ActionResponse:
    data = await entry_service.approve_entry(db, entry_id)
    return ActionResponse(**data)
