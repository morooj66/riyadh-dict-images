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
    # quick data integrity: entries without valid current_image_id
    broken = await db["entries"].count_documents({"current_image_id": None})
    # orphan images (not linked to any entry)
    entry_ids = await db["entries"].distinct("_id")
    orphan = await db["images"].count_documents(
        {"entry_id": {"$nin": entry_ids}, "image_role": "original"}
    )
    return {
        "db_connected": True,
        "entries": entries_count,
        "images": images_count,
        "generation_jobs": jobs_count,
        "broken_current_image_id": broken,
        "orphan_images": orphan,
    }


@router.get("/db-ping")
async def db_ping() -> dict:
    """
    Public diagnostic: attempts a DB ping and returns connectivity status.
    Does NOT expose MONGO_URI or any credentials.
    Useful when /db-check returns 503 to understand the error type.
    """
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.config import get_settings
    try:
        settings = get_settings()
    except Exception as exc:
        return {"connected": False, "error_type": "settings_missing", "hint": str(exc)[:200]}

    # Detect URI type without revealing it
    uri = settings.mongo_uri
    if "localhost" in uri or "127.0.0.1" in uri:
        uri_type = "localhost"
    elif "mongodb+srv" in uri:
        uri_type = "atlas_srv"
    elif "mongodb.net" in uri:
        uri_type = "atlas"
    else:
        uri_type = "other"

    # Try 1: normal TLS (as configured)
    result_normal = await _try_mongo_ping(uri, tls_insecure=False)

    if result_normal["connected"]:
        return {"connected": True, "uri_type": uri_type, "tls_mode": "normal"}

    # Try 2: relaxed TLS (to distinguish TLS cert issue from IP block)
    result_insecure = await _try_mongo_ping(uri, tls_insecure=True)

    if result_insecure["connected"]:
        return {
            "connected": False,
            "uri_type": uri_type,
            "tls_mode": "normal_fails_insecure_ok",
            "error_type": result_normal["error_type"],
            "hint": "TLS certificate verification failing. Try adding ?tls=true&tlsAllowInvalidCertificates=true to MONGO_URI in HF Secrets.",
            "normal_error": result_normal["hint"][:200],
        }

    # Both fail — likely IP access list
    return {
        "connected": False,
        "uri_type": uri_type,
        "tls_mode": "both_fail",
        "error_type": result_normal["error_type"],
        "hint": (
            "Both normal and insecure TLS failed. "
            "Most likely cause: MongoDB Atlas IP Access List is blocking HF Spaces. "
            "Fix: Atlas → Network Access → Add IP Address → Allow Access From Anywhere (0.0.0.0/0)."
        ),
        "normal_error": result_normal["hint"][:200],
        "insecure_error": result_insecure["hint"][:200],
    }


async def _try_mongo_ping(uri: str, tls_insecure: bool) -> dict:
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    import re
    kwargs: dict = {"serverSelectionTimeoutMS": 7000}
    if tls_insecure:
        kwargs["tlsAllowInvalidCertificates"] = True
        kwargs["tlsAllowInvalidHostnames"] = True
    try:
        client = AsyncIOMotorClient(uri, **kwargs)
        await asyncio.wait_for(client.admin.command("ping"), timeout=8.0)
        client.close()
        return {"connected": True, "error_type": None, "hint": ""}
    except asyncio.TimeoutError:
        return {"connected": False, "error_type": "TimeoutError", "hint": "connection timed out"}
    except Exception as exc:
        err = str(exc)
        err_safe = re.sub(r"mongodb(\+srv)?://[^\s@]+@?", "[URI_REDACTED]://", err)
        return {"connected": False, "error_type": type(exc).__name__, "hint": err_safe[:300]}


@router.get("/storage-check", dependencies=[Depends(verify_api_key)])
async def storage_check() -> dict:
    """
    Diagnostic: tests Supabase Storage access.
    Returns bucket status and upload capability without exposing credentials.
    """
    import re
    try:
        from app.config import get_settings
        from supabase import create_client
        settings = get_settings()
        client = create_client(settings.supabase_url, settings.supabase_service_key)
        bucket_name = settings.supabase_bucket
    except Exception as e:
        return {"ok": False, "step": "init", "error": type(e).__name__, "hint": str(e)[:200]}

    # Step 1: list buckets
    try:
        buckets = client.storage.list_buckets()
        bucket_names = [b.name if hasattr(b, "name") else b.get("name") for b in buckets]
        bucket_exists = bucket_name in bucket_names
    except Exception as e:
        err = re.sub(r"(key|token|secret)[=:\s]+\S+", "[REDACTED]", str(e), flags=re.I)
        return {"ok": False, "step": "list_buckets", "error": type(e).__name__, "hint": err[:300]}

    if not bucket_exists:
        return {
            "ok": False,
            "step": "bucket_check",
            "bucket": bucket_name,
            "available_buckets": bucket_names,
            "hint": f"Bucket '{bucket_name}' not found. Create it in Supabase → Storage.",
        }

    # Step 2: try uploading a tiny test file
    try:
        test_path = "_diagnostic/ping.txt"
        test_data = b"ping"
        client.storage.from_(bucket_name).upload(
            path=test_path,
            file=test_data,
            file_options={"content-type": "text/plain", "upsert": "true"},
        )
        # Clean up
        try:
            client.storage.from_(bucket_name).remove([test_path])
        except Exception:
            pass
        return {"ok": True, "bucket": bucket_name, "upload_test": "passed"}
    except Exception as e:
        err = re.sub(r"(key|token|secret)[=:\s]+\S+", "[REDACTED]", str(e), flags=re.I)
        return {
            "ok": False,
            "step": "upload_test",
            "bucket": bucket_name,
            "error": type(e).__name__,
            "hint": err[:400],
        }


@router.get("/stats", response_model=StatsResponse, dependencies=[Depends(verify_api_key)])
async def stats(db: AsyncIOMotorDatabase = Depends(get_async_db)) -> StatsResponse:
    data = await entry_service.get_stats(db)
    return StatsResponse(**data)
