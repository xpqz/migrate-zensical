"""Review tool for `apl` code fences in the Markdown documentation.

It finds `apl` fences, proposes splitting session input from output, and
validates examples against Dyalog APL. This module holds the block finder.
The tool only reads the corpus; it never writes to it.
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

OPEN_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True)
class Block:
    page: str
    line: int  # 1-based line of the opening fence
    sha256: str  # hex of `text` as UTF-8
    text: str  # opening to closing fence inclusive, newline-terminated
    body: list[str]  # lines between the fences
    nested: bool  # opening fence is indented


def find_blocks(page: str, markdown: str) -> list[Block]:
    """Return every closed fence whose info string is exactly `apl`."""
    # Split on "\n" only: U+2028, \x85, \f and a lone "\r" stay inside lines,
    # so `text` and `sha256` carry the file's exact characters.
    lines = markdown.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = OPEN_RE.match(lines[i])
        if not m or (m[2][0] == "`" and "`" in m[3]):
            i += 1
            continue
        indent, fence, info = m[1], m[2], m[3].strip()
        close = re.compile(rf"^\s*{re.escape(fence[0])}{{{len(fence)},}}\s*$")
        end = next((j for j in range(i + 1, len(lines)) if close.match(lines[j])), None)
        if end is None:
            i += 1
            continue
        if info == "apl":
            text = "\n".join(lines[i : end + 1]) + "\n"
            blocks.append(
                Block(
                    page=page,
                    line=i + 1,
                    sha256=hashlib.sha256(text.encode()).hexdigest(),
                    text=text,
                    body=lines[i + 1 : end],
                    nested=bool(indent),
                )
            )
        i = end + 1
    return blocks


Role = Literal["input", "output", "blank"]

_WS = " \u00a0"
_SESSION_INDENT_RE = re.compile(r"^[ \u00a0]{6}(?=\S)")
_ERROR_RE = re.compile(r"^(?=\S)[A-Z ]*ERROR\b")
_CARET_RE = re.compile(r"^ +[∧^ ]*[∧^][∧^ ]*$")
_DEL_LINE_RE = re.compile(r"^\[\d+\]")
_SCRIPT_RE = re.compile(r"^:(Class|Namespace|Interface)")
_MAX_RE = re.compile(r"┌→|┌↓|^(└∊|↓)", re.M)
_MIN_RE = re.compile(r"[┌┬┐├┼┤└┴┘]")


@dataclass
class Classification:
    kind: Literal["listing", "session", "ambiguous"]
    reason: str | None
    roles: list[Role]
    nbsp: bool
    runnable: bool
    style: Literal["max", "min", "off"]


def strip_indent(line: str) -> str:
    """Remove the six-character session indent."""
    return line[6:]


def _leading(line: str) -> int:
    return len(line) - len(line.lstrip(_WS))


def classify(block: Block) -> Classification:
    # "\r" (CRLF files) counts as trailing whitespace.
    lines = [ln.rstrip() for ln in block.body]
    roles: list[Role] = []
    for ln in lines:
        if not ln:
            roles.append("blank")
        elif _SESSION_INDENT_RE.match(ln):
            roles.append("input")
        else:
            roles.append("output")
    # Error triple: message, repeated input, caret. Repeat and caret are output.
    triple: set[int] = set()
    for i in range(len(lines) - 2):
        if (
            _ERROR_RE.match(lines[i])
            and roles[i + 1] == "input"
            and _CARET_RE.match(lines[i + 2])
        ):
            roles[i + 1] = roles[i + 2] = "output"
            triple.update((i + 1, i + 2))

    inputs = [i for i, r in enumerate(roles) if r == "input"]
    style: Literal["max", "min", "off"] = "off"
    out = "\n".join(ln for ln, r in zip(lines, roles) if r == "output")
    if _MAX_RE.search(out):
        style = "max"
    elif _MIN_RE.search(out):
        style = "min"
    nbsp = any("\u00a0" in lines[i][:6] for i in inputs)
    if not inputs:
        return Classification("listing", None, roles, nbsp, False, style)

    reason = None
    runnable = True
    del_open = False
    has_del = False
    for ln, r in zip(lines, roles):
        if r == "input":
            text = strip_indent(ln)
            has_del = has_del or text.startswith("∇")
            del_open = text.endswith("{") or text.startswith("∇")
        elif _DEL_LINE_RE.match(ln) and del_open:
            has_del = True
    first = inputs[0]
    if has_del:
        reason, runnable = "del-definition", False
    elif any(_SCRIPT_RE.match(ln) for ln in lines):
        reason, runnable = "script", False
    elif any(lines[i] and _leading(lines[i]) for i in range(first)):
        reason, runnable = "indent", False
    elif any(
        r == "output" and i not in triple and _leading(ln) >= 6
        for i, (ln, r) in enumerate(zip(lines, roles))
    ):
        reason = "indented-output"
    kind = "session" if reason is None else "ambiguous"
    return Classification(kind, reason, roles, nbsp, runnable, style)


class SplitError(ValueError):
    pass


def split_kind(
    cls: Classification, verdict: str | None, reason: str | None = None
) -> Literal["confirmed", "unambiguous", "ambiguous", "none"]:
    if cls.kind == "listing":
        return "none"
    if cls.kind == "ambiguous" and not cls.runnable:
        return "ambiguous"
    if verdict == "not-run" and reason == "suspect-input":
        return "ambiguous"
    if cls.reason == "indented-output" and verdict != "match":
        return "ambiguous"
    if verdict == "match":
        return "confirmed"
    return "unambiguous"


def _fence(tag: str, lines: list[str]) -> str:
    longest = max(
        (len(m.group()) for ln in lines if (m := re.match(r"`+", ln.lstrip()))),
        default=0,
    )
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{tag}\n" + "".join(ln + "\n" for ln in lines) + f"{ticks}\n"


def split_block(block: Block, cls: Classification) -> str:
    """Propose an apl block of input followed by a text block of output."""
    if cls.kind == "listing" or (cls.kind == "ambiguous" and not cls.runnable):
        raise ValueError(f"cannot split a {cls.kind} block ({cls.reason})")
    # Each group: [input lines, output lines]
    groups: list[list[list[str]]] = []
    pending_blank = 0
    for raw, role in zip(block.body, cls.roles):
        line = raw.rstrip("\r")
        if role == "blank":
            pending_blank += 1
            continue
        if role == "input":
            if not groups or groups[-1][1]:
                groups.append([[], []])
            groups[-1][0].append(strip_indent(line))
        else:
            if not groups:
                groups.append([[], []])
            out = groups[-1][1]
            if out:
                out.extend([""] * pending_blank)
            out.append(line)
        pending_blank = 0
    parts = []
    for inputs, outputs in groups:
        text = _fence("apl", inputs) if inputs else ""
        if outputs:
            text += ("\n" if inputs else "") + _fence("text", outputs)
        parts.append(text)
    result = "\n".join(parts)
    check_no_lost_lines(block, result)
    return result


_OPEN_RE = re.compile(r" {0,3}(`{3,})([^`]*)$")
_CLOSE_RE = re.compile(r" {0,3}(`{3,})\s*$")


def _fences(text: str) -> list[tuple[str, list[str]]]:
    """Parse (info, content lines) for each fenced block, CommonMark-style."""
    out: list[tuple[str, list[str]]] = []
    cur: list[str] | None = None
    width = 0
    for ln in text.split("\n"):
        ln = ln.rstrip("\r")
        if cur is None:
            if m := _OPEN_RE.match(ln):
                width = len(m.group(1))
                cur = []
                out.append((m.group(2).strip(), cur))
        elif (m := _CLOSE_RE.match(ln)) and len(m.group(1)) >= width:
            cur = None
        else:
            cur.append(ln)
    return out


def check_no_lost_lines(original: Block, proposed: str) -> None:
    """Every non-blank line must survive, on the same side (apl vs text)."""
    cls = classify(original)
    before = {"input": Counter[str](), "output": Counter[str]()}
    for raw, role in zip(original.body, cls.roles):
        ln = raw.rstrip("\r")
        if role == "input":
            ln = strip_indent(ln)
        if ln.strip():
            before[role][ln] += 1
    after = {"input": Counter[str](), "output": Counter[str]()}
    for info, lines in _fences(proposed):
        side = "input" if info == "apl" else "output"
        after[side].update(ln for ln in lines if ln.strip())
    for side in before:
        if before[side] != after[side]:
            diff = (before[side] - after[side]) + (after[side] - before[side])
            raise SplitError(
                f"{original.page}:{original.line}: {side} lines differ: {dict(diff)}"
            )


# --- Running examples under dyalogscript ---------------------------------

HARNESS = Path(__file__).with_name("code-blocks-harness.apls")


_SKIPS = {" SKIP": "system-command", " SKIP QQ": "quote-quad"}


@dataclass
class LineResult:
    output: list[str]
    error: bool
    skipped: str | None


@dataclass
class RunBlock:
    id: int
    style: str | None  # "max" | "min" | "off" | None (leave the box setting)
    lines: list[str]


def find_dyalogscript() -> str | None:
    """`$DYALOGSCRIPT`, else `dyalogscript` on PATH, else None."""
    return os.environ.get("DYALOGSCRIPT") or shutil.which("dyalogscript")


def _launch(
    argv: list[str], timeout: float, cwd: str | None = None
) -> tuple[str, bool]:
    """Run argv in its own process group; kill the group on timeout and on exit.

    Returns (stdout with CR/CRLF turned into LF, timed_out).
    """
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=cwd,
    )
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc.pid)
        out, _ = proc.communicate()
    finally:
        _kill_group(proc.pid)  # an interpreter may outlive the launcher
    text = out.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n"), timed_out


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def dyalog_version(dyalogscript: str) -> str:
    """The interpreter's version field, e.g. "21.0.54424.0"."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp, "version.apls")
        script.write_text("⎕←2⊃#⎕WG'APLVersion'\n⎕OFF\n", encoding="utf-8")
        out, _ = _launch([dyalogscript, str(script)], timeout=30)
    return out.strip()


def run_page(
    blocks: list[RunBlock], dyalogscript: str, timeout: float = 60.0
) -> tuple[dict[int, list[LineResult]], bool]:
    """Run each block's input lines in one fresh interpreter.

    Results are keyed by block id; blocks not reached are absent. The flag is
    True if the run timed out or the interpreter stopped before the last line.
    """
    token = secrets.token_hex(8)
    request = {
        "pw": 80,
        "token": token,
        "blocks": [
            {"id": b.id, "style": b.style, "lines": b.lines} for b in blocks
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp, "request.json")
        req.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        out, timed_out = _launch(
            [dyalogscript, "DYALOG_INITSESSION=1", str(HARNESS), str(req)],
            timeout,
            cwd=tmp,  # examples that write files must not litter the caller's cwd
        )
    results, done = _parse_output(out, token)
    expected = sum(len(b.lines) for b in blocks)
    return results, timed_out or done < expected


def _parse_output(text: str, token: str) -> tuple[dict[int, list[LineResult]], int]:
    """Parse harness sentinels. Returns (results, number of finished lines).

    Sentinels carry the run's token (so output cannot spoof them); anything
    outside a BEGIN/END pair (a traceback, say) is ignored.
    """
    sentinel = re.compile(
        rf"⍝CB-{re.escape(token)}-(?:BEGIN (\d+) (\d+)\n|END( ERROR| SKIP QQ| SKIP)?\n)",
    )
    results: dict[int, list[LineResult]] = {}
    current: int | None = None
    start = 0
    done = 0
    for m in sentinel.finditer(text):
        if m.group(1) is not None:
            current, start = int(m.group(1)), m.end()
            continue
        if current is None:
            continue
        lines = text[start : m.start()].split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        flag = m.group(3)
        results.setdefault(current, []).append(
            LineResult(
                output=lines,
                error=flag == " ERROR",
                skipped=_SKIPS.get(flag or ""),
            )
        )
        current = None
        done += 1
    return results, done


# --- Judging ----------------------------------------------------------------

VOLATILE = frozenset(
    {
        "?", "⎕AI", "⎕TS", "⎕DL", "⎕WA", "⎕RL", "⎕SI", "⎕LC", "⎕TID", "⎕TNUMS",
        "⎕DMX", "⎕PROFILE", "120⌶",
    }
)

# Facilities with side effects on files, processes, the workspace or the GUI.
SIDE_EFFECTS = frozenset(
    "⎕" + n
    for n in (
        "SH CMD SHELL NA MKDIR NCREATE NERASE NDELETE NMOVE NCOPY NPUT NAPPEND "
        "NREPLACE NRENAME NRESIZE NTIE NSHARE NLOCK FCREATE FTIE FSTIE FERASE "
        "FAPPEND FREPLACE FRENAME FRESIZE FDROP FHOLD FUNTIE NUNTIE FCOPY FPACK "
        "FSTAC FCHK SAVE LOAD CY FIX WC WS NQ DQ TGET TPUT CSV MAP SE ED EXPORT NEW "
        "FIO HTTPCMD"
    ).split()
)
# I-beams known to be pure (no files, processes or workspace changes).
PURE_IBEAMS = frozenset({8, 181, 201, 219, 220, 1200, 1500, 5581, 7162, 16808})
_IBEAM_NUM_RE = re.compile(r"(?<![\w∆⍙_.¯])(\d+)\s*$")
_SAFE_COMMANDS = frozenset({"box", "boxing", "display", "disp", "rows"})

_QUAD_NAME_RE = re.compile(r"⎕[A-Za-z0-9_]*")


def _code_only(line: str) -> str:
    """Blank out '…' strings (with '' doubling) and cut at a ⍝ comment."""
    out: list[str] = []
    in_str = False
    for ch in line:
        if in_str:
            if ch == "'":
                in_str = False
            out.append(" ")
        elif ch == "'":
            in_str = True
            out.append(" ")
        elif ch == "⍝":
            break
        else:
            out.append(ch)
    # A doubled quote closes and reopens the string, so '' is handled.
    return "".join(out)


def _strip_comment(line: str) -> str:
    """Cut at a ⍝ comment outside strings, keeping the strings."""
    in_str = False
    for i, ch in enumerate(line):
        if ch == "'":
            in_str = not in_str
        elif ch == "⍝" and not in_str:
            return line[:i]
    return line


def _is_volatile(line: str) -> bool:
    code = _code_only(line)
    # Non-quad entries (`?`) match anywhere in code; ⎕ names match whole
    # tokens, case-insensitively.
    if any(v in code for v in VOLATILE if not v.startswith("⎕")):
        return True
    names = {v.upper() for v in VOLATILE if v.startswith("⎕")}
    found = {m.upper() for m in _QUAD_NAME_RE.findall(code)}
    if found & names or re.search(r"16808\s*⌶", code):
        return True
    # ⎕DT is volatile only when it converts the current time ('Z').
    return "⎕DT" in found and "'Z'" in _strip_comment(line)


def _statements(text: str) -> list[str]:
    """Split a comment-free line at ⋄ outside strings."""
    parts: list[str] = []
    cur: list[str] = []
    in_str = False
    for ch in text:
        if ch == "'":
            in_str = not in_str
        if ch == "⋄" and not in_str:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def has_side_effect(line: str) -> bool:
    """True if an input line may have a side effect.

    Matching is deliberately string-blind in the cautious direction: only the
    comment is removed, so a facility named inside a string (for `⍎`, `⎕FX`,
    `85⌶`) counts. A string that merely mentions one is an accepted false
    positive.
    """
    text = _strip_comment(line)
    if text.lstrip().startswith("]"):
        parts = text.lstrip()[1:].split()
        if not parts or parts[0].lower() not in _SAFE_COMMANDS:
            return True
        # An allowed command still evaluates its argument: check the rest.
    if any(st.lstrip().startswith(")") for st in _statements(text)):
        return True
    names = {m.upper() for m in _QUAD_NAME_RE.findall(text)}
    if names & SIDE_EFFECTS:
        return True
    # A string that starts a system command, or a name built at run time.
    if re.search(r"'\s*\)[A-Za-z]", text):
        return True
    if "⍎" in text and names & {"⎕UCS", "⎕AV"}:
        return True
    for m in re.finditer("⌶", text):
        num = _IBEAM_NUM_RE.search(text[: m.start()])
        if not num or int(num.group(1)) not in PURE_IBEAMS:
            return True
    return False


def expected_outputs(block: Block, cls: Classification) -> list[list[str]]:
    """Printed output lines (verbatim) after each input line."""
    groups: list[list[str]] = []
    for raw, role in zip(block.body, cls.roles):
        if role == "input":
            groups.append([])
        elif groups:
            groups[-1].append(raw)
    return groups


def _input_texts(block: Block, cls: Classification) -> list[str]:
    return [
        strip_indent(raw.rstrip("\r"))
        for raw, role in zip(block.body, cls.roles)
        if role == "input"
    ]


def _normalise(lines: list[str]) -> list[str]:
    out = [ln.rstrip() for ln in lines]
    while out and not out[-1]:
        out.pop()
    return out


def _session(inputs: list[str], results: list[LineResult]) -> list[str]:
    out: list[str] = []
    for text, res in zip(inputs, results):
        out.append("      " + text)
        out.extend(res.output)
    return out


def _uses_name(line: str, name: str) -> bool:
    return bool(
        re.search(rf"(?<![\w∆⍙_.]){re.escape(name)}(?![\w∆⍙_])", _strip_comment(line))
    )


def judge(
    block: Block,
    cls: Classification,
    results: list[LineResult] | None,
    prior_unrun: bool,
    timed_out: bool,
) -> tuple[str, str | None, str | None]:
    """Decide `(verdict, reason, actual)` for a block.

    Rules, in order:
    1. block not runnable: not-run with the classification's reason;
    2. no results, or fewer than input lines, and timed out: timeout
       (no results and not timed out: not-run/not-run);
       fewer results than input lines without timeout: not-run/incomplete;
    3. any skipped line: not-run with the skipped value as reason;
    4. a volatile facility in an input line: not-run/volatile;
    5. an error the printed output does not show: not-run/suspect-input
       (SYNTAX/VALUE ERROR, no prior unrun block), dependency (prior
       unrun block) or error;
    6. every line's output equal after normalisation: match;
    7. otherwise: differs.

    `actual` is the session-style actual output (each input at six-space
    indent, then its output, newline-terminated) for differs and for the
    not-run reasons error, dependency and suspect-input; else None.
    """
    if not cls.runnable:
        return "not-run", cls.reason, None
    inputs = _input_texts(block, cls)
    if results is None:
        return ("timeout", None, None) if timed_out else ("not-run", "not-run", None)
    if len(results) < len(inputs):
        return ("timeout", None, None) if timed_out else ("not-run", "incomplete", None)
    for res in results:
        if res.skipped:
            return "not-run", res.skipped, None
    if any(_is_volatile(t) for t in inputs):
        return "not-run", "volatile", None

    expected = expected_outputs(block, cls)
    actual = "".join(ln + "\n" for ln in _session(inputs, results))
    for idx, (exp, res) in enumerate(zip(expected, results)):
        if not res.error:
            continue
        if any(_ERROR_RE.match(ln.rstrip()) for ln in exp):
            continue
        head = next((ln for ln in res.output if _ERROR_RE.match(ln)), "")
        if prior_unrun:
            return "not-run", "dependency", actual
        if head.startswith("SYNTAX ERROR"):
            return "not-run", "suspect-input", actual
        if head.startswith("VALUE ERROR"):
            m = re.match(r"VALUE ERROR: Undefined name: (\S+)", head)
            name = m.group(1) if m else None
            others = [t for k, t in enumerate(inputs) if k != idx]
            if not name or not any(_uses_name(t, name) for t in others):
                return "not-run", "suspect-input", actual
        return "not-run", "error", actual

    if len(results) == len(expected) and all(
        _normalise(e) == _normalise(r.output) for e, r in zip(expected, results)
    ):
        return "match", None, None
    return "differs", None, actual


class LogError(Exception):
    """The log could not be written faithfully."""


_HEADER = (
    "# Code-blocks review log. Review each entry and set `decision` to\n"
    "# `apply` or `skip` (it starts as `pending`); only `apply` entries are\n"
    "# applied. Text fields are exact; do not re-indent or strip them.\n"
)
_TEXT_KEYS = ("original", "proposed", "actual")


# Characters a literal block keeps verbatim: newline plus the emitter's
# printable set, minus the unicode line breaks the loader would turn into
# newlines. (No tab, no CR.)
_LITERAL_OK = re.compile("[\n\x20-\x7e\xa0-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]*")


class _LogDumper(yaml.SafeDumper):
    quoted: frozenset = frozenset()

    def analyze_scalar(self, scalar):
        analysis = super().analyze_scalar(scalar)
        # PyYAML refuses literal blocks for trailing spaces and
        # whitespace-only lines; the loader keeps them, so allow it.
        if (
            "\n" in scalar
            and not scalar.endswith(" ")
            and _LITERAL_OK.fullmatch(scalar)
            and not any(c in scalar for c in "\u2028\u2029")
        ):
            analysis.allow_block = True
        return analysis


def _represent_str(dumper: _LogDumper, value: str) -> yaml.ScalarNode:
    if value in dumper.quoted:
        style = '"'
    elif "\n" in value:
        style = "|"
    else:
        style = None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LogDumper.add_representer(str, _represent_str)


def _dump(data: dict, quoted: frozenset) -> str:
    dumper = type("_Dumper", (_LogDumper,), {"quoted": quoted})
    return _HEADER + yaml.dump(
        data, Dumper=dumper, allow_unicode=True, sort_keys=False, width=10**9
    )


def _mismatches(text: str, entries: list[dict]) -> list[tuple[int, str]]:
    got = yaml.safe_load(text)
    got = got.get("entries", []) if isinstance(got, dict) else []
    if len(got) != len(entries):
        raise LogError(f"wrote {len(entries)} entries, read back {len(got)}")
    return [
        (i, key)
        for i, (want, have) in enumerate(zip(entries, got))
        for key in _TEXT_KEYS
        if have.get(key) != want.get(key)
    ]


def write_log(path: Path, meta: dict, entries: list[dict]) -> None:
    """Write the review log, then read it back and check the text is exact.

    Multi-line strings use literal blocks; any string that does not survive
    the round trip is rewritten in double-quoted style (per string).
    """
    data = {"meta": meta, "entries": entries}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        text = _dump(data, frozenset())
        tmp.write_text(text, encoding="utf-8", newline="\n")
        bad = _mismatches(tmp.read_text(encoding="utf-8"), entries)
        if bad:
            quoted = frozenset(entries[i][k] for i, k in bad)
            text = _dump(data, quoted)
            tmp.write_text(text, encoding="utf-8", newline="\n")
            text = tmp.read_text(encoding="utf-8")
            bad = _mismatches(text, entries)
        if bad:
            root = yaml.compose(text)
            nodes = next(
                (v.value for k, v in root.value if k.value == "entries"), []
            )
            i, key = bad[0]
            line = nodes[i].start_mark.line + 1 if i < len(nodes) else "?"
            raise LogError(
                f"{path}: {entries[i].get('page')} (block at page line "
                f"{entries[i].get('line')}, log line {line}): {key} did not "
                "survive the round trip"
            )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_log(path: Path) -> tuple[dict, list[dict]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["meta"], data["entries"]


def carry_decisions(old: list[dict], new: list[dict]) -> list[dict]:
    """Keep earlier decisions for blocks whose proposal is unchanged."""
    prior: dict[tuple, list[dict]] = {}
    for e in old:
        prior.setdefault((e["page"], e["sha256"]), []).append(e)
    out = []
    for entry in new:
        entry = dict(entry)
        queue = prior.get((entry["page"], entry["sha256"]))
        before = queue.pop(0) if queue else None
        same = (
            before is not None
            and before["proposed"] == entry["proposed"]
            and before["split"] == entry["split"]
        )
        entry["decision"] = before["decision"] if same else "pending"
        out.append(entry)
    return out


# --- Command line -----------------------------------------------------------

_BOX_RE = re.compile(r"\]box(ing)?\b", re.I)


def _process_page(
    page: str, markdown: str, dyalogscript: str | None, timeout: float
) -> tuple[list[dict], int, int]:
    """Entries for one page, plus (block count, listing count)."""
    blocks = find_blocks(page, markdown)
    classes = {
        i: classify(b) for i, b in enumerate(blocks) if not b.nested
    }
    results: dict[int, list[LineResult]] = {}
    early_exit = False
    side: set[int] = set()
    sent: dict[int, int] = {}  # block index -> number of lines sent
    if dyalogscript:
        boxed = False
        to_run: list[RunBlock] = []
        for i, b in enumerate(blocks):
            cls = classes.get(i)
            if cls is None or not cls.runnable:
                continue
            lines = _input_texts(b, cls)
            if any(has_side_effect(t) for t in lines):
                side.add(i)
                continue
            boxed = boxed or any(_BOX_RE.match(t) for t in lines)
            to_run.append(RunBlock(i, None if boxed else cls.style, lines))
            sent[i] = len(lines)
        if to_run:
            results, early_exit = run_page(to_run, dyalogscript, timeout)

    entries: list[dict] = []
    listings = 0
    prior_unrun = False
    hung_seen = False
    for i, b in enumerate(blocks):
        cls = classes.get(i)
        if cls is None:
            prior_unrun = True
            entries.append(_entry(b, "not-run", "nested", "none", False, None, None))
            continue
        if cls.kind == "listing":
            listings += 1
            continue
        if i in side:
            verdict, reason, actual = "not-run", "side-effect", None
        elif early_exit and i in sent and len(results.get(i, [])) < sent[i] and hung_seen:
            verdict, reason, actual = "not-run", "unreached", None
        elif dyalogscript:
            if early_exit and i in sent and len(results.get(i, [])) < sent[i]:
                hung_seen = True  # the first block with a missing line
            verdict, reason, actual = judge(
                b, cls, results.get(i), prior_unrun, early_exit
            )
        else:
            verdict, reason, actual = judge(b, cls, None, prior_unrun, False)
        if reason is None and cls.kind == "ambiguous":
            reason = cls.reason
        split = split_kind(cls, verdict, reason)
        proposed = None
        if split in ("confirmed", "unambiguous"):
            try:
                proposed = split_block(b, cls)
            except SplitError:
                split, reason, proposed = "ambiguous", "split-check", None
        if (cls.kind == "ambiguous" and not cls.runnable) or verdict == "not-run":
            prior_unrun = True
        entries.append(_entry(b, verdict, reason, split, cls.nbsp, proposed, actual))
    return entries, len(blocks), listings


def _entry(
    b: Block,
    verdict: str,
    reason: str | None,
    split: str,
    nbsp: bool,
    proposed: str | None,
    actual: str | None,
) -> dict:
    return {
        "page": b.page,
        "line": b.line,
        "sha256": b.sha256,
        "verdict": verdict,
        "reason": reason,
        "split": split,
        "nbsp": nbsp,
        "decision": "pending",
        "original": b.text,
        "proposed": proposed,
        "actual": actual,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Review `apl` code fences: propose splits and run them."
    )
    ap.add_argument("--guide", default="language-reference-guide")
    ap.add_argument("--log", default="code-blocks.yml")
    ap.add_argument("--page", help="glob matched against the path inside the guide")
    ap.add_argument("--no-run", action="store_true", help="do not run examples")
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--docs", default="zensical/docs")
    args = ap.parse_args(argv)

    dyalogscript = None if args.no_run else find_dyalogscript()
    if not args.no_run and dyalogscript is None:
        print("note: dyalogscript not found; not running examples", file=sys.stderr)

    docs = Path(args.docs)
    root = docs / args.guide
    pages = sorted(
        (p for p in root.rglob("*.md") if p.is_file()),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    if args.page:
        pages = [
            p for p in pages
            if fnmatch.fnmatch(p.relative_to(root).as_posix(), args.page)
        ]

    entries: list[dict] = []
    total = listings = 0
    for p in pages:
        found, n, lst = _process_page(
            p.relative_to(docs).as_posix(),
            p.read_text(encoding="utf-8"),
            dyalogscript,
            args.timeout,
        )
        entries += found
        total += n
        listings += lst

    log = Path(args.log)
    if log.exists():
        old = read_log(log)[1]
        if args.page:
            done = {p.relative_to(docs).as_posix() for p in pages}
            kept = [e for e in old if e["page"] not in done]
            replaced = carry_decisions(
                [e for e in old if e["page"] in done], entries
            )
            entries = sorted(kept + replaced, key=lambda e: (e["page"], e["line"]))
        else:
            entries = carry_decisions(old, entries)
    meta = {
        "guide": args.guide,
        "dyalog": dyalog_version(dyalogscript) if dyalogscript else None,
        "platform": platform.platform(),
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    write_log(log, meta, entries)

    print(f"Blocks: {total} total, {listings} listing (not logged)")
    print(f"Entries: {len(entries)} in {log}")
    for key in ("verdict", "split"):
        counts = Counter(e[key] for e in entries)
        print(f"By {key}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
