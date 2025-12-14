from __future__ import annotations

from pathlib import Path

from app.diagram_generator_mermaid import MermaidDiagramGenerator
from app.models import (
    ClassInfo,
    CompositionInfo,
    FunctionInfo,
    ModuleInfo,
    ProjectModel,
)


def test_mermaid_renders_composition_and_aggregation() -> None:
    """
    Mermaid-генератор должен:
    - рендерить composition как `*--`
    - рендерить aggregation как `o--`
    - печатать label атрибута после двоеточия без кавычек (как ожидает текущий формат)
    """
    a = ClassInfo(name="A", bases=[], methods=[], lineno=1)
    b = ClassInfo(name="B", bases=[], methods=[], lineno=2)
    c = ClassInfo(name="C", bases=[], methods=[], lineno=3)

    a.compositions.append(CompositionInfo(owner="A", attribute="b", target="B", kind="composition"))
    a.compositions.append(CompositionInfo(owner="A", attribute="c", target="C", kind="aggregation"))

    module = ModuleInfo(path=Path("m.py"), classes=[a, b, c], functions=[], imports=[])
    project = ProjectModel(modules=[module])

    gen = MermaidDiagramGenerator()
    diagram = gen.generate(project)

    assert "A *-- B : b" in diagram
    assert "A o-- C : c" in diagram


def test_mermaid_shows_only_public_methods() -> None:
    """
    Mermaid-генератор должен показывать только публичные методы:
    - pub остаётся
    - _priv и __dunder__ не должны появляться
    """
    a = ClassInfo(
        name="A",
        bases=[],
        methods=[
            FunctionInfo(name="pub", lineno=1, decorators=[]),
            FunctionInfo(name="_priv", lineno=2, decorators=[]),
            FunctionInfo(name="__dunder__", lineno=3, decorators=[]),
        ],
        lineno=1,
    )

    module = ModuleInfo(path=Path("m.py"), classes=[a], functions=[], imports=[])
    project = ProjectModel(modules=[module])

    gen = MermaidDiagramGenerator()
    diagram = gen.generate(project)

    assert "A : +pub()" in diagram
    assert "_priv" not in diagram
    assert "__dunder__" not in diagram


def test_mermaid_inheritance() -> None:
    """
    Наследование в Mermaid должно быть в формате: `Base <|-- Child`.
    """
    base = ClassInfo(name="Base", bases=[], methods=[], lineno=1)
    child = ClassInfo(name="Child", bases=["Base"], methods=[], lineno=2)

    module = ModuleInfo(path=Path("m.py"), classes=[base, child], functions=[], imports=[])
    project = ProjectModel(modules=[module])

    gen = MermaidDiagramGenerator()
    diagram = gen.generate(project)

    assert "Base <|-- Child" in diagram
