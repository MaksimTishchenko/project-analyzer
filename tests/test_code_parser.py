# tests/test_code_parser.py
from pathlib import Path

from app.code_parser import CodeParser
from app.models import ClassInfo, FunctionInfo, ModuleInfo, ProjectModel


def test_parse_file_basic_structure(tmp_path):
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

    async_func = next(f for f in module_info.functions if f.name == "async_func")
    assert isinstance(async_func, FunctionInfo)
    assert async_func.decorators == ["decorator1", "decorator2(arg=1)"]

    # --- classes ---
    class_names = {c.name for c in module_info.classes}
    assert class_names == {"Base", "MyClass"}

    my_class = next(c for c in module_info.classes if c.name == "MyClass")
    assert isinstance(my_class, ClassInfo)
    assert "Base" in my_class.bases
    assert "object" in my_class.bases

    method_names = {m.name for m in my_class.methods}
    assert method_names == {"from_value", "method"}


def test_parse_files_returns_project_model(tmp_path):
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
