# tests/test_diagram_generator.py
from pathlib import Path

from app.diagram_generator import DiagramGenerator
from app.models import ClassInfo, CompositionInfo, FunctionInfo, ModuleInfo, ProjectModel


def test_diagram_generator_renders_composition_and_aggregation() -> None:
    from pathlib import Path

    a = ClassInfo(name="A", bases=[], methods=[], lineno=1)
    b = ClassInfo(name="B", bases=[], methods=[], lineno=2)
    c = ClassInfo(name="C", bases=[], methods=[], lineno=3)

    # A *-- B
    a.compositions.append(CompositionInfo(owner="A", attribute="b", target="B", kind="composition"))
    # A o-- C
    a.compositions.append(CompositionInfo(owner="A", attribute="c", target="C", kind="aggregation"))

    module = ModuleInfo(path=Path("m.py"), classes=[a, b, c], functions=[], imports=[])
    project = ProjectModel(modules=[module])

    generator = DiagramGenerator()
    plantuml = generator.generate_class_diagram(project)

    assert 'A *-- B : "b"' in plantuml
    assert 'A o-- C : "c"' in plantuml


def test_diagram_generator_shows_only_public_methods_by_default() -> None:
    from pathlib import Path

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

    generator = DiagramGenerator()
    plantuml = generator.generate_class_diagram(project)

    assert "+ pub()" in plantuml
    assert "_priv" not in plantuml
    assert "__dunder__" not in plantuml


def test_diagram_generator_produces_valid_plantuml() -> None:
    base = ClassInfo(
        name="Base",
        bases=[],
        methods=[
            FunctionInfo(name="run", lineno=1, decorators=[]),
        ],
        lineno=1,
    )

    child = ClassInfo(
        name="Child",
        bases=["Base"],
        methods=[
            FunctionInfo(name="do_something", lineno=5, decorators=[]),
        ],
        lineno=5,
    )

    module = ModuleInfo(
        path=Path("module.py"),
        classes=[base, child],
        functions=[],
        imports=[],
    )

    project = ProjectModel(modules=[module])

    generator = DiagramGenerator()
    plantuml = generator.generate_class_diagram(project)

    assert "@startuml" in plantuml
    assert "@enduml" in plantuml
    assert "class Base" in plantuml
    assert "class Child" in plantuml
    assert "Child --|> Base" in plantuml
