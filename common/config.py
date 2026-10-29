from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    request_timeout: int = 120
    # dev：写 server/logs/sessions + latest.md；prod：只写 app.log
    app_env: Literal["dev", "prod"] = "dev"


settings = Settings()


def is_dev() -> bool:
    return settings.app_env == "dev"
