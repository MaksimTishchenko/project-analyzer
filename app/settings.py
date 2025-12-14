from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Глобальные настройки приложения (Pydantic Settings).

    Откуда берутся значения:
    - из переменных окружения
    - из файла .env (если присутствует)
    - иначе используются значения по умолчанию из полей класса

    Ключевой принцип:
    - все пути нормализуются/resolve’ятся как можно раньше, чтобы исключить двусмысленность.
    - security-sensitive опции (например analysis_root) валидируются максимально строго.
    """

    # ---------------------------------------------------------------------
    # GitHub fetcher
    # ---------------------------------------------------------------------
    github_fetcher_allow_clone: bool = False
    github_fetcher_workspace_dir: Path = Path(".cache") / "repos"
    github_fetcher_timeout_sec: int = 180

    # Cache cleanup (TTL для клонов в workspace_dir)
    github_fetcher_cache_ttl_hours: int = 72  # 3 days

    # ---------------------------------------------------------------------
    # Local analysis security
    # ---------------------------------------------------------------------
    # Если задан, локальный анализ разрешён только внутри этой директории.
    # Любые пути вне analysis_root должны быть отклонены (см. service._enforce_analysis_root).
    analysis_root: Path | None = None

    # ---------------------------------------------------------------------
    # LLM settings (optional)
    # ---------------------------------------------------------------------
    llm_enabled: bool = False
    llm_api_base: str | None = None  # например "http://localhost:1234" или "https://api.openai.com"
    llm_api_key: str | None = None   # локальным моделям обычно не нужен
    llm_model: str = "gpt-4.1-mini"
    llm_timeout_sec: int = 120

    # ---------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------
    @field_validator("analysis_root", mode="before")
    @classmethod
    def _validate_analysis_root(cls, v):
        """
        Нормализует и валидирует analysis_root (sandbox root).

        Правила:
        - None разрешён: sandbox отключён (FULL ACCESS) — использовать осторожно.
        - строка/путь -> Path, expanduser(~), затем resolve(strict=True)
        - путь обязан существовать и быть директорией
        """
        if v is None:
            return None

        p = Path(v).expanduser()
        try:
            p = p.resolve(strict=True)
        except FileNotFoundError as e:
            raise ValueError(f"analysis_root does not exist: {p}") from e

        if not p.is_dir():
            raise ValueError(f"analysis_root is not a directory: {p}")

        return p

    @field_validator("github_fetcher_workspace_dir", mode="before")
    @classmethod
    def _validate_workspace_dir(cls, v):
        """
        Нормализует workspace dir для кэшей git-клонов.

        Важно:
        - директория может ещё не существовать — это нормально (создаётся лениво при fetch()).
        """
        if v is None:
            return v
        return Path(v).expanduser().resolve()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",   # явно: без скрытых префиксов
        extra="ignore",  # лишние env vars не ломают загрузку
    )


# Singleton settings instance (единая точка доступа)
settings = Settings()
