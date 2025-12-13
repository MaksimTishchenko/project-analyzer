# app/file_scanner.py
from __future__ import annotations

import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

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

DEPENDENCY_FILENAMES: Tuple[str, ...] = (
    "requirements.txt",
    "pyproject.toml",
    "setup.cfg",
)


# -----------------------------
# Result models (backward-safe)
# -----------------------------

@dataclass
class ScanStats:
    """Optional scan statistics for logging/observability."""
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
    Result of scanning a directory for Python project files.

    Backward compatible:
      - python_files (existing)
      - requirements_file (existing)

    New fields are optional / defaulted, so old code won't break.
    """
    python_files: List[Path]
    requirements_file: Optional[Path] = None

    # New dependency/metadata files
    pyproject_file: Optional[Path] = None
    setup_cfg_file: Optional[Path] = None

    # All dependency files found, keyed by canonical name
    dependency_files: Dict[str, Path] = field(default_factory=dict)

    # Observability
    stats: ScanStats = field(default_factory=ScanStats)


# -----------------------------
# Config
# -----------------------------

@dataclass(frozen=True)
class FileScannerConfig:
    """
    Scanner configuration.

    max_file_size_bytes:
      - applies to all files we might include (e.g., .py)
      - dependency files are also checked (keeps scanner predictable)
    """
    skip_dirs: Set[str] = field(default_factory=lambda: set(DEFAULT_SKIP_DIRS))
    binary_extensions: Set[str] = field(default_factory=lambda: set(DEFAULT_BINARY_EXTENSIONS))
    max_file_size_bytes: int = 2 * 1024 * 1024  # 2 MiB
    respect_gitignore: bool = True
    # Whether to skip symlinks (recommended to avoid cycles / unexpected traversal)
    skip_symlinks: bool = True


# -----------------------------
# .gitignore support
# -----------------------------

class IgnoreMatcher:
    """Interface-like base for ignore matchers."""
    def ignores(self, path: Path, is_dir: bool) -> bool:  # pragma: no cover (simple interface)
        raise NotImplementedError


class NoopIgnoreMatcher(IgnoreMatcher):
    def ignores(self, path: Path, is_dir: bool) -> bool:
        return False


class GitignoreMatcher(IgnoreMatcher):
    """
    Supports multiple .gitignore files in a repo.

    Strategy:
      - we keep a stack of rules per directory level (like git)
      - on entering a directory, if it has a .gitignore, we load it and push rules
      - on leaving, we pop
      - last matching rule wins, supports negation (!)

    If `pathspec` is installed: uses gitwildmatch semantics (closer to real git).
    Else: uses a conservative fnmatch-based fallback (good enough for most repos).
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
            # fallback: store raw patterns; implement minimal "!" negation
            self._stack.append((dir_path, list(lines)))

    def pop_dir(self, dir_path: Path) -> None:
        if self._stack and self._stack[-1][0] == dir_path:
            self._stack.pop()

    def ignores(self, path: Path, is_dir: bool) -> bool:
        """
        Evaluate stacked .gitignore rules.
        If any matcher applies, the last matching rule in the traversal stack wins.
        """
        rel_to_root = self._safe_rel(path, self.root)
        if rel_to_root is None:
            return False

        # Git matches dirs with trailing slash; we pass that info to the fallback.
        ignored: Optional[bool] = None

        for base_dir, spec_or_rules in self._stack:
            # only apply .gitignore that are in an ancestor dir
            rel_to_base = self._safe_rel(path, base_dir)
            if rel_to_base is None:
                continue

            if self._has_pathspec:
                # PathSpec expects posix-ish paths
                rel_str = rel_to_base.as_posix()
                # IMPORTANT: pathspec doesn't automatically treat dirs with trailing slash;
                # but common patterns still work (e.g., "dist/", "*.pyc", etc.).
                if spec_or_rules.match_file(rel_str):
                    ignored = True
                # pathspec handles negation internally, so we can't easily compute "last rule wins"
                # by ourselves here. However PathSpec already follows gitwildmatch including negation,
                # so `match_file()` reflects final decision for that spec.
                # Therefore: later .gitignore files (deeper) should override earlier -> we keep iterating.
                continue

            # Fallback matching: last matching rule wins across all stacked files.
            rules: Sequence[str] = spec_or_rules  # type: ignore[assignment]
            rel_str = rel_to_base.as_posix()
            ignored = self._fallback_eval_rules(rules, rel_str, is_dir, ignored)

        return bool(ignored)

    @staticmethod
    def _safe_rel(path: Path, base: Path) -> Optional[Path]:
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
        Minimal gitignore-ish evaluator:
          - supports comments stripped earlier
          - supports "!" negation
          - supports trailing "/" for directories
          - supports glob matching with fnmatch
          - supports patterns without "/" as "match anywhere" (approx.)
        """
        for pat in rules:
            neg = pat.startswith("!")
            pat_clean = pat[1:] if neg else pat

            # directory-only rule "foo/" -> match only dirs
            dir_only = pat_clean.endswith("/")
            if dir_only:
                pat_clean = pat_clean[:-1]
                if not is_dir:
                    continue

            if not pat_clean:
                continue

            # If pattern contains '/', match against the relative path.
            # If it doesn't, approximate git behavior by matching basename or any segment.
            matched = False
            if "/" in pat_clean:
                matched = fnmatch(rel_path_posix, pat_clean)
            else:
                # match basename
                if fnmatch(Path(rel_path_posix).name, pat_clean):
                    matched = True
                else:
                    # match any segment
                    parts = rel_path_posix.split("/")
                    matched = any(fnmatch(p, pat_clean) for p in parts)

            if matched:
                current = (not neg)
        return current


# -----------------------------
# FileScanner itself
# -----------------------------

class FileScanner:
    """
    Recursively scans a directory to collect Python files and dependency metadata files.

    Production-oriented behavior:
      - directory skip rules (like before)
      - optional .gitignore support
      - skip obvious binary extensions
      - skip symlinks (default)
      - skip files larger than config.max_file_size_bytes
      - returns first occurrences of key dependency files + full map for convenience
    """

    def __init__(self, root: Path | str, config: Optional[FileScannerConfig] = None):
        self.root = Path(root).resolve()
        self.config = config or FileScannerConfig()

        if self.config.respect_gitignore:
            self._ignore = GitignoreMatcher(self.root)
        else:
            self._ignore = NoopIgnoreMatcher()

    def scan(self) -> ScanResult:
        if not self.root.is_dir():
            raise ValueError(f"Root path is not a directory: {self.root}")

        stats = ScanStats()
        python_files: List[Path] = []

        dependency_files: Dict[str, Path] = {}
        requirements_file: Optional[Path] = None
        pyproject_file: Optional[Path] = None
        setup_cfg_file: Optional[Path] = None

        for dir_path, files in self._walk_dirs():
            stats.visited_dirs += 1

            # Check dependency files in this directory first (fast path)
            # We store the first occurrence (like your current requirements behavior).
            for name in DEPENDENCY_FILENAMES:
                if name in files:
                    p = dir_path / name
                    if self._should_collect_file(p):
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

                # .gitignore check (files)
                if self.config.respect_gitignore and self._ignore.ignores(file_path, is_dir=False):
                    stats.skipped_by_gitignore += 1
                    continue

                # Skip binary files by extension
                if file_path.suffix.lower() in self.config.binary_extensions:
                    stats.skipped_binary_ext += 1
                    continue

                # Only collect .py here (dependency files handled above)
                if file_path.suffix.lower() != ".py":
                    continue

                # Enforce size limit
                if not self._should_collect_file(file_path):
                    stats.skipped_too_large += 1
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

    def _walk_dirs(self) -> Iterable[Tuple[Path, List[str]]]:
        """
        os.walk-based traversal with:
          - skip_dirs pruning
          - .gitignore pruning for directories (when enabled)
          - symlink handling (when enabled)
        """
        # We implement our own stack-aware traversal to support .gitignore push/pop.
        # This avoids expensive "match against every .gitignore in repo" approaches.

        def iter_dir(dir_path: Path) -> Iterable[Tuple[Path, List[str]]]:
            try:
                with os.scandir(dir_path) as it:
                    entries = list(it)
            except OSError:
                return

            files: List[str] = []
            subdirs: List[Path] = []

            for e in entries:
                try:
                    is_symlink = e.is_symlink()
                    if self.config.skip_symlinks and is_symlink:
                        continue

                    if e.is_dir(follow_symlinks=not self.config.skip_symlinks):
                        # Skip by explicit dir rules (name-based)
                        if e.name in self.config.skip_dirs:
                            continue

                        p = Path(e.path)

                        # .gitignore check (dirs)
                        if self.config.respect_gitignore and self._ignore.ignores(p, is_dir=True):
                            continue

                        subdirs.append(p)
                    elif e.is_file(follow_symlinks=not self.config.skip_symlinks):
                        files.append(e.name)
                except OSError:
                    # stat race etc.
                    continue

            yield dir_path, files

            for sd in sorted(subdirs):
                # push .gitignore rules for this dir (if any)
                if self.config.respect_gitignore and isinstance(self._ignore, GitignoreMatcher):
                    self._ignore.push_dir(sd)
                yield from iter_dir(sd)
                if self.config.respect_gitignore and isinstance(self._ignore, GitignoreMatcher):
                    self._ignore.pop_dir(sd)

        # Initialize root .gitignore
        if self.config.respect_gitignore and isinstance(self._ignore, GitignoreMatcher):
            self._ignore.push_dir(self.root)
        yield from iter_dir(self.root)
        if self.config.respect_gitignore and isinstance(self._ignore, GitignoreMatcher):
            self._ignore.pop_dir(self.root)

    def _should_collect_file(self, path: Path) -> bool:
        """
        Common checks for files we might include:
          - must be a regular file
          - must be <= max size
        """
        try:
            if not path.is_file():
                return False
            size = path.stat().st_size
            return size <= self.config.max_file_size_bytes
        except OSError:
            return False
