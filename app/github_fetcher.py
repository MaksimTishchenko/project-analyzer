from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class GitHubFetcherError(Exception):
    """Базовая ошибка для операций получения репозитория."""


class GitHubFetcherNotImplemented(GitHubFetcherError):
    """
    Поднимается, если клонирование запрещено настройкой allow_clone.

    В проекте это используется как “защитный флаг”, чтобы случайно не запускать git
    в окружениях, где это нежелательно.
    """


class InvalidRepoUrl(GitHubFetcherError):
    """repo_url пустой или не соответствует поддерживаемому формату."""


class GitNotInstalled(GitHubFetcherError):
    """git не найден в PATH (невозможно выполнить clone/fetch/checkout)."""


class CloneFailed(GitHubFetcherError):
    """git завершился с ошибкой или истёк timeout выполнения команды."""


@dataclass(frozen=True)
class FetchResult:
    """
    Результат операции fetch.

    repo_url:
      URL репозитория (как был передан пользователем, после strip()).
    local_path:
      Путь к локальной копии (кэшируем в workspace_dir).
    ref:
      Опциональная ветка/тэг/коммит, если запрашивался.
    """
    repo_url: str
    local_path: Path
    ref: Optional[str] = None


class GitHubFetcher:
    """
    Клонирует GitHub-репозиторий в локальный workspace (кэш), опционально по ref.

    Возможности:
    - кэширует клоны в `workspace_dir` (по sha256(repo_url + ref));
    - shallow clone (`--depth 1`) для скорости;
    - если задан ref: делает `git fetch --depth 1 origin <ref>` и `checkout FETCH_HEAD`;
    - удаляет старые кэши по TTL (cache_ttl_hours).

    Важно:
    - по умолчанию allow_clone=False, чтобы внешне “ничего не происходило” без явного разрешения.
    - поддерживаются только https:// URL (как в исходнике).
    """

    def __init__(
        self,
        *,
        allow_clone: bool = False,
        workspace_dir: Optional[Path] = None,
        timeout_sec: int = 180,
        cache_ttl_hours: int = 72,
    ) -> None:
        self.allow_clone = allow_clone
        self.workspace_dir = workspace_dir or Path(".cache") / "repos"
        self.timeout_sec = timeout_sec
        self.cache_ttl_hours = cache_ttl_hours

    def fetch(self, repo_url: str, *, ref: Optional[str] = None) -> FetchResult:
        """
        Возвращает локальный путь к репозиторию (клон/кэш).

        Поведение:
        1) Валидация repo_url (не пустой, только https://)
        2) Проверка allow_clone и наличия git
        3) Создаём workspace_dir и чистим кэш по TTL
        4) Определяем target_dir по repo_url + ref
        5) Если .git уже есть — считаем кэш валидным и возвращаем путь
        6) Иначе: shallow clone
        7) Если задан ref — делаем shallow fetch ref и checkout FETCH_HEAD
        """
        repo_url = (repo_url or "").strip()
        if not repo_url:
            raise InvalidRepoUrl("repo_url is required")

        if not repo_url.startswith("https://"):
            raise InvalidRepoUrl("only https:// GitHub URLs are supported")

        if not self.allow_clone:
            # Сохраняем исходное поведение/сообщение.
            raise GitHubFetcherNotImplemented("не реализовано")

        if shutil.which("git") is None:
            raise GitNotInstalled("git is not installed or not in PATH")

        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_cache_ttl()

        target_dir = self._target_dir(repo_url, ref)

        # Если уже есть — считаем кэш валидным (как в исходнике).
        if (target_dir / ".git").exists():
            return FetchResult(repo_url=repo_url, local_path=target_dir, ref=ref)

        # Shallow clone для скорости; submodules выключены.
        self._run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--recurse-submodules=no",
                repo_url,
                str(target_dir),
            ],
            cwd=None,
        )

        if ref:
            # Для ветки/тэга shallow clone может не содержать нужный ref.
            # Поэтому делаем shallow fetch конкретного ref и чекаутим FETCH_HEAD.
            self._run(["git", "fetch", "--depth", "1", "origin", ref], cwd=target_dir)
            self._run(["git", "checkout", "FETCH_HEAD"], cwd=target_dir)

        return FetchResult(repo_url=repo_url, local_path=target_dir, ref=ref)

    def _target_dir(self, repo_url: str, ref: Optional[str]) -> Path:
        """
        Вычисляет директорию кэша для (repo_url, ref).

        Ключ зависит от ref, чтобы разные ветки/тэги не конфликтовали в одном кэше.
        """
        key = repo_url if not ref else f"{repo_url}#{ref}"
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return self.workspace_dir / h

    def _cleanup_cache_ttl(self) -> None:
        """
        Удаляет кэш-директории старше TTL.

        Удаляем только директории, которые выглядят как git-репо (с `.git`),
        чтобы не снести “посторонние” файлы.
        """
        ttl_sec = max(0, int(self.cache_ttl_hours)) * 3600
        if ttl_sec <= 0:
            return

        now = time.time()
        for d in self.workspace_dir.iterdir():
            if not d.is_dir():
                continue
            if not (d / ".git").exists():
                continue

            try:
                mtime = d.stat().st_mtime
            except OSError:
                continue

            if now - mtime > ttl_sec:
                shutil.rmtree(d, ignore_errors=True)

    def _run(self, cmd: list[str], *, cwd: Optional[Path]) -> None:
        """
        Запускает git-команду с таймаутом и превращает ошибки subprocess в CloneFailed.

        capture_output=True + text=True:
        - позволяет вернуть человеку сообщение из stderr/stdout без лишнего шума.
        """
        try:
            subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
        except subprocess.TimeoutExpired as e:
            raise CloneFailed(f"git timeout after {self.timeout_sec}s: {' '.join(cmd)}") from e
        except subprocess.CalledProcessError as e:
            msg = (e.stderr or e.stdout or "").strip()
            raise CloneFailed(msg or f"git failed: {' '.join(cmd)}") from e
