# tests/test_file_scanner.py
from pathlib import Path

from app.file_scanner import FileScanner


def create_file(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_file_scanner_finds_python_files_and_requirements(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Valid Python files
    create_file(project_root / "main.py", "print('hello')")
    create_file(project_root / "module" / "utils.py", "def foo():\n    return 42\n")

    # Ignored directories
    create_file(project_root / ".git" / "ignored.py", "print('should be ignored')")
    create_file(project_root / "__pycache__" / "ignored.py", "print('ignored')")
    create_file(project_root / "env" / "ignored.py", "print('ignored')")
    create_file(project_root / "node_modules" / "ignored.js", "console.log('ignored');")

    # Binary file
    create_file(project_root / "image.png", "not a real png, but treated as binary")

    # requirements.txt
    create_file(project_root / "requirements.txt", "fastapi\npytest\n")

    scanner = FileScanner(project_root)
    result = scanner.scan()

    python_files_names = sorted(
        p.relative_to(project_root).as_posix() for p in result.python_files
    )

    assert "main.py" in python_files_names
    assert "module/utils.py" in python_files_names

    # Ensure files in skipped directories are not included
    assert ".git/ignored.py" not in python_files_names
    assert "__pycache__/ignored.py" not in python_files_names

    # requirements.txt found
    assert result.requirements_file is not None
    assert result.requirements_file.name == "requirements.txt"
    assert result.requirements_file.parent == project_root
