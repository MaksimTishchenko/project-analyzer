# app/code_parser.py
from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Sequence

from .models import ClassInfo, FunctionInfo, ModuleInfo, ProjectModel


class _ModuleVisitor(ast.NodeVisitor):
    """
    AST-визитор, который собирает информацию о модулях, классах, функциях и импортах.
    """

    def __init__(self) -> None:
        self.imports: List[str] = []
        self.functions: List[FunctionInfo] = []
        self.classes: List[ClassInfo] = []
        self._class_stack: List[ClassInfo] = []

    # ---------- Импорты ----------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            # import os
            # import os as something
            if alias.asname:
                statement = f"import {alias.name} as {alias.asname}"
            else:
                statement = f"import {alias.name}"
            self.imports.append(statement)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # from x import y as z
        module = (
            "." * node.level + (node.module or "") if node.level or node.module else ""
        )
        for alias in node.names:
            if alias.asname:
                statement = f"from {module} import {alias.name} as {alias.asname}"
            else:
                statement = f"from {module} import {alias.name}"
            self.imports.append(statement)
        self.generic_visit(node)

    # ---------- Классы и функции ----------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [ast.unparse(base) for base in node.bases] if node.bases else []
        class_info = ClassInfo(
            name=node.name,
            bases=bases,
            methods=[],
            lineno=node.lineno,
        )

        self.classes.append(class_info)
        self._class_stack.append(class_info)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function_like(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function_like(node)

    # ---------- Вспомогательное ----------

    def _handle_function_like(self, node: ast.AST) -> None:
        # node: FunctionDef | AsyncFunctionDef
        name = getattr(node, "name", "<unknown>")
        lineno = getattr(node, "lineno", None)
        decorator_list = getattr(node, "decorator_list", [])

        decorators = (
            [ast.unparse(dec) for dec in decorator_list] if decorator_list else []
        )

        func_info = FunctionInfo(
            name=name,
            lineno=lineno,
            decorators=decorators,
        )

        if self._class_stack:
            # Метод класса
            current_class = self._class_stack[-1]
            current_class.methods.append(func_info)
        else:
            # Топ-уровневая функция
            self.functions.append(func_info)


class CodeParser:
    """
    Code parser that uses Python AST to extract structural information.

    На этом этапе:
    - собираем импорты
    - находим классы и их методы
    - находим топ-уровневые функции
    """

    def parse_file(self, path: str | Path) -> ModuleInfo:
        """
        Parse a single Python file and return ModuleInfo.
        """
        path = Path(path).resolve()
        source = path.read_text(encoding="utf-8")

        tree = ast.parse(source, filename=str(path))
        visitor = _ModuleVisitor()
        visitor.visit(tree)

        return ModuleInfo(
            path=path,
            classes=visitor.classes,
            functions=visitor.functions,
            imports=visitor.imports,
        )

    def parse_files(self, paths: Sequence[str | Path]) -> ProjectModel:
        """
        Parse multiple files and return a ProjectModel.
        """
        modules: List[ModuleInfo] = [self.parse_file(p) for p in paths]
        return ProjectModel(modules=modules)
