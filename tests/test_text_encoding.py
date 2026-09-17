"""Text files are read and written with an explicit encoding, always.

Python's default is the locale's encoding, which is cp1252 on Windows. Every
source file here carries em dashes, so a bare `open(path).read()` passes on
Linux and macOS and fails on Windows with

    UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f

That is how this rule arrived: a test read a module without an encoding and
broke all three Windows jobs while every Linux job stayed green. An AST check
is cheaper than finding out from CI again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCANNED = sorted(
    path
    for directory in ("src", "tests")
    for path in (PROJECT_ROOT / directory).rglob("*.py")
)

# Calls that touch text and therefore need an encoding. `open` in binary mode
# is exempt, and so is anything already passing one.
TEXT_CALLS = {"open", "read_text", "write_text"}


def _callee_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_binary_open(node: ast.Call) -> bool:
    mode = next((kw.value for kw in node.keywords if kw.arg == "mode"), None)
    if mode is None and len(node.args) > 1:
        mode = node.args[1]
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value


def offenders(source: str) -> list:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        if name not in TEXT_CALLS:
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        # Both take the encoding positionally too, but at different places:
        # read_text(encoding, ...) has it first, write_text(data, encoding, ...)
        # second. Getting that backwards would wave through every bare
        # write_text("...") — the exact call this check exists to catch.
        if name == "read_text" and node.args:
            continue
        if name == "write_text" and len(node.args) > 1:
            continue
        if name == "open" and _is_binary_open(node):
            continue
        found.append((name, node.lineno))
    return found


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: str(p.relative_to(PROJECT_ROOT)))
def test_text_io_names_its_encoding(path):
    found = offenders(path.read_text("utf-8"))
    assert not found, "; ".join(f"{name}() at line {line} has no encoding" for name, line in found)


class TestTheCheckItself:
    def test_it_catches_a_bare_read(self):
        assert offenders("open(path).read()") == [("open", 1)]
        assert offenders("p.read_text()") == [("read_text", 1)]

    def test_it_accepts_an_explicit_encoding(self):
        assert offenders("open(path, encoding='utf-8').read()") == []
        assert offenders("p.read_text('utf-8')") == []
        assert offenders("p.write_text(data, encoding='utf-8')") == []
        assert offenders("p.write_text(data, 'utf-8')") == []

    def test_a_lone_positional_to_write_text_is_the_data_not_the_encoding(self):
        assert offenders("p.write_text('hello')") == [("write_text", 1)]

    def test_binary_mode_needs_no_encoding(self):
        assert offenders("open(path, 'rb').read()") == []
        assert offenders("open(path, mode='wb')") == []
        assert offenders("p.read_bytes()") == []
