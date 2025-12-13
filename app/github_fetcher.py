from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class GitHubFetcherError(Exception):
    """Base error for GitHub fetching."""


class GitHubFetcherNotImplemented(GitHubFetcherError):
    """Raised when cloning is not allowed."""


class InvalidRepoUrl(GitHubFetcherError):
    """Raised when repo_url is empty or invalid."""


class GitNotInstalled(GitHubFetcherError):
    """Raised when git is not available in PATH."""


class CloneFailed(GitHubFetcherError):
    """Raised when git clone/fetch/checkout fails."""


@dataclass(frozen=True)
class FetchResult:
    repo_url: str
    local_path: Path
    ref: Optional[str] = None


class GitHubFetcher:
    """
    Реальная реализация:
      - клонит репо в workspace_dir (кэш)
      - умеет ref (ветка/тэг/коммит)
      - авто-очистка старых кэшей по TTL
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
        repo_url = (repo_url or "").strip()
        if not repo_url:
            raise InvalidRepoUrl("repo_url is required")

        if not repo_url.startswith("https://"):
            raise InvalidRepoUrl("only https:// GitHub URLs are supported")

        if not self.allow_clone:
            raise GitHubFetcherNotImplemented("не реализовано")

        if shutil.which("git") is None:
            raise GitNotInstalled("git is not installed or not in PATH")

        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_cache_ttl()

        target_dir = self._target_dir(repo_url, ref)

        # Если уже есть — считаем валидным
        if (target_dir / ".git").exists():
            return FetchResult(repo_url=repo_url, local_path=target_dir, ref=ref)

        # Клон
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

        # Если нужен ref — чек-аут
        if ref:
            # для ветки/тэга shallow clone может не содержать ref -> делаем fetch ref
            # (безопасно, быстро, без полной истории)
            self._run(["git", "fetch", "--depth", "1", "origin", ref], cwd=target_dir)
            self._run(["git", "checkout", "FETCH_HEAD"], cwd=target_dir)

        return FetchResult(repo_url=repo_url, local_path=target_dir, ref=ref)

    def _target_dir(self, repo_url: str, ref: Optional[str]) -> Path:
        # Ключ кэша зависит и от ref (чтобы разные ветки не мешались)
        key = repo_url if not ref else f"{repo_url}#{ref}"
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return self.workspace_dir / h

    def _cleanup_cache_ttl(self) -> None:
        ttl_sec = max(0, int(self.cache_ttl_hours)) * 3600
        if ttl_sec <= 0:
            return

        now = time.time()
        for d in self.workspace_dir.iterdir():
            if not d.is_dir():
                continue
            git_dir = d / ".git"
            if not git_dir.exists():
                continue

            try:
                mtime = d.stat().st_mtime
            except OSError:
                continue

            if now - mtime > ttl_sec:
                shutil.rmtree(d, ignore_errors=True)

    def _run(self, cmd: list[str], *, cwd: Optional[Path]) -> None:
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
