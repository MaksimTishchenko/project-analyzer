from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

from .models import ProjectModel

# =============================================================================
# Knowledge bases / heuristics
# =============================================================================

# sys.stdlib_module_names есть начиная с Python 3.10+. Здесь мы полагаемся на него,
# чтобы не считать стандартную библиотеку внешними зависимостями.
_STDLIB_MODULES: Set[str] = set(sys.stdlib_module_names)

# --- Шум/мусор, который НЕ должен считаться зависимостями ---
# 1) старые stdlib-модули из Python 2 (часто встречаются в совместимости/тестах)
_STDLIB_PY2_COMPAT: Set[str] = {
    "basehttpserver",
    "simplehttpserver",
    "stringio",
    "cstringio",
    "dummy_threading",
}

# 2) типичные локальные/служебные пакеты в репозиториях
_NOISE_PREFIXES: Tuple[str, ...] = (
    "tests",
    "test",
    "testing",
    "docs",
    "doc",
    "examples",
    "example",
    "scripts",
    "script",
    "tools",
    "tool",
    "bench",
    "benchmark",
    "conftest",
)


def _is_noise_module(name: str) -> bool:
    """
    Быстрая фильтрация «локального шума» по имени модуля.

    Примеры:
    - tests.*, docs.*, examples.*, scripts.*, tools.* и т.п.
    """
    n = (name or "").strip().lower()
    if not n:
        return True
    for pref in _NOISE_PREFIXES:
        if n == pref or n.startswith(pref + "."):
            return True
    return False


# --- Детекторы фреймворков/технологий ---
WEB_FRAMEWORKS: Set[str] = {
    "django",
    "flask",
    "fastapi",
    "starlette",
    "tornado",
    "sanic",
    "aiohttp",
}
WEB_RUNTIME: Set[str] = {"uvicorn", "gunicorn", "hypercorn", "granian"}
WEB_RELATED: Set[str] = {
    "pydantic",
    "sqlalchemy",
    "alembic",
    "jinja2",
    "httpx",
    "requests",
    "celery",
    "redis",
    "psycopg2",
    "asyncpg",
}

ML_CORE: Set[str] = {
    "torch",
    "tensorflow",
    "keras",
    "jax",
    "flax",
    "sklearn",
    "scikit-learn",
    "xgboost",
    "lightgbm",
    "catboost",
    "transformers",
    "datasets",
    "spacy",
    "opencv-python",
}
SCIENTIFIC_CORE: Set[str] = {
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "seaborn",
    "sympy",
    "statsmodels",
    "jupyter",
    "ipykernel",
    "notebook",
    "plotly",
    "bokeh",
    "astropy",
}

CLI_CORE: Set[str] = {"click", "typer", "rich", "textual", "prompt-toolkit", "docopt"}

DEV_TOOLS: Set[str] = {
    "pytest",
    "hypothesis",
    "black",
    "isort",
    "ruff",
    "flake8",
    "mypy",
    "pre-commit",
    "coverage",
    "tox",
}

# --- Категоризация пакетов для расширенного отчёта ---
CATEGORY_RULES: Dict[str, str] = {}
for p in WEB_FRAMEWORKS:
    CATEGORY_RULES[p] = "framework:web"
for p in WEB_RUNTIME:
    CATEGORY_RULES[p] = "runtime:web"
for p in WEB_RELATED:
    CATEGORY_RULES[p] = "web"
for p in ML_CORE:
    CATEGORY_RULES[p] = "ml"
for p in SCIENTIFIC_CORE:
    CATEGORY_RULES[p] = "scientific"
for p in CLI_CORE:
    CATEGORY_RULES[p] = "cli"
for p in DEV_TOOLS:
    CATEGORY_RULES[p] = "dev"


# =============================================================================
# Parsing utilities
# =============================================================================

def _normalize_package_name(raw: str) -> str:
    """
    Нормализует «пакетоподобную» строку до имени пакета (lowercase).

    Поддерживает входы:
    - "requests>=2.0"
    - "pydantic[email]"
    - "pkg.submodule"
    """
    raw = raw.strip()
    if not raw:
        return ""
    match = re.match(r"([A-Za-z0-9_.-]+)", raw)
    if not match:
        return ""
    name = match.group(1)
    name = name.split("[", 1)[0]
    name = name.split(".", 1)[0]
    return name.lower()


def _iter_import_modules(imports: Iterable[str]) -> Iterable[str]:
    """
    Извлекает top-level имя пакета из строк импортов, собранных CodeParser.

    Поддерживает:
    - "import a, b as c"
    - "from pkg.sub import x"
    - игнорирует относительные импорты "from ."
    """
    for stmt in imports:
        stmt = stmt.strip()
        if not stmt:
            continue

        # локальные относительные импорты
        if stmt.startswith("from ."):
            continue

        if stmt.startswith("from "):
            parts = stmt.split()
            if len(parts) >= 2:
                module = parts[1]
                module = module.split("import", 1)[0].strip().lstrip(".")
                name = module.split(".", 1)[0]
            else:
                continue
        elif stmt.startswith("import "):
            rest = stmt[len("import ") :]
            first_part = rest.split(",", 1)[0].strip()
            name = first_part.split(" as ", 1)[0].strip()
            name = name.split(".", 1)[0]
        else:
            continue

        pkg = _normalize_package_name(name)
        if pkg:
            yield pkg


def _parse_requirements(path: Optional[Path]) -> List[str]:
    """
    Простой парсер requirements.txt (консервативно).

    Пропускает:
    - пустые строки и комментарии
    - editable installs (-e ...)
    """
    if path is None or not path.is_file():
        return []
    packages: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-e "):
            continue
        pkg = _normalize_package_name(line)
        if pkg:
            packages.add(pkg)
    return sorted(packages)


def _safe_getattr(obj: Any, attr: str) -> Any:
    """getattr(..., None) как отдельная утилита для единообразия."""
    return getattr(obj, attr, None)


def _detect_pyproject_path(project: ProjectModel) -> Optional[Path]:
    """
    Пытается найти pyproject.toml разными путями.

    Порядок приоритетов:
    1) project.pyproject_path (если он уже проставлен service/file_scanner)
    2) рядом с requirements.txt (если оно есть)
    3) в корне проекта, если он доступен как root_path/project_path/path
    """
    pp = _safe_getattr(project, "pyproject_path")
    if isinstance(pp, (str, Path)):
        p = Path(pp)
        if p.is_file():
            return p

    req = _safe_getattr(project, "requirements_path")
    if isinstance(req, (str, Path)):
        reqp = Path(req)
        candidate = reqp.parent / "pyproject.toml"
        if candidate.is_file():
            return candidate

    root = _safe_getattr(project, "root_path") or _safe_getattr(project, "project_path") or _safe_getattr(project, "path")
    if isinstance(root, (str, Path)):
        candidate = Path(root) / "pyproject.toml"
        if candidate.is_file():
            return candidate

    return None


def _toml_load(path: Path) -> Dict[str, Any]:
    """
    Загружает TOML, если доступен tomllib (py3.11+).

    Гарантия: не выбрасывает исключения наружу — возвращает {} при ошибках.
    """
    if tomllib is None:
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_poetry_deps(
    pyproject_path: Optional[Path],
) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str], Dict[str, str]]:
    """
    Извлекает зависимости Poetry из `tool.poetry.*`.

    Возвращает:
    - deps: runtime зависимости (tool.poetry.dependencies, кроме python)
    - dev_deps: dev зависимости (tool.poetry.dev-dependencies и/или group.dev.dependencies)
    - optional_deps: зависимости других групп (group.<name>.dependencies, кроме dev)
    - scripts: entrypoints tool.poetry.scripts
    """
    if pyproject_path is None or not pyproject_path.is_file():
        return {}, {}, {}, {}

    data = _toml_load(pyproject_path)
    tool = (data.get("tool") or {})
    poetry = (tool.get("poetry") or {})

    deps: Dict[str, str] = {}
    dev_deps: Dict[str, str] = {}
    optional_deps: Dict[str, str] = {}
    scripts: Dict[str, str] = {}

    for name, spec in (poetry.get("dependencies") or {}).items():
        n = _normalize_package_name(str(name))
        if not n or n == "python":
            continue
        deps[n] = str(spec)

    groups = poetry.get("group") or {}
    for group_name, group_data in groups.items():
        gdeps = (group_data or {}).get("dependencies") or {}
        target = dev_deps if group_name == "dev" else optional_deps
        for name, spec in gdeps.items():
            n = _normalize_package_name(str(name))
            if not n or n == "python":
                continue
            target[n] = str(spec)

    for name, spec in (poetry.get("dev-dependencies") or {}).items():
        n = _normalize_package_name(str(name))
        if not n or n == "python":
            continue
        dev_deps[n] = str(spec)

    for name, target in (poetry.get("scripts") or {}).items():
        scripts[str(name)] = str(target)

    return deps, dev_deps, optional_deps, scripts


# =============================================================================
# Report model
# =============================================================================

@dataclass
class TechStackReport:
    """
    Нормализованный отчёт о стеке проекта.

    Этот объект — “внутренний DTO”: удобно держать структуру в одном месте,
    а наружу отдавать dict через as_dict() (плюс legacy-ключи в TechStackAnalyzer.analyze()).
    """

    imports: List[str]
    requirements: List[str]
    poetry_runtime: List[str]
    poetry_dev: List[str]
    poetry_optional: List[str]

    python_constraint: Optional[str]
    package_manager: str
    frameworks: List[str]

    categories: Dict[str, List[str]]
    all_packages: List[str]

    project_type: str
    type_scores: Dict[str, float]
    confidence: float

    signals: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        """
        Возвращает словарь в стабильном формате (удобно сериализовать в JSON).
        """
        return {
            "project_type": self.project_type,
            "confidence": self.confidence,
            "type_scores": self.type_scores,
            "tech_stack": {
                "python": {"constraint": self.python_constraint},
                "package_manager": self.package_manager,
                "frameworks": self.frameworks,
                "categories": self.categories,
                "all_packages": self.all_packages,
                "sources": {
                    "imports": self.imports,
                    "requirements_txt": self.requirements,
                    "poetry": {
                        "runtime": self.poetry_runtime,
                        "dev": self.poetry_dev,
                        "optional": self.poetry_optional,
                    },
                },
                "signals": self.signals,
            },
        }


# =============================================================================
# Public analyzer
# =============================================================================

class TechStackAnalyzer:
    """
    Собирает расширенный тех-отчёт по проекту.

    Особенность: одновременно поддерживает legacy-ключи:
      - result["frameworks"]
      - result["libraries"]
      - result["imports"]
    чтобы старые тесты/клиенты не ломались. :contentReference[oaicite:1]{index=1}
    """

    def analyze(self, project: ProjectModel) -> Dict[str, Any]:
        """
        Основной вход: принимает ProjectModel и возвращает dict-отчёт.

        Источники сигналов:
        - импорты из AST (project.modules[*].imports)
        - requirements.txt (если был найден)
        - pyproject.toml (Poetry), если доступен
        """
        imported_modules: Set[str] = set()
        raw_imports: List[str] = []

        # --- imports from analyzed modules ---
        for module in project.modules:
            raw_imports.extend(module.imports)
            for pkg in _iter_import_modules(module.imports):
                # шум/пустое/не-нормализованное будет отфильтровано далее
                imported_modules.add(pkg)

        # --- requirements.txt ---
        req_path = _safe_getattr(project, "requirements_path")
        req_list = _parse_requirements(Path(req_path)) if req_path else []
        requirement_modules = set(req_list)

        # --- Poetry deps (pyproject.toml) ---
        pyproject_path = _detect_pyproject_path(project)
        poetry_deps, poetry_dev, poetry_opt, poetry_scripts = _parse_poetry_deps(pyproject_path)

        poetry_runtime_pkgs = set(poetry_deps.keys())
        poetry_dev_pkgs = set(poetry_dev.keys())
        poetry_opt_pkgs = set(poetry_opt.keys())

        # --- python constraint (if available) ---
        python_constraint = None
        if pyproject_path and pyproject_path.is_file():
            data = _toml_load(pyproject_path)
            python_constraint = (
                (((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}).get("python")
                if isinstance(data, dict)
                else None
            )
            if python_constraint is not None:
                python_constraint = str(python_constraint)

        # --- merge all packages (imports + manifests) ---
        all_packages = (imported_modules | requirement_modules | poetry_runtime_pkgs | poetry_dev_pkgs | poetry_opt_pkgs)

        # Фильтрация:
        # - пустые
        # - stdlib (текущий python)
        # - stdlib py2 compat
        # - шумовые пространства имён (tests/docs/etc)
        all_packages = {
            p
            for p in all_packages
            if p
            and p not in _STDLIB_MODULES
            and p not in _STDLIB_PY2_COMPAT
            and not _is_noise_module(p)
        }

        frameworks = sorted([p for p in all_packages if p in WEB_FRAMEWORKS])

        # --- choose package manager label ---
        has_req = bool(requirement_modules)
        has_poetry = bool(pyproject_path and pyproject_path.is_file() and (poetry_runtime_pkgs or poetry_dev_pkgs or poetry_opt_pkgs))
        if has_poetry and has_req:
            package_manager = "mixed"
        elif has_poetry:
            package_manager = "poetry"
        elif has_req:
            package_manager = "pip"
        else:
            package_manager = "unknown"

        # --- categorize packages ---
        categories: Dict[str, Set[str]] = {}
        for pkg in all_packages:
            cat = CATEGORY_RULES.get(pkg, "library")
            categories.setdefault(cat, set()).add(pkg)
        # dev deps всегда считаем dev-категорией дополнительно
        for pkg in poetry_dev_pkgs:
            categories.setdefault("dev", set()).add(pkg)
        categories_out = {k: sorted(v) for k, v in sorted(categories.items(), key=lambda kv: kv[0])}

        # --- classify project type ---
        project_type, type_scores, confidence, signals = self._classify(
            all_packages=all_packages,
            frameworks=set(frameworks),
            poetry_scripts=poetry_scripts,
            pyproject_path=pyproject_path,
        )

        report = TechStackReport(
            imports=sorted(imported_modules),
            requirements=sorted(requirement_modules),
            poetry_runtime=sorted(poetry_runtime_pkgs),
            poetry_dev=sorted(poetry_dev_pkgs),
            poetry_optional=sorted(poetry_opt_pkgs),
            python_constraint=python_constraint,
            package_manager=package_manager,
            frameworks=frameworks,
            categories=categories_out,
            all_packages=sorted(all_packages),
            project_type=project_type,
            type_scores=type_scores,
            confidence=confidence,
            signals=signals,
        )

        out = report.as_dict()

        # --- LEGACY KEYS (для старых тестов/клиентов) ---
        # Старый формат ожидал эти ключи на верхнем уровне.
        out["frameworks"] = report.frameworks
        out["libraries"] = sorted([p for p in all_packages if p not in WEB_FRAMEWORKS])
        out["imports"] = raw_imports  # как было раньше: исходные строки импортов

        return out

    def _classify(
        self,
        *,
        all_packages: Set[str],
        frameworks: Set[str],
        poetry_scripts: Dict[str, str],
        pyproject_path: Optional[Path],
    ) -> Tuple[str, Dict[str, float], float, Dict[str, Any]]:
        """
        Классифицирует “тип проекта” на основе слабых сигналов (эвристика).

        Выход:
        - best_type: web/ml/cli/scientific/unknown
        - scores: score-таблица для каждого типа
        - confidence: [0..1], грубая уверенность
        - signals: отладочные сигналы (что именно сработало)
        """
        scores: Dict[str, float] = {"web": 0.0, "ml": 0.0, "cli": 0.0, "scientific": 0.0}

        for _ in frameworks:
            scores["web"] += 4.0

        if all_packages & WEB_RUNTIME:
            scores["web"] += 2.0
        scores["web"] += 0.5 * len(all_packages & WEB_RELATED)

        scores["ml"] += 1.5 * len(all_packages & ML_CORE)
        if all_packages & {"torch", "tensorflow", "jax"}:
            scores["ml"] += 2.0

        scores["scientific"] += 1.0 * len(all_packages & SCIENTIFIC_CORE)
        if all_packages & {"numpy", "scipy"}:
            scores["scientific"] += 1.0

        scores["cli"] += 1.2 * len(all_packages & CLI_CORE)
        if poetry_scripts:
            scores["cli"] += 4.0

        has_pyproject = bool(pyproject_path and pyproject_path.is_file())

        best_type = max(scores.items(), key=lambda kv: kv[1])[0]
        best_score = scores[best_type]
        sorted_scores = sorted(scores.values(), reverse=True)
        second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0.0

        margin = max(0.0, best_score - second_score)
        confidence = min(1.0, 0.25 * margin) if best_score > 0 else 0.0

        if best_score <= 0.0:
            best_type = "unknown"
            confidence = 0.0

        signals: Dict[str, Any] = {
            "has_pyproject": has_pyproject,
            "poetry_scripts": list(poetry_scripts.keys()),
            "frameworks_detected": sorted(frameworks),
            "web_runtime_detected": sorted(all_packages & WEB_RUNTIME),
            "web_related_hits": sorted(all_packages & WEB_RELATED),
            "ml_hits": sorted(all_packages & ML_CORE),
            "scientific_hits": sorted(all_packages & SCIENTIFIC_CORE),
            "cli_hits": sorted(all_packages & CLI_CORE),
        }

        return best_type, scores, confidence, signals
