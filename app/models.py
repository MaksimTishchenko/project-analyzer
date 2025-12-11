# app/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class FunctionInfo:
    """Information about a function or method in the codebase."""

    name: str
    lineno: Optional[int] = None
    decorators: List[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    """Information about a class in a module."""

    name: str
    bases: List[str] = field(default_factory=list)
    methods: List[FunctionInfo] = field(default_factory=list)
    lineno: Optional[int] = None


@dataclass
class ModuleInfo:
    """
    Information about a Python module (file).

    path: filesystem path to the module
    """

    path: Path
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)


@dataclass
class ProjectModel:
    """
    Aggregated information about the whole project.

    modules: list of parsed Python modules
    requirements_path: optional path to requirements.txt (if present)
    """

    modules: List[ModuleInfo] = field(default_factory=list)
    requirements_path: Optional[Path] = None
