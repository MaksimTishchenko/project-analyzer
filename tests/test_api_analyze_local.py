from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from main import app
from app.settings import settings

client = TestClient(app)


def _make_min_project(tmp_path: Path) -> Path:
    """
    Создаёт минимальный валидный проект для тестов /analyze/local.

    Состав:
    - main.py с одним классом и методом (чтобы диаграмма не была пустой)
    - requirements.txt (чтобы tech_stack мог что-то обнаружить)
    """
    project = tmp_path / "project"
    project.mkdir()

    (project / "main.py").write_text(
        "class Foo:\n"
        "    def bar(self):\n"
        "        return 42\n",
        encoding="utf-8",
    )

    (project / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
    return project


def test_analyze_local_ok(tmp_path: Path) -> None:
    """
    Happy-path: корректный проект -> 200 + базовые поля присутствуют.

    Важно:
    - временно отключаем sandbox (analysis_root), чтобы тест не зависел от окружения.
    """
    old_root = settings.analysis_root
    settings.analysis_root = None  # avoid sandbox affecting tests

    try:
        project_root = _make_min_project(tmp_path)

        resp = client.post(
            "/analyze/local",
            json={"path": str(project_root), "use_llm": False, "include_tech_stack": True},
        )
        assert resp.status_code == 200, resp.text

        data = resp.json()
        assert data["project_path"] == str(project_root.resolve())
        assert "diagram_plantuml" in data
        assert data["diagram_plantuml"] is None or "@startuml" in data["diagram_plantuml"]
        assert isinstance(data["python_files"], list)
        assert data["tech_stack"] is not None
    finally:
        settings.analysis_root = old_root


def test_analyze_local_404_when_path_missing(tmp_path: Path) -> None:
    """Если путь не существует — API отвечает 404 и в detail есть 'Path not found'."""
    old_root = settings.analysis_root
    settings.analysis_root = None

    try:
        missing = tmp_path / "no_such_dir"
        resp = client.post("/analyze/local", json={"path": str(missing)})
        assert resp.status_code == 404
        assert "Path not found" in resp.json()["detail"]
    finally:
        settings.analysis_root = old_root


def test_analyze_local_400_when_path_is_file(tmp_path: Path) -> None:
    """Если path указывает на файл — API отвечает 400 и сообщает, что это не директория."""
    old_root = settings.analysis_root
    settings.analysis_root = None

    try:
        f = tmp_path / "file.txt"
        f.write_text("hi", encoding="utf-8")

        resp = client.post("/analyze/local", json={"path": str(f)})
        assert resp.status_code == 400
        assert "not a directory" in resp.json()["detail"].lower()
    finally:
        settings.analysis_root = old_root


def test_analyze_local_422_when_path_empty() -> None:
    """
    Пустой path — это validation-style ошибка (422).

    Важно: текст ошибки должен содержать 'path is required'.
    """
    resp = client.post("/analyze/local", json={"path": ""})
    assert resp.status_code == 422
    assert "path is required" in resp.json()["detail"]


def test_analyze_local_403_when_outside_analysis_root(tmp_path: Path) -> None:
    """
    Если sandbox включён (analysis_root задан), а path вне его — API отвечает 403.
    """
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()

    old_root = settings.analysis_root
    settings.analysis_root = inside

    try:
        resp = client.post("/analyze/local", json={"path": str(outside)})
        assert resp.status_code == 403
        assert "outside ANALYSIS_ROOT" in resp.json()["detail"]
    finally:
        settings.analysis_root = old_root
