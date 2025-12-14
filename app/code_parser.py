from __future__ import annotations

import ast
from dataclasses import dataclass
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

# =============================================================================
# Helpers
# =============================================================================


def _safe_unparse(node: ast.AST) -> str:
    """
    Безопасно превращает AST-узел в строку.

    Зачем:
    - Нам нужны человекочитаемые имена базовых классов, декораторов, типов и т.п.
    - `ast.unparse()` удобен, но может быть недоступен/ломаться на некоторых узлах.

    Поведение (никогда не бросает исключения):
    1) Пытаемся использовать `ast.unparse()`
    2) Если не получилось — используем `ast.dump()` (без атрибутов)
    3) Если и это упало — возвращаем placeholder "<unparseable>"
    """
    try:
        return ast.unparse(node)  # type: ignore[attr-defined]
    except Exception:
        try:
            return ast.dump(node, include_attributes=False)
        except Exception:
            return "<unparseable>"


@dataclass(frozen=True)
class _SourceLoadResult:
    """
    Результат best-effort загрузки исходника.

    - text: содержимое файла (может быть пустым при фатальной ошибке чтения)
    - encoding: определённая/использованная кодировка, если удалось определить
    - used_fallback: признак, что использовался запасной путь чтения
    - error: текст ошибки (если она была), но при этом исключение наружу не летит
    """

    text: str
    encoding: str | None = None
    used_fallback: bool = False
    error: str | None = None


def _read_source_best_effort(path: Path) -> _SourceLoadResult:
    """
    Загружает исходник «как получится», не руша анализ всего проекта.

    Алгоритм:
    - Пытаемся использовать `.text_loader.read_python_source`, если модуль доступен.
      Это предпочтительный путь: он обычно корректнее определяет кодировку.
    - Если не удалось (импорт/ошибка чтения) — fallback на `read_text(utf-8, errors=replace)`.
    - Если даже это не удалось — возвращаем пустой текст и ошибку.

    Гарантия: функция *никогда* не выбрасывает исключений.
    """
    try:
        from .text_loader import read_python_source  # preferred path

        src = read_python_source(path)
        # ожидается: src.text, src.encoding, src.used_fallback
        return _SourceLoadResult(
            text=src.text,
            encoding=getattr(src, "encoding", None),
            used_fallback=bool(getattr(src, "used_fallback", False)),
            error=None,
        )
    except Exception as e:
        # fallback: читаем как UTF-8 с подменой битых символов
        try:
            txt = path.read_text(encoding="utf-8", errors="replace")
            return _SourceLoadResult(
                text=txt,
                encoding="utf-8",
                used_fallback=True,
                error=f"text_loader_failed: {type(e).__name__}: {e}",
            )
        except Exception as e2:
            return _SourceLoadResult(
                text="",
                encoding=None,
                used_fallback=True,
                error=f"read_failed: {type(e2).__name__}: {e2}",
            )


# =============================================================================
# AST visitor
# =============================================================================


class _ModuleVisitor(ast.NodeVisitor):
    """
    AST-визитор, который собирает структурную информацию о модуле.

    Что собираем:
    - imports: все `import ...` и `from ... import ...` (в текстовом виде)
    - functions: топ-уровневые функции (FunctionDef/AsyncFunctionDef), включая декораторы
    - classes: классы, их базовые классы и методы (также с декораторами)
    - init: метод `__init__` сохраняется отдельно как `ClassInfo.init`
    - attributes: атрибуты классов/экземпляров:
        * class attrs: `x = ...` и `x: T = ...` на уровне тела класса
        * instance attrs: `self.x = ...` и `self.x: T = ...` внутри методов
    - compositions: композиции/агрегации:
        * если поле класса/экземпляра имеет тип B — добавляем связь A -> B
    - lineno: номер строки определения сущностей, когда доступен

    Важно:
    - Визитор не делает «семантическую» проверку проекта, а лишь снимает структуру.
    - Используется дедупликация атрибутов и связей (через _seen_*).
    """

    _PRIMITIVE_TYPES: Set[str] = {
        "int",
        "str",
        "float",
        "bool",
        "dict",
        "list",
        "set",
        "tuple",
        "None",
    }

    def __init__(self) -> None:
        self.imports: List[str] = []
        self.functions: List[FunctionInfo] = []
        self.classes: List[ClassInfo] = []

        # Текущий контекст обхода:
        self._class_stack: List[ClassInfo] = []
        self._function_depth: int = 0  # для отслеживания top-level функций
        self._function_stack: List[str] = []  # имена функций (для определения scope)

        # Дедупликация:
        # (ClassName, attr, is_instance)
        self._seen_attrs: Set[tuple[str, str, bool]] = set()
        # (A, attr, B, kind)
        self._seen_comps: Set[tuple[str, str, str, str]] = set()

    # -------------------------------------------------------------------------
    # Imports
    # -------------------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:  # type: ignore[override]
        """
        Превращает `import x [as y]` в строку и сохраняет в imports.
        """
        for alias in node.names:
            if alias.asname:
                self.imports.append(f"import {alias.name} as {alias.asname}")
            else:
                self.imports.append(f"import {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # type: ignore[override]
        """
        Превращает `from m import x [as y]` в строку и сохраняет в imports.

        Поддерживает относительные импорты (`level`) через префикс точек.
        """
        if node.level or node.module:
            module = "." * node.level + (node.module or "")
        else:
            module = ""

        for alias in node.names:
            if alias.asname:
                self.imports.append(f"from {module} import {alias.name} as {alias.asname}")
            else:
                self.imports.append(f"from {module} import {alias.name}")
        self.generic_visit(node)

    # -------------------------------------------------------------------------
    # Classes / Functions
    # -------------------------------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
        """
        Регистрирует класс и запускает обход его тела.
        """
        bases = [_safe_unparse(base) for base in node.bases] if node.bases else []

        class_info = ClassInfo(
            name=node.name,
            bases=bases,
            methods=[],
            lineno=getattr(node, "lineno", None),
        )

        self.classes.append(class_info)
        self._class_stack.append(class_info)

        self.generic_visit(node)

        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
        """
        Обрабатывает обычную функцию/метод: собирает метаданные и обходит тело.
        """
        self._handle_function_like(node)

        self._function_stack.append(node.name)
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
        """
        Обрабатывает async-функцию/метод: поведение аналогично обычной функции.
        """
        self._handle_function_like(node)

        self._function_stack.append(node.name)
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1
        self._function_stack.pop()

    # -------------------------------------------------------------------------
    # Attributes / Composition
    # -------------------------------------------------------------------------

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # type: ignore[override]
        """
        Обрабатывает аннотированное присваивание.

        Сценарии:
        - self.x: T = ...  -> instance attr (+ aggregation связь по аннотации)
        - x: T = ...       -> class attr    (+ aggregation связь по аннотации)
        """
        if not self._class_stack:
            return self.generic_visit(node)

        current_class = self._class_stack[-1]
        lineno = getattr(node, "lineno", None)

        # self.x: T = ...
        self_attr = self._get_self_attr_name(node.target)
        if self_attr and self._in_method_scope():
            anno_str = _safe_unparse(node.annotation) if node.annotation else None
            self._add_instance_attr(current_class, self_attr, anno_str, lineno)
            for t in self._extract_type_names(node.annotation):
                self._add_composition(current_class, self_attr, t, lineno, kind="aggregation")

        # x: T = ... (class attr)
        if isinstance(node.target, ast.Name) and not self._in_method_scope():
            name = node.target.id
            anno_str = _safe_unparse(node.annotation) if node.annotation else None
            self._add_class_attr(current_class, name, anno_str, lineno)
            for t in self._extract_type_names(node.annotation):
                self._add_composition(current_class, name, t, lineno, kind="aggregation")

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # type: ignore[override]
        """
        Обрабатывает обычное присваивание.

        Сценарии:
        - self.x = Call(...) -> instance attr (type inferred по вызываемому символу) + composition
        - x = Call(...)      -> class attr    (type inferred по вызываемому символу) + composition

        Примечание:
        - Тип выводится *только* для `ast.Call` (как и было), иначе None.
        """
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

            # x = ... (class attr)
            if isinstance(tgt, ast.Name) and not in_method:
                name = tgt.id
                inferred = self._infer_type_from_value(node.value)
                self._add_class_attr(current_class, name, inferred, lineno)
                if inferred:
                    for t in self._extract_type_names_from_str(inferred):
                        self._add_composition(current_class, name, t, lineno, kind="composition")

        self.generic_visit(node)

    # -------------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------------

    def _handle_function_like(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """
        Регистрирует функцию/метод в зависимости от текущего контекста.

        Правила:
        - Если мы внутри класса:
            * __init__ -> сохраняем в `ClassInfo.init`
            * иначе    -> добавляем в `ClassInfo.methods`
        - Если мы не внутри класса и это top-level функция (depth == 0) ->
          добавляем в `self.functions`
        """
        decorators = [_safe_unparse(dec) for dec in node.decorator_list] if node.decorator_list else []

        func_info = FunctionInfo(
            name=node.name,
            lineno=getattr(node, "lineno", None),
            decorators=decorators,
        )

        if self._class_stack:
            current_class = self._class_stack[-1]
            if node.name == "__init__":
                current_class.init = func_info
                return
            current_class.methods.append(func_info)
            return

        if self._function_depth == 0:
            self.functions.append(func_info)

    def _in_method_scope(self) -> bool:
        """
        True, если мы находимся внутри функции/метода (неважно какого уровня вложенности).
        """
        return bool(self._function_stack)

    def _is_in_init(self) -> bool:
        """
        True, если текущая функция — это __init__ (используется в метаданных атрибутов).
        """
        return bool(self._function_stack) and self._function_stack[-1] == "__init__"

    def _get_self_attr_name(self, target: ast.AST) -> Optional[str]:
        """
        Если target имеет вид `self.<attr>`, возвращает имя attr, иначе None.
        """
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
            return target.attr
        return None

    def _add_instance_attr(self, cls: ClassInfo, name: str, type_str: Optional[str], lineno: Optional[int]) -> None:
        """
        Добавляет атрибут экземпляра (self.x) с дедупликацией.
        """
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

    def _add_class_attr(self, cls: ClassInfo, name: str, type_str: Optional[str], lineno: Optional[int]) -> None:
        """
        Добавляет атрибут класса (x = ...) с дедупликацией.
        """
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
        """
        Добавляет связь композиции/агрегации A -> B с дедупликацией.

        kind:
        - "composition": тип выведен из присваивания (обычно `Call(...)`)
        - "aggregation": тип взят из аннотации
        """
        if target in self._PRIMITIVE_TYPES:
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
        """
        Очень простая эвристика вывода типа из RHS:

        - если RHS это вызов `X(...)` -> считаем типом `X`
        - иначе -> None
        """
        if isinstance(value, ast.Call):
            return _safe_unparse(value.func)
        return None

    def _extract_type_names(self, node: Optional[ast.AST]) -> Set[str]:
        """
        Извлекает «именованные» типы из аннотации.

        Поддержка:
        - Name: `T`
        - Attribute: `pkg.T` -> берём `T` (attr)
        - Constant(str): forward-ref `'T'` или `'pkg.T'` (парсим как expr)
        """
        if node is None:
            return set()

        names: Set[str] = set()

        class _TypeNameVisitor(ast.NodeVisitor):
            def visit_Name(self, n: ast.Name) -> None:  # type: ignore[override]
                names.add(n.id)

            def visit_Attribute(self, n: ast.Attribute) -> None:  # type: ignore[override]
                names.add(n.attr)

            def visit_Constant(self, n: ast.Constant) -> None:  # type: ignore[override]
                if isinstance(n.value, str):
                    try:
                        expr = ast.parse(n.value, mode="eval").body
                        self.visit(expr)
                    except SyntaxError:
                        # не распарсили forward-ref — просто пропускаем
                        pass

        _TypeNameVisitor().visit(node)
        return names

    def _extract_type_names_from_str(self, type_str: str) -> Set[str]:
        """
        То же самое, что _extract_type_names, но вход — строка (например, от infer).
        """
        try:
            expr = ast.parse(type_str, mode="eval").body
            return self._extract_type_names(expr)
        except SyntaxError:
            return set()


# =============================================================================
# Public API
# =============================================================================


class CodeParser:
    """
    Парсер Python-кода через AST, который вытаскивает структуру проекта.

    Главная цель: *надёжность*.
    - best-effort чтение исходников (включая случаи с проблемной кодировкой)
    - ошибки парсинга не валят весь анализ
    - в случае проблем возвращается пустой ModuleInfo + метаданные об ошибке (через setattr)

    Важно:
    - API класса предсказуем: `parse_file()` и `parse_files()` всегда возвращают модели,
      даже если часть файлов битые/непарсятся.
    """

    def parse_file(self, path: str | Path) -> ModuleInfo:
        """
        Парсит один файл и возвращает ModuleInfo.

        Гарантии:
        - не бросает исключения наружу из-за ошибок чтения/парсинга (максимум вернёт пустую модель)
        - при проблемах добавляет атрибуты:
            * source_encoding, source_used_fallback, source_error
            * parse_error
          (через setattr, чтобы не зависеть от того, есть ли поля в dataclass)
        """
        path = Path(path).expanduser().resolve()

        src = _read_source_best_effort(path)
        source = src.text

        visitor = _ModuleVisitor()

        parse_error: str | None = None
        try:
            tree = ast.parse(source, filename=str(path))
            visitor.visit(tree)
        except SyntaxError as e:
            parse_error = (
                f"SyntaxError: {e.msg} (line {getattr(e, 'lineno', '?')}, col {getattr(e, 'offset', '?')})"
            )
        except Exception as e:
            parse_error = f"ParseError: {type(e).__name__}: {e}"

        module = ModuleInfo(
            path=path,
            classes=visitor.classes if parse_error is None else [],
            functions=visitor.functions if parse_error is None else [],
            imports=visitor.imports if parse_error is None else [],
        )

        # Наблюдаемость без риска сломать модель: поля могут отсутствовать, поэтому setattr.
        try:
            setattr(module, "source_encoding", src.encoding)
            setattr(module, "source_used_fallback", src.used_fallback)
            if src.error:
                setattr(module, "source_error", src.error)
            if parse_error:
                setattr(module, "parse_error", parse_error)
        except Exception:
            pass

        return module

    def parse_files(self, paths: Sequence[str | Path]) -> ProjectModel:
        """
        Парсит набор файлов и возвращает ProjectModel(modules=[...]).

        Поведение:
        - каждый файл парсится независимо
        - если parse_file по какой-то причине выбросил исключение (это крайний случай),
          мы добавляем пустой ModuleInfo и сохраняем parse_error через setattr
        """
        modules: List[ModuleInfo] = []
        for p in paths:
            try:
                modules.append(self.parse_file(p))
            except Exception as e:
                # Absolute last-resort: никогда не падаем всем прогоном из-за одного файла.
                path = Path(p).expanduser().resolve()
                m = ModuleInfo(path=path, classes=[], functions=[], imports=[])
                try:
                    setattr(m, "parse_error", f"UnhandledError: {type(e).__name__}: {e}")
                except Exception:
                    pass
                modules.append(m)

        return ProjectModel(modules=modules)
