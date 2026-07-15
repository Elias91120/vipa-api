from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    vipagence_webhook_secret: str = ""
    port: int = 8000
    log_level: str = "INFO"

    @property
    def configured(self) -> bool:
        key = (self.supabase_service_role_key or "").strip()
        return bool(
            self.supabase_url
            and key
            and key not in {"REPLACE_ME", "changeme", "todo"}
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
