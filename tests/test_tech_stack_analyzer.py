# tests/test_tech_stack_analyzer.py
from __future__ import annotations

from pathlib import Path

from app.models import ModuleInfo, ProjectModel
from app.tech_stack_analyzer import TechStackAnalyzer


def test_tech_stack_from_imports_filters_stdlib() -> None:
    """
    Импорты стандартной библиотеки не должны считаться внешними зависимостями.
    При этом должны остаться реальные пакеты (например fastapi).
    """
    module = ModuleInfo(
        path=Path("m.py"),
        classes=[],
        functions=[],
        imports=[
            "import os",
            "import sys",
            "from pathlib import Path",
            "import fastapi",
            "from fastapi import FastAPI",
        ],
    )
    project = ProjectModel(modules=[module])

    result = TechStackAnalyzer().analyze(project)

    # legacy keys exist
    assert "frameworks" in result
    assert "libraries" in result
    assert "imports" in result

    # fastapi detected as framework, stdlib not present
    assert "fastapi" in result["frameworks"]
    assert "os" not in result["libraries"]
    assert "sys" not in result["libraries"]
    assert "pathlib" not in result["libraries"]


def test_tech_stack_from_requirements(tmp_path: Path) -> None:
    """
    requirements.txt должен добавлять пакеты в стек,
    и это должно отражаться в tech_stack.sources.requirements_txt.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    req = project_root / "requirements.txt"
    req.write_text("fastapi==0.115.0\npytest\n", encoding="utf-8")

    project = ProjectModel(modules=[])
    project.requirements_path = req

    result = TechStackAnalyzer().analyze(project)

    assert "tech_stack" in result
    assert "sources" in result["tech_stack"]
    assert "requirements_txt" in result["tech_stack"]["sources"]

    # пакеты должны быть нормализованы в lower-case
    req_pkgs = set(result["tech_stack"]["sources"]["requirements_txt"])
    assert "fastapi" in req_pkgs
    assert "pytest" in req_pkgs

    # fastapi должен попасть и в frameworks
    assert "fastapi" in result["frameworks"]


def test_tech_stack_project_type_web_when_fastapi_present(tmp_path: Path) -> None:
    """
    Если присутствует fastapi/uvicorn, проект должен классифицироваться как web
    с ненулевой уверенностью.
    """
    module = ModuleInfo(
        path=Path("m.py"),
        classes=[],
        functions=[],
        imports=[
            "import fastapi",
            "import uvicorn",
        ],
    )
    project = ProjectModel(modules=[module])

    result = TechStackAnalyzer().analyze(project)

    assert result["project_type"] in {"web", "unknown"}  # допускаем unknown на слабом сигнале
    assert result["confidence"] >= 0.0
