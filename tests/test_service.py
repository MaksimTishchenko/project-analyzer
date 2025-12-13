# tests/test_service.py
from pathlib import Path

from app.service import analyze_local_project


def test_analyze_local_project_end_to_end(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Минимальный Python-файл с классом
    main_py = project_root / "main.py"
    main_py.write_text(
        "class Foo:\n"
        "    def bar(self):\n"
        "        return 42\n",
        encoding="utf-8",
    )

    # Минимальный requirements.txt
    reqs = project_root / "requirements.txt"
    reqs.write_text("fastapi==0.115.0\n", encoding="utf-8")

    result = analyze_local_project(project_root)

    assert result["project_path"] == str(project_root.resolve())
    assert str(main_py.resolve()) in result["python_files"]
    assert "diagram_plantuml" in result
    assert "@startuml" in result["diagram_plantuml"]
    assert result["tech_stack"] is not None
    assert "fastapi" in result["tech_stack"]["frameworks"]
