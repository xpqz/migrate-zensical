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


def _classify(cb, *lines):
    md = "```apl\n" + "\n".join(lines) + "\n```\n"
    (b,) = cb.find_blocks("a.md", md)
    return cb.classify(b)


def test_classify_syntax_line_is_listing(cb):
    c = _classify(cb, "R←⍳Y")
    assert (c.kind, c.reason, c.runnable) == ("listing", None, False)


def test_classify_session_example(cb):
    c = _classify(cb, "      ⍳5", "1 2 3 4 5")
    assert (c.kind, c.reason, c.runnable) == ("session", None, True)
    assert c.roles == ["input", "output"]
    assert c.nbsp is False


def test_classify_nbsp_indent_counts_as_input(cb):
    c = _classify(cb, " " * 5 + " ⍳5", "1 2 3 4 5")
    assert c.kind == "session"
    assert c.roles == ["input", "output"]
    assert c.nbsp is True
    assert cb.strip_indent(" " * 5 + " ⍳5") == "⍳5"


def test_classify_error_triple_is_output(cb):
    c = _classify(cb, "      1÷0", "DOMAIN ERROR: Divide by zero", "      1÷0", "       ∧")
    assert (c.kind, c.reason, c.runnable) == ("session", None, True)
    assert c.roles == ["input", "output", "output", "output"]


def test_classify_del_definition_is_ambiguous(cb):
    c = _classify(cb, "      ∇ R←foo Y", "[1] R←Y", "      ∇")
    assert (c.kind, c.reason, c.runnable) == ("ambiguous", "del-definition", False)


def test_classify_dfn_with_line_numbers_is_ambiguous(cb):
    c = _classify(cb, "      f←{", "[1] ⍵", "[2] }")
    assert (c.kind, c.reason, c.runnable) == ("ambiguous", "del-definition", False)


def test_classify_del_in_output_is_not_a_reason(cb):
    c = _classify(cb, "      ⎕VR 'foo'", "     ∇ R←foo", "[1]  R←1", "     ∇")
    assert (c.kind, c.reason, c.runnable) == ("session", None, True)


def test_classify_script_is_ambiguous(cb):
    c = _classify(cb, "      ⎕FIX ':Class c'", ":Class c", ":EndClass")
    assert (c.kind, c.reason, c.runnable) == ("ambiguous", "script", False)


def test_classify_odd_indent_is_ambiguous(cb):
    c = _classify(cb, "   ⍳5", "      ⍳3", "1 2 3")
    assert (c.kind, c.reason, c.runnable) == ("ambiguous", "indent", False)


def test_classify_indented_output_after_input_is_not_indent(cb):
    c = _classify(cb, "      1 2,⍪3 4", " 1 3", " 2 4")
    assert (c.kind, c.reason, c.runnable) == ("session", None, True)


def test_classify_char_matrix_with_indented_rows_is_indented_output(cb):
    # The row is 7 spaces: a 6-space output row is indistinguishable from
    # input by indent alone and is settled later by running the block.
    c = _classify(cb, "      ↑'       ab' 'cd'", "       ab", "cd")
    assert (c.kind, c.reason, c.runnable) == ("ambiguous", "indented-output", True)
    assert c.roles == ["input", "output", "output"]


def test_classify_blank_lines_have_blank_role(cb):
    c = _classify(cb, "      ⍳2", "1 2", "", "      ⍳1")
    assert c.roles == ["input", "output", "blank", "input"]


def test_classify_crlf_is_trailing_whitespace(cb):
    c = _classify(cb, "      ⍳2\r", "1 2\r", "\r")
    assert c.roles == ["input", "output", "blank"]


def test_classify_style_max_min_off(cb):
    assert _classify(cb, "      x", "┌→──┐", "│1 2│", "└~──┘").style == "max"
    assert _classify(cb, "      x", "┌↓─┐").style == "max"
    assert _classify(cb, "      x", "└∊──┘").style == "max"
    assert _classify(cb, "      x", "↓").style == "max"
    assert _classify(cb, "      x", "┌─┬─┐", "│1│2│", "└─┴─┘").style == "min"
    assert _classify(cb, "      x", "1 2").style == "off"
    # Only output lines are inspected: box characters in input do not count.
    assert _classify(cb, "      '┌→┐'", "1").style == "off"
