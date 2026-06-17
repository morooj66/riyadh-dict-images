from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

from app.config import get_settings
from app.routers import entries, generation, health

# Frontend dist directory — exists in production Docker build, absent in local dev
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings()
    yield


app = FastAPI(
    title="Riyadh Dictionary Reviewer API",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
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
