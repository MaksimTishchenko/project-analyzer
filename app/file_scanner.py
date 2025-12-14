from __future__ import annotations

import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


# =============================================================================
# Defaults
# =============================================================================

DEFAULT_SKIP_DIRS: Set[str] = {
    ".git",
    "__pycache__",
    "env",
    "venv",
    ".venv",
    "node_modules",
    ".idea",
    ".mypy_cache",
}

DEFAULT_BINARY_EXTENSIONS: Set[str] = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".pdf",
    ".bin",
}

# We intentionally treat these as "dependency / metadata" files for Python projects.
DEPENDENCY_FILENAMES: Tuple[str, ...] = (
    "requirements.txt",
    "pyproject.toml",
    "setup.cfg",
)


# =============================================================================
# Result models (backward-safe)
# =============================================================================

@dataclass
class ScanStats:
    """
    Счётчики сканирования (наблюдаемость).

    Эти поля не участвуют в логике выбора файлов, но помогают:
    - логировать поведение сканера;
    - понимать, что и почему пропускается.
    """
    visited_dirs: int = 0
    visited_files: int = 0
    collected_python_files: int = 0

    skipped_by_dir_rule: int = 0
    skipped_by_gitignore: int = 0
    skipped_binary_ext: int = 0
    skipped_too_large: int = 0
    skipped_symlink: int = 0
    skipped_io_error: int = 0


@dataclass
class ScanResult:
    """
    Результат обхода проекта.

    Backward compatible поля (важно для существующего кода):
    - python_files
    - requirements_file

    Новые поля добавлены так, чтобы не ломать старый код:
    - pyproject_file / setup_cfg_file
    - dependency_files (словарь всех найденных dependency-файлов)
    - stats
    """
    python_files: List[Path]
    requirements_file: Optional[Path] = None

    pyproject_file: Optional[Path] = None
    setup_cfg_file: Optional[Path] = None

    dependency_files: Dict[str, Path] = field(default_factory=dict)
    stats: ScanStats = field(default_factory=ScanStats)


# =============================================================================
# Config
# =============================================================================

@dataclass(frozen=True)
class FileScannerConfig:
    """
    Конфиг сканера.

    max_file_size_bytes:
      - применяется ко всем файлам, которые мы собираем (и к .py, и к dependency файлам),
        чтобы сканер был предсказуемым и не тянул гигантские файлы.
    """
    skip_dirs: Set[str] = field(default_factory=lambda: set(DEFAULT_SKIP_DIRS))
    binary_extensions: Set[str] = field(default_factory=lambda: set(DEFAULT_BINARY_EXTENSIONS))
    max_file_size_bytes: int = 2 * 1024 * 1024  # 2 MiB
    respect_gitignore: bool = True
    # Рекомендуется True: предотвращает циклы и неожиданные обходы.
    skip_symlinks: bool = True


# =============================================================================
# .gitignore support
# =============================================================================

class IgnoreMatcher:
    """
    Мини-интерфейс матчера игнора.

    ignores(path, is_dir) -> True, если путь нужно пропустить.
    """
    def ignores(self, path: Path, is_dir: bool) -> bool:  # pragma: no cover
        raise NotImplementedError


class NoopIgnoreMatcher(IgnoreMatcher):
    """Матчер-заглушка: ничего не игнорирует."""
    def ignores(self, path: Path, is_dir: bool) -> bool:
        return False


class GitignoreMatcher(IgnoreMatcher):
    """
    Поддержка нескольких .gitignore внутри репозитория.

    Идея:
      - держим стек правил по уровням директорий (похоже на поведение git)
      - при входе в директорию: если есть .gitignore, добавляем его правила в стек
      - при выходе: убираем
      - последнее совпавшее правило побеждает, поддерживается negation (!)

    Реализация:
      - если установлен pathspec: используем gitwildmatch (максимально близко к git)
      - иначе: используем консервативный fnmatch fallback
    """

    def __init__(self, root: Path):
        self.root = root
        self._has_pathspec = False
        self._stack: List[Tuple[Path, object]] = []  # (base_dir, compiled_spec_or_rules)

        try:
            import pathspec  # type: ignore
            self._pathspec = pathspec
            self._has_pathspec = True
        except Exception:
            self._pathspec = None
            self._has_pathspec = False

    def push_dir(self, dir_path: Path) -> None:
        """Если в dir_path есть .gitignore — прочитать и добавить правила в стек."""
        gitignore = dir_path / ".gitignore"
        if not gitignore.is_file():
            return

        try:
            raw = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return

        lines: List[str] = []
        for line in raw:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(line)

        if not lines:
            return

        if self._has_pathspec:
            spec = self._pathspec.PathSpec.from_lines("gitwildmatch", lines)
            self._stack.append((dir_path, spec))
        else:
            self._stack.append((dir_path, list(lines)))

    def pop_dir(self, dir_path: Path) -> None:
        """Снять верхний уровень правил, если он относится к dir_path."""
        if self._stack and self._stack[-1][0] == dir_path:
            self._stack.pop()

    def ignores(self, path: Path, is_dir: bool) -> bool:
        """
        Проверяет, игнорируется ли path текущими правилами стека.

        Важно:
        - если path не лежит внутри root — ничего не игнорируем
        - при fallback-режиме поддерживаем общий смысл gitignore, но не 100% эквивалент git
        """
        rel_to_root = self._safe_rel(path, self.root)
        if rel_to_root is None:
            return False

        ignored: Optional[bool] = None

        for base_dir, spec_or_rules in self._stack:
            rel_to_base = self._safe_rel(path, base_dir)
            if rel_to_base is None:
                continue

            if self._has_pathspec:
                rel_str = rel_to_base.as_posix()
                if spec_or_rules.match_file(rel_str):
                    ignored = True
                continue

            rules: Sequence[str] = spec_or_rules  # type: ignore[assignment]
            rel_str = rel_to_base.as_posix()
            ignored = self._fallback_eval_rules(rules, rel_str, is_dir, ignored)

        return bool(ignored)

    @staticmethod
    def _safe_rel(path: Path, base: Path) -> Optional[Path]:
        """path.relative_to(base), но без исключений."""
        try:
            return path.relative_to(base)
        except ValueError:
            return None

    @staticmethod
    def _fallback_eval_rules(
        rules: Sequence[str],
        rel_path_posix: str,
        is_dir: bool,
        current: Optional[bool],
    ) -> Optional[bool]:
        """
        Fallback интерпретация gitignore-подобных правил через fnmatch.

        Особенности:
        - поддерживает negation (!)
        - поддерживает dir-only правила (оканчиваются на '/')
        - последнее совпавшее правило побеждает (как в git)
        """
        for pat in rules:
            neg = pat.startswith("!")
            pat_clean = pat[1:] if neg else pat

            dir_only = pat_clean.endswith("/")
            if dir_only:
                pat_clean = pat_clean[:-1]
                if not is_dir:
                    continue

            if not pat_clean:
                continue

            matched = False
            if "/" in pat_clean:
                matched = fnmatch(rel_path_posix, pat_clean)
            else:
                # Пытаемся сопоставить по имени, а затем по компонентам пути
                if fnmatch(Path(rel_path_posix).name, pat_clean):
                    matched = True
                else:
                    parts = rel_path_posix.split("/")
                    matched = any(fnmatch(p, pat_clean) for p in parts)

            if matched:
                current = (not neg)

        return current


# =============================================================================
# FileScanner
# =============================================================================

class FileScanner:
    """
    Рекурсивно сканирует директорию проекта и собирает:
    - список .py файлов
    - dependency/metadata файлы (requirements.txt / pyproject.toml / setup.cfg)

    Поведение сканера “production-oriented”:
    - skip_dirs (например .git, venv, node_modules)
    - опциональная поддержка .gitignore
    - пропуск очевидных бинарных расширений
    - (по умолчанию) пропуск symlink’ов, чтобы избежать циклов
    - лимит размера файла
    """

    def __init__(self, root: Path | str, config: Optional[FileScannerConfig] = None):
        self.root = Path(root).resolve()
        self.config = config or FileScannerConfig()

        if self.config.respect_gitignore:
            self._ignore: IgnoreMatcher = GitignoreMatcher(self.root)
        else:
            self._ignore = NoopIgnoreMatcher()

    def scan(self) -> ScanResult:
        """
        Запускает сканирование.

        Возвращает ScanResult:
        - python_files: отсортированный список Path до .py файлов
        - requirements_file / pyproject_file / setup_cfg_file: первый найденный файл каждого типа
        - dependency_files: карта всех найденных dependency файлов (по каноническому имени)
        - stats: счётчики обхода/пропусков
        """
        if not self.root.is_dir():
            raise ValueError(f"Root path is not a directory: {self.root}")

        stats = ScanStats()
        python_files: List[Path] = []

        dependency_files: Dict[str, Path] = {}
        requirements_file: Optional[Path] = None
        pyproject_file: Optional[Path] = None
        setup_cfg_file: Optional[Path] = None

        for dir_path, files in self._walk_dirs(stats):
            stats.visited_dirs += 1

            # Dependency files in this directory (если удовлетворяют общим условиям)
            for name in DEPENDENCY_FILENAMES:
                if name in files:
                    p = dir_path / name
                    if self._should_collect_file(p, stats):
                        dependency_files.setdefault(name, p)
                        if name == "requirements.txt" and requirements_file is None:
                            requirements_file = p
                        elif name == "pyproject.toml" and pyproject_file is None:
                            pyproject_file = p
                        elif name == "setup.cfg" and setup_cfg_file is None:
                            setup_cfg_file = p

            for filename in files:
                stats.visited_files += 1
                file_path = dir_path / filename

                if self.config.skip_symlinks and file_path.is_symlink():
                    stats.skipped_symlink += 1
                    continue

                if self.config.respect_gitignore and self._ignore.ignores(file_path, is_dir=False):
                    stats.skipped_by_gitignore += 1
                    continue

                if file_path.suffix.lower() in self.config.binary_extensions:
                    stats.skipped_binary_ext += 1
                    continue

                if file_path.suffix.lower() != ".py":
                    continue

                if not self._should_collect_file(file_path, stats):
                    # _should_collect_file уже увеличил нужный skipped_* счётчик
                    continue

                python_files.append(file_path)
                stats.collected_python_files += 1

        python_files.sort()

        return ScanResult(
            python_files=python_files,
            requirements_file=requirements_file,
            pyproject_file=pyproject_file,
            setup_cfg_file=setup_cfg_file,
            dependency_files=dependency_files,
            stats=stats,
        )

    def _walk_dirs(self, stats: ScanStats) -> Iterable[Tuple[Path, List[str]]]:
        """
        Обход директорий на базе `os.scandir`.

        Делает:
        - pruning по `skip_dirs`
        - pruning по `.gitignore` (для директорий), если включено
        - обработку symlink’ов согласно конфигу
        - сбор статистики по пропускам/ошибкам

        Возвращает итератор пар (dir_path, files_in_dir).
        """

        def iter_dir(dir_path: Path) -> Iterable[Tuple[Path, List[str]]]:
            try:
                with os.scandir(dir_path) as it:
                    entries = list(it)
            except OSError:
                stats.skipped_io_error += 1
                return

            files: List[str] = []
            subdirs: List[Path] = []

            for e in entries:
                try:
                    if self.config.skip_symlinks and e.is_symlink():
                        stats.skipped_symlink += 1
                        continue

                    if e.is_dir(follow_symlinks=not self.config.skip_symlinks):
                        if e.name in self.config.skip_dirs:
                            stats.skipped_by_dir_rule += 1
                            continue

                        p = Path(e.path)

                        if self.config.respect_gitignore and self._ignore.ignores(p, is_dir=True):
                            stats.skipped_by_gitignore += 1
                            continue

                        subdirs.append(p)

                    elif e.is_file(follow_symlinks=not self.config.skip_symlinks):
                        files.append(e.name)

                except OSError:
                    stats.skipped_io_error += 1
                    continue

            yield dir_path, files

            for sd in sorted(subdirs):
                if self.config.respect_gitignore and isinstance(self._ignore, GitignoreMatcher):
                    self._ignore.push_dir(sd)
                yield from iter_dir(sd)
                if self.config.respect_gitignore and isinstance(self._ignore, GitignoreMatcher):
                    self._ignore.pop_dir(sd)

        if self.config.respect_gitignore and isinstance(self._ignore, GitignoreMatcher):
            self._ignore.push_dir(self.root)
        yield from iter_dir(self.root)
        if self.config.respect_gitignore and isinstance(self._ignore, GitignoreMatcher):
            self._ignore.pop_dir(self.root)

    def _should_collect_file(self, path: Path, stats: ScanStats) -> bool:
        """
        Общие проверки для файлов, которые мы потенциально можем включить в результат:
        - файл должен существовать и быть обычным файлом
        - размер не должен превышать max_file_size_bytes
        """
        try:
            if not path.is_file():
                return False

            size = path.stat().st_size
            if size > self.config.max_file_size_bytes:
                stats.skipped_too_large += 1
                return False

            return True
        except OSError:
            stats.skipped_io_error += 1
            return False
