from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .code_parser import CodeParser
from .diagram_generator import DiagramAI, DiagramGenerator
from .file_scanner import FileScanner
from .github_fetcher import GitHubFetcher
from .settings import settings
from .tech_stack_analyzer import TechStackAnalyzer


def _to_jsonable(obj: Any) -> Any:
    """
    Приводит объект к JSON-сериализуемому виду.

    Поддерживает:
    - None -> None
    - Path -> str(path)
    - dataclass -> asdict + рекурсивное преобразование
    - dict/list/tuple/set -> рекурсивное преобразование элементов
    - pydantic-подобные объекты -> пробует `model_dump()` или `dict()`

    Важно:
    - Это *утилита вывода*, она не должна ломать анализ.
    - Любые ошибки сериализации "мягко" игнорируются и возвращается исходный объект.
    """
    if obj is None:
        return None

    if isinstance(obj, Path):
        return str(obj)

    if is_dataclass(obj):
        return _to_jsonable(asdict(obj))

    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(x) for x in obj]

    for attr in ("model_dump", "dict"):
        if hasattr(obj, attr):
            try:
                return _to_jsonable(getattr(obj, attr)())
            except Exception:
                pass

    return obj


def _compute_summary(project_model: Any) -> dict[str, int]:
    """
    Быстрый агрегированный summary по результату парсинга.

    Использует getattr(..., default) чтобы не зависеть жёстко от точной формы моделей.
    """
    modules = getattr(project_model, "modules", []) or []

    classes_count = 0
    functions_count = 0
    methods_count = 0
    imports_count = 0

    for m in modules:
        classes = getattr(m, "classes", []) or []
        funcs = getattr(m, "functions", []) or []
        imports = getattr(m, "imports", []) or []

        classes_count += len(classes)
        functions_count += len(funcs)
        imports_count += len(imports)

        for c in classes:
            methods = getattr(c, "methods", []) or []
            methods_count += len(methods)

    return {
        "modules": len(modules),
        "classes": classes_count,
        "functions": functions_count,
        "methods": methods_count,
        "imports": imports_count,
    }


def _enforce_analysis_root(root: Path) -> None:
    """
    Defense-in-depth "gate": если задан settings.analysis_root, анализируемый root
    обязан быть внутри него.

    IMPORTANT:
    - root должен быть уже resolved (без '..', symlinks схлопнуты)
    - analysis_root уже resolve/провалидирован в settings
    """
    if settings.analysis_root is None:
        return

    ar = settings.analysis_root
    try:
        root.relative_to(ar)
    except ValueError as e:
        raise ValueError(f"Path '{root}' is outside ANALYSIS_ROOT='{ar}'") from e


def _build_plantuml_generator(
    *,
    diagram_public_only: bool,
    diagram_group_by_module: bool,
    diagram_max_classes: int,
) -> DiagramGenerator:
    """
    Создаёт DiagramGenerator максимально безопасно по совместимости.

    Причина:
    - В некоторых версиях DiagramGenerator может не поддерживать max_classes
      (тогда конструктор бросит TypeError).
    - Мы сохраняем совместимость через try/except.
    """
    try:
        return DiagramGenerator(
            public_only=diagram_public_only,
            group_by_module=diagram_group_by_module,
            max_classes=diagram_max_classes,
        )
    except TypeError:
        return DiagramGenerator(
            public_only=diagram_public_only,
            group_by_module=diagram_group_by_module,
        )


def _normalize_diagram_format(diagram_format: str | None) -> str:
    """
    Нормализует формат диаграммы и валидирует, что он поддерживается.
    """
    fmt = (diagram_format or "plantuml").strip().lower()
    if fmt not in {"plantuml", "mermaid"}:
        raise ValueError("diagram_format must be 'plantuml' or 'mermaid'")
    return fmt


def analyze_local_project(
    path: str | Path,
    use_llm: bool = False,
    include_tech_stack: bool = True,
    diagram_group_by_module: bool = True,
    diagram_public_only: bool = False,
    diagram_format: str = "plantuml",
    diagram_max_classes: int = 40,
) -> dict[str, Any]:
    """
    Анализирует локальную директорию проекта и возвращает “расширенный” результат.

    Pipeline:
    1) Валидация root (существует, директория, resolve)
    2) Security gate: enforce analysis_root (если настроено)
    3) FileScanner -> список .py + dependency файлы
    4) CodeParser -> ProjectModel
    5) (опционально) TechStackAnalyzer
    6) Генерация диаграммы (PlantUML или Mermaid, опционально с LLM)
    7) Сбор результата: legacy-поля + meta/scan/summary/project_model/diagram
    """
    root = Path(path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    if not root.is_dir():
        raise ValueError(f"Root path is not a directory: {root}")

    # Resolve strictly to remove '..' and collapse symlinks.
    root = root.resolve()
    _enforce_analysis_root(root)

    scanner = FileScanner(root)
    scan_result = scanner.scan()

    parser = CodeParser()
    project = parser.parse_files(scan_result.python_files)

    # propagate dependency paths into ProjectModel (existing behavior)
    project.requirements_path = scan_result.requirements_file
    project.pyproject_path = scan_result.pyproject_file
    project.setup_cfg_path = scan_result.setup_cfg_file
    project.dependency_files = scan_result.dependency_files

    tech_stack: dict[str, Any] | None = None
    if include_tech_stack:
        tech_stack = TechStackAnalyzer().analyze(project)

    fmt = _normalize_diagram_format(diagram_format)

    # --- Diagram generation (safe mode) ---
    if fmt == "plantuml":
        generator = _build_plantuml_generator(
            diagram_public_only=diagram_public_only,
            diagram_group_by_module=diagram_group_by_module,
            diagram_max_classes=int(diagram_max_classes or 0),
        )

        if use_llm:
            diagram_text = DiagramAI(generator=generator).generate_with_llm(project)
        else:
            diagram_text = generator.generate_class_diagram(project)

    else:
        # Mermaid is optional. If not available -> clear error.
        try:
            from .diagram_generator_mermaid import MermaidDiagramGenerator  # type: ignore
        except Exception as e:
            raise ValueError(
                "Mermaid output is not available: missing MermaidDiagramGenerator "
                "(expected in app/diagram_generator_mermaid.py)."
            ) from e

        try:
            gen = MermaidDiagramGenerator(
                public_only=diagram_public_only,
                group_by_module=diagram_group_by_module,
                max_classes=int(diagram_max_classes or 0),
            )
        except TypeError:
            gen = MermaidDiagramGenerator(
                public_only=diagram_public_only,
                group_by_module=diagram_group_by_module,
            )

        diagram_text = gen.generate_class_diagram(project)

    # --- Backward compatible top-level fields ---
    result: dict[str, Any] = {
        "project_path": str(root),
        "python_files": [str(p) for p in scan_result.python_files],
        "requirements_path": str(scan_result.requirements_file) if scan_result.requirements_file else None,
        "pyproject_path": str(scan_result.pyproject_file) if scan_result.pyproject_file else None,
        "tech_stack": tech_stack,
        # legacy field: only meaningful for plantuml
        "diagram_plantuml": diagram_text if fmt == "plantuml" else None,
    }

    # --- Extended "pretty" contract ---
    result.update(
        {
            "meta": {
                "project_path": str(root),
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "options": {
                    "use_llm": bool(use_llm),
                    "include_tech_stack": bool(include_tech_stack),
                    "diagram_group_by_module": bool(diagram_group_by_module),
                    "diagram_public_only": bool(diagram_public_only),
                    "diagram_format": fmt,
                    "diagram_max_classes": int(diagram_max_classes),
                },
            },
            "scan": {
                "stats": _to_jsonable(scan_result.stats),
                "dependency_files": _to_jsonable(scan_result.dependency_files),
                "requirements_file": str(scan_result.requirements_file) if scan_result.requirements_file else None,
                "pyproject_file": str(scan_result.pyproject_file) if scan_result.pyproject_file else None,
                "setup_cfg_file": str(scan_result.setup_cfg_file) if scan_result.setup_cfg_file else None,
            },
            "summary": _compute_summary(project),
            "project_model": _to_jsonable(project),
            "diagram": {"format": fmt, "text": diagram_text},
        }
    )

    return result


def analyze_github_project(
    *,
    repo_url: str,
    ref: str | None = None,
    use_llm: bool = False,
    include_tech_stack: bool = True,
    diagram_group_by_module: bool = True,
    diagram_public_only: bool = False,
    diagram_format: str = "plantuml",
    diagram_max_classes: int = 40,
    allow_clone: bool = False,
    workspace_dir: Path | None = None,
    timeout_sec: int = 180,
    cache_ttl_hours: int = 72,
) -> dict[str, Any]:
    """
    Анализирует GitHub-репозиторий:
    - скачивает/кэширует repo (GitHubFetcher)
    - затем прогоняет analyze_local_project на локальном пути

    Примечание:
    - allow_clone по умолчанию False (безопасный режим)
    """
    fetcher = GitHubFetcher(
        allow_clone=allow_clone,
        workspace_dir=workspace_dir,
        timeout_sec=timeout_sec,
        cache_ttl_hours=cache_ttl_hours,
    )
    fetched = fetcher.fetch(repo_url, ref=ref)

    return analyze_local_project(
        path=fetched.local_path,
        use_llm=use_llm,
        include_tech_stack=include_tech_stack,
        diagram_group_by_module=diagram_group_by_module,
        diagram_public_only=diagram_public_only,
        diagram_format=diagram_format,
        diagram_max_classes=diagram_max_classes,
    )
