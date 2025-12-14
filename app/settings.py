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

    # --- LLM settings ---
    #
    # По умолчанию LLM выключен, так что ничего не ломается.
    # Включить можно через переменные окружения или .env:
    #   LLM_ENABLED=true
    #   LLM_API_BASE=http://localhost:1234
    #   LLM_MODEL=...
    #
    llm_enabled: bool = False
    llm_api_base: str | None = None      # например: "http://localhost:1234" или "https://api.openai.com"
    llm_api_key: str | None = None       # для локальных моделей обычно не нужен
    llm_model: str = "gpt-4.1-mini"      # любое имя модели, которое понимает твой backend
    llm_timeout_sec: int = 120           # таймаут HTTP-запроса к LLM

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
    )


settings = Settings()
