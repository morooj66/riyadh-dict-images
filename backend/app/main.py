from contextlib import asynccontextmanager
from pathlib import Path
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

from app.config import get_settings
from app.routers import entries, generation, health

logger = logging.getLogger(__name__)

# Frontend dist directory — exists in production Docker build, absent in local dev
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        s = get_settings()
        logger.info("Settings loaded OK. app_env=%s", s.app_env)
    except Exception as exc:
        logger.error("Settings validation failed at startup: %s", exc)
    yield


app = FastAPI(
    title="Riyadh Dictionary Reviewer API",
    version="0.1.0",
    lifespan=lifespan,
)

# Load settings gracefully — if a required env var is missing we still start
# the app and serve the /health & /config endpoints so the error is diagnosable.
try:
    settings = get_settings()
    _cors_origins = settings.cors_origins_list
except Exception as _settings_error:
    logger.error(
        "Could not load settings (%s). "
        "Check that all required HF Secrets are set: "
        "MONGO_URI, SUPABASE_URL, SUPABASE_SERVICE_KEY, OPENAI_API_KEY, API_KEY",
        _settings_error,
    )
    _cors_origins = ["*"]  # allow all origins so /health is reachable for debugging

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(entries.router)
app.include_router(generation.router)


@app.exception_handler(PyMongoError)
async def mongo_error_handler(_request: Request, exc: PyMongoError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "تعذر الاتصال بقاعدة البيانات — تحقق من MONGO_URI وقائمة IP المسموحة في MongoDB Atlas",
        },
    )


# ── Serve React frontend (production only — when dist/ exists) ────────────
# Must be registered AFTER all API routes so API paths take priority.
if _FRONTEND_DIST.exists():
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    @app.get("/icons.svg", include_in_schema=False)
    async def _static_svg(request: Request) -> FileResponse:
        name = request.url.path.lstrip("/")
        candidate = _FRONTEND_DIST / name
        if candidate.exists():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_spa(full_path: str) -> FileResponse:
        """Catch-all: serve index.html so React Router handles client-side paths."""
        return FileResponse(_FRONTEND_DIST / "index.html")
