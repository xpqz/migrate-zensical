"""Behavioural surface for the apl block finder in code-blocks.py."""

import hashlib
import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cb():
    path = Path(__file__).with_name("code-blocks.py")
    spec = importlib.util.spec_from_file_location("code_blocks", path)
    assert spec is not None and spec.loader is not None
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


def test_classify_indented_error_message_is_not_a_triple(cb):
    c = _classify(cb, "      1÷0", "   VALUE ERROR", "      1÷0", "       ∧")
    assert c.roles == ["input", "output", "input", "output"]
    assert (c.kind, c.reason, c.runnable) == ("ambiguous", "indented-output", True)


def test_classify_bare_error_message_is_a_triple(cb):
    c = _classify(cb, "      x", "ERROR", "      x", "      ∧")
    assert c.roles == ["input", "output", "output", "output"]


def _block(cb, *lines):
    md = "```apl\n" + "\n".join(lines) + "\n```\n"
    (b,) = cb.find_blocks("a.md", md)
    return b, cb.classify(b)


def test_split_simple_example(cb):
    b, c = _block(cb, "      ⍳5", "1 2 3 4 5")
    assert cb.split_block(b, c) == "```apl\n⍳5\n```\n\n```text\n1 2 3 4 5\n```\n"


def test_split_groups_consecutive_inputs(cb):
    b, c = _block(cb, "      a←1", "", "      a+1", "2", "      a+2", "3")
    assert cb.split_block(b, c) == (
        "```apl\na←1\na+1\n```\n\n```text\n2\n```\n"
        "\n```apl\na+2\n```\n\n```text\n3\n```\n"
    )


def test_split_keeps_error_triple_in_text_block(cb):
    b, c = _block(cb, "      1÷0", "DOMAIN ERROR", "      1÷0", "       ∧")
    assert cb.split_block(b, c) == (
        "```apl\n1÷0\n```\n\n```text\nDOMAIN ERROR\n      1÷0\n       ∧\n```\n"
    )


def test_split_strips_nbsp_indent(cb):
    b, c = _block(cb, "      ⍳2", "1 2")
    assert cb.split_block(b, c).startswith("```apl\n⍳2\n```\n")


def test_split_drops_trailing_blank_output_lines(cb):
    b, c = _block(cb, "      ⍳2", "1 2", "", "3", "", "")
    assert cb.split_block(b, c) == "```apl\n⍳2\n```\n\n```text\n1 2\n\n3\n```\n"


def test_split_keeps_trailing_whitespace_and_drops_cr(cb):
    b, c = _block(cb, "      ⍳2\r", "1 2 \r")
    assert cb.split_block(b, c) == "```apl\n⍳2\n```\n\n```text\n1 2 \n```\n"


def test_split_input_only_has_no_text_block(cb):
    b, c = _block(cb, "      a←1", "      a+1")
    assert cb.split_block(b, c) == "```apl\na←1\na+1\n```\n"


def test_split_rejects_listing_and_unrunnable(cb):
    b, c = _block(cb, "R←⍳Y")
    with pytest.raises(ValueError):
        cb.split_block(b, c)
    b, c = _block(cb, "      ∇ foo", "      x")
    assert not c.runnable
    with pytest.raises(ValueError):
        cb.split_block(b, c)


def test_check_no_lost_lines_rejects_dropped_line(cb):
    b, _ = _block(cb, "      ⍳2", "1 2", "3")
    cb.check_no_lost_lines(b, "```apl\n⍳2\n```\n\n```text\n1 2\n3\n```\n")
    with pytest.raises(cb.SplitError):
        cb.check_no_lost_lines(b, "```apl\n⍳2\n```\n\n```text\n1 2\n```\n")


@pytest.mark.parametrize(
    "lines, verdict, reason, expected",
    [
        (("R←⍳Y",), None, None, "none"),
        (("      x", "1"), None, None, "unambiguous"),
        (("      x", "1"), "match", None, "confirmed"),
        (("      x", "1"), "mismatch", None, "unambiguous"),
        (("      x", "1"), "not-run", "suspect-input", "ambiguous"),
        (("      x", "1"), "not-run", "other", "unambiguous"),
        (("      ∇ foo", "      x"), None, None, "ambiguous"),
        (("   x", "      y", "1"), "match", None, "ambiguous"),
        (("      x", "        a", "      y", "      b"), None, None, "ambiguous"),
        (("      1÷0", "   VALUE ERROR", "      1÷0", "       ∧"), "match", None, "confirmed"),
        (("      1÷0", "   VALUE ERROR", "      1÷0", "       ∧"), "mismatch", None, "ambiguous"),
        (("      1÷0", "   VALUE ERROR", "      1÷0", "       ∧"), None, None, "ambiguous"),
    ],
)
def test_split_kind_table(cb, lines, verdict, reason, expected):
    _, c = _block(cb, *lines)
    assert cb.split_kind(c, verdict, reason) == expected


def test_check_no_lost_lines_rejects_line_on_wrong_side(cb):
    b, _ = _block(cb, "      ⍳2", "1 2")
    with pytest.raises(cb.SplitError):
        cb.check_no_lost_lines(b, "```apl\n1 2\n```\n\n```text\n⍳2\n```\n")


def test_split_fence_longer_than_backtick_content(cb):
    b, _ = _block(cb, "      x", "1")
    b = cb.Block(b.page, b.line, b.sha256, b.text, ["      x", "```", "``` y"], False)
    c = cb.classify(b)
    got = cb.split_block(b, c)
    assert got == "```apl\nx\n```\n\n````text\n```\n``` y\n````\n"
    cb.check_no_lost_lines(b, got)


def test_split_output_before_input(cb):
    b, c = _block(cb, "1 2", "      x", "3")
    assert cb.split_block(b, c) == (
        "```text\n1 2\n```\n\n```apl\nx\n```\n\n```text\n3\n```\n"
    )


# --- Running examples under dyalogscript ---------------------------------


def _dyalogscript():
    path = Path(__file__).with_name("code-blocks.py")
    spec = importlib.util.spec_from_file_location("code_blocks_probe", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.find_dyalogscript()


needs_dyalog = pytest.mark.skipif(
    _dyalogscript() is None, reason="dyalogscript is not installed"
)


def _run(cb, *blocks, timeout=60.0):
    """Run (style, lines) blocks, numbered from 1."""
    rb = [cb.RunBlock(i, st, ln) for i, (st, ln) in enumerate(blocks, 1)]
    return cb.run_page(rb, cb.find_dyalogscript(), timeout=timeout)


def _out(results, block=1):
    return [r.output for r in results[block]]


@needs_dyalog
def test_run_page_displays_non_shy_result_and_suppresses_shy(cb):
    res, timed_out = _run(cb, (None, ["2+3", "x←5", "x"]))
    assert timed_out is False
    assert _out(res) == [["5"], [], ["5"]]
    assert [r.error for r in res[1]] == [False] * 3
    assert [r.skipped for r in res[1]] == [None] * 3


@needs_dyalog
def test_run_page_state_carries_across_blocks(cb):
    res, _ = _run(cb, (None, ["x←7"]), (None, ["x+1"]))
    assert _out(res, 2) == [["8"]]


@needs_dyalog
def test_run_page_error_display(cb):
    res, _ = _run(
        cb, (None, ["1÷0", "⎕←3 ⋄ 1÷0", "foo 1", "1+(2 3+4 5 6)"]))
    r = res[1]
    assert r[0].output == ["DOMAIN ERROR: Divide by zero", "      1÷0", "       ∧"]
    assert r[0].error is True
    assert r[1].output == [
        "3",
        "DOMAIN ERROR: Divide by zero",
        "      ⎕←3 ⋄ 1÷0",
        "             ∧",
    ]
    assert r[2].output == ["VALUE ERROR: Undefined name: foo", "      foo 1", "      ∧"]
    assert r[3].output[1:] == ["      1+(2 3+4 5 6)", "            ∧"]


@needs_dyalog
def test_run_page_box_max(cb):
    res, _ = _run(cb, ("max", ["2 2⍴1 'ab' (1 2) 3"]), ("off", ["2 2⍴1 'ab' (1 2) 3"]))
    assert _out(res)[0][0] == "┌→───────────┐"
    assert [ln.rstrip() for ln in _out(res, 2)[0]] == ["   1  ab", " 1 2   3"]


@needs_dyalog
def test_run_page_user_command(cb):
    res, _ = _run(cb, (None, ["]box on", "1+1"]))
    assert res[1][0].output and res[1][0].output != [""]
    assert res[1][0].error is False


@needs_dyalog
def test_run_page_skips_off(cb):
    res, _ = _run(cb, (None, ["⎕OFF", ")off", "1+1"]))
    assert [(r.skipped, r.output) for r in res[1]] == [
        ("system-command", []),
        ("system-command", []),
        (None, ["2"]),
    ]


@needs_dyalog
def test_run_page_input_does_not_hang(cb):
    start = time.monotonic()
    _run(cb, (None, ["⍞"]), timeout=10)
    assert time.monotonic() - start < 15


@needs_dyalog
def test_run_page_timeout_kills_process(cb):
    res, timed_out = _run(cb, (None, ["1+1", "⎕DL 1000"]), timeout=5)
    assert timed_out is True
    assert _out(res) == [["2"]]  # the finished line is kept
    left = subprocess.run(
        ["pgrep", "-f", "code-blocks-harness"], capture_output=True, text=True
    )
    assert left.stdout == ""


@needs_dyalog
def test_dyalog_version(cb):
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.\d+)?", cb.dyalog_version(cb.find_dyalogscript()))


@needs_dyalog
def test_run_page_error_in_called_function_shows_function_line(cb):
    res, _ = _run(cb, (None, ["⎕FX 'r←foo x' 'r←10+1÷x'", "foo 0", "1"]))
    r = res[1]
    assert r[1].output == [
        "DOMAIN ERROR: Divide by zero",
        "foo[1] r←10+1÷x",
        "             ∧",
    ]
    assert r[1].error is True
    assert r[2].output == ["1"]  # the failed call left nothing suspended


@needs_dyalog
def test_run_page_splits_statements_on_diamond(cb):
    lines = [
        "x←1 ⋄ y←2",
        "1 ⋄ 2",
        "⎕←'a' ⋄ ⎕←'b'",
        "'a⋄b' ⋄ {1 ⋄ 2}0 ⍝ c ⋄ d",
        "1 ⋄ 1÷0 ⋄ 5",
        "x←3 ⋄ x+1",
    ]
    res, _ = _run(cb, (None, lines))
    got = [r.output for r in res[1]]
    assert got[:4] == [[], ["1", "2"], ["a", "b"], ["a⋄b", "1"]]
    assert got[4] == [
        "1",
        "DOMAIN ERROR: Divide by zero",
        "      1 ⋄ 1÷0 ⋄ 5",
        "           ∧",
    ]
    assert res[1][4].error is True
    assert got[5] == ["4"]


@needs_dyalog
def test_run_page_sentinel_text_in_output_is_not_a_sentinel(cb):
    res, _ = _run(cb, (None, ["⎕←'⍝CB-END'", "⎕←'⍝CB-BEGIN 9 9'", "1+1"]))
    assert _out(res) == [["⍝CB-END"], ["⍝CB-BEGIN 9 9"], ["2"]]
    assert 9 not in res


@needs_dyalog
def test_run_page_skips_quote_quad_and_scans_outside_strings(cb):
    lines = [
        "⍞←'q'",
        "1+1",
        "'⍞' ⍝ ⍞",
        "⍝ ⎕OFF",
        "⎕off",
        "'⎕OFF'",
        "⎕OFFSET←3",
        "x←⎕Off ⋄ 1",
    ]
    res, _ = _run(cb, (None, lines))
    r = res[1]
    assert (r[0].skipped, r[0].output) == ("quote-quad", [])
    assert (r[1].skipped, r[1].output) == (None, ["2"])
    assert r[2].output == ["⍞"] and r[2].skipped is None
    assert (r[3].skipped, r[3].output) == (None, [])
    assert (r[4].skipped, r[4].output) == ("system-command", [])
    assert (r[5].skipped, r[5].output) == (None, ["⎕OFF"])
    assert r[6].skipped is None
    assert r[7].skipped == "system-command"


@needs_dyalog
def test_run_page_top_level_dfn_error_arrow_and_mid_line_output(cb):
    res, _ = _run(cb, (None, ["{1÷⍵}0", "→0", "1 ⋄ →⍬", "→⍬ ⋄ 2", "1+1"]))
    r = res[1]
    assert r[0].output == ["DOMAIN ERROR: Divide by zero", "      {1÷⍵}0", "        ∧"]
    assert r[0].error is True
    for i in (1, 2, 3):
        assert (r[i].error, r[i].skipped) == (False, None)
    assert [r[i].output for i in (1, 2, 3, 4)] == [[], ["1"], ["2"], ["2"]]


def test_parse_output_accepts_sentinel_after_partial_line(cb):
    text = "⍝CB-t-BEGIN 1 0\nabc⍝CB-t-END\n"
    res, done = cb._parse_output(text, "t")
    assert done == 1
    assert res[1][0].output == ["abc"]


# --- judge -------------------------------------------------------------------


def _lr(cb, output, error=False, skipped=None):
    return cb.LineResult(output, error, skipped)


def _judge(cb, lines, results, prior_unrun=False, timed_out=False):
    b, c = _block(cb, *lines)
    return cb.judge(b, c, results, prior_unrun, timed_out)


def test_judge_match(cb):
    assert _judge(cb, ["      1+1", "2"], [_lr(cb, ["2"])]) == ("match", None, None)


def test_judge_differs(cb):
    v, r, actual = _judge(cb, ["      1+1", "3"], [_lr(cb, ["2"])])
    assert (v, r, actual) == ("differs", None, "      1+1\n2\n")


def test_judge_trailing_whitespace_is_not_a_difference(cb):
    res = [_lr(cb, ["2  ", ""])]
    assert _judge(cb, ["      1+1", "2\r", ""], res)[0] == "match"


def test_judge_leading_whitespace_is_a_difference(cb):
    assert _judge(cb, ["      1+1", "2"], [_lr(cb, [" 2"])])[0] == "differs"


def test_judge_unexpected_error_is_not_run(cb):
    err = ["VALUE ERROR: Undefined name: ab", "      ab", "      ∧"]
    v, r, actual = _judge(cb, ["      1+1", "2", "      ab", "3"],
                          [_lr(cb, ["2"]), _lr(cb, err, True)])
    assert (v, r) == ("not-run", "suspect-input")
    assert actual.startswith("      1+1\n2\n      ab\nVALUE ERROR")
    err = ["DOMAIN ERROR", "      1÷'a'", "      ∧"]
    assert _judge(cb, ["      1÷'a'", "3"], [_lr(cb, err, True)])[:2] == (
        "not-run", "error")


def test_judge_unexpected_error_after_unrun_block_is_dependency(cb):
    err = ["VALUE ERROR: Undefined name: ab", "      ab", "      ∧"]
    v, r, actual = _judge(cb, ["      ab", "3"], [_lr(cb, err, True)], prior_unrun=True)
    assert (v, r) == ("not-run", "dependency") and actual


def test_judge_expected_error_matching_is_match(cb):
    err = ["VALUE ERROR: Undefined name: ab", "      ab", "      ∧"]
    lines = ["      ab", *err]
    assert _judge(cb, lines, [_lr(cb, err, True)]) == ("match", None, None)


def test_judge_volatile_is_not_run(cb):
    for line in ("      ?6", "      ⎕ts", "      1+⎕TS"):
        assert _judge(cb, [line, "1"], [_lr(cb, ["1"])])[:2] == ("not-run", "volatile")
    for line in ("      '?'", "      1 ⍝ ?", "      ⎕TSX", "      'it''s ?'"):
        assert _judge(cb, [line, "1"], [_lr(cb, ["1"])])[0] == "match"


def test_judge_skipped_reason(cb):
    for s in ("system-command", "quote-quad"):
        assert _judge(cb, ["      1", "1"], [_lr(cb, [], skipped=s)]) == (
            "not-run", s, None)


def test_judge_unrunnable_and_not_run(cb):
    assert _judge(cb, ["      ∇ f", "x"], None, timed_out=True)[:2] == (
        "not-run", "del-definition")
    assert _judge(cb, ["      1", "1"], None) == ("not-run", "not-run", None)


def test_judge_timeout(cb):
    assert _judge(cb, ["      1", "1"], None, timed_out=True) == ("timeout", None, None)
    two = ["      1", "1", "      2", "2"]
    assert _judge(cb, two, [_lr(cb, ["1"])], timed_out=True)[0] == "timeout"


def test_judge_indented_output_confirmed_by_match(cb):
    lines = ["      ↑'       ab' 'cd'", "       ab", "cd"]
    b, c = _block(cb, *lines)
    v, r, _ = cb.judge(b, c, [_lr(cb, ["       ab", "cd"])], False, False)
    assert v == "match" and cb.split_kind(c, v, r) == "confirmed"


def test_expected_outputs(cb):
    b, c = _block(cb, "      a", "1", "", "      b", "2")
    assert cb.expected_outputs(b, c) == [["1", ""], ["2"]]


def test_judge_normalisation_trailing_blanks_are_asymmetric(cb):
    assert _judge(cb, ["      1+1", "2", ""], [_lr(cb, ["2"])])[0] == "match"
    assert _judge(cb, ["      1+1", "2"], [_lr(cb, ["2", "", ""])])[0] == "match"


def test_judge_rule_order(cb):
    two = ["      1", "1", "      ?2", "2"]
    # timeout before skipped
    r = [_lr(cb, [], skipped="system-command")]
    assert _judge(cb, two, r, timed_out=True)[0] == "timeout"
    # skipped before volatile
    r = [_lr(cb, [], skipped="quote-quad"), _lr(cb, ["2"])]
    assert _judge(cb, two, r)[:2] == ("not-run", "quote-quad")
    # volatile before unexpected error
    err = ["DOMAIN ERROR", "      ?2", "      ∧"]
    r = [_lr(cb, ["1"]), _lr(cb, err, True)]
    assert _judge(cb, two, r)[:2] == ("not-run", "volatile")
    # unexpected error before differs
    plain = ["      1", "1", "      2÷0", "2"]
    r = [_lr(cb, ["9"]), _lr(cb, err, True)]
    assert _judge(cb, plain, r)[:2] == ("not-run", "error")


def test_judge_incomplete_results_without_timeout(cb):
    two = ["      1", "1", "      2", "2"]
    assert _judge(cb, two, [_lr(cb, ["1"])]) == ("not-run", "incomplete", None)


def test_expected_outputs_leading_output_and_error_triple(cb):
    b, c = _block(
        cb, "stray", "      a", "VALUE ERROR", "      a", "      ∧", "      b", "2"
    )
    assert cb.expected_outputs(b, c) == [
        ["VALUE ERROR", "      a", "      ∧"],
        ["2"],
    ]
