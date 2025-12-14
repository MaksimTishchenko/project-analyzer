from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# PEP-263: encoding cookie может быть только в 1-й или 2-й строке файла:
#   coding[:=]\s*([-\w.]+)
# Мы якорим regex на комментарий, чтобы уменьшить количество ложных совпадений.
_PEP263_LINE_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)", re.IGNORECASE)

# UTF-8 BOM (Byte Order Mark)
_UTF8_BOM = "\ufeff"
_UTF8_BOM_BYTES = b"\xef\xbb\xbf"

# Safety limit по умолчанию (желательно держать в согласовании со scanner max size)
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB


@dataclass(frozen=True)
class SourceText:
    """
    Результат чтения исходника Python-файла.

    text:
      Текст файла (возможно усечённый до max_bytes).
    encoding:
      Кодировка, которой удалось декодировать файл (PEP-263/utf-8/utf-8-sig).
    used_fallback:
      True, если применялся decode(errors="replace") (то есть были проблемы с декодированием).
    truncated:
      True, если файл был обрезан по лимиту max_bytes.
    """
    text: str
    encoding: str
    used_fallback: bool = False
    truncated: bool = False


def _detect_pep263_encoding_from_lines(line1: str, line2: str) -> Optional[str]:
    """
    Ищет PEP-263 encoding cookie строго в первых двух строках.

    Возвращает:
      - строку кодировки (например 'utf-8', 'cp1251') если cookie найден
      - None если cookie не найден или пустой
    """
    for line in (line1, line2):
        m = _PEP263_LINE_RE.match(line)
        if m:
            enc = (m.group(1) or "").strip()
            if enc:
                return enc
    return None


def read_python_source(path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> SourceText:
    """
    Надёжное и безопасное чтение Python-исходников.

    Цели:
    - не падать на “битой” кодировке;
    - корректно учитывать BOM и PEP-263;
    - не читать гигантские файлы целиком.

    Алгоритм:
      0) Читаем bytes и (при необходимости) ограничиваем `max_bytes` (ставим truncated=True)
      1) Если есть UTF-8 BOM -> декодируем как utf-8-sig и убираем BOM из текста
      2) Иначе пытаемся определить кодировку по PEP-263 (первые 2 строки)
      3) Если не определили — пробуем utf-8
      4) Последний fallback: utf-8 с errors='replace' (гарантированно не падает)

    Возвращает SourceText с флагом `truncated`.
    """
    raw = path.read_bytes()

    truncated = False
    if max_bytes is not None and max_bytes > 0 and len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True

    # Quick path: UTF-8 BOM
    if raw.startswith(_UTF8_BOM_BYTES):
        txt = raw.decode("utf-8", errors="replace")
        if txt.startswith(_UTF8_BOM):
            txt = txt.lstrip(_UTF8_BOM)
        return SourceText(text=txt, encoding="utf-8-sig", used_fallback=False, truncated=truncated)

    # Detect encoding from first two lines per PEP-263.
    # Декодируем заголовок как latin-1, чтобы сохранить 1:1 отображение байтов в символы.
    head = raw.splitlines(True)[:2]  # keep line endings
    line1 = head[0].decode("latin-1", errors="ignore") if len(head) >= 1 else ""
    line2 = head[1].decode("latin-1", errors="ignore") if len(head) >= 2 else ""
    pep263 = _detect_pep263_encoding_from_lines(line1, line2)

    if pep263:
        try:
            return SourceText(text=raw.decode(pep263), encoding=pep263, used_fallback=False, truncated=truncated)
        except LookupError:
            # неизвестная кодировка в cookie
            pass
        except UnicodeDecodeError:
            # cookie указал неверную кодировку для содержимого
            pass

    # Try utf-8
    try:
        return SourceText(text=raw.decode("utf-8"), encoding="utf-8", used_fallback=False, truncated=truncated)
    except UnicodeDecodeError:
        # Last resort: never crash
        return SourceText(
            text=raw.decode("utf-8", errors="replace"),
            encoding="utf-8",
            used_fallback=True,
            truncated=truncated,
        )
