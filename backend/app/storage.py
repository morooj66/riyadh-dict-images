"""
Supabase Storage helper.

Single responsibility: upload image bytes, return a stable public URL.
Used by both the migration script and FastAPI's /regenerate endpoint.

Bucket must be PUBLIC for stateless access (or generate signed URLs if
you ever flip it to private — see _public_url method).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from supabase import create_client, Client
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


@dataclass
class UploadResult:
    storage_path: str   # e.g. "entries/abc123.png"
    public_url: str     # e.g. "https://xxx.supabase.co/storage/v1/object/public/..."
    size_bytes: int


class SupabaseStorage:
    def __init__(self, client: Optional[Client] = None):
        settings = get_settings()
        self._settings = settings
        self._client = client or create_client(
            settings.supabase_url.strip(),
            settings.supabase_service_key.strip(),
        )
        # .strip() guards against accidental whitespace/newlines in HF Secrets
        self._bucket = settings.supabase_bucket.strip()

    # ------------------------------------------------------------------
    # Bucket lifecycle
    # ------------------------------------------------------------------
    def ensure_bucket(self, public: bool = True) -> None:
        """Idempotent: creates the bucket if missing, no-op otherwise."""
        try:
            buckets = self._client.storage.list_buckets()
            names = {b.name if hasattr(b, "name") else b["name"] for b in buckets}
            if self._bucket in names:
                return
            self._client.storage.create_bucket(
                self._bucket,
                options={"public": public, "file_size_limit": 10 * 1024 * 1024},
            )
        except Exception as e:
            # If bucket already exists, ignore. Re-raise anything else.
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                return
            raise

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def upload_bytes(
        self,
        data: bytes,
        storage_path: str,
        content_type: str = "image/png",
        upsert: bool = True,
    ) -> UploadResult:
        """
        Upload raw bytes to `storage_path` (e.g. "entries/migrafa_v1.png").
        Returns a stable public URL.
        """
        self._client.storage.from_(self._bucket).upload(
            path=storage_path,
            file=data,
            file_options={
                "content-type": content_type,
                "upsert": "true" if upsert else "false",
                "cache-control": "31536000",   # 1 year, images are immutable
            },
        )
        return UploadResult(
            storage_path=storage_path,
            public_url=self._public_url(storage_path),
            size_bytes=len(data),
        )

    def upload_file(self, file_path: str | Path, storage_path: str) -> UploadResult:
        p = Path(file_path)
        return self.upload_bytes(p.read_bytes(), storage_path)

    # ------------------------------------------------------------------
    # URLs
    # ------------------------------------------------------------------
    def _public_url(self, storage_path: str) -> str:
        return self._client.storage.from_(self._bucket).get_public_url(storage_path)

    def exists(self, storage_path: str) -> bool:
        """Check if an object exists (cheap HEAD-style call)."""
        try:
            folder = "/".join(storage_path.split("/")[:-1]) or ""
            filename = storage_path.split("/")[-1]
            results = self._client.storage.from_(self._bucket).list(folder)
            return any(
                (r.get("name") if isinstance(r, dict) else r.name) == filename
                for r in results
            )
        except Exception:
            return False

    def delete(self, storage_path: str) -> None:
        self._client.storage.from_(self._bucket).remove([storage_path])
