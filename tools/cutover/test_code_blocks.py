"""Behavioural surface for the apl block finder in code-blocks.py."""

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cb():
    path = Path(__file__).with_name("code-blocks.py")
    spec = importlib.util.spec_from_file_location("code_blocks", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["code_blocks"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_find_blocks_returns_apl_blocks_only(cb):
    md = "intro\n\n```apl\n      1+1\n2\n```\n\n```c\napl\nint x;\n```\n\n```\nbare\n```\n"
    blocks = cb.find_blocks("a.md", md)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.page == "a.md"
    assert b.line == 3
    assert b.body == ["      1+1", "2"]
    assert b.text == "```apl\n      1+1\n2\n```\n"
    assert b.sha256 == hashlib.sha256(b.text.encode()).hexdigest()
    assert b.nested is False


def test_find_blocks_marks_indented_fences_nested(cb):
    md = "- item\n\n    ```apl\n    1+1\n    ```\n"
    (b,) = cb.find_blocks("a.md", md)
    assert b.nested is True
    assert b.line == 3
    assert b.body == ["    1+1"]


def test_find_blocks_ignores_unclosed_fence(cb):
    assert cb.find_blocks("a.md", "```apl\n1+1\n") == []


def test_find_blocks_accepts_longer_closing_fence(cb):
    md = "~~~apl\n1+1\n~~~~~\nafter\n"
    (b,) = cb.find_blocks("a.md", md)
    assert b.body == ["1+1"]
    assert b.text == "~~~apl\n1+1\n~~~~~\n"


def test_find_blocks_does_not_split_on_unicode_line_breaks(cb):
    body = "a\u2028b\x85c\fd"
    md = f"```apl\n{body}\n```\n"
    (b,) = cb.find_blocks("a.md", md)
    assert b.body == [body]
    assert b.text == md
    assert b.sha256 == hashlib.sha256(md.encode()).hexdigest()


def test_find_blocks_keeps_crlf_verbatim(cb):
    md = "```apl\r\n1+1\r\n```\r\n"
    (b,) = cb.find_blocks("a.md", md)
    assert b.text == md
    assert b.sha256 == hashlib.sha256(md.encode()).hexdigest()


def test_find_blocks_skips_apl_line_inside_longer_c_fence(cb):
    assert cb.find_blocks("a.md", "````c\n```apl\nx\n```\n````\n") == []


def test_find_blocks_closer_cannot_have_trailing_text(cb):
    md = "```apl\n1+1\n``` x\n2\n```\n"
    (b,) = cb.find_blocks("a.md", md)
    assert b.body == ["1+1", "``` x", "2"]


def test_find_blocks_tilde_line_does_not_close_backtick_fence(cb):
    md = "```apl\n1+1\n~~~\n2\n```\n"
    (b,) = cb.find_blocks("a.md", md)
    assert b.body == ["1+1", "~~~", "2"]
