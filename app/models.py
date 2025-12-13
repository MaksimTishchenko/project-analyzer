# app/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class FunctionInfo:
    """Information about a function or method in the codebase."""

    name: str
    lineno: Optional[int] = None
    decorators: List[str] = field(default_factory=list)


@dataclass
class AttributeInfo:
    """Information about a class or instance attribute."""

    name: str
    type: Optional[str] = None
    lineno: Optional[int] = None
    is_instance: bool = True
    declared_in_init: bool = False


@dataclass
class CompositionInfo:
    """Represents relation A (*-- or o--) B (A has field of type B)."""

    owner: str
    attribute: str
    target: str
    lineno: Optional[int] = None

    # "composition" -> *-- (владение: создаём внутри)
    # "aggregation" -> o-- (ссылка: получили извне/аннотация)
    kind: str = "composition"


@dataclass
class ClassInfo:
    """Information about a class in a module."""

    name: str
    bases: List[str] = field(default_factory=list)

    # __init__ is tracked separately (if present)
    init: Optional[FunctionInfo] = None

    # other methods
    methods: List[FunctionInfo] = field(default_factory=list)

    # attributes and composition relations
    attributes: List[AttributeInfo] = field(default_factory=list)
    compositions: List[CompositionInfo] = field(default_factory=list)

    lineno: Optional[int] = None


@dataclass
class ModuleInfo:
    """Information about a Python module (file)."""

    path: Path
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)


@dataclass
class ProjectModel:
    """Aggregated information about the whole project."""

    modules: List[ModuleInfo] = field(default_factory=list)
    requirements_path: Optional[Path] = None

    # Optional dependency-related paths (kept for backward compatibility if you already use them)
    pyproject_path: Optional[Path] = None
    setup_cfg_path: Optional[Path] = None
    dependency_files: Dict[str, Path] = field(default_factory=dict)
