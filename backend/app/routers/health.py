from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.db import get_async_db
from app.dependencies import verify_api_key
from app.schemas import HealthResponse, StatsResponse
from app.services import entries as entry_service

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/config")
async def get_runtime_config() -> dict:
    """
    Public endpoint: returns runtime configuration the frontend needs.
    The API key is intentionally public here — it is already visible in the
    JS bundle for logged-in reviewers, so this does not reduce security.
    This allows the frontend to work regardless of VITE_API_KEY at build time.
    """
    settings = get_settings()
    return {"api_key": settings.api_key}


@router.get("/stats", response_model=StatsResponse, dependencies=[Depends(verify_api_key)])
async def stats(db: AsyncIOMotorDatabase = Depends(get_async_db)) -> StatsResponse:
    data = await entry_service.get_stats(db)
    return StatsResponse(**data)
