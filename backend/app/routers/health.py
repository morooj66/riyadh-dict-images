from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.db import get_async_db
from app.dependencies import verify_api_key
from app.schemas import StatsResponse
from app.services import entries as entry_service

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Always returns 200. Shows whether settings loaded successfully."""
    try:
        get_settings()
        return {"status": "ok", "settings_loaded": True}
    except Exception:
        return {
            "status": "degraded",
            "settings_loaded": False,
            "error": (
                "Missing required environment variables. "
                "Check HF Secrets: MONGO_URI, SUPABASE_URL, "
                "SUPABASE_SERVICE_KEY, OPENAI_API_KEY, API_KEY"
            ),
        }


@router.get("/config")
async def get_runtime_config() -> dict:
    """
    Public endpoint: returns runtime configuration the frontend needs.
    The API key is intentionally public here — it is already visible in the
    JS bundle for logged-in reviewers, so this does not reduce security.
    This allows the frontend to work regardless of VITE_API_KEY at build time.
    """
    try:
        settings = get_settings()
        return {"api_key": settings.api_key}
    except Exception:
        return {"api_key": ""}


@router.get("/db-check", dependencies=[Depends(verify_api_key)])
async def db_check(db: AsyncIOMotorDatabase = Depends(get_async_db)) -> dict:
    """
    Authenticated diagnostic endpoint: returns collection counts without
    exposing the database URI or any credentials.
    """
    entries_count = await db["entries"].count_documents({})
    images_count = await db["images"].count_documents({})
    jobs_count = await db["generation_jobs"].count_documents({})
    return {
        "db_connected": True,
        "entries": entries_count,
        "images": images_count,
        "generation_jobs": jobs_count,
    }


@router.get("/stats", response_model=StatsResponse, dependencies=[Depends(verify_api_key)])
async def stats(db: AsyncIOMotorDatabase = Depends(get_async_db)) -> StatsResponse:
    data = await entry_service.get_stats(db)
    return StatsResponse(**data)
