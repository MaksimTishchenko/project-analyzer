from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple, Any

from .models import ProjectModel
from .llm_client import LLMClient


def _short_class_name(raw: str) -> str:
    """
    Возвращает «короткое» имя класса из произвольной строки.

    Зачем:
    - В данных анализа встречаются fully-qualified имена (`pkg.mod.Class`);
    - Также могут попадаться типы с generics/параметрами (`Class[T]`).

    Правила:
    - пустая/None строка -> пустая строка;
    - отрезаем всё после `[` (generic/параметры типа);
    - берём последний сегмент после `.` (короткое имя).
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
    Признак «публичности» по python-стилю именования.

    Сейчас используется для фильтрации методов:
    - публичные: не начинаются с `_`
    - приватные/служебные: начинаются с `_`
    """
    return bool(name) and not name.startswith("_")


def _module_to_package_name(path_str: str) -> str:
    """
    Преобразует путь файла модуля в имя пакета (для PlantUML `package`).

    Текущее поведение намеренно простое и стабильное:
    - берём stem у пути (имя файла без расширения).
    """
    return Path(path_str).stem


def _class_score(cls: Any) -> int:
    """
    Грубая эвристика важности класса для ограничения размера диаграммы (top-N).

    Чем больше «структурных» связей, тем выше приоритет:
    - количество методов (вес 2)
    - количество базовых классов / наследование (вес 3)
    - количество composition/aggregation связей (вес 3)

    Важно: это *не* строгая метрика, а лишь способ выбрать наиболее заметные классы.
    """
    return (
        len(getattr(cls, "methods", [])) * 2
        + len(getattr(cls, "bases", [])) * 3
        + len(getattr(cls, "compositions", [])) * 3
    )


@dataclass
class DiagramGenerator:
    """
    Генерирует PlantUML-диаграмму классов из `ProjectModel`.

    Основные опции:
    - public_only: показывать только публичные методы (не начинающиеся с `_`);
    - group_by_module: группировать классы по модулям через PlantUML `package`;
    - show_relations: добавлять наследование и композиции/агрегации;
    - max_classes: ограничить размер диаграммы (top-N по эвристике важности).
      Значение 0 означает «без ограничений».
    """

    public_only: bool = True
    group_by_module: bool = False
    show_relations: bool = True
    max_classes: int = 0  # 0 = без лимита

    def generate_class_diagram(
        self,
        project: ProjectModel,
        *,
        public_only: Optional[bool] = None,
        group_by_module: Optional[bool] = None,
        show_relations: Optional[bool] = None,
        max_classes: Optional[int] = None,
    ) -> str:
        """
        Собирает PlantUML-диаграмму классов для всего проекта.

        Важное поведение:
        - возвращает строку PlantUML, всегда включает `@startuml` и `@enduml`;
        - если включён max_classes > 0, отбирает top-N наиболее «важных» классов;
        - связи (inheritance/composition) рисуются только между *выбранными* классами.
        """
        public_only = self.public_only if public_only is None else public_only
        group_by_module = self.group_by_module if group_by_module is None else group_by_module
        show_relations = self.show_relations if show_relations is None else show_relations
        max_classes = self.max_classes if max_classes is None else max_classes

        lines: List[str] = ["@startuml", ""]

        # --- collect classes ---
        all_classes: List[Tuple[Any, Any]] = []
        for module in project.modules:
            for cls in module.classes:
                all_classes.append((module, cls))

        # --- sort & cut top-N ---
        if max_classes and max_classes > 0:
            all_classes.sort(key=lambda mc: _class_score(mc[1]), reverse=True)
            all_classes = all_classes[:max_classes]

        selected_class_names: Set[str] = {cls.name for _, cls in all_classes}

        def render_class(cls: Any) -> None:
            """
            Рендерит один class-блок PlantUML (с методами, отфильтрованными по public_only).
            """
            lines.append(f"class {cls.name} {{")
            for method in getattr(cls, "methods", []):
                if public_only and not _is_public(method.name):
                    continue
                # Сохраняем прежний формат: "+ methodName()"
                lines.append(f"    + {method.name}()")
            lines.append("}")
            lines.append("")

        # --- render classes ---
        if group_by_module:
            by_module: dict[str, List[Any]] = {}
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

        # --- inheritance (child --|> parent) ---
        inheritance: Set[Tuple[str, str]] = set()
        for _, cls in all_classes:
            for base in getattr(cls, "bases", []):
                parent = _short_class_name(base)
                if not parent or parent == "object":
                    continue
                # показываем только связи между классами, которые попали в текущую диаграмму
                if parent not in selected_class_names:
                    continue
                inheritance.add((cls.name, parent))

        for child, parent in sorted(inheritance):
            lines.append(f"{child} --|> {parent}")

        # --- composition / aggregation ---
        # tuple: (owner, arrow, target, label)
        relations: Set[Tuple[str, str, str, str]] = set()
        for _, cls in all_classes:
            for rel in getattr(cls, "compositions", []):
                a = rel.owner or cls.name
                b = _short_class_name(rel.target)
                if a not in selected_class_names or b not in selected_class_names:
                    continue

                kind = getattr(rel, "kind", "composition")
                arrow = "*--" if kind == "composition" else "o--"
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
    Обёртка над DiagramGenerator, которая при желании улучшает диаграмму через LLM.

    Гарантии поведения:
    - Всегда строим baseline диаграмму статически (есть безопасный fallback).
    - Если LLM выключен/не настроен -> возвращаем baseline.
    - Если LLM вернул некорректный формат -> возвращаем baseline.
    - Если в процессе запроса была ошибка -> возвращаем baseline.
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

        Алгоритм:
        1) Строим baseline диаграмму статически (без LLM).
        2) Если LLM не активен -> возвращаем baseline.
        3) Иначе отправляем baseline как вход и просим улучшить (с фильтрацией мусора/группировкой).
        4) Возвращаем ответ LLM только если он содержит валидный блок PlantUML.
        5) При любых проблемах возвращаем baseline.
        """
        static_diagram = self._generator.generate_class_diagram(project)

        if not self._client.is_enabled():
            return static_diagram

        prompt = (
            "Ты — помощник по анализу архитектуры Python-проектов.\n\n"
            "Ниже дана PlantUML-диаграмма классов, сгенерированная статическим анализом AST.\n"
            "Твоя задача — сделать её более аккуратной и обзорной:\n\n"
            "1. Удали второстепенные или технические классы (tests, utils, internal и т.п.), если они не критичны для архитектуры.\n"
            "2. Сгруппируй классы по смысловым подсистемам через package, если это уместно.\n"
            "3. Сохрани только самые важные связи наследования и композиции.\n"
            "4. СТРОГО сохрани синтаксис PlantUML.\n\n"
            "Важные требования:\n"
            "- Не добавляй никакого текста/комментариев вне блока диаграммы.\n"
            "- Обязательно оставь строки @startuml и @enduml.\n"
            "- Выведи только итоговый PlantUML, без пояснений.\n\n"
            "Вот исходная диаграмма:\n"
            "```plantuml\n"
            f"{static_diagram}\n"
            "```\n"
        ).strip()

        try:
            result = self._client.chat(prompt)
        except Exception:
            return static_diagram

        text = (result or "").strip()

        # Модель часто оборачивает ответ в fenced code block: ```plantuml ... ```
        if "```" in text:
            parts = text.split("```")
            if len(parts) >= 3:
                candidate = parts[1]
                # иногда первым идёт язык блока: "plantuml\n..."
                if "\n" in candidate:
                    first_line, rest = candidate.split("\n", 1)
                    if first_line.strip().lower() in {"plantuml", "puml"}:
                        candidate = rest
                text = candidate.strip()

        # Минимальная валидация: должны быть маркеры начала/конца диаграммы
        if "@startuml" in text and "@enduml" in text:
            return text

        return static_diagram
