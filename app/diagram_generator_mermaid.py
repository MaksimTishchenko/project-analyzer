from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from .models import ProjectModel


def _short_class_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        return ""
    if "[" in name:
        name = name.split("[", 1)[0]
    if "." in name:
        name = name.split(".")[-1]
    return name


def _is_public(name: str) -> bool:
    return bool(name) and not name.startswith("_")


def _class_score(cls) -> int:
    return (
        len(getattr(cls, "methods", [])) * 2
        + len(getattr(cls, "bases", [])) * 3
        + len(getattr(cls, "compositions", [])) * 3
    )


@dataclass
class MermaidDiagramGenerator:
    """
    Mermaid classDiagram generator.

    Compatibility with project expectations/tests:
    - .generate(project) method exists
    - public_only filtering
    - inheritance
    - composition/aggregation relations
    - max_classes (top-N)
    - group_by_module accepted (ignored)
    """
    public_only: bool = True
    show_relations: bool = True
    max_classes: int = 0
    group_by_module: bool = False  # accepted for API compatibility

    # --- Backward compatible API expected by tests ---
    def generate(self, project: ProjectModel) -> str:
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
        public_only = self.public_only if public_only is None else public_only
        show_relations = self.show_relations if show_relations is None else show_relations
        max_classes = self.max_classes if max_classes is None else max_classes
        _ = self.group_by_module if group_by_module is None else group_by_module

        lines: List[str] = ["classDiagram"]

        # collect classes
        all_classes = []
        for module in project.modules:
            all_classes.extend(module.classes)

        # top-N
        if max_classes and max_classes > 0:
            all_classes.sort(key=_class_score, reverse=True)
            all_classes = all_classes[:max_classes]

        class_names: Set[str] = {cls.name for cls in all_classes}

        # render class stubs + methods
        for cls in all_classes:
            lines.append(f"class {cls.name}")
            for method in cls.methods:
                if public_only and not _is_public(method.name):
                    continue
                lines.append(f"{cls.name} : +{method.name}()")

        if not show_relations:
            return "\n".join(lines)

        # inheritance
        inheritance: Set[Tuple[str, str]] = set()
        for cls in all_classes:
            for base in cls.bases:
                parent = _short_class_name(base)
                if not parent or parent == "object":
                    continue
                if parent in class_names:
                    inheritance.add((cls.name, parent))

        # Mermaid: Parent <|-- Child
        for child, parent in sorted(inheritance):
            lines.append(f"{parent} <|-- {child}")

        # composition / aggregation
        relations: Set[Tuple[str, str, str, str]] = set()
        for cls in all_classes:
            for rel in getattr(cls, "compositions", []):
                a = rel.owner or cls.name
                b = _short_class_name(rel.target)
                if a not in class_names or b not in class_names:
                    continue
                arrow = "*--" if getattr(rel, "kind", "composition") == "composition" else "o--"
                label = rel.attribute or ""
                relations.add((a, arrow, b, label))

        for a, arrow, b, label in sorted(relations):
            if label:
                lines.append(f"{a} {arrow} {b} : {label}")
            else:
                lines.append(f"{a} {arrow} {b}")

        return "\n".join(lines)
