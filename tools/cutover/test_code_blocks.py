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


def _entry(**kw):
    e = {
        "page": "a.md",
        "line": 3,
        "sha256": "abc",
        "verdict": "differs",
        "reason": None,
        "split": "confirmed",
        "nbsp": False,
        "decision": "pending",
        "original": "```apl\n      ⍳3\n1 2 3\n```\n",
        "proposed": "```apl\n      ⍳3\n```\n\n```text\n1 2 3\n```\n",
        "actual": "      ⍳3\n1 2 3\n",
    }
    e.update(kw)
    return e


META = {"guide": "g", "dyalog": "21.0", "platform": "mac", "generated": "now"}


def test_log_round_trips_multiline_text(cb, tmp_path):
    entries = [_entry(), _entry(line=9, proposed=None, actual=None)]
    path = tmp_path / "log.yaml"
    cb.write_log(path, META, entries)
    meta, back = cb.read_log(path)
    assert meta == META
    assert back == entries
    assert "⍳" in path.read_text(encoding="utf-8")
    assert list(back[0]) == list(entries[0])


def test_log_round_trips_trailing_spaces_and_blank_lines(cb, tmp_path):
    tricky = [
        "   \n1 2  \n\n",
        " lead\nx\n",
        "a b\n \n",
        "crlf\r\nline\r\n",
        "`a` ∇ ⍝ ┌→┐\n",
        "no newline",
    ]
    entries = [_entry(original=t, proposed=t, actual=t) for t in tricky]
    path = tmp_path / "log.yaml"
    cb.write_log(path, META, entries)
    _, back = cb.read_log(path)
    assert [(e["original"], e["proposed"], e["actual"]) for e in back] == [
        (t, t, t) for t in tricky
    ]


def test_log_uses_literal_blocks_for_plain_text(cb, tmp_path):
    path = tmp_path / "log.yaml"
    cb.write_log(path, META, [_entry()])
    text = path.read_text(encoding="utf-8")
    assert "original: |" in text
    assert text.startswith("#")


def test_write_log_names_page_and_line_on_mismatch(cb, tmp_path, monkeypatch):
    monkeypatch.setattr(cb.yaml, "safe_load", lambda s: {"meta": META, "entries": [_entry(original="x")]})
    with pytest.raises(cb.LogError, match=r"a\.md.*line"):
        cb.write_log(tmp_path / "log.yaml", META, [_entry()])


def test_carry_decisions_keeps_matching(cb):
    old = [_entry(decision="apply")]
    new = [_entry(decision="pending", line=7)]
    out = cb.carry_decisions(old, new)
    assert out[0]["decision"] == "apply"
    assert out[0]["line"] == 7
    assert new[0]["decision"] == "pending" and old[0]["decision"] == "apply"


def test_carry_decisions_resets_when_proposal_changed(cb):
    old = [_entry(decision="apply"), _entry(sha256="s2", decision="skip")]
    new = [_entry(proposed="other\n"), _entry(sha256="s2", split="ambiguous")]
    out = cb.carry_decisions(old, new)
    assert [e["decision"] for e in out] == ["pending", "pending"]


def test_carry_decisions_drops_vanished_blocks(cb):
    old = [_entry(sha256="gone", decision="apply"), _entry(decision="skip")]
    new = [_entry(sha256="new"), _entry()]
    out = cb.carry_decisions(old, new)
    assert [e["sha256"] for e in out] == ["new", "abc"]
    assert [e["decision"] for e in out] == ["pending", "skip"]


@pytest.mark.parametrize(
    "text",
    [
        "a\nb  \nc\n",
        "a\n   \nb\n",
        "  lead\nx\n",
        "a b\n⍳⍴ ⎕ ∇ ⍝ ┌→┐\n",
    ],
)
def test_log_keeps_awkward_text_as_literal_blocks(cb, tmp_path, text):
    path = tmp_path / "log.yaml"
    cb.write_log(path, META, [_entry(original=text, proposed=None, actual=None)])
    raw = path.read_text(encoding="utf-8")
    line = next(ln for ln in raw.splitlines() if ln.lstrip(" -").startswith("original:"))
    assert re.match(r"\s+original: \|\d?$", line), line
    assert cb.read_log(path)[1][0]["original"] == text


def test_log_quotes_only_strings_with_cr(cb, tmp_path):
    path = tmp_path / "log.yaml"
    cb.write_log(path, META, [_entry(original="a\r\nb\r\n")])
    raw = path.read_text(encoding="utf-8")
    assert 'original: "a\\r\\nb\\r\\n"' in raw
    assert "proposed: |" in raw
    assert cb.read_log(path)[1][0]["original"] == "a\r\nb\r\n"


def test_log_single_line_text_field_falls_back_to_quotes(cb, tmp_path):
    path = tmp_path / "log.yaml"
    cb.write_log(path, META, [_entry(original="a\x85b", proposed=None)])
    assert cb.read_log(path)[1][0]["original"] == "a\x85b"


def test_carry_decisions_matches_duplicates_by_occurrence(cb):
    old = [_entry(decision="apply"), _entry(decision="skip")]
    new = [_entry(line=1), _entry(line=2), _entry(line=3)]
    out = cb.carry_decisions(old, new)
    assert [e["decision"] for e in out] == ["apply", "skip", "pending"]


def test_write_log_failure_leaves_existing_log_untouched(cb, tmp_path, monkeypatch):
    path = tmp_path / "log.yaml"
    path.write_text("old contents\n", encoding="utf-8")
    monkeypatch.setattr(
        cb.yaml, "safe_load",
        lambda s: {"meta": META, "entries": [_entry(original="x")]},
    )
    with pytest.raises(cb.LogError):
        cb.write_log(path, META, [_entry()])
    assert path.read_text(encoding="utf-8") == "old contents\n"
    assert [p.name for p in tmp_path.iterdir()] == ["log.yaml"]


# --- main ---------------------------------------------------------------

import shutil as _shutil

_PAGE = (
    "# T\n\n```apl\n      1+1\n3\n```\n\n```apl\nplain listing\n```\n\n"
    "```apl\n      2+2\n4\n```\n"
)


def _docs(tmp_path, pages):
    docs = tmp_path / "docs"
    for rel, text in pages.items():
        p = docs / "g" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return docs


def _snapshot(root):
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _main(cb, tmp_path, *extra):
    log = tmp_path / "log.yml"
    rc = cb.main(
        ["--docs", str(tmp_path / "docs"), "--guide", "g", "--log", str(log), *extra]
    )
    return rc, log


def test_main_no_run_writes_log_and_leaves_docs_untouched(cb, tmp_path, capsys):
    docs = _docs(tmp_path, {"a.md": _PAGE, "sub/b.md": _PAGE})
    before = _snapshot(docs)
    rc, log = _main(cb, tmp_path, "--no-run")
    assert rc == 0
    assert _snapshot(docs) == before
    meta, entries = cb.read_log(log)
    assert meta["guide"] == "g" and meta["dyalog"] is None
    assert [e["page"] for e in entries] == ["g/a.md"] * 2 + ["g/sub/b.md"] * 2
    assert {e["verdict"] for e in entries} == {"not-run"}
    assert {e["reason"] for e in entries} == {"not-run"}
    assert entries[0]["split"] == "unambiguous"
    assert entries[0]["decision"] == "pending"
    assert entries[0]["original"] == "```apl\n      1+1\n3\n```\n"
    assert "total" in capsys.readouterr().out.lower()


def test_main_listing_blocks_are_counted_not_logged(cb, tmp_path, capsys):
    _docs(tmp_path, {"a.md": _PAGE})
    _, log = _main(cb, tmp_path, "--no-run")
    _, entries = cb.read_log(log)
    assert len(entries) == 2
    assert all("plain listing" not in e["original"] for e in entries)
    out = capsys.readouterr().out
    assert "3" in out and "1 listing" in out


def test_main_logs_nested_blocks_as_not_run(cb, tmp_path):
    md = "- item\n\n    ```apl\n          1+1\n    2\n    ```\n"
    _docs(tmp_path, {"a.md": md})
    _, log = _main(cb, tmp_path, "--no-run")
    _, entries = cb.read_log(log)
    assert len(entries) == 1
    e = entries[0]
    assert (e["verdict"], e["reason"], e["split"]) == ("not-run", "nested", "none")
    assert e["proposed"] is None and e["actual"] is None and e["nbsp"] is False


def test_main_carries_decisions_on_rerun(cb, tmp_path):
    _docs(tmp_path, {"a.md": _PAGE})
    _, log = _main(cb, tmp_path, "--no-run")
    meta, entries = cb.read_log(log)
    entries[0]["decision"] = "apply"
    cb.write_log(log, meta, entries)
    _main(cb, tmp_path, "--no-run")
    _, again = cb.read_log(log)
    assert [e["decision"] for e in again] == ["apply", "pending"]


def test_main_page_glob_is_relative_to_guide(cb, tmp_path):
    _docs(tmp_path, {"a.md": _PAGE, "sub/b.md": _PAGE})
    _, log = _main(cb, tmp_path, "--no-run", "--page", "sub/*.md")
    _, entries = cb.read_log(log)
    assert {e["page"] for e in entries} == {"g/sub/b.md"}


def test_main_split_error_becomes_ambiguous_split_check(cb, tmp_path, monkeypatch):
    _docs(tmp_path, {"a.md": _PAGE})

    def boom(block, cls):
        raise cb.SplitError("x")

    monkeypatch.setattr(cb, "split_block", boom)
    _, log = _main(cb, tmp_path, "--no-run")
    _, entries = cb.read_log(log)
    assert {(e["split"], e["reason"], e["proposed"]) for e in entries} == {
        ("ambiguous", "split-check", None)
    }


@pytest.mark.skipif(_shutil.which("dyalogscript") is None, reason="no dyalogscript")
def test_main_end_to_end_match_and_differs(cb, tmp_path):
    _docs(tmp_path, {"a.md": "```apl\n      1+1\n2\n```\n\n```apl\n      2+2\n5\n```\n"})
    rc, log = _main(cb, tmp_path)
    assert rc == 0
    meta, entries = cb.read_log(log)
    assert meta["dyalog"]
    assert [e["verdict"] for e in entries] == ["match", "differs"]
    assert [e["split"] for e in entries] == ["confirmed", "unambiguous"]
    assert entries[1]["actual"] == "      2+2\n4\n"


@needs_dyalog
def test_run_page_does_not_write_into_the_callers_cwd(cb, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _run(cb, (None, ["(⊂'x') ⎕NPUT 'litter.txt'"]))
    assert list(tmp_path.iterdir()) == []


# --- side effects, box pre-set, volatile, unreached, carets -------------------


def test_volatile_additions(cb):
    v = cb._is_volatile
    assert v("⎕PROFILE 'stop'") and v("120⌶4") and v("¯1 (16808 ⌶) 'U' 5")
    assert v("⎕DT 'Z'") and v("¯1 ⎕DT 'Z' ⍝ x")
    assert not v("0 ⎕DT 2020 1 1") and not v("⎕DT ⍝ 'Z'")


def _fake_run(cb, monkeypatch, results_fn, early=False):
    calls = []

    def fake(blocks, dyalogscript, timeout):
        calls.append(blocks)
        return results_fn(blocks), early

    monkeypatch.setattr(cb, "run_page", fake)
    monkeypatch.setattr(cb, "find_dyalogscript", lambda: "/fake")
    monkeypatch.setattr(cb, "dyalog_version", lambda d: "v")
    return calls


def _lres(cb, out):
    return cb.LineResult(out, False, None)


def test_main_side_effect_block_is_not_sent(cb, tmp_path, monkeypatch):
    md = "```apl\n      ⎕NDELETE 'x'\n```\n\n```apl\n      1+1\n2\n```\n"
    _docs(tmp_path, {"a.md": md})
    calls = _fake_run(cb, monkeypatch, lambda bs: {b.id: [_lres(cb, ["2"])] for b in bs})
    _, log = _main(cb, tmp_path)
    assert [[b.id for b in c] for c in calls] == [[1]]
    _, entries = cb.read_log(log)
    assert (entries[0]["verdict"], entries[0]["reason"]) == ("not-run", "side-effect")
    assert entries[1]["verdict"] == "match"


def test_main_box_style_is_none_when_block_or_earlier_block_uses_box(
    cb, tmp_path, monkeypatch
):
    box = "┌→┐\n│1│\n└─┘"
    md = (
        f"```apl\n      ]Boxing ON\n      ⍳1\n{box}\n```\n\n"
        f"```apl\n      ⍳1\n{box}\n```\n"
    )
    _docs(tmp_path, {"a.md": md})
    calls = _fake_run(cb, monkeypatch, lambda bs: {})
    _main(cb, tmp_path)
    assert [b.style for b in calls[0]] == [None, None]


def test_main_unreached_blocks_after_the_first_missing_line(
    cb, tmp_path, monkeypatch
):
    md = "".join(f"```apl\n      {i}+0\n{i}\n```\n\n" for i in (1, 2, 3, 4))
    _docs(tmp_path, {"a.md": md})
    _fake_run(
        cb, monkeypatch, lambda bs: {bs[0].id: [_lres(cb, ["1"])]}, early=True
    )
    _, log = _main(cb, tmp_path)
    _, entries = cb.read_log(log)
    assert [(e["verdict"], e["reason"]) for e in entries] == [
        ("match", None),
        ("timeout", None),
        ("not-run", "unreached"),
        ("not-run", "unreached"),
    ]


def test_classify_accepts_short_caret_in_error_triple(cb):
    body = ["      ÷0", "DOMAIN ERROR: Divide by zero", "      ÷0", "     ∧", "      1"]
    md = "```apl\n" + "\n".join(body) + "\n```\n"
    cls = cb.classify(cb.find_blocks("a.md", md)[0])
    assert cls.roles == ["input", "output", "output", "output", "input"]
    assert cls.kind == "session"


@needs_dyalog
def test_run_page_harness_leaves_no_names_in_the_workspace(cb):
    res, _ = _run(cb, (None, ["≢⎕NL ¯2 ¯9"]))
    assert _out(res) == [["0"]]


def test_has_side_effect(cb):
    for line in [
        "⎕NDELETE 'x'", "⎕ndelete'x'", "]Foo bar", "]", "x←⎕CSV 'a'",
        "⍎'⎕NDELETE ''x'''", "⍎'⎕SH ''rm x'''", "85⌶'⎕SH ''x'''",
        "⎕FX 'f' '⎕NDELETE ''x'''", "'⎕NDELETE'", "⎕SE.UCMD 'x'",
        "⎕se.SALT.Load 'x'", "⎕ED 'x'", "⎕EXPORT 'x'", "⎕NEW ⍬", "⎕FIO.x",
        "⎕HTTPCMD 'x'", "⌶", "x⌶1", "1 ⋄ )load x",
        ")load x", "1 ⋄ 2 ⋄  )off", "2⌶1", "8⌶1 ⋄ 85⌶x",
        "]display ⎕SH 'rm x'", "]rows ⎕NDELETE 'x'", "]box on ⍎')load x'",
        "]disp ⍎'⎕NDELETE ''x'''", "⍎'⎕',⎕UCS 83 72", "⍎⎕AV[5]", "¯8⌶1",
        "(¯8 ⌶)1",
    ]:
        assert cb.has_side_effect(line), line
    for line in [
        "]box on", "]Boxing", "]DISPLAY ⍳3", "]disp 1", "]rows 5",
        "1 ⍝ ⎕NDELETE ⎕SH", "1+1", "'a)b'", "1 ⋄ 2)",
        "8⌶1", "(181⌶)1", "(201 ⌶)1", "219⌶4", "1200⌶1", "16808⌶1",
        "(7162 ⌶)'x'", "]box on", "]display 2 2⍴⍳4", "⎕UCS 83 72",
    ]:
        assert not cb.has_side_effect(line), line


@pytest.mark.parametrize(
    "line",
    [
        # Evidence from the full run: these ran or were sent.
        "⎕XT'V'", "'V' ⎕XT 'file'", "9 ⎕ARBOUT 49 50 51", "⎕CLEAR",
        "'DDE:X'⎕SVO'X'", "⎕SVC 'X'", "⎕SVS 'X'", "⎕SVR 'X'", "⎕SVQ 'X'",
        "⎕USING←'System.IO' ⋄ File.Delete⊂'x'",
        "⍎'⎕','SH ''ls'''",
        # Allowlist: unknown or writing system names.
        "⎕NDELETEX", "⎕SEX", "⎕U234E 'x'", "⎕NINFO 'x'", "⎕OFF", "⎕WG 'x'",
        "⎕TKILL 0", "⎕TGET 1", "⎕PFKEY 1", "⎕NXLATE 0", "⎕SM",
        # Refs or names that lead back into the harness in ⎕SE.
        "⎕RSI", "⊃⎕NSI", "⊃⎕XSI", "⎕STACK",
        # Evaluated input.
        "x←⎕", "⎕", "1+⎕",
        # .NET names.
        "System.IO.File.Delete⊂'x'", "Microsoft.Win32.Registry", "x←'System.IO'",
        # Execute, ⎕FX and 85⌶ with an argument that is not a literal.
        "⍎x", "⍎¨'a' 'b'", "⍎&'1'", "⍎'1',x", "⍎'1' x", "{⍎⍵}'1'",
        "⎕FX f", "⎕FX ↑'a' 'b'", "⎕FX¨('a' 'b')", "⎕FX 'f' x",
        "85⌶x", "1(85⌶)x", "(85⌶)'1',x",
        # A literal is checked by the same rules.
        "⍎'⍎x'", "⎕FX 'r←f' 'r←⍎⍵'", "⍎'⎕XT''V'''", "1(85⌶)'⎕CLEAR'",
        # ⎕TRAP actions built at run time.
        "⎕TRAP←0 'E' x", "⎕TRAP←0 'E' (⎕UCS 41)", "⎕TRAP←0 'E' '⎕SH''x'''",
    ],
)
def test_has_side_effect_allowlist_flags(cb, line):
    assert cb.has_side_effect(line), line


@pytest.mark.parametrize(
    "line",
    [
        "⎕IO←0", "⎕io", "⎕←1", "⎕ ← 'a'", "x←⎕←2", "⎕NGET 'x'", "⎕NEXISTS 'x'",
        "⎕SIGNAL 11", "⎕WSID", "⎕FLIB 'x'", "⎕JSON '[1]'", "⎕DT 'Z'", "⎕AN",
        "⍎'2+2'", "⍎ '1+1'", "A←⍎''", "(⍎'1+2')+3", "'#' ⍎ '⎕PP ⎕CT ⎕RL'",
        "4=⍎'1+1 ⋄ 2+2'", "⍎'A←2|¯1↑⎕TS ⋄ →0⍴⍨A ⋄ A'",
        "⎕FX'r←foo' 'r←10'", "F←⎕OR ⎕FX'R←FOO' 'R←10'", "1(85⌶)'2+2'",
        "85⌶'1'", "(85⌶)'1'", "⎕TRAP⍴⍨←0", "≡⎕TRAP←0'C' '''Eh?'''",
        "'it''s'", "'a',⍕1",
    ],
)
def test_has_side_effect_allowlist_passes(cb, line):
    assert not cb.has_side_effect(line), line


def test_safe_quads_exclude_writers_and_harness_refs(cb):
    for name in (
        "⎕XT", "⎕ARBOUT", "⎕ARBIN", "⎕CLEAR", "⎕SVO", "⎕USING", "⎕SE", "⎕NA",
        "⎕NINFO", "⎕CSV", "⎕RSI", "⎕NSI", "⎕XSI", "⎕STACK", "⎕EXCEPTION",
        "⎕OFF", "⎕SAVE", "⎕LOAD", "⎕FIX", "⎕CY", "⎕WX",
    ):
        assert name not in cb.SAFE_QUADS, name
    # Side effect wins over volatile and environment: a name may be in both.
    for name in ("⎕TS", "⎕AI", "⎕DL", "⎕WSID", "⎕AN", "⎕FLIB"):
        assert name in cb.SAFE_QUADS, name


def test_main_using_taints_the_block_and_later_blocks(cb, tmp_path, monkeypatch):
    md = (
        "```apl\n      1+1\n2\n```\n\n"
        "```apl\n      ⎕USING←'System.IO'\n```\n\n"
        "```apl\n      File.Delete⊂'x'\n```\n\n"
        "```apl\n      2+2\n4\n```\n"
    )
    _docs(tmp_path, {"a.md": md})
    calls = _fake_run(
        cb, monkeypatch, lambda bs: {b.id: [_lres(cb, ["2"])] for b in bs}
    )
    _, log = _main(cb, tmp_path)
    assert [[b.id for b in c] for c in calls] == [[0]]
    _, entries = cb.read_log(log)
    assert [e["reason"] for e in entries[1:]] == ["side-effect"] * 3


def test_main_evidence_forms_are_never_sent(cb, tmp_path, monkeypatch):
    lines = [
        "⎕XT'V'", "9 ⎕ARBOUT 49 50 51", "⎕CLEAR", "'DDE:X'⎕SVO'X'",
        "⍎'⎕','SH ''ls'''", "System.IO.File.Delete⊂'x'",
    ]
    md = "".join(f"```apl\n      {ln}\n```\n\n" for ln in lines)
    _docs(tmp_path, {"a.md": md + "```apl\n      2+2\n4\n```\n"})
    calls = _fake_run(
        cb, monkeypatch, lambda bs: {b.id: [_lres(cb, ["4"])] for b in bs}
    )
    _main(cb, tmp_path)
    assert [[b.lines for b in c] for c in calls] == [[["2+2"]]]


@needs_dyalog
@pytest.mark.skipif(
    not Path("/usr/bin/sandbox-exec").exists(), reason="no macOS sandbox-exec"
)
def test_run_page_sandbox_blocks_writes_outside_the_run_directory(cb, tmp_path):
    # run_page does not filter side effects; the sandbox must stop this write.
    target = tmp_path / "escaped.txt"
    res, _ = _run(cb, (None, [f"(⊂'x') ⎕NPUT '{target}'", "1+1"]))
    assert not target.exists()
    assert res[1][0].error is True
    assert _out(res)[1] == ["2"]


def test_judge_value_error_suspect_only_if_name_not_used_elsewhere(cb):
    err = cb.LineResult(
        ["VALUE ERROR: Undefined name: M", "      M", "      ∧"], True, None
    )
    # the name is used by another input line: generic error, not suspect
    r = _judge(cb, ["      M", "      2 0 1/M"], [err, err])
    assert r[:2] == ("not-run", "error")
    # only this line uses the name: suspect
    assert _judge(cb, ["      M"], [err])[:2] == ("not-run", "suspect-input")
    # a longer name containing it does not count
    r = _judge(cb, ["      M", "      MM+1"], [err, _lr(cb, ["1"])])
    assert r[:2] == ("not-run", "suspect-input")
    # SYNTAX ERROR stays suspect
    syn = cb.LineResult(["SYNTAX ERROR", "      1+", "       ∧"], True, None)
    r = _judge(cb, ["      M", "      M"], [syn, syn])
    assert r[:2] == ("not-run", "suspect-input")


def test_main_side_effect_forms_are_never_sent(cb, tmp_path, monkeypatch):
    blocks = [
        "      1+1\n2\n      ⎕NDELETE 'x'",
        "      ⍎'⎕SH ''rm x'''",
        "      1 ⋄ )load x",
        "      ⎕SE.UCMD 'x'",
        "      85⌶x",  # a literal argument would be checked and allowed
        "      2+2\n4",
    ]
    md = "".join(f"```apl\n{b}\n```\n\n" for b in blocks)
    _docs(tmp_path, {"a.md": md})
    calls = _fake_run(
        cb, monkeypatch, lambda bs: {b.id: [_lres(cb, ["4"])] for b in bs}
    )
    _, log = _main(cb, tmp_path)
    assert [[(b.id, b.lines) for b in c] for c in calls] == [[(5, ["2+2"])]]
    _, entries = cb.read_log(log)
    assert [e["reason"] for e in entries[:5]] == ["side-effect"] * 5


def test_main_page_with_existing_log_keeps_other_pages(cb, tmp_path):
    _docs(tmp_path, {"a.md": _PAGE, "sub/b.md": _PAGE})
    _, log = _main(cb, tmp_path, "--no-run")
    meta, entries = cb.read_log(log)
    for e in entries:
        e["decision"] = "apply"
    cb.write_log(log, meta, entries)
    (tmp_path / "docs" / "g" / "sub" / "b.md").write_text(
        _PAGE.replace("2+2\n4\n", "3+3\n6\n"), encoding="utf-8"
    )
    _main(cb, tmp_path, "--no-run", "--page", "sub/*.md")
    _, after = cb.read_log(log)
    assert [(e["page"], e["line"]) for e in after] == [
        (e["page"], e["line"]) for e in entries[:2]
    ] + [("g/sub/b.md", 3), ("g/sub/b.md", 12)]
    assert after[:2] == entries[:2]
    assert [e["decision"] for e in after[2:]] == ["apply", "pending"]
