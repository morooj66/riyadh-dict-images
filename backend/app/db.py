"""
MongoDB connection helpers.

Two clients:
- get_sync_db()  → used by migration scripts and sync jobs (pymongo)
- get_async_db() → used by FastAPI endpoints (motor)

Both connect to the same database; they just differ in I/O model.
"""
from typing import Optional
from pymongo import MongoClient
from pymongo.database import Database
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings


# --- Sync (for scripts) ---
_sync_client: Optional[MongoClient] = None


def get_sync_client() -> MongoClient:
    global _sync_client
    if _sync_client is None:
        settings = get_settings()
        _sync_client = MongoClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=10_000,
            retryWrites=True,
            appname="riyadh-dict-migration",
        )
        # Fail fast: ping the server to confirm credentials work
        _sync_client.admin.command("ping")
    return _sync_client


def get_sync_db() -> Database:
    settings = get_settings()
    return get_sync_client()[settings.mongo_db_name]


# --- Async (for FastAPI) ---
_async_client: Optional[AsyncIOMotorClient] = None


def get_async_client() -> AsyncIOMotorClient:
    global _async_client
    if _async_client is None:
        settings = get_settings()
        _async_client = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=10_000,
            retryWrites=True,
            appname="riyadh-dict-api",
        )
    return _async_client


def get_async_db() -> AsyncIOMotorDatabase:
    settings = get_settings()
    return get_async_client()[settings.mongo_db_name]


# --- Collection names (single source of truth) ---
class Collections:
    ENTRIES = "entries"
    IMAGES = "images"
    REVIEWS = "reviews"
    GENERATION_JOBS = "generation_jobs"
    SYNC_LOG = "sync_log"
    MIGRATION_CHECKPOINTS = "migration_checkpoints"
