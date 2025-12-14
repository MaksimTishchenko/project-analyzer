from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .models import ProjectModel
from .llm_client import LLMClient

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


def _module_to_package_name(path_str: str) -> str:
    return Path(path_str).stem


def _class_score(cls) -> int:
    """
    Heuristic importance score:
    - methods
    - bases
    - compositions
    """
    return (
        len(getattr(cls, "methods", [])) * 2
        + len(getattr(cls, "bases", [])) * 3
        + len(getattr(cls, "compositions", [])) * 3
    )


@dataclass
class DiagramGenerator:
    """
    Generates PlantUML class diagrams from ProjectModel.

    NEW:
    - max_classes: limit diagram size (top-N by importance)
    """

    public_only: bool = True
    group_by_module: bool = False
    show_relations: bool = True
    max_classes: int = 0  # 0 = no limit

    def generate_class_diagram(
        self,
        project: ProjectModel,
        *,
        public_only: Optional[bool] = None,
        group_by_module: Optional[bool] = None,
        show_relations: Optional[bool] = None,
        max_classes: Optional[int] = None,
    ) -> str:
        public_only = self.public_only if public_only is None else public_only
        group_by_module = self.group_by_module if group_by_module is None else group_by_module
        show_relations = self.show_relations if show_relations is None else show_relations
        max_classes = self.max_classes if max_classes is None else max_classes

        lines: List[str] = ["@startuml", ""]

        # --- collect classes ---
        all_classes = []
        for module in project.modules:
            for cls in module.classes:
                all_classes.append((module, cls))

        # --- sort & cut top-N ---
        if max_classes and max_classes > 0:
            all_classes.sort(key=lambda mc: _class_score(mc[1]), reverse=True)
            all_classes = all_classes[:max_classes]

        selected_class_names: Set[str] = {cls.name for _, cls in all_classes}

        def render_class(cls) -> None:
            lines.append(f"class {cls.name} {{")
            for method in cls.methods:
                if public_only and not _is_public(method.name):
                    continue
                lines.append(f"    + {method.name}()")
            lines.append("}")
            lines.append("")

        # --- render classes ---
        if group_by_module:
            by_module: dict[str, List] = {}
            for module, cls in all_classes:
                by_module.setdefault(str(module.path), []).append(cls)

            for module_path, classes in by_module.items():
                pkg = _module_to_package_name(module_path)
                lines.append(f'package "{pkg}" {{')
                for cls in classes:
                    render_class(cls)
                lines.append("}")
                lines.append("")
        else:
            for _, cls in all_classes:
                render_class(cls)

        if not show_relations:
            lines.append("@enduml")
            return "\n".join(lines)

        # --- inheritance ---
        inheritance: Set[Tuple[str, str]] = set()
        for _, cls in all_classes:
            for base in cls.bases:
                parent = _short_class_name(base)
                if not parent or parent == "object":
                    continue
                if parent not in selected_class_names:
                    continue
                inheritance.add((cls.name, parent))

        for child, parent in sorted(inheritance):
            lines.append(f"{child} --|> {parent}")

        # --- composition / aggregation ---
        relations: Set[Tuple[str, str, str, str]] = set()
        for _, cls in all_classes:
            for rel in cls.compositions:
                a = rel.owner or cls.name
                b = _short_class_name(rel.target)
                if a not in selected_class_names or b not in selected_class_names:
                    continue
                arrow = "*--" if getattr(rel, "kind", "composition") == "composition" else "o--"
                label = rel.attribute or ""
                relations.add((a, arrow, b, label))

        for a, arrow, b, label in sorted(relations):
            if label:
                lines.append(f'{a} {arrow} {b} : "{label}"')
            else:
                lines.append(f"{a} {arrow} {b}")

        lines.append("")
        lines.append("@enduml")
        return "\n".join(lines)


class DiagramAI:
    """
    Обёртка над DiagramGenerator, которая при желании прогоняет диаграмму через LLM.

    Поведение по умолчанию:
    - если LLM выключен или не сконфигурирован -> просто возвращаем статическую диаграмму;
    - если LLM включён, но во время запроса произошла ошибка -> тихо откатываемся к статике.
    """

    def __init__(
        self,
        generator: DiagramGenerator | None = None,
        client: LLMClient | None = None,
    ) -> None:
        self._generator = generator or DiagramGenerator()
        self._client = client or LLMClient()

    def generate_with_llm(self, project: ProjectModel) -> str:
        """
        Генерирует PlantUML-диаграмму с учётом LLM.

        1) Всегда строит baseline-диаграмму статически (чтобы было на что откатиться).
        2) Если LLM выключен/не настроен -> сразу возвращает baseline.
        3) Если LLM отвечает валидным PlantUML (@startuml/@enduml) -> возвращаем его.
        4) Если что-то пошло не так -> возвращаем baseline.
        """
        # Базовая диаграмма (как раньше)
        static_diagram = self._generator.generate_class_diagram(project)

        # Если LLM не включен или не настроен — ничего не меняем
        if not self._client.is_enabled():
            return static_diagram

        prompt = f"""
Ты — помощник по анализу архитектуры Python-проектов.

Ниже дана PlantUML-диаграмма классов, сгенерированная статическим анализом AST.
Твоя задача — сделать её более аккуратной и обзорной:

1. Удали второстепенные или технические классы (tests, utils, internal и т.п.), если они не критичны для архитектуры.
2. Сгруппируй классы по смысловым подсистемам через package, если это уместно.
3. Сохрани только самые важные связи наследования и композиции.
4. СТРОГО сохрани синтаксис PlantUML.

Важные требования:
- Не добавляй никакого текста/комментариев вне блока диаграммы.
- Обязательно оставь строки @startuml и @enduml.
- Выведи только итоговый PlantUML, без пояснений.

Вот исходная диаграмма:
```plantuml
{static_diagram}
```
""".strip()

        try:
            result = self._client.chat(prompt)
        except Exception:
            return static_diagram

        text = (result or "").strip()

        # Модель часто оборачивает ответ в ```plantuml ... ```
        if "```" in text:
            # вытаскиваем внутренности fenced-кода
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
                # может быть "plantuml\n@startuml..."
                if "\n" in text:
                    first_line, rest = text.split("\n", 1)
                    if first_line.strip().lower() in {"plantuml", "puml"}:
                        text = rest
                text = text.strip()

        if "@startuml" in text and "@enduml" in text:
            return text

        return static_diagram