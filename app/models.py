# app/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class FunctionInfo:
    """
    Метаданные о функции или методе в кодовой базе.

    name:
      Имя функции/метода как оно объявлено в исходнике.
    lineno:
      Номер строки объявления (1-based), если AST смог его дать.
    decorators:
      Список декораторов в строковом виде (как извлечено из AST).
      Используется для различения @classmethod/@staticmethod и др. в диаграммах/отчётах.
    """

    name: str
    lineno: Optional[int] = None
    decorators: List[str] = field(default_factory=list)


@dataclass
class AttributeInfo:
    """
    Метаданные об атрибуте класса или экземпляра.

    name:
      Имя атрибута (например, `x` для `self.x`).
    type:
      Строковое представление типа (из аннотации или эвристически выведенное),
      либо None, если тип не удалось определить.
    lineno:
      Номер строки, где атрибут был обнаружен.
    is_instance:
      True  -> атрибут экземпляра (self.x)
      False -> атрибут класса (Class.x)
    declared_in_init:
      True, если атрибут экземпляра впервые обнаружен внутри __init__.
      Это полезно для понимания “инициализируется ли поле в конструкторе”.
    """

    name: str
    type: Optional[str] = None
    lineno: Optional[int] = None
    is_instance: bool = True
    declared_in_init: bool = False


@dataclass
class CompositionInfo:
    """
    Связь "A имеет поле типа B" (для диаграмм).

    owner:
      Имя владельца (класс A).
    attribute:
      Имя поля/атрибута, через которое держится ссылка/владение.
    target:
      Имя целевого класса/типа (B).
    lineno:
      Номер строки, где связь была обнаружена (если известен).

    kind:
      "composition" -> *-- (владение: объект создаётся/присваивается внутри)
      "aggregation" -> o-- (ссылка: обычно приходит извне или только аннотирована)
    """

    owner: str
    attribute: str
    target: str
    lineno: Optional[int] = None

    kind: str = "composition"


@dataclass
class ClassInfo:
    """
    Метаданные о классе в модуле.

    name:
      Имя класса.
    bases:
      Список базовых классов (как строки из AST). Может содержать qualified-имена.
    init:
      Метаданные __init__ (если присутствует). Хранится отдельно от остальных методов.
    methods:
      Метаданные остальных методов (кроме __init__).
    attributes:
      Список найденных атрибутов класса/экземпляра.
    compositions:
      Список связей композиции/агрегации, найденных по типам/присваиваниям.
    lineno:
      Номер строки объявления класса.
    """

    name: str
    bases: List[str] = field(default_factory=list)

    # __init__ is tracked separately (if present)
    init: Optional[FunctionInfo] = None

    # other methods
    methods: List[FunctionInfo] = field(default_factory=list)

    # attributes and composition relations
    attributes: List[AttributeInfo] = field(default_factory=list)
    compositions: List[CompositionInfo] = field(default_factory=list)

    lineno: Optional[int] = None


@dataclass
class ModuleInfo:
    """
    Метаданные о Python-модуле (файле).

    path:
      Путь к файлу модуля.
    classes:
      Список классов, найденных в модуле.
    functions:
      Список топ-уровневых функций, найденных в модуле.
    imports:
      Импорты модуля, сохранённые в строковом виде для отчётов/диаграмм.
    """

    path: Path
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)


@dataclass
class ProjectModel:
    """
    Агрегированная модель проекта (результат анализа).

    modules:
      Список модулей (каждый соответствует одному .py файлу).
    requirements_path:
      Путь к requirements.txt, если он был найден сканером.

    pyproject_path/setup_cfg_path/dependency_files:
      Дополнительные dependency-related пути.
      Оставлены ради обратной совместимости, если их уже использует код вокруг.
    """

    modules: List[ModuleInfo] = field(default_factory=list)
    requirements_path: Optional[Path] = None

    # Optional dependency-related paths (kept for backward compatibility if you already use them)
    pyproject_path: Optional[Path] = None
    setup_cfg_path: Optional[Path] = None
    dependency_files: Dict[str, Path] = field(default_factory=dict)
