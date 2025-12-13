# app/code_parser.py
from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Optional, Sequence, Set

from .models import (
    AttributeInfo,
    ClassInfo,
    CompositionInfo,
    FunctionInfo,
    ModuleInfo,
    ProjectModel,
)


class _ModuleVisitor(ast.NodeVisitor):
    """
    AST-визитор, который собирает информацию о модуле:
    - импорты (import, from ... import ...)
    - топ-уровневые функции (включая async и декораторы)
    - классы, их базовые классы и методы (включая декораторы)
    - __init__ отдельно
    - атрибуты классов/экземпляров (self.x = ..., self.x: T = ...)
    - композиции (A *-- B), если в классе A есть поле типа B
    - строки определения (lineno) для классов и функций/методов/атрибутов
    """

    def __init__(self) -> None:
        # Сырой список строк импортов вида:
        #   "import os"
        #   "from pathlib import Path"
        self.imports: List[str] = []

        # Топ-уровневые функции модуля
        self.functions: List[FunctionInfo] = []

        # Описания классов модуля
        self.classes: List[ClassInfo] = []

        # Стек текущих классов (для определения методов и атрибутов)
        self._class_stack: List[ClassInfo] = []

        # Глубина вложенности функций (чтобы отделить топ-уровневые от вложенных)
        self._function_depth: int = 0

        # Стек имён текущих функций (чтобы понимать, что мы внутри метода и какой именно метод)
        self._function_stack: List[str] = []

        # Дедупликация атрибутов/композиций
        self._seen_attrs: Set[tuple[str, str, bool]] = set()  # (ClassName, attr, is_instance)
        self._seen_comps: Set[tuple[str, str, str, str]] = set()   # (A, attr, B, kind)

    # ---------- Импорты ----------

    def visit_Import(self, node: ast.Import) -> None:  # type: ignore[override]
        for alias in node.names:
            # import os
            # import os as something
            if alias.asname:
                statement = f"import {alias.name} as {alias.asname}"
            else:
                statement = f"import {alias.name}"
            self.imports.append(statement)

        # Продолжаем обход для вложенных нод (обычно не обязательно, но безвредно)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # type: ignore[override]
        # from x import y as z
        # поддерживаем относительные импорты: from . import local_mod
        if node.level or node.module:
            module = "." * node.level + (node.module or "")
        else:
            module = ""

        for alias in node.names:
            if alias.asname:
                statement = f"from {module} import {alias.name} as {alias.asname}"
            else:
                statement = f"from {module} import {alias.name}"
            self.imports.append(statement)

        self.generic_visit(node)

    # ---------- Классы и функции ----------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
        # Базовые классы в виде строк, например: ["Base", "object"]
        bases = [ast.unparse(base) for base in node.bases] if node.bases else []

        class_info = ClassInfo(
            name=node.name,
            bases=bases,
            methods=[],
            lineno=node.lineno,
        )

        # Регистрируем класс и добавляем его в стек текущего контекста
        self.classes.append(class_info)
        self._class_stack.append(class_info)

        # Обходим содержимое класса (методы, атрибуты и т.д.)
        self.generic_visit(node)

        # Выходим из класса
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
        # Обрабатываем саму функцию (как топ-уровневую или метод)
        self._handle_function_like(node)

        # Теперь считаем, что мы вошли в тело функции — для отслеживания вложенных def
        self._function_stack.append(node.name)
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1
        self._function_stack.pop()

    def visit_AsyncFunctionDef(  # type: ignore[override]
        self,
        node: ast.AsyncFunctionDef,
    ) -> None:
        # Обрабатываем саму функцию (как топ-уровневую или метод)
        self._handle_function_like(node)

        # То же самое, что и для обычной функции
        self._function_stack.append(node.name)
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1
        self._function_stack.pop()

    # ---------- Атрибуты и композиция ----------

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # type: ignore[override]
        """Ловим аннотированные присваивания для полей класса/экземпляра."""
        if not self._class_stack:
            return self.generic_visit(node)

        current_class = self._class_stack[-1]
        lineno = getattr(node, "lineno", None)

        # self.x: T = ...
        self_attr = self._get_self_attr_name(node.target)
        if self_attr and self._in_method_scope():
            anno_str = ast.unparse(node.annotation) if node.annotation else None
            self._add_instance_attr(current_class, self_attr, anno_str, lineno)
            for t in self._extract_type_names(node.annotation):
                self._add_composition(current_class, self_attr, t, lineno, kind="aggregation")

        # x: T = ... (класс-атрибут)
        if isinstance(node.target, ast.Name) and not self._in_method_scope():
            name = node.target.id
            anno_str = ast.unparse(node.annotation) if node.annotation else None
            self._add_class_attr(current_class, name, anno_str, lineno)
            for t in self._extract_type_names(node.annotation):
                self._add_composition(current_class, name, t, lineno, kind="aggregation")


        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # type: ignore[override]
        """Ловим присваивания для полей класса/экземпляра."""
        if not self._class_stack:
            return self.generic_visit(node)

        current_class = self._class_stack[-1]
        in_method = self._in_method_scope()
        lineno = getattr(node, "lineno", None)

        for tgt in node.targets:
            # self.x = ...
            attr = self._get_self_attr_name(tgt)
            if attr and in_method:
                inferred = self._infer_type_from_value(node.value)
                self._add_instance_attr(current_class, attr, inferred, lineno)
                if inferred:
                    for t in self._extract_type_names_from_str(inferred):
                        self._add_composition(current_class, attr, t, lineno, kind="composition")
                continue

            # x = ... (класс-атрибут)
            if isinstance(tgt, ast.Name) and not in_method:
                name = tgt.id
                inferred = self._infer_type_from_value(node.value)
                self._add_class_attr(current_class, name, inferred, lineno)
                if inferred:
                    for t in self._extract_type_names_from_str(inferred):
                        self._add_composition(current_class, name, t, lineno, kind="composition")

        self.generic_visit(node)

    # ---------- Вспомогательное ----------

    def _handle_function_like(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        """
        Общая обработка для обычных и асинхронных функций.

        В этот момент:
        - если мы находимся внутри класса (self._class_stack не пуст),
          то это метод класса;
        - если глубина функций == 0 и мы не в классе — это топ-уровневая функция;
        - вложенные функции (внутри других функций) сейчас игнорируются.
        """
        decorators = (
            [ast.unparse(dec) for dec in node.decorator_list]
            if node.decorator_list
            else []
        )

        func_info = FunctionInfo(
            name=node.name,
            lineno=node.lineno,
            decorators=decorators,
        )

        if self._class_stack:
            # Метод класса
            current_class = self._class_stack[-1]

            # __init__ сохраняем отдельно
            if node.name == "__init__":
                current_class.init = func_info
                return

            current_class.methods.append(func_info)
            return

        if self._function_depth == 0:
            # Топ-уровневая функция (не вложенная)
            self.functions.append(func_info)
        else:
            # Вложенные функции сейчас не включаем в модель
            return

    def _in_method_scope(self) -> bool:
        return bool(self._function_stack)

    def _is_in_init(self) -> bool:
        return bool(self._function_stack) and self._function_stack[-1] == "__init__"

    def _get_self_attr_name(self, target: ast.AST) -> Optional[str]:
        # self.x
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            return target.attr
        return None

    def _add_instance_attr(
        self,
        cls: ClassInfo,
        name: str,
        type_str: Optional[str],
        lineno: Optional[int],
    ) -> None:
        key = (cls.name, name, True)
        if key in self._seen_attrs:
            return
        self._seen_attrs.add(key)

        cls.attributes.append(
            AttributeInfo(
                name=name,
                type=type_str,
                lineno=lineno,
                is_instance=True,
                declared_in_init=self._is_in_init(),
            )
        )

    def _add_class_attr(
        self,
        cls: ClassInfo,
        name: str,
        type_str: Optional[str],
        lineno: Optional[int],
    ) -> None:
        key = (cls.name, name, False)
        if key in self._seen_attrs:
            return
        self._seen_attrs.add(key)

        cls.attributes.append(
            AttributeInfo(
                name=name,
                type=type_str,
                lineno=lineno,
                is_instance=False,
                declared_in_init=False,
            )
        )

    def _add_composition(
            self,
            cls: ClassInfo,
            attr: str,
            target: str,
            lineno: Optional[int],
            *,
            kind: str,
    ) -> None:
        if target in {
            "int", "str", "float", "bool",
            "dict", "list", "set", "tuple", "None"
        }:
            return

        key = (cls.name, attr, target, kind)
        if key in self._seen_comps:
            return
        self._seen_comps.add(key)

        cls.compositions.append(
            CompositionInfo(
                owner=cls.name,
                attribute=attr,
                target=target,
                lineno=lineno,
                kind=kind,
            )
        )

    def _infer_type_from_value(self, value: ast.AST) -> Optional[str]:
        """Простая эвристика: self.x = B(...) -> "B" (или "module.B")."""
        if isinstance(value, ast.Call):
            return ast.unparse(value.func)
        return None

    def _extract_type_names(self, node: Optional[ast.AST]) -> Set[str]:
        """Достаём имена типов из аннотации: B, Optional[B], list[B], "B"."""
        if node is None:
            return set()

        names: Set[str] = set()

        class _TypeNameVisitor(ast.NodeVisitor):
            def visit_Name(self, n: ast.Name) -> None:  # type: ignore[override]
                names.add(n.id)

            def visit_Attribute(self, n: ast.Attribute) -> None:  # type: ignore[override]
                # module.B -> "B" (для UML чаще хватает локального имени)
                names.add(n.attr)

            def visit_Constant(self, n: ast.Constant) -> None:  # type: ignore[override]
                if isinstance(n.value, str):
                    try:
                        expr = ast.parse(n.value, mode="eval").body
                        self.visit(expr)
                    except SyntaxError:
                        pass

        _TypeNameVisitor().visit(node)
        return names

    def _extract_type_names_from_str(self, type_str: str) -> Set[str]:
        try:
            expr = ast.parse(type_str, mode="eval").body
            return self._extract_type_names(expr)
        except SyntaxError:
            return set()


class CodeParser:
    """
    Code parser that uses Python AST to extract structural information.

    На этом этапе:
    - собираем импорты
    - находим классы и их методы
    - находим __init__ отдельно
    - находим атрибуты и композиции
    - находим топ-уровневые функции (включая async)
    - сохраняем строки определения (lineno)
    """

    def parse_file(self, path: str | Path) -> ModuleInfo:
        """Parse a single Python file and return ModuleInfo."""
        path = Path(path).resolve()

        from .text_loader import read_python_source

        src = read_python_source(path)
        source = src.text

        tree = ast.parse(source, filename=str(path))
        visitor = _ModuleVisitor()
        visitor.visit(tree)

        module = ModuleInfo(
            path=path,
            classes=visitor.classes,
            functions=visitor.functions,
            imports=visitor.imports,
        )

        # Optional observability (doesn't break anything if models don't have these fields)
        try:
            setattr(module, "source_encoding", src.encoding)
            setattr(module, "source_used_fallback", src.used_fallback)
        except Exception:
            pass

        return module

    def parse_files(self, paths: Sequence[str | Path]) -> ProjectModel:
        """Parse multiple files and aggregate results into a ProjectModel."""
        modules: List[ModuleInfo] = [self.parse_file(p) for p in paths]
        return ProjectModel(modules=modules)
