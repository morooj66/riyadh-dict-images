from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_async_db
from app.dependencies import verify_api_key
from app.schemas import GenerationJobResponse
from app.services import generation as generation_service

router = APIRouter(prefix="/generation-jobs", tags=["generation"], dependencies=[Depends(verify_api_key)])


@router.get("/{job_id}", response_model=GenerationJobResponse)
async def get_generation_job(
    job_id: str,
    db: AsyncIOMotorDatabase = Depends(get_async_db),
) -> GenerationJobResponse:
    data = await generation_service.get_generation_job(db, job_id)
    return GenerationJobResponse(**data)
