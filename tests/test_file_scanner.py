from __future__ import annotations

from pathlib import Path

from app.file_scanner import FileScanner, FileScannerConfig


def create_file(path: Path, content: str = "") -> None:
    """
    Утилита для тестов: создаёт файл, гарантируя существование директорий.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_file_scanner_finds_python_files_and_requirements(tmp_path):
    """
    Базовый сценарий:
    - находятся .py файлы
    - игнорируются стандартные директории (.git, __pycache__, env, node_modules)
    - бинарные расширения пропускаются
    - requirements.txt обнаруживается
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Валидные Python-файлы
    create_file(project_root / "main.py", "print('hello')")
    create_file(project_root / "module" / "utils.py", "def foo():\n    return 42\n")

    # Игнорируемые директории
    create_file(project_root / ".git" / "ignored.py", "print('should be ignored')")
    create_file(project_root / "__pycache__" / "ignored.py", "print('ignored')")
    create_file(project_root / "env" / "ignored.py", "print('ignored')")
    create_file(project_root / "node_modules" / "ignored.js", "console.log('ignored');")

    # Бинарный файл (по расширению должен быть пропущен)
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

    # Проверяем, что файлы из skip-директорий не попали в результат
    assert ".git/ignored.py" not in python_files_names
    assert "__pycache__/ignored.py" not in python_files_names

    # requirements.txt найден
    assert result.requirements_file is not None
    assert result.requirements_file.name == "requirements.txt"
    assert result.requirements_file.parent == project_root


def test_file_scanner_respects_gitignore(tmp_path):
    """
    Сканер должен уважать .gitignore и пропускать файлы по его правилам.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    create_file(project_root / "keep.py", "print('ok')")
    create_file(project_root / "ignored.py", "print('no')")

    # Игнорируем ignored.py
    create_file(project_root / ".gitignore", "ignored.py\n")

    scanner = FileScanner(project_root)
    result = scanner.scan()

    names = {p.relative_to(project_root).as_posix() for p in result.python_files}
    assert "keep.py" in names
    assert "ignored.py" not in names


def test_file_scanner_gitignore_negation_unignores(tmp_path):
    """
    .gitignore с negation (!) должен работать:
    - *.py игнорирует всё
    - !keep.py возвращает конкретный файл обратно
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    create_file(project_root / "a.py", "print('a')")
    create_file(project_root / "keep.py", "print('keep')")

    create_file(project_root / ".gitignore", "*.py\n!keep.py\n")

    scanner = FileScanner(project_root)
    result = scanner.scan()

    names = {p.relative_to(project_root).as_posix() for p in result.python_files}
    assert "keep.py" in names
    assert "a.py" not in names


def test_file_scanner_max_file_size_skips_large_files(tmp_path):
    """
    Проверка лимита размера файла:
    - маленький файл включается
    - большой файл пропускается
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Маленький файл
    create_file(project_root / "small.py", "print(1)\n")

    # Большой файл (достаточно большой для лимита ниже)
    create_file(project_root / "big.py", "x" * 100)

    config = FileScannerConfig(max_file_size_bytes=50)  # big.py должен быть пропущен
    scanner = FileScanner(project_root, config=config)
    result = scanner.scan()

    names = {p.relative_to(project_root).as_posix() for p in result.python_files}
    assert "small.py" in names
    assert "big.py" not in names


def test_file_scanner_finds_pyproject_and_setup_cfg(tmp_path):
    """
    Сканер должен находить dependency/metadata файлы:
    - pyproject.toml
    - setup.cfg
    - requirements.txt

    И заполнять как legacy-поля, так и новый dependency_files.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    create_file(project_root / "pyproject.toml", "[project]\nname = 'demo'\n")
    create_file(project_root / "setup.cfg", "[metadata]\nname = demo\n")
    create_file(project_root / "requirements.txt", "pytest\n")

    scanner = FileScanner(project_root)
    result = scanner.scan()

    assert result.pyproject_file is not None
    assert result.pyproject_file.name == "pyproject.toml"

    assert result.setup_cfg_file is not None
    assert result.setup_cfg_file.name == "setup.cfg"

    # legacy-поле
    assert result.requirements_file is not None
    assert result.requirements_file.name == "requirements.txt"

    # агрегированная карта зависимостей
    assert result.dependency_files["pyproject.toml"].name == "pyproject.toml"
    assert result.dependency_files["setup.cfg"].name == "setup.cfg"
    assert result.dependency_files["requirements.txt"].name == "requirements.txt"


def test_scan_result_is_backward_compatible(tmp_path):
    """
    Защитный тест от регрессий:
    старый код ожидает, что эти поля всегда существуют.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()

    create_file(project_root / "main.py", "print('hello')")
    create_file(project_root / "requirements.txt", "pytest\n")

    result = FileScanner(project_root).scan()

    # старые поля
    assert isinstance(result.python_files, list)
    assert result.requirements_file is not None

    # новые поля (не должны отсутствовать)
    assert hasattr(result, "pyproject_file")
    assert hasattr(result, "setup_cfg_file")
    assert hasattr(result, "dependency_files")
    assert hasattr(result, "stats")
