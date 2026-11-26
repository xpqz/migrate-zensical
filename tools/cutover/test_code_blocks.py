"""Behavioural surface for the apl block finder in code-blocks.py."""

import hashlib
import importlib.util
import re
import subprocess
import sys
import os
import signal
import tempfile
import time
from pathlib import Path

import shutil as _shutil

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


# --- Running examples in Docker ---------------------------------------------


def _docker_image():
    """The tool's default image if Docker runs and the image is built, else None.

    Cheap on purpose: collection never builds or pulls an image.
    """
    path = Path(__file__).with_name("code-blocks.py")
    spec = importlib.util.spec_from_file_location("code_blocks_probe", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    image = mod.default_image()
    docker = _shutil.which("docker")
    if docker is None:
        return None
    for args in (["info"], ["image", "inspect", image]):
        try:
            done = subprocess.run(
                [docker, *args], capture_output=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode:
            return None
    return image


IMAGE = _docker_image()
needs_docker = pytest.mark.skipif(
    IMAGE is None, reason="Docker or the code-blocks image is not available"
)


def _run(cb, *blocks, timeout=60.0, box=None):
    """Run (style, lines) blocks, numbered from 1, as one page.

    Without `box`, in a sandbox of its own that is closed afterwards.
    """
    rb = [cb.RunBlock(i, st, ln) for i, (st, ln) in enumerate(blocks, 1)]
    if box is not None:
        return cb.run_page(rb, box, timeout=timeout)
    with cb.Sandbox(IMAGE) as own:
        return cb.run_page(rb, own, timeout=timeout)


def _containers(name="cb-"):
    out = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", f"name={name}"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.split()


def _out(results, block=1):
    return [r.output for r in results[block]]


@needs_docker
def test_run_page_displays_non_shy_result_and_suppresses_shy(cb):
    res, stop = _run(cb, (None, ["2+3", "x←5", "x"]))
    assert stop is None
    assert _out(res) == [["5"], [], ["5"]]
    assert [r.error for r in res[1]] == [False] * 3
    assert [r.skipped for r in res[1]] == [None] * 3


@needs_docker
def test_run_page_state_carries_across_blocks(cb):
    res, _ = _run(cb, (None, ["x←7"]), (None, ["x+1"]))
    assert _out(res, 2) == [["8"]]


@needs_docker
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


@needs_docker
def test_run_page_box_max(cb):
    res, _ = _run(cb, ("max", ["2 2⍴1 'ab' (1 2) 3"]), ("off", ["2 2⍴1 'ab' (1 2) 3"]))
    assert _out(res)[0][0] == "┌→───────────┐"
    assert [ln.rstrip() for ln in _out(res, 2)[0]] == ["   1  ab", " 1 2   3"]


@needs_docker
def test_run_page_user_command(cb):
    res, _ = _run(cb, (None, ["]box on", "1+1"]))
    assert res[1][0].output and res[1][0].output != [""]
    assert res[1][0].error is False


@needs_docker
def test_run_page_display_command_sees_user_names_and_has_no_result(cb):
    res, _ = _run(cb, (None, ["Q1←2 2⍴⍳4", "]Display Q1", "]disp 2 2⍴⍳4", "1+1"]))
    r = res[1]
    assert [x.error for x in r] == [False] * 4
    assert r[1].output == ["┌→──┐", "↓1 2│", "│3 4│", "└~──┘"]
    assert r[2].output and not any("ERROR" in ln for ln in r[2].output)
    assert r[3].output == ["2"]


@needs_docker
def test_run_page_skips_off(cb):
    res, _ = _run(cb, (None, ["⎕OFF", ")off", "1+1"]))
    assert [(r.skipped, r.output) for r in res[1]] == [
        ("system-command", []),
        ("system-command", []),
        (None, ["2"]),
    ]


@needs_docker
def test_run_page_input_does_not_hang(cb):
    start = time.monotonic()
    _run(cb, (None, ["⍞"]), timeout=10)
    assert time.monotonic() - start < 15


@needs_docker
def test_run_page_timeout_kills_the_interpreter_and_keeps_the_container(cb):
    with cb.Sandbox(IMAGE) as box:
        res, stop = _run(cb, (None, ["1+1", "⎕DL 1000"]), timeout=5, box=box)
        assert stop == "timeout"
        assert _out(res) == [["2"]]  # the finished line is kept
        name = box.name
        assert name and _containers(name)
        res, stop = _run(cb, (None, ["⎕SH 'grep -l unicode/dyalo[g] /proc/[0-9]*/cmdline | wc -l'"]),
                         box=box)
        assert stop is None
        assert _out(res) == [["1"]]  # only this page's interpreter
        assert box.name == name
    assert _containers(name) == []


@needs_docker
def test_dyalog_version(cb):
    with cb.Sandbox(IMAGE) as box:
        assert re.fullmatch(r"\d+\.\d+\.\d+(\.\d+)?", cb.dyalog_version(box))
        name = box.name
    assert name and _containers(name) == []


@needs_docker
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


@needs_docker
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


@needs_docker
def test_run_page_sentinel_text_in_output_is_not_a_sentinel(cb):
    res, _ = _run(cb, (None, ["⎕←'⍝CB-END'", "⎕←'⍝CB-BEGIN 9 9'", "1+1"]))
    assert _out(res) == [["⍝CB-END"], ["⍝CB-BEGIN 9 9"], ["2"]]
    assert 9 not in res


@needs_docker
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


@needs_docker
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
    stop = timed_out if isinstance(timed_out, str) else ("timeout" if timed_out else None)
    return cb.judge(b, c, results, prior_unrun, stop)


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
    v, r, _ = cb.judge(b, c, [_lr(cb, ["       ab", "cd"])], False, None)
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


@needs_docker
def test_main_end_to_end_match_and_differs(cb, tmp_path):
    _docs(tmp_path, {"a.md": "```apl\n      1+1\n2\n```\n\n```apl\n      2+2\n5\n```\n"})
    rc, log = _main(cb, tmp_path)
    assert rc == 0
    meta, entries = cb.read_log(log)
    assert meta["dyalog"]
    # The container's image and platform, not the host's.
    assert meta["image"] == IMAGE
    assert meta["image_id"].startswith("sha256:")
    assert re.fullmatch(r"linux/\w+", meta["platform"])
    assert [e["verdict"] for e in entries] == ["match", "differs"]
    assert [e["split"] for e in entries] == ["confirmed", "unambiguous"]
    assert entries[1]["actual"] == "      2+2\n4\n"


@needs_docker
def test_run_page_does_not_write_into_the_callers_cwd(cb, tmp_path, monkeypatch):
    # The ⎕NPUT is deliberate: run_page does not filter side effects (that is
    # _process_page's job), so this checks the run's own scratch directory.
    monkeypatch.chdir(tmp_path)
    _run(cb, (None, ["(⊂'x') ⎕NPUT 'litter.txt'"]))
    assert list(tmp_path.iterdir()) == []


# --- side effects, box pre-set, volatile, unreached, carets -------------------


def test_volatile_additions(cb):
    v = cb._is_volatile
    assert v("⎕PROFILE 'stop'") and v("120⌶4") and v("¯1 (16808 ⌶) 'U' 5")
    assert v("⎕DT 'Z'") and v("¯1 ⎕DT 'Z' ⍝ x")
    assert not v("0 ⎕DT 2020 1 1") and not v("⎕DT ⍝ 'Z'")


def _fake_run(cb, monkeypatch, results_fn, early=None):
    calls = []

    def fake(blocks, image, timeout):
        calls.append(blocks)
        return results_fn(blocks), early

    monkeypatch.setattr(cb, "run_page", fake)
    monkeypatch.setattr(cb, "prepare_image", lambda image: "fake:image")
    monkeypatch.setattr(cb, "dyalog_version", lambda image: "v")
    monkeypatch.setattr(
        cb, "image_meta", lambda image: {"image": image, "image_id": "x", "platform": "p"}
    )
    return calls


def _lres(cb, out):
    return cb.LineResult(out, False, None)


def test_main_side_effect_block_is_not_sent(cb, tmp_path, monkeypatch):
    md = "```apl\n      ⎕SE.UCMD 'x'\n```\n\n```apl\n      1+1\n2\n```\n"
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
        cb, monkeypatch, lambda bs: {bs[0].id: [_lres(cb, ["1"])]}, early="timeout"
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


@needs_docker
def test_run_page_harness_leaves_no_names_in_the_workspace(cb):
    res, _ = _run(cb, (None, ["≢⎕NL ¯2 ¯9"]))
    assert _out(res) == [["0"]]


def test_has_side_effect(cb):
    for line in [
        "]Foo bar", "]", "⍎'⎕SE.UCMD ''x'''", "85⌶'⎕CLEAR'",
        "⎕FX 'f' '⎕CLEAR'", "'⎕CLEAR'", "⎕SE.UCMD 'x'",
        "⎕se.SALT.Load 'x'", "⎕ED 'x'", "⎕FIO.x",
        "⎕HTTPCMD 'x'", "⌶", "x⌶1", "1 ⋄ )load x",
        ")load x", "1 ⋄ 2 ⋄  )off", "2⌶1", "8⌶1 ⋄ 85⌶x",
        "]display ⎕SE.x", "]rows ⎕CLEAR", "]box on ⍎')load x'",
        "]disp ⍎'⎕CLEAR'", "⍎'⎕',⎕UCS 83 69", "⍎⎕AV[5]", "¯8⌶1",
        "(¯8 ⌶)1",
    ]:
        assert cb.has_side_effect(line), line
    for line in [
        "]box on", "]Boxing", "]DISPLAY ⍳3", "]disp 1", "]rows 5",
        "1 ⍝ ⎕CLEAR ⎕SE", "1+1", "'a)b'", "1 ⋄ 2)",
        "8⌶1", "(181⌶)1", "(201 ⌶)1", "219⌶4", "1200⌶1", "16808⌶1",
        "(7162 ⌶)'x'", "]box on", "]display 2 2⍴⍳4", "⎕UCS 83 72",
        "]display ⎕NGET 'x'",
    ]:
        assert not cb.has_side_effect(line), line


@pytest.mark.parametrize(
    "line",
    [
        # Still blocked in the jail: these break or escape the run.
        "9 ⎕ARBOUT 49 50 51", "⎕CLEAR", "⎕LOAD 'x'", "⎕OFF", "⎕SAVE 'x'",
        "'DDE:X'⎕SVO'X'", "⎕SVC 'X'", "⎕SVS 'X'", "⎕SVR 'X'", "⎕SVQ 'X'",
        "⎕WC 'Form'", "'.' ⎕WS 'x' 1", "⎕WG 'x'", "⎕WN 'x'", "⎕NQ 'x'",
        "⎕DQ 'x'", "⎕SE", "x←⎕SE.UCMD 'x'",
        "⍎'⎕','SE'",
        # Allowlist: unknown names and names left out on purpose.
        "⎕NDELETEX", "⎕SEX", "⎕U234E 'x'", "⎕TKILL 0", "⎕TGET 1",
        "⎕PFKEY 1", "⎕SM", "⎕ARBIN 1", "⎕WX",
        # Removed from the allowlist: they change or reach other namespaces.
        "⎕CS ⎕SE", "⎕CS 'x'", "⎕PATH←'⎕SE'", "x←⎕PATH",
        # Refs or names that lead back into the harness in ⎕SE.
        "⎕RSI", "⊃⎕NSI", "⊃⎕XSI", "⎕STACK",
        # Evaluated input.
        "x←⎕", "⎕", "1+⎕",
        # Execute, ⎕FX and 85⌶ with an argument that is not a literal.
        "⍎x", "⍎¨'a' 'b'", "⍎&'1'", "⍎'1',x", "⍎'1' x", "{⍎⍵}'1'",
        "⎕FX f", "⎕FX ↑'a' 'b'", "⎕FX¨('a' 'b')", "⎕FX 'f' x",
        "85⌶x", "1(85⌶)x", "(85⌶)'1',x",
        # Dyadic ⍎ whose left argument is not a name, # or a literal string.
        "(⎕NS'')⍎'1'", "ns.sub⍎'1'", "#.x⍎'1'", "⎕THIS⍎'1'", "x[1]⍎'1'",
        "⍺⍎'1'", "⍵⍎'1'", "##⍎'1'", "x∘⍎'1'", "1⍎'1'", "(f x)⍎'1'",
        # A literal is checked by the same rules.
        "⍎'⍎x'", "⎕FX 'r←f' 'r←⍎⍵'", "⍎'⎕CLEAR'", "1(85⌶)'⎕CLEAR'",
        # ⎕TRAP actions built at run time.
        "⎕TRAP←0 'E' x", "⎕TRAP←0 'E' (⎕UCS 41)", "⎕TRAP←0 'E' '⎕CLEAR'",
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
        # Dyadic ⍎ with a plain name, # or a literal string on the left.
        "space⍎'nums←⍳6'", "space ⍎ 'nums'", "#⍎'1+1'", "'#'⍎'1'",
        "x←ns⍎'a'", "{⍎'1'}0", "+/⍎'1 2'",
        # Allowed inside the jail: files, workspaces, .NET, shell, ⎕NA.
        "⎕NDELETE 'x'", "⎕ndelete'x'", "x←⎕CSV 'a'", "(⊂'x') ⎕NPUT 'f'",
        "'f' ⎕NCREATE 0", "⎕NINFO 'x'", "⎕NXLATE 0", "⎕MKDIR 'd'",
        "'t' ⎕FCREATE 1", "x ⎕FAPPEND 1", "⎕FREAD 1 1", "⎕FERASE 't'",
        "⎕XT'V'", "'V' ⎕XT 'file'", "⎕FIX 'file://x.apln'", "⎕CY 'dfns'",
        "⎕EXPORT 'x'", "⎕MAP 'f'",
        "⎕USING←'System.IO' ⋄ File.Delete⊂'x'", "System.IO.File.Delete⊂'x'",
        "Microsoft.Win32.Registry", "x←'System.IO'", "⎕NEW DateTime", "⎕EXCEPTION",
        "⎕SH 'ls'", "⎕CMD 'dir'", "⎕SHELL 'ls'", "⎕NA 'I4 libc.so.6|getpid'",
        "⍎'⎕SH ''ls'''", "⍎'⎕NDELETE ''x'''",
    ],
)
def test_has_side_effect_allowlist_passes(cb, line):
    assert not cb.has_side_effect(line), line


def test_safe_quads_exclude_what_breaks_or_escapes_the_run(cb):
    for name in (
        "⎕ARBOUT", "⎕ARBIN", "⎕CLEAR", "⎕LOAD", "⎕OFF", "⎕SVO", "⎕SVC", "⎕SVQ",
        "⎕SVR", "⎕SVS", "⎕SE", "⎕WC", "⎕WS", "⎕WG", "⎕WN", "⎕WX", "⎕NQ", "⎕DQ",
        "⎕RSI", "⎕NSI", "⎕XSI", "⎕STACK", "⎕CS", "⎕PATH", "⎕ED", "⎕TKILL",
        "⎕SAVE",
    ):
        assert name not in cb.SAFE_QUADS, name
    for name in (
        "⎕TS", "⎕AI", "⎕DL", "⎕WSID", "⎕AN", "⎕FLIB", "⎕NPUT", "⎕FCREATE",
        "⎕XT", "⎕FIX", "⎕CY", "⎕USING", "⎕NEW", "⎕SH", "⎕NA",
    ):
        assert name in cb.SAFE_QUADS, name


def test_main_using_no_longer_taints_later_blocks(cb, tmp_path, monkeypatch):
    md = (
        "```apl\n      1+1\n2\n```\n\n"
        "```apl\n      ⎕USING←'System.IO'\n```\n\n"
        "```apl\n      File.Exists⊂'x'\n0\n```\n\n"
        "```apl\n      2+2\n4\n```\n"
    )
    _docs(tmp_path, {"a.md": md})
    calls = _fake_run(cb, monkeypatch, lambda bs: {})
    _main(cb, tmp_path)
    assert [[b.id for b in c] for c in calls] == [[0, 1, 2, 3]]


def test_main_evidence_forms_are_never_sent(cb, tmp_path, monkeypatch):
    lines = [
        "9 ⎕ARBOUT 49 50 51", "⎕CLEAR", "'DDE:X'⎕SVO'X'",
        "⍎'⎕','SE'", "⎕CS ⎕SE", "(⎕NS'')⍎'1'",
    ]
    md = "".join(f"```apl\n      {ln}\n```\n\n" for ln in lines)
    _docs(tmp_path, {"a.md": md + "```apl\n      2+2\n4\n```\n"})
    calls = _fake_run(
        cb, monkeypatch, lambda bs: {b.id: [_lres(cb, ["4"])] for b in bs}
    )
    _main(cb, tmp_path)
    assert [[b.lines for b in c] for c in calls] == [[["2+2"]]]


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
        "      1+1\n2\n      ⎕CLEAR",
        "      ⍎'⎕SE.UCMD ''x'''",
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


# --- differs triage: after-unrun, diamond, annotation, environment, suspect --


def test_judge_differs_after_unrun_block(cb):
    v, r, actual = _judge(cb, ["      1+1", "3"], [_lr(cb, ["2"])], prior_unrun=True)
    assert (v, r, actual) == ("differs", "after-unrun", "      1+1\n2\n")


def test_judge_differs_with_top_level_diamond(cb):
    res = [_lr(cb, ["1", "2"])]
    assert _judge(cb, ["      1 ⋄ 2", "3"], res)[:2] == ("differs", "diamond")
    for line in ("      {1 ⋄ 2}0", "      '1⋄2'", "      1 ⍝ ⋄", "      x[1⋄2]"):
        assert _judge(cb, [line, "3"], res)[:2] == ("differs", None), line


@pytest.mark.parametrize("name", ["⎕DM", "⎕EM 11", "⎕EN", "⎕STACK", "⎕XSI", "⎕NSI", "⎕RSI"])
def test_volatile_harness_artefacts(cb, name):
    assert cb._is_volatile(name)


@pytest.mark.parametrize(
    "line", ["⎕FLIB '.'", "⎕TCNUMS 0", "⎕WSID", "⎕AN", "⎕SD"]
)
def test_judge_environment_is_not_run(cb, line):
    v = _judge(cb, ["      " + line, "x"], [_lr(cb, ["y"])])
    assert v == ("not-run", "environment", None)


@pytest.mark.parametrize("line", ["⎕NGET 'x'", "⎕NEXISTS 'x'"])
def test_judge_file_reads_are_judged_in_the_jail(cb, line):
    # The jail starts empty, so what a page reads it wrote itself.
    v = _judge(cb, ["      " + line, "x"], [_lr(cb, ["y"])])
    assert v[0] == "differs"


def test_judge_annotation_lines_are_not_compared(cb):
    lines = ["      x←1", "⍝ x is now 1", "      x", "1", "⍝ see above"]
    b, c = _block(cb, *lines)
    assert c.annotation is True
    v, r, _ = cb.judge(b, c, [_lr(cb, []), _lr(cb, ["1"])], False, None)
    assert (v, r) == ("match", "annotation")
    assert cb.split_kind(c, v, r) == "ambiguous"
    # an indented ⍝ line is not an annotation
    assert _block(cb, "      x", " ⍝ 1")[1].annotation is False


def test_judge_suspect_input_for_wrapped_output_row(cb):
    # pw.md: a folded output row at six spaces reads as input.
    lines = [
        "      ⎕PW←42",
        "",
        "      ⎕←3⍴÷3",
        "0.3333333333 0.3333333333 0.3333333333",
        "      0.3333333333",
    ]
    results = [
        _lr(cb, []),
        _lr(cb, ["0.3333333333 0.3333333333", "      0.3333333333"]),
        _lr(cb, ["0.3333333333"]),
    ]
    b, c = _block(cb, *lines)
    v, r, actual = cb.judge(b, c, results, False, None)
    assert (v, r) == ("differs", "suspect-input") and actual
    assert cb.split_kind(c, v, r) == "ambiguous"


def test_judge_differs_reason_precedence(cb):
    # suspect-input wins over after-unrun, which wins over diamond.
    two = ["      1 ⋄ 2", "1", "      9"]
    res = [_lr(cb, ["1", "2"]), _lr(cb, ["9"])]
    assert _judge(cb, two, res, prior_unrun=True)[:2] == ("differs", "suspect-input")
    one = ["      1 ⋄ 2", "1"]
    res = [_lr(cb, ["1", "2"])]
    assert _judge(cb, one, res, prior_unrun=True)[:2] == ("differs", "after-unrun")
    lines = ["      1 ⋄ 2", "⍝ note", "1"]
    assert _judge(cb, lines, res)[:2] == ("differs", "diamond")


def test_split_kind_suspect_input_is_ambiguous_for_any_verdict(cb):
    _, c = _block(cb, "      x", "1")
    for verdict in ("differs", "not-run", "match"):
        assert cb.split_kind(c, verdict, "suspect-input") == "ambiguous"


# --- keeping review decisions ------------------------------------------------


def test_carry_decisions_across_confirmed_and_unambiguous(cb):
    old = [_entry(split="confirmed", decision="apply")]
    out = cb.carry_decisions(old, [_entry(split="unambiguous")])
    assert out[0]["decision"] == "apply"
    old = [_entry(split="unambiguous", decision="skip")]
    out = cb.carry_decisions(old, [_entry(split="confirmed")])
    assert out[0]["decision"] == "skip"
    # a changed proposal or an ambiguous split still resets
    old = [_entry(split="confirmed", decision="apply")]
    out = cb.carry_decisions(old, [_entry(split="ambiguous", proposed=None)])
    assert out[0]["decision"] == "pending"


@pytest.mark.parametrize("extra", [["--guide", "nope"], ["--page", "zzz*.md"]])
def test_main_missing_guide_or_no_pages_leaves_log_untouched(cb, tmp_path, capsys, extra):
    _docs(tmp_path, {"a.md": _PAGE})
    log = tmp_path / "log.yml"
    log.write_text("previous\n", encoding="utf-8")
    argv = ["--docs", str(tmp_path / "docs"), "--guide", "g", "--log", str(log), "--no-run"]
    rc = cb.main(argv + extra)
    assert rc != 0
    assert log.read_text(encoding="utf-8") == "previous\n"
    assert not (tmp_path / "log.yml.bak").exists()
    assert capsys.readouterr().err.strip()


def test_main_keeps_a_backup_and_reports_reset_decisions(cb, tmp_path, capsys):
    _docs(tmp_path, {"a.md": _PAGE})
    _, log = _main(cb, tmp_path, "--no-run")
    meta, entries = cb.read_log(log)
    entries[0]["decision"] = "apply"
    entries[0]["proposed"] = "an older proposal\n"  # reset: proposal changed
    entries[1]["decision"] = "skip"
    entries[1]["sha256"] = "0" * 64  # dropped: the block is gone
    cb.write_log(log, meta, entries)
    before = log.read_text(encoding="utf-8")
    capsys.readouterr()
    _main(cb, tmp_path, "--no-run")
    assert (tmp_path / "log.yml.bak").read_text(encoding="utf-8") == before
    out = capsys.readouterr().out
    assert "Decisions: 1 reset to pending, 1 dropped" in out
    _, after = cb.read_log(log)
    assert [e["decision"] for e in after] == ["pending", "pending"]


# --- minors: exited, read_log, per-page counts, --page merge ------------------


@needs_docker
def test_run_page_reports_an_early_exit(cb):
    # Evaluated input reads the closed stdin and ends the interpreter.
    res, stop = _run(cb, (None, ["1+1", "x←⎕", "2+2"]), timeout=20)
    assert stop == "exited"
    assert _out(res) == [["2"]]


def test_judge_early_exit_is_timeout_exited(cb):
    two = ["      1", "1", "      2", "2"]
    assert _judge(cb, two, [_lr(cb, ["1"])], timed_out="exited") == (
        "timeout", "exited", None)
    assert _judge(cb, two, None, timed_out="exited") == ("timeout", "exited", None)


def test_main_early_exit_entry(cb, tmp_path, monkeypatch):
    md = "```apl\n      1+0\n1\n      2+0\n2\n```\n"
    _docs(tmp_path, {"a.md": md})
    _fake_run(cb, monkeypatch, lambda bs: {bs[0].id: [_lres(cb, ["1"])]}, early="exited")
    _, log = _main(cb, tmp_path)
    _, entries = cb.read_log(log)
    assert [(e["verdict"], e["reason"]) for e in entries] == [("timeout", "exited")]


def test_read_log_rejects_unknown_decision(cb, tmp_path):
    path = tmp_path / "log.yaml"
    cb.write_log(path, META, [_entry(), _entry(line=9, decision="aply")])
    with pytest.raises(cb.LogError, match=r"a\.md.*9.*aply"):
        cb.read_log(path)


def test_main_meta_pages_counts_pages_with_differs(cb, tmp_path, monkeypatch):
    two = "```apl\n      1+1\n2\n```\n\n```apl\n      1+1\n3\n```\n"
    one = "```apl\n      1+1\n2\n```\n"
    _docs(tmp_path, {"a.md": two, "b.md": one})
    _fake_run(cb, monkeypatch, lambda bs: {b.id: [_lres(cb, ["2"])] for b in bs})
    _, log = _main(cb, tmp_path)
    meta, _ = cb.read_log(log)
    assert meta["pages"] == {"g/a.md": {"differs": 1, "match": 1}}


def test_main_page_merge_when_page_has_no_entries_left(cb, tmp_path):
    _docs(tmp_path, {"a.md": _PAGE, "sub/b.md": _PAGE})
    _, log = _main(cb, tmp_path, "--no-run")
    _, entries = cb.read_log(log)
    (tmp_path / "docs" / "g" / "sub" / "b.md").write_text("# only text\n", encoding="utf-8")
    _main(cb, tmp_path, "--no-run", "--page", "sub/*.md")
    _, after = cb.read_log(log)
    assert after == [e for e in entries if e["page"] == "g/a.md"]


def test_judge_suspect_input_applies_to_volatile_and_environment(cb):
    # Any verdict but match: a line with no printed output that printed
    # something may be a folded output row.
    for line in ("      ?6", "      ⎕AN"):
        lines = [line, "x", "      1 2"]  # "1 2" may be a folded output row
        v, r, actual = _judge(cb, lines, [_lr(cb, ["x"]), _lr(cb, ["1 2"])])
        assert (v, r) == ("not-run", "suspect-input") and actual == f"{line}\nx\n      1 2\n1 2\n"
        b, c = _block(cb, *lines)
        assert cb.split_kind(c, v, r) == "ambiguous"
    # a plainly coded line that printed: the output was left out
    assert _judge(cb, ["      ⎕AN"], [_lr(cb, ["3"])])[:2] == ("not-run", "environment")
    assert _judge(cb, ["      ?6", "3"], [_lr(cb, ["4"])])[:2] == ("not-run", "volatile")


def test_judge_user_command_output_is_not_suspect(cb):
    # ]box on prints "Was OFF": a user command line cannot be folded output.
    lines = ["      ]box on", "      1+1", "2"]
    res = [_lr(cb, ["Was OFF"]), _lr(cb, ["3"])]
    assert _judge(cb, lines, res)[:2] == ("differs", None)


# --- the Docker jail -----------------------------------------------------------


@needs_docker
def test_docker_has_no_network(cb):
    # Opening a TCP socket fails at once: there is no route out.
    res, _ = _run(cb, (None, [
        "⎕SH 'bash -c \"exec 3<>/dev/tcp/1.1.1.1/53\" 2>&1; echo rc=$?'",
    ]))
    out = res[1][0].output
    assert any("Network is unreachable" in ln for ln in out), out
    assert out[-1] == "rc=1"


@needs_docker
def test_docker_cannot_write_outside_work(cb, tmp_path):
    host = tmp_path / "escaped.txt"
    res, _ = _run(cb, (None, [
        "(⊂'x') ⎕NPUT '/etc/x'",
        f"(⊂'x') ⎕NPUT '{host}'",
        "(⊂'x') ⎕NPUT '/work/ok.txt'",
        "⊃⊃⎕NGET '/work/ok.txt' 1",
    ]))
    r = res[1]
    assert r[0].error and r[0].output[0].startswith("FILE ACCESS ERROR")
    assert r[1].error
    assert not host.exists()
    assert (r[2].error, r[3].output) == (False, ["x"])


@needs_docker
def test_docker_files_persist_between_blocks_of_a_page(cb):
    res, _ = _run(
        cb,
        (None, ["(⊂'hello') ⎕NPUT 'a.txt'", "⎕MKDIR 'd'"]),
        (None, ["⊃⊃⎕NGET 'a.txt' 1", "⎕NEXISTS 'd'"]),
    )
    assert _out(res, 2) == [["hello"], ["1"]]


@needs_docker
def test_docker_nothing_persists_between_pages(cb):
    with cb.Sandbox(IMAGE) as box:
        _run(cb, (None, [
            "x←1",
            "(⊂'hello') ⎕NPUT '/work/a.txt'",
            "(⊂'x') ⎕NPUT '/tmp/b.txt'",
            "(⊂'x') ⎕NPUT '/home/dyalog/.c'",
            "⎕SH 'sleep 299.5 >/dev/null 2>&1 &'",
        ]), box=box)
        name = box.name
        res, _ = _run(cb, (None, [
            "⎕NEXISTS '/work/a.txt' '/tmp/b.txt' '/home/dyalog/.c'",
            "x",
            "⎕SH 'grep -l 29[9].5 /proc/[0-9]*/cmdline | wc -l'",
            "⎕SH 'grep -l cb-keepe[r] /proc/[0-9]*/cmdline | wc -l'",
        ]), box=box)
        assert box.name == name  # the same container
    r = res[1]
    assert r[0].output == ["0 0 0"]
    assert r[1].error  # no name from the earlier page
    assert r[2].output == ["0"]  # the background job was killed
    assert r[3].output == ["2"]  # the keeper (and docker-init) were not
    assert _containers(name) == []


@needs_docker
def test_docker_dotnet_runs_and_cannot_write_outside_work(cb):
    res, _ = _run(cb, (None, [
        "⎕USING←'System' 'System.IO'",
        "(⎕NEW DateTime (2020 1 2)).Year",
        "File.WriteAllText '/etc/x' 'hi'",
        "File.WriteAllText '/work/y' 'hi'",
        "⊃⊃⎕NGET 'y' 1",
    ]))
    r = res[1]
    assert r[1].output == ["2020"]
    assert r[2].error and r[2].output[0].startswith("EXCEPTION")
    assert (r[3].error, r[4].output) == (False, ["hi"])


@needs_docker
def test_docker_shell_runs_as_uid_1000_and_cannot_write_outside_work(cb):
    res, _ = _run(cb, (None, [
        "⎕SH 'id -u'",
        "⎕SH 'touch /etc/x 2>/dev/null || echo refused'",
        "⎕SH 'touch /work/x && echo ok'",
    ]))
    assert _out(res) == [["1000"], ["refused"], ["ok"]]


def test_main_without_docker_runs_as_no_run(cb, tmp_path, monkeypatch, capsys):
    _docs(tmp_path, {"a.md": _PAGE})
    monkeypatch.setattr(cb, "DOCKER", str(tmp_path / "no-such-docker"))
    called = []
    monkeypatch.setattr(cb, "run_page", lambda *a, **k: called.append(a))
    rc, log = _main(cb, tmp_path)
    assert rc == 0 and called == []
    assert "Docker" in capsys.readouterr().err
    meta, entries = cb.read_log(log)
    assert {e["reason"] for e in entries} == {"not-run"}
    assert meta["dyalog"] is None and meta["image"] is None


def test_main_image_option_is_passed_on(cb, tmp_path, monkeypatch):
    _docs(tmp_path, {"a.md": _PAGE})
    seen = []
    _fake_run(cb, monkeypatch, lambda bs: {})
    monkeypatch.setattr(cb, "prepare_image", lambda image: seen.append(image) or image)
    _main(cb, tmp_path, "--image", "my/image:1")
    assert seen == ["my/image:1"]


def test_default_image_is_tagged_by_the_dockerfile(cb):
    digest = hashlib.sha256(cb.DOCKERFILE.read_bytes()).hexdigest()
    assert cb.default_image() == f"code-blocks-dyalog:{digest[:12]}"
    assert cb.BASE_IMAGE in cb.DOCKERFILE.read_text(encoding="utf-8")


def test_container_argv_is_locked_down(cb):
    argv = cb._container_argv("img", "cb-x", "/host/dir")
    assert argv[:2] == [cb.DOCKER, "run"]
    for flag in (
        "-d", "--rm", "--init", "--read-only", "--network none", "--cap-drop ALL",
        "--security-opt no-new-privileges", "--pids-limit 64", "--memory 512m",
        "--cpus 1", "--name cb-x", "-w /work", "--user 1000:1000",
        "--entrypoint sh",
        "--tmpfs /work:rw,size=16m,mode=1777", "--tmpfs /tmp:rw,size=64m",
        "--tmpfs /home/dyalog:rw,size=16m,uid=1000,gid=1000",
        "-v /host/dir:/cb:ro",
    ):
        assert f" {flag} " in " " + " ".join(argv) + " ", flag
    assert argv[-5:] == ["img", "-c", cb._KEEPER, "cb-keeper", str(cb.KEEPER_STALE)]


def test_exec_argv_has_its_own_deadline(cb):
    # The interpreter's own deadline: the page's time-out plus a grace period.
    assert cb._exec_argv("cb-x", ["a", "b"], timeout=60) == [
        cb.DOCKER, "exec", "-w", "/work", "cb-x",
        "timeout", "--signal=KILL", "75s", "dyalogscript", "a", "b"]
    assert cb._exec_argv("n", [], timeout=2.5)[-3:] == [
        "--signal=KILL", "18s", "dyalogscript"]


@needs_docker
def test_run_page_exception_leaves_no_container(cb, monkeypatch):
    real = subprocess.Popen

    class Boom(real):
        def communicate(self, *a, **k):
            if self.args[1:2] == ["exec"]:
                time.sleep(2)  # the interpreter is up
                raise RuntimeError("interrupted")
            return super().communicate(*a, **k)

    monkeypatch.setattr(cb.subprocess, "Popen", Boom)
    box = cb.Sandbox(IMAGE)
    with pytest.raises(RuntimeError, match="interrupted"):
        with box:
            _run(cb, (None, ["⎕DL 1000"]), box=box)
    monkeypatch.setattr(cb.subprocess, "Popen", real)
    assert box.name is None and box._dir is None
    assert _containers() == []


@needs_docker
def test_run_page_save_breaks_later_lines(cb):
    # Why ⎕SAVE stays blocked: after it, every line's execute fails with
    # error 85 (taken as "no result"), so nothing more shows, errors included.
    res, stop = _run(cb, (None, ["1+1", "⎕SAVE 'ws'", "1+1", "1÷0"]))
    assert stop is None
    assert _out(res) == [["2"], [], [], []]
    assert not res[1][3].error


@needs_docker
def test_run_page_workspace_script_and_native_code_examples(cb):
    res, _ = _run(cb, (None, [
        "⎕CY 'dfns'", "⎕NC 'cmpx'",
        "⎕FIX ':Namespace N' 'f←{⍵+1}' ':EndNamespace'", "N.f 1",
        "⎕NA 'I4 libc.so.6|getpid'", "0<getpid",
    ]))
    assert _out(res) == [[], ["3"], [], ["2"], [], ["1"]]


def test_carry_decisions_counts_carried_decisions_that_now_differ(cb):
    def e(verdict, decision):
        return {"page": "p", "sha256": "s", "proposed": "x\n", "split": "unambiguous",
                "verdict": verdict, "decision": decision}
    stats = cb.Counter()
    old = [e("match", "apply"), e("match", "pending"), e("differs", "skip")]
    new = [e("differs", "pending"), e("not-run", "pending"), e("differs", "pending")]
    out = cb.carry_decisions(old, new, stats)
    assert [x["decision"] for x in out] == ["apply", "pending", "skip"]
    assert stats["now_differs"] == 1


def test_main_reports_carried_decisions_that_now_differ(cb, tmp_path, monkeypatch, capsys):
    _docs(tmp_path, {"a.md": _PAGE})
    _fake_run(cb, monkeypatch, lambda bs: {b.id: [_lres(cb, ["9"])] for b in bs})
    _, log = _main(cb, tmp_path, "--no-run")
    meta, entries = cb.read_log(log)
    for x in entries:
        x["verdict"], x["decision"] = "match", "apply"
    cb.write_log(log, meta, entries)
    capsys.readouterr()
    _main(cb, tmp_path)
    out = capsys.readouterr().out
    assert "carried, now differs: 2" in out


@needs_docker
def test_a_sandbox_whose_container_died_starts_one_new_container(cb):
    with cb.Sandbox(IMAGE) as box:
        _run(cb, (None, ["1+1"]), box=box)
        first = box.name
        assert first is not None
        subprocess.run(["docker", "rm", "-f", first], capture_output=True)
        res, _ = _run(cb, (None, ["1+1"]), box=box)
        assert _out(res) == [["2"]]
        assert box.name not in (None, first)
        assert set(_containers()) == set(_containers(box.name))
    assert _containers() == []


def test_sweep_removes_only_stale_dirs(cb, tmp_path, monkeypatch):
    monkeypatch.setattr(cb.tempfile, "gettempdir", lambda: str(tmp_path))
    old, new = tmp_path / "code-blocks-old", tmp_path / "code-blocks-new"
    for d in (old, new):
        d.mkdir()
        (d / "alive").touch()
    stale = time.time() - 5 * cb.KEEPER_STALE
    os.utime(old / "alive", (stale, stale))
    cb._sweep_stale_dirs()
    assert not old.exists() and new.exists()


@needs_docker
def test_main_runs_every_page_in_one_container_and_removes_it(cb, tmp_path, monkeypatch):
    _docs(tmp_path, {"a.md": _PAGE, "b.md": _PAGE, "c.md": _PAGE})
    runs = []
    real = cb._container_argv

    def spy(image, name, mount):
        runs.append(name)
        return real(image, name, mount)

    monkeypatch.setattr(cb, "_container_argv", spy)
    rc, _ = _main(cb, tmp_path)
    assert rc == 0
    assert len(runs) == 1
    assert _containers() == []
    assert not list(Path(tempfile.gettempdir()).glob("code-blocks-*"))


@needs_docker
def test_main_end_to_end_dotnet_file_and_shell_blocks_match(cb, tmp_path):
    md = (
        "```apl\n      ⎕USING←'System'\n      (⎕NEW DateTime (2020 1 2)).Year\n2020\n```\n\n"
        "```apl\n      (⊂'hi') ⎕NPUT 'f.txt'\n      ⊃⊃⎕NGET 'f.txt' 1\nhi\n```\n\n"
        "```apl\n      ⎕SH 'id -u'\n1000\n```\n"
    )
    _docs(tmp_path, {"a.md": md})
    rc, log = _main(cb, tmp_path)
    assert rc == 0
    _, entries = cb.read_log(log)
    assert [(e["verdict"], e["reason"]) for e in entries] == [("match", None)] * 3


# --- review fixes: signals, own deadline, cleanup, platform ------------------


@needs_docker
def test_killed_tool_leaves_no_container_for_long(cb, tmp_path):
    # SIGKILL: Python cannot clean up; the keeper sees the heartbeat stop.
    _docs(tmp_path, {"a.md": "```apl\n      ⎕DL 1000\n```\n"})
    before = set(_containers())
    proc = subprocess.Popen(
        [sys.executable, str(Path(cb.__file__)), "--docs", str(tmp_path / "docs"),
         "--guide", "g", "--log", str(tmp_path / "log.yml"), "--timeout", "300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    started: set = set()
    dirs = set(Path(tempfile.gettempdir()).glob("code-blocks-*"))
    try:
        deadline = time.monotonic() + 60
        while not started and time.monotonic() < deadline:
            time.sleep(0.2)
            started = set(_containers()) - before
        assert started
        proc.kill()
        proc.wait()
        deadline = time.monotonic() + cb.KEEPER_STALE + 30
        while set(_containers()) & started and time.monotonic() < deadline:
            time.sleep(1)
        assert set(_containers()) & started == set()
    finally:
        proc.kill()
        for c in started:
            subprocess.run(["docker", "rm", "-f", c], capture_output=True)
        for d in set(Path(tempfile.gettempdir()).glob("code-blocks-*")) - dirs:
            _shutil.rmtree(d, ignore_errors=True)  # the killed run's /cb


@needs_docker
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_signal_to_the_tool_leaves_no_container(cb, tmp_path, sig):
    _docs(tmp_path, {"a.md": "```apl\n      ⎕DL 1000\n```\n"})
    before = set(_containers())
    proc = subprocess.Popen(
        [sys.executable, str(Path(cb.__file__)), "--docs", str(tmp_path / "docs"),
         "--guide", "g", "--log", str(tmp_path / "log.yml"), "--timeout", "300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while not (set(_containers()) - before) and time.monotonic() < deadline:
            time.sleep(0.2)
        started = set(_containers()) - before
        assert started
        time.sleep(1)
        proc.send_signal(sig)
        proc.wait(timeout=60)
        left = set(_containers()) & started
        assert left == set()
    finally:
        proc.kill()
        for c in set(_containers()) - before:
            subprocess.run(["docker", "rm", "-f", c], capture_output=True)


def test_left_counts_a_failing_docker_ps_as_still_there(cb, monkeypatch):
    fail = subprocess.CompletedProcess([], 1, "", "daemon gone")
    monkeypatch.setattr(cb, "_docker", lambda *a, **k: fail)
    monkeypatch.setattr(cb.time, "sleep", lambda s: None)
    assert cb._left("cb-x") is True
    with pytest.raises(cb.ContainerError):
        cb._remove_container("cb-x")


@pytest.mark.parametrize(
    "line",
    [
        "⎕SH 'ls'", "⎕CMD 'dir'", "⎕SHELL 'ls'", "'f' ⎕NA 'I4 x|y'",
        "⎕USING←'System'", "x←⎕NEW DateTime", "DateTime.⎕NL -2",
        "FormBorderStyle.({⍵}⎕NL -2)", "⎕NINFO 'x'", "(⊂'a') ⎕NPUT 'f'",
        "'a' ⎕NAPPEND 1", "'a' ⎕NREPLACE 1 0", "⎕NGET 'c:\\temp\\x.txt'",
        "⎕NEXISTS '\\\\server\\share'",
    ],
)
def test_judge_platform_reason_for_differs(cb, line):
    v = _judge(cb, ["      " + line, "x"], [_lr(cb, ["y"])])
    assert v[:2] == ("differs", "platform")


def test_judge_platform_reason_precedence_and_scope(cb):
    lines = ["      ⎕SH 'ls' ⋄ 1", "x"]
    # after-unrun first, then platform, then diamond.
    assert _judge(cb, lines, [_lr(cb, ["y"])], prior_unrun=True)[:2] == (
        "differs", "after-unrun")
    assert _judge(cb, lines, [_lr(cb, ["y"])])[:2] == ("differs", "platform")
    # Only differs gets it: a match stays a plain match.
    assert _judge(cb, ["      ⎕SH 'ls'", "x"], [_lr(cb, ["x"])]) == ("match", None, None)
    # ⎕NL on its own, ⎕NGET with a POSIX path: not platform.
    for line in ("⎕NL -2", "⎕NGET 'a/b.txt'"):
        assert _judge(cb, ["      " + line, "x"], [_lr(cb, ["y"])])[:2] == ("differs", None)


@pytest.mark.parametrize(
    "line",
    ["'f' (⎕NCREATE⍠'Unique' 1) 0", "'f'(⎕NCREATE⍠('unique' 1))0", "⎕ncreate⍠'UNIQUE' 1"],
)
def test_ncreate_unique_is_volatile(cb, line):
    assert cb._is_volatile(line)


def test_ncreate_without_unique_is_not_volatile(cb):
    assert not cb._is_volatile("'f' ⎕NCREATE 0")


def test_dockerfile_pins_dotnet_and_checks_the_installer(cb):
    text = cb.DOCKERFILE.read_text(encoding="utf-8")
    assert "--version 10.0.15" in text and "--channel" not in text
    assert re.search(r"DOTNET_INSTALL_SHA256=[0-9a-f]{64}\b", text)
    assert "sha256sum -c" in text


# --- multi-line units in the harness ---------------------------------------


@needs_docker
def test_run_page_multi_line_unit_runs_as_source(cb):
    unit = ["M←[10 20 30 40", "   50 60 70 80]"]
    res, stop = _run(cb, (None, ["s←5", unit, "M", "⍴M", "s"]))
    assert stop is None
    assert _out(res) == [[], [], ["10 20 30 40", "50 60 70 80"], ["2 4"], ["5"]]
    assert [r.error for r in res[1]] == [False] * 5


@needs_docker
def test_run_page_multi_line_unit_error_does_not_abort(cb):
    res, stop = _run(
        cb,
        (None, [["X←[1 2", "   3 4"], "1+1", ["Y←{", "⍵+1"], "2+2", ["Z←1", "÷0"], "Z"]),
    )
    assert stop is None
    assert [r.error for r in res[1]] == [True, False, True, False, True, False]
    assert res[1][0].output[0].startswith("SYNTAX ERROR")
    assert res[1][4].output[0].startswith("DOMAIN ERROR")
    assert _out(res)[1] == ["2"] and _out(res)[3] == ["4"] and _out(res)[5] == ["1"]


@needs_docker
def test_run_page_multi_line_unit_leaves_no_names_and_keeps_diamonds_in_strings(cb):
    unit = ["C←['a⋄b'", "   'cd']"]
    res, _ = _run(cb, (None, [unit, "C", "⍴C", "≢⎕NL ¯2 ¯3 ¯4 ¯9"]))
    assert _out(res) == [[], ["a⋄b", "cd "], ["2 3"], ["1"]]


@needs_docker
def test_run_page_multi_line_unit_skips_system_commands_and_quote_quad(cb):
    res, _ = _run(cb, (None, [["A←1", ")CLEAR"], ["B←⍞", "B"], "1"]))
    assert [r.skipped for r in res[1]] == ["system-command", "quote-quad", None]


# --- --define-shown: candidates and array notation ---------------------------


@pytest.mark.parametrize(
    "rows, oneline, lines",
    [
        (["1 2  3"], "V←1 2 3", ["V←1 2 3"]),
        (["42"], "V←42", ["V←42"]),
        (["¯1.5 2E10 3J¯4 1.5E¯7 ¯2J0.5"], "V←¯1.5 2E10 3J¯4 1.5E¯7 ¯2J0.5",
         ["V←¯1.5 2E10 3J¯4 1.5E¯7 ¯2J0.5"]),
        (["10 20 30 40", "50 60 70 80"], "V←[10 20 30 40 ⋄ 50 60 70 80]",
         ["V←[10 20 30 40", "   50 60 70 80]"]),
        ([" 1  2", "¯3 40"], "V←[ 1  2 ⋄ ¯3 40]", ["V←[ 1  2", "   ¯3 40]"]),
        (["1 2  3  4", "5 6  7  8", "9 10 11 12"], "V←[1 2  3  4 ⋄ 5 6  7  8 ⋄ 9 10 11 12]",
         ["V←[1 2  3  4", "   5 6  7  8", "   9 10 11 12]"]),
        (["It's here"], "V←'It''s here'", ["V←'It''s here'"]),
        (["abc", "de   ", "f"], "V←['abc' ⋄ 'de' ⋄ 'f']", ["V←['abc'", "   'de'", "   'f']"]),
        (["123", "ab"], "V←['123' ⋄ 'ab']", ["V←['123'", "   'ab']"]),
    ],
)
def test_aplan(cb, rows, oneline, lines):
    assert cb.aplan("V", rows) == (oneline, lines)


def test_aplan_aligns_under_the_first_element_for_long_names(cb):
    assert cb.aplan("mat", ["1 2", "3 4"])[1] == ["mat←[1 2", "     3 4]"]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [""],
        ["┌→──┐", "│abc│", "└───┘"],
        ["1 2", "", "3 4"],
        ["abc 1 2"],
        ["1 2", "x 3"],
    ],
)
def test_aplan_unsupported(cb, rows):
    with pytest.raises(cb.Unsupported):
        cb.aplan("V", rows)


def _ve(cb, name):
    return cb.LineResult(
        [f"VALUE ERROR: Undefined name: {name}", f"      {name}", "      ∧"], True, None
    )


def _cands(cb, md, results):
    blocks = cb.find_blocks("a.md", md)
    classes = {i: cb.classify(b) for i, b in enumerate(blocks) if not b.nested}
    return blocks, cb.shown_candidates(blocks, classes, results)


def test_shown_candidates_bare_name_with_value_error(cb):
    md = "```apl\n      M \n1 2\n3 4\n      M[1;1]\n1\n```\n"
    _, found = _cands(cb, md, {0: [_ve(cb, "M"), _ve(cb, "M")]})
    (s,) = found[0]
    assert (s.block, s.line, s.name) == (0, 0, "M")
    assert s.oneline == "M←[1 2 ⋄ 3 4]" and s.lines == ("M←[1 2", "   3 4]")
    assert s.unsupported is None


def test_shown_candidates_needs_value_error_for_that_name(cb):
    md = "```apl\n      M\n1 2\n```\n"
    assert _cands(cb, md, {0: [_lres(cb, ["1 2"])]})[1] == {}
    assert _cands(cb, md, {0: [_ve(cb, "N")]})[1] == {}
    assert _cands(cb, md, {})[1] == {}


def test_shown_candidates_not_if_assigned_earlier_on_the_page(cb):
    for first in ("      M←⍳3\n", "      ∇R←M\n", "      M ← 1\n", "M←1\n"):
        md = f"```apl\n{first}```\n\n```apl\n      M\n1 2\n```\n"
        _, found = _cands(cb, md, {1: [_ve(cb, "M")]})
        assert found == {}, first


def test_shown_candidates_only_the_first_bare_occurrence(cb):
    md = "```apl\n      M\n1 2\n      M\n1 2\n```\n"
    _, found = _cands(cb, md, {0: [_ve(cb, "M"), _ve(cb, "M")]})
    assert [s.line for s in found[0]] == [0]


def test_shown_candidates_not_for_expressions_or_shown_errors(cb):
    md = "```apl\n      M+1\n2\n      M\nVALUE ERROR: Undefined name: M\n      M\n      ∧\n```\n"
    _, found = _cands(cb, md, {0: [_ve(cb, "M"), _ve(cb, "M")]})
    assert found == {}


def test_shown_candidates_boxed_output_is_unsupported(cb):
    md = "```apl\n      M\n┌─┬─┐\n│1│2│\n└─┴─┘\n```\n"
    _, found = _cands(cb, md, {0: [_ve(cb, "M")]})
    (s,) = found[0]
    assert s.unsupported and s.lines == () and s.oneline is None


def test_main_define_shown_logs_unsupported_only_with_the_option(cb, tmp_path, monkeypatch):
    md = "```apl\n      M\n┌─┬─┐\n│1│2│\n└─┴─┘\n```\n"
    _docs(tmp_path, {"a.md": md})
    calls = _fake_run(cb, monkeypatch, lambda bs: {b.id: [_ve(cb, "M")] for b in bs})
    _, log = _main(cb, tmp_path)
    _, (e,) = cb.read_log(log)
    assert "rewrites" not in e and "rewrite_rejected" not in e
    _, log = _main(cb, tmp_path, "--define-shown")
    _, (e,) = cb.read_log(log)
    assert e["rewrite_rejected"]["M"].startswith("define-shown: unsupported")
    assert e.get("rewrites") is None
    assert len(calls) == 2  # one run each: nothing to prove


# --- --define-shown in Docker -------------------------------------------------

_INDEXING = (
    "```apl\n"
    "      M\n10 20 30 40\n50 60 70 80\n \n"
    "      M[⊂1 2]\n20\n \n"
    "      M[2 2⍴⊂2 4]\n80 80\n80 80\n \n"
    "      M[(2 1)(1 2)]\n50 20\n"
    "```\n"
)


@needs_docker
def test_define_shown_indexing_example(cb, tmp_path, capsys):
    _docs(tmp_path, {"a.md": _INDEXING + "\n```apl\n      M[1;]\n10 20 30 40\n```\n"})
    _, log = _main(cb, tmp_path, "--define-shown")
    _, (e, later) = cb.read_log(log)
    assert (e["verdict"], e["split"], e["rewrites"], e["defines"]) == (
        "match", "confirmed", ["aplan"], ["M"])
    assert e["original"] == _INDEXING
    assert e["proposed"] == (
        "```apl\nM←[10 20 30 40\n   50 60 70 80]\nM[⊂1 2]\n```\n\n"
        "```text\n20\n```\n\n"
        "```apl\nM[2 2⍴⊂2 4]\n```\n\n```text\n80 80\n80 80\n```\n\n"
        "```apl\nM[(2 1)(1 2)]\n```\n\n```text\n50 20\n```\n"
    )
    assert later["verdict"] == "match"  # runs because M is now defined
    assert "define-shown: 1 rewrites accepted, 0 rejected" in capsys.readouterr().out
    assert _containers() == []


@needs_docker
def test_define_shown_without_the_option_leaves_entries_alone(cb, tmp_path):
    _docs(tmp_path, {"a.md": _INDEXING})
    _, log = _main(cb, tmp_path)
    _, (e,) = cb.read_log(log)
    assert e["verdict"] == "not-run" and "rewrites" not in e


@needs_docker
def test_define_shown_wrong_guess_is_rejected(cb, tmp_path, capsys):
    # Printed like a number, but ⍴ shows it is a character vector.
    md = "```apl\n      M\n123\n      ⍴M\n3\n```\n"
    _docs(tmp_path, {"a.md": md})
    _, log = _main(cb, tmp_path, "--define-shown")
    _, (e,) = cb.read_log(log)
    assert e.get("rewrites") is None
    assert "differs" in e["rewrite_rejected"]["M"]
    assert e["verdict"] == "not-run" and e["original"] == md
    assert e["proposed"] == cb.split_block(*(lambda b: (b, cb.classify(b)))(
        cb.find_blocks("a.md", md)[0]))
    assert "define-shown: 0 rewrites accepted, 1 rejected" in capsys.readouterr().out


@needs_docker
def test_define_shown_char_matrix_with_short_rows_and_diamonds(cb, tmp_path):
    md = "```apl\n      C\na⋄b\ncd\n      ⍴C\n2 3\n```\n"
    _docs(tmp_path, {"a.md": md})
    _, log = _main(cb, tmp_path, "--define-shown")
    _, (e,) = cb.read_log(log)
    assert e["rewrites"] == ["aplan"] and e["verdict"] == "match"
    assert e["proposed"].startswith("```apl\nC←['a⋄b'\n   'cd']\n⍴C\n```\n")


def test_shown_candidates_annotation_in_the_output_is_unsupported(cb):
    # The rewrite drops the name's output; a ⍝ note there would be lost.
    md = "```apl\n      M\n1 2\n⍝ a note\n      M+1\n2 3\n```\n"
    _, found = _cands(cb, md, {0: [_ve(cb, "M"), _ve(cb, "M")]})
    (s,) = found[0]
    assert s.unsupported and "annotation" in s.unsupported


# --- review fixes -------------------------------------------------------------


@needs_docker
def test_run_page_multi_line_unit_displays_only_explicit_output(cb):
    # A unit runs as a function body: values it does not assign or send to ⎕
    # are not displayed.
    res, stop = _run(cb, (None, [["X←1", "2+2", "⎕←X+4"], "X"]))
    assert stop is None
    assert _out(res) == [["5"], ["1"]]


@needs_docker
def test_run_page_trap_assignment_does_not_end_the_run(cb):
    lines = ["⎕TRAP←11 'E' '⎕←''trapped'''", "1+1", "÷0", "⎕TRAP←0 'S'", "2+2"]
    res, stop = _run(cb, (None, lines))
    assert stop is None
    assert len(res[1]) == 5
    assert _out(res)[1] == ["2"] and _out(res)[4] == ["4"]
    # The harness's own trapping still shows the error.
    assert res[1][2].error and res[1][2].output[0].startswith("DOMAIN ERROR")


@needs_docker
def test_run_page_trap_assignment_forms(cb):
    # A modified assignment does not end the run either; an assignment
    # inside a larger statement still displays the statement's value.
    lines = ["⎕TRAP⍴⍨←0", "≡⎕TRAP←0 'C' '''Eh?'''", "1+1"]
    res, stop = _run(cb, (None, lines))
    assert stop is None
    assert _out(res) == [[], ["¯2"], ["2"]]


def test_shown_candidates_output_mentioning_the_name_is_unsupported(cb):
    # symbols/down-shoe.md: an input line at five spaces reads as output.
    md = (
        "```apl\n      mat\nflywheel\nshyster \nflywheel\n     ∪mat\n"
        "flywheel\nshyster \n```\n"
    )
    _, found = _cands(cb, md, {0: [_ve(cb, "mat")]})
    (s,) = found[0]
    assert s.unsupported and "mentions" in s.unsupported


def test_shown_candidates_ambiguous_block_is_unsupported(cb):
    md = "```apl\n      M\n1 2\n       x\n      M+1\n2 3\n```\n"
    blocks, found = _cands(cb, md, {0: [_ve(cb, "M"), _ve(cb, "M")]})
    assert cb.classify(blocks[0]).kind == "ambiguous"
    (s,) = found[0]
    assert s.unsupported and "ambiguous" in s.unsupported


def _shown_run(cb, monkeypatch, value, uses):
    """Fake runs where `v` is undefined until a unit assigns it; `value`
    is its display, `uses` maps other lines to their results."""

    def results(bs):
        defined = False
        out = {}
        for b in bs:
            res = []
            for u in b.lines:
                if not isinstance(u, str) or u.startswith("v←"):
                    defined = True
                    res.append(_lres(cb, []))
                elif u == "v":
                    res.append(_lres(cb, value) if defined else _ve(cb, "v"))
                elif defined:
                    res.append(uses[u])
                else:
                    res.append(_ve(cb, "v"))
            out[b.id] = res
        return out

    return _fake_run(cb, monkeypatch, results)


_UNRUN = "```apl\n      ⎕SE.UCMD 'x'\n```\n\n"  # a block that is never run


def test_define_shown_suspect_input_block_is_unsupported(cb, tmp_path, monkeypatch):
    md = "```apl\n      v\n1 2\n```\n\n```apl\n      v+1\n2 3\n```\n"
    _docs(tmp_path, {"a.md": md})
    calls = _shown_run(cb, monkeypatch, ["1 2"], {"v+1": _lres(cb, ["2 3"])})
    _, log = _main(cb, tmp_path, "--define-shown")
    _, entries = cb.read_log(log)
    why = entries[0]["rewrite_rejected"]["v"]
    assert why.startswith("define-shown: unsupported") and "suspect-input" in why
    assert len(calls) == 1


def test_define_shown_later_use_that_errs_rejects(cb, tmp_path, monkeypatch):
    # system-functions/r.md: the value reads as a character matrix, but the
    # page uses it as a vector of strings; later uses fail.
    md = _UNRUN + (
        "```apl\n      v\nTo be\nOr not\n```\n\n"
        "```apl\n      ('[aeiou]' ⎕R 'X') v\nTX bX\nXr nXt\n```\n"
    )
    _docs(tmp_path, {"a.md": md})
    err = cb.LineResult(["DOMAIN ERROR", "      x", "      ∧"], True, None)
    _shown_run(cb, monkeypatch, ["To be", "Or not"], {"('[aeiou]' ⎕R 'X') v": err})
    _, log = _main(cb, tmp_path, "--define-shown")
    _, entries = cb.read_log(log)
    e = entries[1]
    assert e.get("rewrites") is None
    assert "later use" in e["rewrite_rejected"]["v"]
    assert "DOMAIN ERROR" in e["rewrite_rejected"]["v"]


def test_define_shown_later_use_with_shown_error_is_fine(cb, tmp_path, monkeypatch):
    md = _UNRUN + (
        "```apl\n      v\n1 2\n```\n\n"
        "```apl\n      v[3]\nINDEX ERROR\n      v[3]\n       ∧\n```\n"
    )
    _docs(tmp_path, {"a.md": md})
    err = cb.LineResult(["INDEX ERROR", "      v[3]", "       ∧"], True, None)
    _shown_run(cb, monkeypatch, ["1 2"], {"v[3]": err})
    _, log = _main(cb, tmp_path, "--define-shown")
    _, entries = cb.read_log(log)
    assert entries[1]["rewrites"] == ["aplan"] and entries[2]["verdict"] == "match"


def test_define_shown_unused_value_is_rejected(cb, tmp_path, monkeypatch):
    md = _UNRUN + "```apl\n      v\n1 2\n```\n"
    _docs(tmp_path, {"a.md": md})
    _shown_run(cb, monkeypatch, ["1 2"], {})
    _, log = _main(cb, tmp_path, "--define-shown")
    _, entries = cb.read_log(log)
    assert entries[1].get("rewrites") is None
    assert "nothing" in entries[1]["rewrite_rejected"]["v"]


def test_define_shown_without_docker_says_so(cb, tmp_path, capsys):
    _docs(tmp_path, {"a.md": _PAGE})
    _main(cb, tmp_path, "--no-run", "--define-shown")
    assert "--define-shown" in capsys.readouterr().err


# --- normalising input: shy results, line numbers -----------------------------


def _norm(cb, *lines):
    md = "```apl\n" + "\n".join(lines) + "\n```\n"
    (b,) = cb.find_blocks("a.md", md)
    return cb.normalise(b)


@pytest.mark.parametrize(
    "line, want",
    [
        ("      +A←⍳5", "      ⎕←A←⍳5"),
        ("      ⊢A←2 4⍴'MAINEXIT'", "      ⎕←A←2 4⍴'MAINEXIT'"),
        ("      ⊣ x←1", "      ⎕←x←1"),
        ("      ⊢ A←5 5⍴0", "      ⎕←A←5 5⍴0"),
        ("      +A[2 4]+←1", "      ⎕←A[2 4]+←1"),
        ("      +(a b)←1 2", "      ⎕←(a b)←1 2"),
        ("      +B←⍕A←2 6⍴'HELLO'", "      ⎕←B←⍕A←2 6⍴'HELLO'"),
        ("      ⊢mat← myJSON JSON", "      ⎕←mat← myJSON JSON"),
    ],
)
def test_normalise_shy_display(cb, line, want):
    n = _norm(cb, line, "x")
    assert n.block.body[0] == want
    assert n.rewrites == ("shy",)
    assert n.plus == ((0,) if line.lstrip().startswith("+") else ())


@pytest.mark.parametrize(
    "line",
    [
        "      +⎕EX'A'",  # not an assignment
        "      +/A←1 2",  # not monadic + on the assignment
        "      +A",
        "      x←+A←1",  # not at the start
        "      ⊢⎕IO←0",  # a system name
        "      +'A'←1",
        "+A←⍳5",  # output, not input
        "       +A←⍳5",  # seven spaces
    ],
)
def test_normalise_leaves_other_lines(cb, line):
    n = _norm(cb, line, "x")
    assert n.rewrites == () and n.block.body[0] == line


def test_normalise_unnumbers_a_typed_definition(cb):
    n = _norm(
        cb,
        "     ∇ R←A DIVIDE B;⎕TRAP",
        "[1]   ⍝ Divide",
        "[2]    ⎕TRAP←11 'E' '→ERR'",
        "[3]    :If A>0",
        "[4]        R←A÷B ⋄ →0",
        "[5]    :EndIf",
        "[6]   ERR:'DIVISION ERROR' ⎕SIGNAL 11",
        "     ∇",
        "      2 DIVIDE 1",
        "2",
    )
    assert n.rewrites == ("unnumbered",) and n.keep is None
    assert n.block.body == [
        "      ∇ R←A DIVIDE B;⎕TRAP",
        "        ⍝ Divide",
        "        ⎕TRAP←11 'E' '→ERR'",
        "        :If A>0",
        "            R←A÷B ⋄ →0",
        "        :EndIf",
        "      ERR:'DIVISION ERROR' ⎕SIGNAL 11",
        "      ∇",
        "      2 DIVIDE 1",
        "2",
    ]
    assert n.block.text == _norm(cb, "x").block.text.replace("x", "\n".join([
        "     ∇ R←A DIVIDE B;⎕TRAP", "[1]   ⍝ Divide", "[2]    ⎕TRAP←11 'E' '→ERR'",
        "[3]    :If A>0", "[4]        R←A÷B ⋄ →0", "[5]    :EndIf",
        "[6]   ERR:'DIVISION ERROR' ⎕SIGNAL 11", "     ∇", "      2 DIVIDE 1", "2",
    ]))  # text and sha256 stay the original's


def test_normalise_numbered_closing_two_digit_numbers_and_no_space_header(cb):
    lines = ["      ∇R←MEAN X"] + [f"[{k}]  R←{k}" if k < 10 else f"[{k}] R←{k}" for k in range(1, 11)]
    n = _norm(cb, *lines, "[11] ∇")
    assert n.block.body[0] == "      ∇R←MEAN X"
    assert n.block.body[1:11] == [f"        R←{k}" for k in range(1, 11)]
    assert n.block.body[11] == "      ∇"


@pytest.mark.parametrize(
    "lines",
    [
        # output of ⎕VR (any input line right before it), and a second listing
        ["      ⎕VR'F'", "     ∇ F", "[1]    1", "     ∇"],
        ["       F", "      ∇ R←FOO", "[1]     R←10", "      ∇"],
        ["      ⎕∘←∘⎕VR¨↓⎕NL 3", "     ∇ F", "[1]    1", "     ∇", "     ∇ G", "[1]    2", "     ∇"],
        # a fragment, numbers out of order, an empty numbered line, unclosed
        ["[5]   →NEXT⍴⍨TEST", "[6]  NEXT:"],
        ["     ∇ F", "[1]    1", "[3]    3", "     ∇"],
        ["     ∇ F", "[1]    1", "[2]", "     ∇"],
        ["     ∇ F", "[1]    1"],
    ],
)
def test_normalise_leaves_listings_and_odd_definitions(cb, lines):
    n = _norm(cb, *lines)
    assert n.rewrites == () and n.keep is None and n.block.body == lines


@pytest.mark.parametrize(
    "lines",
    [
        ["     ∇ TEST", "[1]    1", "[2]    →4", "[3]    3", "[4]    4", "     ∇"],
        ["     ∇ R←F", "[1]    R←1", "[2]    →(R>0)/3", "     ∇"],
        ["      ∇ F", "[1]    'F[1]'", "      ∇"],
        ["     ∇ F", "[1]   1", "     ∇", "      2 ⎕STOP'F'", "      F", "1"],
    ],
)
def test_normalise_keeps_numbers_the_text_refers_to(cb, lines):
    n = _norm(cb, *lines)
    assert n.rewrites == () and n.keep == "line-numbers" and n.block.body == lines


def test_normalise_branch_to_zero_and_labels_are_not_line_numbers(cb):
    n = _norm(cb, "     ∇ F", "[1]    →0", "[2]    →END", "[3]   END:", "     ∇")
    assert n.rewrites == ("unnumbered",) and n.keep is None


@pytest.mark.parametrize(
    "header, name",
    [("R←A DIVIDE B;⎕TRAP", "DIVIDE"), ("TEST", "TEST"), ("foo n;⎕TRAP", "foo"),
     ("R←CONDITION (FN1 ELSE FN2) X", "ELSE"), ("r←{left}foo right", "foo"),
     ("R←(F OP) X", "OP")],
)
def test_function_name(cb, header, name):
    assert cb._function_name(header) == name


def test_classify_typed_definition_is_one_runnable_unit(cb):
    b, c = _block(
        cb, "      ∇ R←F X", "        R←X+1", "      L:", "      ∇", "      F 1", "2"
    )
    assert c.roles == ["input", "cont", "cont", "cont", "input", "output"]
    assert c.runnable and c.kind == "session" and c.reason is None
    assert cb._input_texts(b, c) == ["∇ R←F X\n  R←X+1\nL:\n∇", "F 1"]
    assert cb.expected_outputs(b, c) == [[], ["2"]]
    assert cb.split_block(b, c) == (
        "```apl\n∇ R←F X\n  R←X+1\nL:\n∇\nF 1\n```\n\n```text\n2\n```\n"
    )


def test_classify_numbered_or_unclosed_definitions_stay_del_definition(cb):
    for lines in (
        ["      ∇ F", "[1]    1", "      ∇"],
        ["      ∇ F", "        1"],
        ["      ∇ F", "        1", "", "      ∇"],
    ):
        _, c = _block(cb, *lines)
        assert (c.reason, c.runnable) == ("del-definition", False), lines


def test_main_no_run_keeps_plus_and_rewrites_identity(cb, tmp_path):
    _docs(tmp_path, {"a.md": "```apl\n      +A←⍳5\n1 2 3 4 5\n      ⊢B←1\n1\n```\n"})
    _, log = _main(cb, tmp_path, "--no-run")
    _, (e,) = cb.read_log(log)
    assert e["rewrites"] == ["shy"]
    assert "+A←⍳5" in e["proposed"] and "⎕←B←1" in e["proposed"]
    assert e["rewrite_rejected"] == {"+A←⍳5": "shy: + kept: not run"}
    assert e["original"].count("⊢B←1") == 1


def test_main_line_numbers_reason(cb, tmp_path):
    md = "```apl\n     ∇ T\n[1]    →2\n[2]    1\n     ∇\n      T\n1\n```\n"
    _docs(tmp_path, {"a.md": md})
    _, log = _main(cb, tmp_path, "--no-run")
    _, (e,) = cb.read_log(log)
    assert (e["verdict"], e["reason"], e["split"]) == ("not-run", "line-numbers", "ambiguous")
    assert "rewrites" not in e and e["proposed"] is None


@needs_docker
def test_run_page_typed_definition_is_fixed_silently(cb):
    res, stop = _run(cb, (None, [
        ["∇ R←F X", "  R←X+1", "∇"], "F 1", ["∇ 1 2", "  x", "∇"], "⎕NC'G'",
    ]))
    assert stop is None
    assert _out(res) == [[], ["2"], ["DEFN ERROR"], ["0"]]
    assert [r.error for r in res[1]] == [False, False, True, False]


@needs_docker
def test_main_shy_and_unnumbered_end_to_end(cb, tmp_path):
    md = (
        "```apl\n      +A←⍳5\n1 2 3 4 5\n      ⊢B←2 2⍴⍳4\n1 2\n3 4\n```\n\n"
        "```apl\n      +C←1J2\n1J¯2\n```\n\n"
        "```apl\n     ∇R←MEAN X\n[1]  R←(+/X)÷⍴X\n[2]  ∇\n      +F←MEAN 2 4\n3\n"
        "      MEAN 1 2 3\n2\n```\n"
    )
    _docs(tmp_path, {"a.md": md})
    _, log = _main(cb, tmp_path)
    _, (a, c, f) = cb.read_log(log)
    assert (a["verdict"], a["split"], a["rewrites"]) == ("match", "confirmed", ["shy"])
    assert "```apl\n⎕←A←⍳5\n```" in a["proposed"] and "⎕←B←2 2⍴⍳4" in a["proposed"]
    # conjugate shows 1J¯2; ⎕← would not: the + stays and the block matches
    assert (c["verdict"], c.get("rewrites")) == ("match", None)
    assert c["rewrite_rejected"]["+C←1J2"] == "shy: + kept: conjugate changes the value"
    assert "+C←1J2" in c["proposed"]
    assert (f["verdict"], f["rewrites"]) == ("match", ["unnumbered", "shy"])
    assert f["proposed"].startswith("```apl\n∇R←MEAN X\n  R←(+/X)÷⍴X\n∇\n⎕←F←MEAN 2 4\n```")


def test_classify_style_ignores_display_command_output(cb):
    _, c = _block(cb, "      ]Display 1 2", "┌→──┐", "│1 2│", "└~──┘", "      ⍴1 2", "2")
    assert c.style == "off"
    _, c = _block(cb, "      ]disp ⊂1 2", "┌→──┐", "│1 2│", "└~──┘", "      ⊂1 2", "┌→──┐",
                  "│1 2│", "└~──┘")
    assert c.style == "max"


def test_plus_checks_only_for_plain_names(cb):
    md = "```apl\n      +A←1\n1\n      +A[1]+←1\n1\n      +(a b)←1 2\n1 2\n```\n"
    (b,) = cb.find_blocks("a.md", md)
    n = cb.normalise(b)
    blocks, classes = cb._normalised_page([b], [n], set())
    assert cb._plus_checks(blocks, classes, [n]) == {0: {0: "{0::¯1 ⋄ ⍵≡+⍵}A"}}


@needs_docker
def test_main_plus_rewrite_proved_by_the_hidden_check(cb, tmp_path):
    # The printed output is out of date (no leading space) but + leaves the
    # character matrix as it is, so ⎕← shows what + showed: the rewrite stays.
    md = (
        "```apl\n      ∇R←MEAN X\n        R←X\n      ∇\n      +F←⎕CR'MEAN'\n"
        "R←MEAN X\nR←X\n```\n\n"
        "```apl\n      +C←1J2 3\n1J¯2 3\n      +D←1J2\n1J2\n```\n"
    )
    _docs(tmp_path, {"a.md": md})
    _, log = _main(cb, tmp_path)
    _, (f, c) = cb.read_log(log)
    assert f["rewrites"] == ["shy"] and "⎕←F←⎕CR'MEAN'" in f["proposed"]
    assert f["verdict"] == "differs" and "rewrite_rejected" not in f
    # a complex value: +C shows the conjugate, so + stays; +D's printed
    # output was not the conjugate, and ⎕← shows it: proved by display
    assert c["rewrite_rejected"] == {"+C←1J2 3": "shy: + kept: conjugate changes the value"}
    assert "+C←1J2 3" in c["proposed"] and "⎕←D←1J2" in c["proposed"]


# --- review fixes: normalising -------------------------------------------------


def test_normalise_definition_after_editor_line_is_left(cb):
    lines = ["      ∇foo∇", "     ∇ foo;local", "[1]    global", "     ∇"]
    n = _norm(cb, *lines)
    assert n.rewrites == () and n.block.body == lines


def test_normalise_line_count_quad_in_body_keeps_numbers(cb):
    lines = ["     ∇ Foo", "[1]    :If 4=⍴⎕LC", "[2]        Foo", "[3]    :EndIf", "     ∇"]
    assert _norm(cb, *lines).keep == "line-numbers"


def test_normalise_page_citing_numbers_keeps_them(cb):
    md = "```apl\n     ∇ NewFile\n[1]    1\n     ∇\n```\n\nThe error in `NewFile[1]` …\n"
    (b,) = cb.find_blocks("a.md", md)
    assert cb.normalise(b, md).keep == "line-numbers"
    assert cb.normalise(b, md.replace("NewFile[1]", "NewFile")).rewrites == ("unnumbered",)
    md2 = md.replace("The error in `NewFile[1]` …", "```apl\n      x\n#.NewFile[1]\n```")
    assert cb.normalise(cb.find_blocks("a.md", md2)[0], md2).keep == "line-numbers"


def test_normalise_skips_nested_blocks_and_echoed_error_lines(cb):
    md = "1. item\n\n   ```apl\n         ⊢A←1\n   1\n   ```\n"
    (b,) = cb.find_blocks("a.md", md)
    assert b.nested and cb.normalise(b, md).rewrites == ()
    n = _norm(cb, "      +A←⍳'a'", "DOMAIN ERROR", "      +A←⍳'a'", "       ∧")
    assert n.block.body[2] == "      +A←⍳'a'"  # the echo stays as printed
    assert n.block.body[0] == "      ⎕←A←⍳'a'"


def test_function_name_strand_argument_is_not_an_operator(cb):
    assert cb._function_name("foo(ev arg)") == "foo"
    assert cb._function_name("R←(F OP G) X") == "OP"


def test_classify_closing_del_must_be_at_the_session_indent(cb):
    _, c = _block(cb, "      ∇ R←F X", "        R←X", "∇", "      F 1", "1")
    assert (c.reason, c.runnable) == ("del-definition", False)


def test_plus_check_skipped_for_diamond_lines(cb):
    md = "```apl\n      +A←1J2 ⋄ A←1\n1J¯2\n```\n"
    (b,) = cb.find_blocks("a.md", md)
    n = cb.normalise(b, md)
    blocks, classes = cb._normalised_page([b], [n], set())
    assert n.plus == (0,) and cb._plus_checks(blocks, classes, [n]) == {}


def test_unproved_plus_error_shown_on_page_is_not_hidden(cb):
    md = "```apl\n      +A←⎕NS''\nDOMAIN ERROR\n      +A←⎕NS''\n      ∧\n```\n"
    (b,) = cb.find_blocks("a.md", md)
    n = cb.normalise(b, md)
    blocks, classes = cb._normalised_page([b], [n], set())
    checks = cb._plus_checks(blocks, classes, [n])
    res = {0: [cb.LineResult(["#.[Namespace]"], False, None), cb.LineResult(["¯1"], False, None)]}
    assert cb._unproved_plus(blocks, classes, [n], res, checks) == {
        (0, 0): "⎕← displays otherwise: #.[Namespace]"}
    res[0][1] = cb.LineResult(["1"], False, None)
    assert cb._unproved_plus(blocks, classes, [n], res, checks) == {}


# --- re-indenting likely input lines --------------------------------------------


def test_reindent_candidates_only_between_input_lines(cb):
    b, c = _block(
        cb, "      A←1", "     A+←1", "", "    A+←1", "      A", " 3", "     4",
    )
    assert cb._reindent_candidates(b, c) == ([1, 3], True)  # " 3", "     4" end the block
    b, c = _block(cb, "      ⍳3", "     1 2 3", "      2", "2")
    assert cb._reindent_candidates(b, c) == ([1], False)  # tried; kept only if proved
    b, c = _block(cb, "      ⍳3", "1 2 3", "     x", "      2")
    assert cb._reindent_candidates(b, c) == ([], False)  # output before it, not code
    b, c = _block(cb, "      ⍳3", "1 2 3", "       ⍴⍳3", "3", "   ⍳2", "        'a'")
    assert cb._reindent_candidates(b, c) == ([2, 5], True)  # code-like at 4-8 spaces


@needs_docker
def test_main_reindents_a_differing_block_only_when_it_then_matches(cb, tmp_path):
    md = (
        "```apl\n      MAT←2 2⍴⍳4\n     MAT[1;1]←9\n      MAT\n9 2\n3 4\n```\n\n"
        "```apl\n      ⍳3\n     1 2 3\n      2\n2\n```\n"
    )
    _docs(tmp_path, {"a.md": md})
    _, log = _main(cb, tmp_path)
    _, (a, b) = cb.read_log(log)
    assert (a["verdict"], a["split"], a["rewrites"]) == ("match", "confirmed", ["reindented"])
    assert a["proposed"] == "```apl\nMAT←2 2⍴⍳4\nMAT[1;1]←9\nMAT\n```\n\n```text\n9 2\n3 4\n```\n"
    assert a["original"].count("     MAT[1;1]←9\n") == 1
    assert (b["verdict"], b.get("rewrites")) == ("differs", None)



def test_classify_comment_continuation_is_input(cb):
    _, c = _block(cb, "      R,←5    ⍝ ,← updates", "              ⍝ the table", "      R", "5")
    assert c.roles == ["input", "input", "input", "output"]
    assert c.reason is None


@pytest.mark.parametrize("text, code", [
    ("{⍵⍳2 3 5}R", True), ("5 10 ⍪⍛⊥ 1 0 2", True), ("'Andy' 'Geoff'", True),
    ("1 2 3", False), (" MONDAY  WEDNESDAY", False), ("┌→──┐", False), ("Hello, World", False),
])
def test_looks_like_code(cb, text, code):
    assert cb._looks_like_code(text) is code


def test_judge_unshown_output_keeps_the_split(cb):
    lines = ["      (⍪5 10)⊥ 1 0 2", "      5 10 ⍪⍛⊥ 1 0 2", "27 102"]
    v = _judge(cb, lines, [_lr(cb, ["27 102"]), _lr(cb, ["27 102"])])
    assert v[:2] == ("differs", "unshown-output")
    b, c = _block(cb, *lines)
    assert cb.split_kind(c, "differs", "unshown-output") == "unambiguous"
    v = _judge(cb, ["      1 2", "      3 4", "x"], [_lr(cb, ["1 2"]), _lr(cb, ["x"])])
    assert v[:2] == ("differs", "suspect-input")  # a number row may be output


def test_box_command_was_reply_is_not_compared(cb):
    v = _judge(cb, ["      ]Boxing on", "Was OFF", "      1", "1"], [_lr(cb, ["Was ON"]), _lr(cb, ["1"])])
    assert v[:2] == ("match", None)
    v = _judge(cb, ["      ⎕←'Was OFF'", "Was OFF"], [_lr(cb, ["Was ON"])])
    assert v[0] == "differs"


@needs_docker
def test_main_reindent_proved_by_the_moved_lines(cb, tmp_path):
    md = (
        "```apl\n      ⎕←2+2\n5\n       ⍳3\n1 2 3\n```\n\n"  # differs on 2+2; ⍳3 proved
        "```apl\n      ⍳2\n1 2\n     ⍴⍳9\n7\n```\n"  # ⍴⍳9 prints 9, not 7: not proved
    )
    _docs(tmp_path, {"a.md": md})
    _, log = _main(cb, tmp_path)
    _, (a, b) = cb.read_log(log)
    assert (a["verdict"], a["split"], a["rewrites"]) == ("differs", "unambiguous", ["reindented"])
    assert "```apl\n⍳3\n```" in a["proposed"]
    assert (b["verdict"], b["reason"], b["split"], b["proposed"]) == (
        "differs", "odd-indent", "ambiguous", None)


def test_reindent_candidates_skip_function_listings(cb):
    b, c = _block(cb, "      ⎕VR'FOO'", "     ∇ R←FOO", "[1]    R←10", "     ∇", "      1", "1")
    assert cb._reindent_candidates(b, c) == ([], False)


@needs_docker
def test_define_shown_lets_a_failed_plus_rewrite_be_proved(cb, tmp_path):
    md = "```apl\n      A\n1 2 3 4 5\n\n      +A[2 4]+←1\n1\n\n      A\n1 3 3 5 5\n```\n"
    _docs(tmp_path, {"a.md": md})
    _, log = _main(cb, tmp_path)
    _, (e,) = cb.read_log(log)
    assert "rewrites" not in e and "the ⎕← line fails" in e["rewrite_rejected"]["+A[2 4]+←1"]
    _, log = _main(cb, tmp_path, "--define-shown")
    _, (e,) = cb.read_log(log)
    assert (e["verdict"], e["rewrites"], e.get("rewrite_rejected")) == (
        "match", ["shy", "aplan"], None)
    assert "⎕←A[2 4]+←1" in e["proposed"] and "A←1 2 3 4 5" in e["proposed"]


# --- applying the log -------------------------------------------------------------

_APPLY_PAGE = (
    "# T\n\nText.\n\n```apl\n      1+1\n2\n```\n\nMore.\n\n```apl\n      2+2\n4\n```\n\n"
    "```apl\n      3+3\n6\n```\n"
)


def _applied(cb, tmp_path, decisions, verdicts, *extra):
    _docs(tmp_path, {"a.md": _APPLY_PAGE})
    _, log = _main(cb, tmp_path, "--no-run")
    meta, entries = cb.read_log(log)
    for e, d, v in zip(entries, decisions, verdicts):
        e["decision"], e["verdict"] = d, v
    cb.write_log(log, meta, entries)
    rc, _ = _main(cb, tmp_path, "--apply", *extra)
    return rc, (tmp_path / "docs" / "g" / "a.md").read_text(encoding="utf-8")


def test_apply_decision_apply_only(cb, tmp_path, capsys):
    rc, text = _applied(cb, tmp_path, ["apply", "pending", "skip"], ["differs"] * 3)
    assert rc == 0
    assert text == (
        "# T\n\nText.\n\n```apl\n1+1\n```\n\n```text\n2\n```\n\nMore.\n\n"
        "```apl\n      2+2\n4\n```\n\n```apl\n      3+3\n6\n```\n"
    )
    assert "Apply (decision apply): 1 applied, 0 stale, 2 not selected" in capsys.readouterr().out


def test_apply_match_only_ignores_decisions_but_not_skip(cb, tmp_path):
    rc, text = _applied(
        cb, tmp_path, ["pending", "pending", "skip"], ["match", "differs", "match"], "--match-only"
    )
    assert rc == 0
    assert "```apl\n1+1\n```\n\n```text\n2\n```" in text
    assert "```apl\n      2+2\n4\n```" in text and "```apl\n      3+3\n6\n```" in text


def test_apply_dry_run_and_page_filter(cb, tmp_path, capsys):
    rc, text = _applied(cb, tmp_path, ["apply"] * 3, ["match"] * 3, "--dry-run")
    assert rc == 0 and text == _APPLY_PAGE
    assert "3 would apply" in capsys.readouterr().out
    rc, _ = _main(cb, tmp_path, "--apply", "--page", "b*.md")
    assert "0 applied" in capsys.readouterr().out


def test_apply_leaves_a_changed_block_alone(cb, tmp_path, capsys):
    _docs(tmp_path, {"a.md": _APPLY_PAGE})
    _, log = _main(cb, tmp_path, "--no-run")
    meta, entries = cb.read_log(log)
    for e in entries:
        e["decision"] = "apply"
    cb.write_log(log, meta, entries)
    page = tmp_path / "docs" / "g" / "a.md"
    page.write_text(_APPLY_PAGE.replace("      2+2\n4", "      2+2\n5"), encoding="utf-8")
    capsys.readouterr()
    rc, _ = _main(cb, tmp_path, "--apply")
    assert rc == 1
    text = page.read_text(encoding="utf-8")
    assert "```apl\n      2+2\n5\n```" in text  # unchanged
    assert "```apl\n1+1\n```" in text and "```apl\n3+3\n```" in text
    out = capsys.readouterr()
    assert "2 applied, 1 stale" in out.out and "g/a.md:12: the block changed" in out.err


def test_apply_flags_need_apply(cb, tmp_path, capsys):
    _docs(tmp_path, {"a.md": _APPLY_PAGE})
    assert _main(cb, tmp_path, "--match-only")[0] == 2
    assert _main(cb, tmp_path, "--dry-run")[0] == 2
    assert _main(cb, tmp_path, "--apply")[0] == 2  # no log yet
