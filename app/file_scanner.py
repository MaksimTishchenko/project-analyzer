# app/file_scanner.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set

SKIP_DIRS: Set[str] = {
    ".git",
    "__pycache__",
    "env",
    "venv",
    ".venv",
    "node_modules",
    ".idea",
    ".mypy_cache",
}

BINARY_EXTENSIONS: Set[str] = {
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


@dataclass
class ScanResult:
    """Result of scanning a directory for Python project files."""

    python_files: List[Path]
    requirements_file: Optional[Path] = None


class FileScanner:
    """
    Recursively scans a directory to collect Python files and requirements.txt.

    - ignores .git, __pycache__, env, venv, node_modules and similar
    - skips obvious binary files
    - returns list of .py files and path to requirements.txt (if present)
    """

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    def scan(self) -> ScanResult:
        python_files: List[Path] = []
        requirements_file: Optional[Path] = None

        if not self.root.is_dir():
            raise ValueError(f"Root path is not a directory: {self.root}")

        for directory, dirs, files in self._walk():
            dir_path = Path(directory)

            # Find requirements.txt (take the first one encountered)
            if requirements_file is None and "requirements.txt" in files:
                candidate = dir_path / "requirements.txt"
                if candidate.is_file():
                    requirements_file = candidate

            for filename in files:
                file_path = dir_path / filename

                # Skip binary files by extension
                if file_path.suffix.lower() in BINARY_EXTENSIONS:
                    continue

                # Only care about .py files for now
                if file_path.suffix.lower() == ".py":
                    python_files.append(file_path)

        python_files.sort()
        return ScanResult(
            python_files=python_files, requirements_file=requirements_file
        )

    def _walk(self) -> Iterable[tuple[str, list[str], list[str]]]:
        """
        Wrapper around Path.walk / os.walk that applies directory skipping rules.
        """

        # Python 3.11: Path.walk is available, but we stay compatible using rglob-like walk
        # Here we use os.walk implicitly via Path.walk if available.
        try:
            walker = self.root.walk()
        except AttributeError:  # fallback for older Python, though 3.11+ is preferred
            import os

            walker = os.walk(self.root)

        for directory, dirs, files in walker:
            # Filter out directories we want to skip
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            yield directory, dirs, files
