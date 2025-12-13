# app/service.py
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .code_parser import CodeParser
from .diagram_generator import DiagramAI, DiagramGenerator
from .file_scanner import FileScanner
from .tech_stack_analyzer import TechStackAnalyzer
from .github_fetcher import GitHubFetcher


def _to_jsonable(obj: Any) -> Any:
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


def _build_plantuml_generator(
    *,
    diagram_public_only: bool,
    diagram_group_by_module: bool,
    diagram_max_classes: int,
) -> DiagramGenerator:
    """
    Create DiagramGenerator safely:
    - If DiagramGenerator supports max_classes, pass it.
    - If not, don't pass it (to avoid TypeError).
    """
    # Try with max_classes (newer interface)
    try:
        return DiagramGenerator(
            public_only=diagram_public_only,
            group_by_module=diagram_group_by_module,
            max_classes=diagram_max_classes,
        )
    except TypeError:
        # Fallback to old interface (your current one)
        return DiagramGenerator(
            public_only=diagram_public_only,
            group_by_module=diagram_group_by_module,
        )


def analyze_local_project(
    path: str | Path,
    use_llm: bool = False,
    include_tech_stack: bool = True,
    diagram_group_by_module: bool = True,
    diagram_public_only: bool = False,
    diagram_format: str = "plantuml",
    diagram_max_classes: int = 40,
) -> dict[str, Any]:
    root = Path(path)

    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    if not root.is_dir():
        raise ValueError(f"Root path is not a directory: {root}")

    root = root.resolve()

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

    diagram_format_norm = (diagram_format or "plantuml").strip().lower()
    if diagram_format_norm not in {"plantuml", "mermaid"}:
        raise ValueError("diagram_format must be 'plantuml' or 'mermaid'")

    # --- Diagram generation (safe mode) ---
    if diagram_format_norm == "plantuml":
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
        # Mermaid is optional. We keep it safe: if module/class doesn't exist -> clear error.
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
            # Older mermaid generator interface
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
        "diagram_plantuml": diagram_text if diagram_format_norm == "plantuml" else None,
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
                    "diagram_format": diagram_format_norm,
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
            "diagram": {"format": diagram_format_norm, "text": diagram_text},
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
