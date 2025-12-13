# app/settings.py
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # GitHub fetcher
    github_fetcher_allow_clone: bool = False
    github_fetcher_workspace_dir: Path = Path(".cache") / "repos"
    github_fetcher_timeout_sec: int = 180

    # cache cleanup
    github_fetcher_cache_ttl_hours: int = 72  # 3 дня

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
    )


settings = Settings()
