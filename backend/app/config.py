"""
Centralized settings.
All env vars are validated at startup - if anything is missing, the app
fails loudly with a clear error instead of breaking mysteriously later.
"""
from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # MongoDB
    mongo_uri: str
    mongo_db_name: str = "riyadh_dictionary"

    # Supabase
    supabase_url: str
    supabase_service_key: str
    supabase_bucket: str = "dictionary-images"

    # OpenAI
    openai_api_key: str
    openai_image_model: str = "gpt-image-1"
    openai_prompt_repair_model: str = "gpt-4o-mini"

    # Google
    google_sa_keyfile: str = "./google_service_account.json"
    sheets_spreadsheet_id: str = ""
    sheets_worksheet_name: str = ""

    # Security
    api_key: str

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton - settings are loaded once per process."""
    return Settings()
