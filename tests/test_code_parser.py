# tests/test_code_parser.py
from pathlib import Path

from app.code_parser import CodeParser
from app.models import ClassInfo, FunctionInfo, ModuleInfo, ProjectModel


def test_parse_file_basic_structure(tmp_path: Path) -> None:
    file_path = tmp_path / "example.py"
    file_path.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from . import local_mod\n"
        "\n"
        "def top_level(a, b):\n"
        "    return a + b\n"
        "\n"
        "@decorator1\n"
        "@decorator2(arg=1)\n"
        "async def async_func():\n"
        "    return 'ok'\n"
        "\n"
        "class Base:\n"
        "    def base_method(self):\n"
        "        return 'base'\n"
        "\n"
        "class MyClass(Base, object):\n"
        "    @classmethod\n"
        "    def from_value(cls, value):\n"
        "        return cls()\n"
        "\n"
        "    def method(self):\n"
        "        return value\n",
        encoding="utf-8",
    )

    parser = CodeParser()
    module_info = parser.parse_file(file_path)

    # --- типы и путь ---
    assert isinstance(module_info, ModuleInfo)
    assert module_info.path == file_path.resolve()

    # --- imports ---
    assert sorted(module_info.imports) == sorted(
        [
            "import os",
            "from pathlib import Path",
            "from . import local_mod",
        ]
    )

    # --- top-level functions ---
    func_names = {f.name for f in module_info.functions}
    assert func_names == {"top_level", "async_func"}

    # Проверяем async-функцию и декораторы
    async_func = next(f for f in module_info.functions if f.name == "async_func")
    assert isinstance(async_func, FunctionInfo)
    assert async_func.decorators == ["decorator1", "decorator2(arg=1)"]
    # Проверяем строки определения (lineno): см. нумерацию в тестовом файле
    assert async_func.lineno == 10

    top_level = next(f for f in module_info.functions if f.name == "top_level")
    assert top_level.lineno == 5

    # --- classes ---
    class_names = {c.name for c in module_info.classes}
    assert class_names == {"Base", "MyClass"}

    base_class = next(c for c in module_info.classes if c.name == "Base")
    my_class = next(c for c in module_info.classes if c.name == "MyClass")

    assert isinstance(base_class, ClassInfo)
    assert isinstance(my_class, ClassInfo)

    # Проверяем bases
    assert "Base" not in base_class.bases  # Base не наследует Base
    assert my_class.bases == ["Base", "object"]

    # Проверяем lineno для классов
    assert base_class.lineno == 13
    assert my_class.lineno == 17

    # --- methods ---
    method_names = {m.name for m in my_class.methods}
    assert method_names == {"from_value", "method"}

    methods_by_name = {m.name: m for m in my_class.methods}

    from_value = methods_by_name["from_value"]
    method = methods_by_name["method"]

    assert isinstance(from_value, FunctionInfo)
    assert isinstance(method, FunctionInfo)

    # Декораторы метода
    assert from_value.decorators == ["classmethod"]

    # Проверяем строки определения методов
    # (см. нумерацию строк в исходном тексте)
    assert from_value.lineno == 19
    assert method.lineno == 22

    # Проверяем, что методы Base тоже распознаны
    base_method_names = {m.name for m in base_class.methods}
    assert base_method_names == {"base_method"}


def test_parse_files_returns_project_model(tmp_path: Path) -> None:
    file1 = tmp_path / "a.py"
    file2 = tmp_path / "b.py"

    file1.write_text("def a():\n    return 1\n", encoding="utf-8")
    file2.write_text("def b():\n    return 2\n", encoding="utf-8")

    parser = CodeParser()
    project = parser.parse_files([file1, file2])

    assert isinstance(project, ProjectModel)
    assert len(project.modules) == 2

    paths = sorted(m.path for m in project.modules)
    assert paths == sorted([file1.resolve(), file2.resolve()])

    func_names_per_file = [{f.name for f in m.functions} for m in project.modules]
    assert {"a"} in func_names_per_file
    assert {"b"} in func_names_per_file


def test_parse_attributes_init_and_composition(tmp_path: Path) -> None:
    file_path = tmp_path / "m.py"
    file_path.write_text(
        "class B:\n"
        "    pass\n\n"
        "class A:\n"
        "    VERSION: str = '1'\n"
        "    def __init__(self, b: B):\n"
        "        self.b = b\n"
        "        self.c: B = B()\n"
        "        self.n = 123\n"
        "    def method(self):\n"
        "        self.m = B()\n",
        encoding="utf-8",
    )

    parser = CodeParser()
    module = parser.parse_file(file_path)

    a = next(c for c in module.classes if c.name == "A")

    # __init__ хранится отдельно
    assert a.init is not None
    assert a.init.name == "__init__"
    # остальные методы в methods
    assert {m.name for m in a.methods} == {"method"}

    # атрибуты (класс-атрибут и экземпляра)
    attrs = {x.name for x in a.attributes}
    assert {"VERSION", "b", "c", "n", "m"} <= attrs

    # declared_in_init помечает атрибуты из __init__
    init_attrs = {x.name for x in a.attributes if x.declared_in_init}
    assert {"b", "c", "n"} <= init_attrs

    # композиция A *-- B (по b: B и c: B = B() и m = B())
    comps = {(x.owner, x.attribute, x.target) for x in a.compositions}
    assert ("A", "b", "B") in comps or ("A", "c", "B") in comps or ("A", "m", "B") in comps
