# tests/test_text_loader.py
from pathlib import Path

from app.text_loader import read_python_source


def test_read_python_source_pep263_cp1251(tmp_path: Path) -> None:
    p = tmp_path / "cp1251.py"
    # PEP-263 header + Cyrillic text in cp1251 bytes
    raw = "# -*- coding: cp1251 -*-\n# Привет\nx = 1\n".encode("cp1251")
    p.write_bytes(raw)

    src = read_python_source(p)
    assert "Привет" in src.text
    assert src.encoding.lower() == "cp1251"
    assert src.used_fallback is False


def test_read_python_source_fallback_never_crashes(tmp_path: Path) -> None:
    p = tmp_path / "broken.py"
    # invalid utf-8 bytes
    p.write_bytes(b"\xff\xfe\xfa\nx=1\n")

    src = read_python_source(p)
    assert "x=1" in src.text
    # fallback is allowed
    assert src.encoding.startswith("utf-8")
