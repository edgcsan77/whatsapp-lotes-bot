from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "whatsapp-lotes-bot"
    app_env: str = "production"
    tz: str = "America/Monterrey"

    app_host: str = "127.0.0.1"
    app_port: int = 8200
    app_secret_key: str

    database_url: str
    redis_url: str

    evolution_base_url: str
    evolution_api_key: str
    evolution_instance: str
    evolution_webhook_secret: str

    public_base_url: str
    admin_session_secret: str
    whatsapp_admin_jids: str = ""

    default_batch_interval_minutes: int = 15
    default_batch_max_items: int = 50
    default_daily_cutoff_time: str = "23:30"
    default_timezone: str = "America/Monterrey"

    chrome_bin: str = "/usr/bin/google-chrome-stable"
    chromedriver_bin: str = "/usr/local/bin/chromedriver-google"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
