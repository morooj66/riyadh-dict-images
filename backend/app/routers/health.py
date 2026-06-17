from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_async_db
from app.dependencies import verify_api_key
from app.schemas import HealthResponse, StatsResponse
from app.services import entries as entry_service

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/stats", response_model=StatsResponse, dependencies=[Depends(verify_api_key)])
async def stats(db: AsyncIOMotorDatabase = Depends(get_async_db)) -> StatsResponse:
    data = await entry_service.get_stats(db)
    return StatsResponse(**data)
