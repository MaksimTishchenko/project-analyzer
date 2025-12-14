from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set, Tuple, Any

from .models import ProjectModel


def _short_class_name(raw: str) -> str:
    """
    Нормализует имя типа/класса до короткого имени для диаграммы.

    Зачем:
    - базы/типы могут приходить как fully-qualified: `pkg.mod.Class`
    - могут содержать generics: `Class[T]`

    Правила:
    - пустая строка -> пустая строка
    - отрезаем всё после `[` (generic)
    - берём последний сегмент после `.`
    """
    name = (raw or "").strip()
    if not name:
        return ""
    if "[" in name:
        name = name.split("[", 1)[0]
    if "." in name:
        name = name.split(".")[-1]
    return name


def _is_public(name: str) -> bool:
    """
    Признак публичного метода по python-стилю именования: не начинается с `_`.
    """
    return bool(name) and not name.startswith("_")


def _class_score(cls: Any) -> int:
    """
    Эвристика "важности" класса для top-N ограничения диаграммы.

    Используется только когда `max_classes > 0`.
    Чем выше score, тем выше шанс попасть в итоговую диаграмму.
    """
    return (
        len(getattr(cls, "methods", [])) * 2
        + len(getattr(cls, "bases", [])) * 3
        + len(getattr(cls, "compositions", [])) * 3
    )


@dataclass
class MermaidDiagramGenerator:
    """
    Генератор Mermaid `classDiagram` из `ProjectModel`.

    Совместимость с ожиданиями проекта/тестов:
    - метод `.generate(project)` присутствует и вызывает `.generate_class_diagram()`
    - фильтрация методов `public_only`
    - наследование
    - composition/aggregation связи
    - `max_classes` (top-N)
    - `group_by_module` принимается (но намеренно игнорируется в Mermaid-версии)
    """

    public_only: bool = True
    show_relations: bool = True
    max_classes: int = 0
    group_by_module: bool = False  # параметр принят ради API-совместимости

    # --- Backward compatible API expected by tests ---
    def generate(self, project: ProjectModel) -> str:
        """
        Совместимый алиас для генерации диаграммы (как ожидают тесты/внешний код).
        """
        return self.generate_class_diagram(project)

    def generate_class_diagram(
        self,
        project: ProjectModel,
        *,
        public_only: Optional[bool] = None,
        show_relations: Optional[bool] = None,
        max_classes: Optional[int] = None,
        group_by_module: Optional[bool] = None,  # accepted, ignored
    ) -> str:
        """
        Собирает Mermaid classDiagram для проекта.

        Важное поведение:
        - `group_by_module` принимается, но не используется (Mermaid grouping не применяем)
        - если `max_classes > 0`, берём top-N по `_class_score`
        - связи рисуются только между классами, попавшими в текущий список (после top-N)
        """
        public_only = self.public_only if public_only is None else public_only
        show_relations = self.show_relations if show_relations is None else show_relations
        max_classes = self.max_classes if max_classes is None else max_classes
        _ = self.group_by_module if group_by_module is None else group_by_module  # намеренно игнорируем

        lines: List[str] = ["classDiagram"]

        # --- collect classes from all modules ---
        all_classes: List[Any] = []
        for module in project.modules:
            all_classes.extend(module.classes)

        # --- apply top-N limit if requested ---
        if max_classes and max_classes > 0:
            all_classes.sort(key=_class_score, reverse=True)
            all_classes = all_classes[:max_classes]

        class_names: Set[str] = {cls.name for cls in all_classes}

        # --- render class stubs and methods ---
        for cls in all_classes:
            lines.append(f"class {cls.name}")
            for method in getattr(cls, "methods", []):
                if public_only and not _is_public(method.name):
                    continue
                # Mermaid method notation: `Class : +method()`
                lines.append(f"{cls.name} : +{method.name}()")

        if not show_relations:
            return "\n".join(lines)

        # --- inheritance ---
        # Mermaid syntax: Parent <|-- Child
        inheritance: Set[Tuple[str, str]] = set()
        for cls in all_classes:
            for base in getattr(cls, "bases", []):
                parent = _short_class_name(base)
                if not parent or parent == "object":
                    continue
                if parent in class_names:
                    inheritance.add((cls.name, parent))

        for child, parent in sorted(inheritance):
            lines.append(f"{parent} <|-- {child}")

        # --- composition / aggregation ---
        relations: Set[Tuple[str, str, str, str]] = set()
        for cls in all_classes:
            for rel in getattr(cls, "compositions", []):
                a = rel.owner or cls.name
                b = _short_class_name(rel.target)
                if a not in class_names or b not in class_names:
                    continue

                kind = getattr(rel, "kind", "composition")
                arrow = "*--" if kind == "composition" else "o--"
                label = rel.attribute or ""
                relations.add((a, arrow, b, label))

        for a, arrow, b, label in sorted(relations):
            if label:
                lines.append(f"{a} {arrow} {b} : {label}")
            else:
                lines.append(f"{a} {arrow} {b}")

        return "\n".join(lines)
