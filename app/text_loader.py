# app/text_loader.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# PEP-263: coding[:=]\s*([-\w.]+)
_CODING_RE = re.compile(r"coding[:=]\s*([-\w.]+)")

# BOM for UTF-8
_UTF8_BOM = "\ufeff"


@dataclass(frozen=True)
class SourceText:
    text: str
    encoding: str
    used_fallback: bool = False


def _detect_pep263_encoding(first_two_lines: str) -> Optional[str]:
    """
    Detect source encoding from PEP-263 header in the first two lines.
    Returns encoding string (e.g., 'utf-8', 'cp1251') or None.
    """
    m = _CODING_RE.search(first_two_lines)
    if not m:
        return None
    enc = (m.group(1) or "").strip()
    return enc or None


def read_python_source(path: Path) -> SourceText:
    """
    Robust reader for Python source files:
      1) tries PEP-263 encoding from first two lines (bytes -> latin-1 decode for header scan)
      2) else tries utf-8
      3) fallback: utf-8 with errors='replace' (never crashes)
    """
    raw = path.read_bytes()

    # Quick path: UTF-8 BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        txt = raw.decode("utf-8")
        # remove BOM char if present
        if txt.startswith(_UTF8_BOM):
            txt = txt.lstrip(_UTF8_BOM)
        return SourceText(text=txt, encoding="utf-8-sig", used_fallback=False)

    # Detect encoding from first two lines per PEP-263.
    # We decode header bytes with latin-1 to preserve byte-to-char mapping reliably.
    head = raw.splitlines(True)[:2]  # keep line endings
    head_text = b"".join(head).decode("latin-1", errors="ignore")
    pep263 = _detect_pep263_encoding(head_text)

    if pep263:
        try:
            return SourceText(text=raw.decode(pep263), encoding=pep263, used_fallback=False)
        except LookupError:
            # unknown encoding name -> fallback further
            pass
        except UnicodeDecodeError:
            # declared encoding but file is broken -> fallback further
            pass

    # Try utf-8
    try:
        return SourceText(text=raw.decode("utf-8"), encoding="utf-8", used_fallback=False)
    except UnicodeDecodeError:
        # Last resort: never crash
        return SourceText(text=raw.decode("utf-8", errors="replace"), encoding="utf-8", used_fallback=True)
