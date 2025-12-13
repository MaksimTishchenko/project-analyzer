from pathlib import Path

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _make_min_project(tmp_path: Path) -> Path:
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
    project_root = _make_min_project(tmp_path)

    resp = client.post(
        "/analyze/local",
        json={"path": str(project_root), "use_llm": False, "include_tech_stack": True},
    )
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["project_path"] == str(project_root.resolve())
    assert "diagram_plantuml" in data
    assert "@startuml" in data["diagram_plantuml"]
    assert isinstance(data["python_files"], list)
    assert data["tech_stack"] is not None


def test_analyze_local_404_when_path_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_dir"

    resp = client.post("/analyze/local", json={"path": str(missing)})
    assert resp.status_code == 404
    assert "Path not found" in resp.json()["detail"]


def test_analyze_local_400_when_path_is_file(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("hi", encoding="utf-8")

    resp = client.post("/analyze/local", json={"path": str(f)})
    assert resp.status_code == 400
    assert "not a directory" in resp.json()["detail"].lower()


def test_analyze_local_400_when_path_empty() -> None:
    resp = client.post("/analyze/local", json={"path": ""})
    assert resp.status_code == 400
    assert "path is required" in resp.json()["detail"]
