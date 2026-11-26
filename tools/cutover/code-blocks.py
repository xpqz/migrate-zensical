"""Review the `apl` code examples of a guide: propose splits, run them.

    uv run python tools/cutover/code-blocks.py [--guide language-reference-guide]
        [--log code-blocks.yml] [--page GLOB] [--no-run] [--timeout SECONDS]
        [--image IMAGE] [--docs zensical/docs] [--define-shown]

The `apl` fences mix session input (the six-space indent) with output. For
each fence of the guide under --docs, the tool proposes splitting it into an
`apl` block of input followed by a `text` block of output, runs the input
in Docker (one locked-down container for the run, one interpreter per
page, through code-blocks-harness.apls) and compares what Dyalog prints with the printed
output. Examples never run on the host. It writes a YAML log for people to review entry by entry; the
approved entries are applied later, during the cutover. The tool only reads
the corpus; it never writes to it. Design:
design/superpowers/specs/2026-11-25-code-blocks-design.md.

--guide is the guide's directory under --docs, --log the log to write (an
existing one is copied to <log>.bak first), --page a glob, relative to the
guide, that limits the run to matching pages (their entries replace those
pages' entries in an existing log; other pages' entries are kept),
--no-run proposes splits without running anything, --timeout is the limit
in seconds for each page's interpreter, --image the Docker image to run in,
--define-shown proposes definitions for shown values (below).
The tool exits
with status 2, leaving the log alone, if the guide does not exist or no
page matches.

Docker: Docker must be running; without it the tool says so on stderr and
runs as --no-run. The default image is built from docker/Dockerfile next to
this file (Dyalog 21.0, pinned by digest, plus the .NET runtime) and tagged
code-blocks-dyalog:<first 12 hex of the Dockerfile's sha256> and built when
missing; an --image is pulled when missing. Building or pulling an image is
the only time the network is used. A run starts one container (none if
nothing runs), with no network, no capabilities, no new privileges, a
read-only root, at most 64 processes, 512 MB and one CPU, as uid 1000;
only /work (the working directory), /tmp and the home directory are
writable, as small tmpfs mounts, and the harness and requests are mounted
read-only. Each page runs in it with `docker exec`, in a fresh interpreter
under its own deadline (the page's time-out plus CONTAINER_GRACE seconds).
After each page, time-out or error included, every process the page
started is killed and the writable mounts are emptied: files a page writes
persist across its blocks and never reach another page. The container is
removed when the run ends (also on an error, Ctrl-C, SIGTERM or SIGHUP);
if the tool is killed outright, the container's keeper notices the tool's
heartbeat stop and the container goes within KEEPER_STALE seconds.
Results are those of Linux Dyalog in that image.

Side effects: inside the container, files, workspaces (⎕CY, ⎕FIX,
⎕EXPORT), .NET, shell commands (⎕SH, ⎕CMD, ⎕SHELL) and ⎕NA may run. An
example is still never run if any input line of its block may break or
escape the run: a line is a side effect if it uses a system name not in
SAFE_QUADS (⎕SE, ⎕SV*, the GUI, ⎕CLEAR, ⎕LOAD, ⎕SAVE, ⎕OFF, ⎕ARBOUT,
⎕CS, ⎕PATH, ...), an I-beam not in PURE_IBEAMS, a system command (`)` at a
statement start), a user command other than ]box, ]boxing, ]display, ]disp
and ]rows, bare `⎕` input, ⍎, ⎕FX or 85⌶ with a computed argument, a
dyadic ⍎ whose left argument is not a plain name, # or a literal string, or
a ⎕TRAP action built at run time. Every string is checked as if it were
code.

The log (YAML) has `meta` and `entries`. `meta`: guide, dyalog (the
interpreter's version, null without a run), image, image_id and platform
(the container's image tag, id and platform, e.g. linux/arm64; null
without a run), generated (UTC), and pages (verdict counts for each page with at least one `differs`). Each
entry is one session or ambiguous block (listings without input lines are
only counted), ordered by page and line:

    page      path relative to --docs
    line      line of the opening fence (1-based)
    sha256    of the block's exact text, fence to fence
    verdict   match | differs | not-run | timeout
    reason    why, for most verdicts other than a plain match (below)
    split     confirmed | unambiguous | ambiguous | none
    nbsp      the input indent used non-breaking spaces
    decision  pending | apply | skip; the reviewer sets it
    original  the block as it is
    proposed  the split, for confirmed and unambiguous splits, else null
    actual    the session as it ran (input at six spaces, then its output),
              for differs and for not-run error, dependency, suspect-input

Verdicts:

    match     every input line printed what the block shows
    differs   it ran and printed something else
    not-run   it was not run, or not meaningfully (reason says why)
    timeout   the page's interpreter timed out or exited before the block
              finished

Reasons:

    del-definition  not run: a ∇ definition the tool cannot unnumber (a
                    fragment, numbers out of order, an empty or unclosed
                    definition)
    line-numbers    not run: a ∇ definition whose text refers to its line
                    numbers (→4, 'F[1]', ⎕STOP, ...): its numbers stay
    script          not run: a :Class, :Namespace or :Interface script
    indent          not run: input at an odd indent
    nested          not run: the fence is indented (in a list, say)
    side-effect     not run: it may break or escape the run (above)
    not-run         not run: --no-run, or no Docker
    system-command  not run: the harness skipped a system command or
                    ⎕OFF (these are normally side-effect already)
    quote-quad      not run: uses ⍞
    volatile        ran, not judged: time, random numbers, the stack, ...
    environment     ran, not judged: depends on the machine (⎕AN, ⎕SD, ...)
    error           not-run: an error the printed output does not show
    dependency      not-run: such an error after a block that did not run
    suspect-input   an input line may be printed output: not-run for an
                    unexpected SYNTAX/VALUE ERROR; differs, or not-run in
                    place of volatile/environment, when a line with no
                    printed output printed something; split ambiguous
    incomplete      not-run: fewer results than input lines, no timeout
    unreached       not-run: after the block where the interpreter stopped
    exited          timeout: the interpreter exited early (no time-out)
    after-unrun     differs: an earlier block on the page did not run
    platform        differs: a line uses ⎕SH, ⎕CMD, ⎕SHELL, ⎕NA, ⎕USING,
                    ⎕NEW, ⎕NL on a .NET class, ⎕NINFO, ⎕NPUT, ⎕NAPPEND,
                    ⎕NREPLACE or a Windows path; the difference may be
                    Linux's, not the guide's
    diamond         differs: a line has ⋄-separated statements, which the
                    harness runs one at a time
    annotation      match or differs: column-0 ⍝ lines in the output, not
                    compared; split ambiguous (also when another reason
                    takes the field: such blocks are always ambiguous)
    indented-output an output line is indented six or more spaces; split
                    ambiguous unless the block matches
    split-check     the split would lose or move a line; split ambiguous

Splits:

    confirmed    it ran and matched: the proposal is backed by Dyalog
    unambiguous  every line's role is clear without running it
    ambiguous    the roles are uncertain; no proposal (reason says why)
    none         nothing to split (a nested block)

Normalising input (always on; see the design): an input line showing a shy
result with monadic +, ⊢ or ⊣ on an assignment (+A←⍳5) uses ⎕← instead
(⎕←A←⍳5); a + rewrite is kept only if the rewritten line ran and displayed
the printed output, else the + stays and rewrite_rejected says why, keyed
by the original line. A ∇ definition typed into the session loses its [n]
line numbers: header and closing ∇ at the six-space indent, body lines at
eight (control-structure nesting kept), labels at six. A definition that
follows an input line is that line's output (⎕VR and the like) and stays.
A typed definition without numbers runs as one input unit, fixed with ⎕FX,
displaying nothing (DEFN ERROR if it does not fix). The rewritten block is
what runs and what `proposed` shows (as one apl block when there is no
split); entries list the rewrites applied in `rewrites` (shy, unnumbered,
and aplan, below).

Defining shown values (--define-shown, off by default): a block often shows
a variable's value (an input line that is just the name, then the value)
that the page never defines. A candidate is an input line that is exactly a
name N, where no earlier line of an `apl` block on the page assigns N (N←,
or a ∇ line naming it), N is not an earlier candidate on the page, the run
gives `VALUE ERROR: Undefined name: N` for it and its printed output is not
an error. The printed output becomes array notation: one row of numbers
(¯, decimals, E exponents, J complex) `N←1 2 3`, single-spaced (a scalar
for one number); several such rows a matrix `N←[1 2 ⋄ 3 4]`, each row
spaced as printed; one row of other text
`N←'text'` (quotes doubled); several rows, not all numeric, a character
matrix `N←['abc' ⋄ 'de']` (trailing spaces removed; array notation pads
rows). A matrix is proposed on several lines, rows aligned under the first
element after `[`. Anything else (no output, box drawing, a blank line
inside, numbers and text in one row, a column-0 ⍝ note among the output
lines, an output row that mentions N, an ambiguous block, a block whose
plain run is suspect-input) is unsupported. The definition
replaces the line and its output; with no output, it joins the following
input in the same `apl` block. Proof: the page runs again with every
definition, exactly as proposed (a multi-line definition as one input unit,
run as source), each followed by a hidden check, the bare name, which is not
judged or proposed. A definition is accepted only if the check displays the
printed output, the rewritten block's verdict is match, at least one later
input line on the page that uses N ran, and none of them raised an error
the page does not show; the failures are
dropped and the rest proved once more (if that fails too, none is applied).
Later blocks are judged in the run with the accepted definitions. Extra
fields, only with the option:

    rewrites          includes aplan: `proposed` defines the names in
                      `defines`
    defines           the names defined
    rewrite_rejected  {name: why} for names not defined: unsupported (why),
                      or why the proof failed; the entry is judged as usual

`original` and `sha256` stay the original block's. The tool prints how many
definitions were accepted, rejected and unsupported.

Decisions are carried to the new log by page and sha256 (duplicate blocks
in order): an entry keeps its decision while `proposed` is unchanged and
its split is unchanged or both splits are confirmed or unambiguous;
otherwise it is reset to pending. The tool prints how many decisions were
reset, how many were dropped because their block changed or went away, and
how many were carried onto a block that matched before and does not now
("carried, now differs"), which deserve a second look.
"""

import argparse
import contextlib
import dataclasses
import datetime
import fnmatch
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
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


# "cont": a line of a typed ∇ definition after its header (body and closing
# ∇); the definition is one input unit with the header.
Role = Literal["input", "cont", "output", "blank"]

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
    # An output line starts with ⍝ at column 0: a note, not session output.
    annotation: bool = False


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

    typed = _mark_definitions(lines, roles)
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
    annotation = any(
        r == "output" and ln.startswith("⍝") for ln, r in zip(lines, roles)
    )

    reason = None
    runnable = True
    del_open = False
    has_del = False
    for i, (ln, r) in enumerate(zip(lines, roles)):
        if r == "input":
            text = strip_indent(ln)
            has_del = has_del or (text.startswith("∇") and i not in typed)
            del_open = text.endswith("{") or text.startswith("∇")
        elif r == "output" and _DEL_LINE_RE.match(ln) and del_open:
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
    return Classification(kind, reason, roles, nbsp, runnable, style, annotation)


def _mark_definitions(lines: list[str], roles: list[Role]) -> set[int]:
    """Mark typed ∇ definitions without line numbers; return their headers.

    A header is an input line `∇…` (not a one-line ∇…∇); its definition
    runs to the next line that is just `∇`. Every line in between must be
    non-blank, indented at least six spaces and unnumbered; then those lines
    and the closing ∇ become "cont". Otherwise nothing is marked.
    """
    headers: set[int] = set()
    i = 0
    while i < len(lines):
        text = strip_indent(lines[i]) if roles[i] == "input" else ""
        if not (text.startswith("∇") and text.strip() != "∇" and not text.rstrip().endswith("∇")):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j] and _leading(lines[j]) >= 6:
            if _CLOSE_DEL_RE.match(lines[j]):
                break
            if _DEL_LINE_RE.match(lines[j].lstrip()):
                j = len(lines)
                break
            j += 1
        if j < len(lines) and _CLOSE_DEL_RE.match(lines[j]):
            for k in range(i + 1, j + 1):
                roles[k] = "cont"
            headers.add(i)
            i = j + 1
        else:
            i += 1
    return headers


class SplitError(ValueError):
    pass


def split_kind(
    cls: Classification, verdict: str | None, reason: str | None = None
) -> Literal["confirmed", "unambiguous", "ambiguous", "none"]:
    if cls.kind == "listing":
        return "none"
    if cls.kind == "ambiguous" and not cls.runnable:
        return "ambiguous"
    if reason == "suspect-input" or cls.annotation:
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
        if role in ("input", "cont"):
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
        side = "output" if role in ("output", "blank") else "input"
        if side == "input":
            ln = strip_indent(ln)
        if ln.strip():
            before[side][ln] += 1
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


# --- Normalising input: shy results, line numbers ---------------------------

# A shy result shown with monadic +, ⊢ or ⊣ applied to an assignment: the
# target (a name, an indexed name, or names in parentheses), an optional
# one-character function (modified assignment), then ←.
_SHY_RE = re.compile(
    r"^(?P<indent>[  ]{6})(?P<fn>[+⊢⊣]) *(?P<rest>"
    r"(?:[A-Za-z_∆⍙][A-Za-z0-9_∆⍙¯]*(?: *\[[^\]]*\])?"
    r"|\( *[A-Za-z_∆⍙][A-Za-z0-9_∆⍙¯ ]*\))"
    r" *[^\sA-Za-z0-9_∆⍙¯'()\[\]←⎕⋄⍝]?←.*)$"
)
_HEADER_RE = re.compile(r"^[  ]*∇(?!.*∇[  ]*$)[  ]*(\S.*)$")
_NUMBERED_RE = re.compile(r"^[  ]*\[(\d+)\](.*)$")
_CLOSE_DEL_RE = re.compile(r"^[  ]*∇[  ]*$")
_LABEL_RE = re.compile(r"^[A-Za-z_∆⍙][A-Za-z0-9_∆⍙¯]*:(?!:)")
_LINE_NUMBER_QUADS_RE = re.compile(r"⎕(STOP|TRACE|MONITOR|LC)(?![A-Za-z0-9_])", re.I)
_BRANCH_NUMBER_RE = re.compile(r"(?<![\w∆⍙¯.])(\d+(?:\.\d*)?)")


@dataclass(frozen=True)
class Normalised:
    """A block with its input rewritten (see "Normalising input" in the design).

    `block` stands in for the original when classifying, running, judging and
    proposing; `rewrites` names the rewrites applied ("shy", "unnumbered");
    `plus` holds the body-line indexes (in `block`) of `+` rewrites, which
    need proof; `keep` is "line-numbers" if a definition keeps its numbers
    because its text refers to them.
    """

    block: Block
    rewrites: tuple[str, ...]
    plus: tuple[int, ...]
    keep: str | None


def _function_name(header: str) -> str | None:
    """The function or operator name in a ∇ header, e.g. "R←A DIVIDE B;X"."""
    h = header.split(";")[0].replace("{", " ").replace("}", " ").strip()
    if "←" in h:
        h = h.split("←", 1)[1].strip()
    if m := re.search(r"\(([^)]*)\)", h):  # an operator: (F OP G) or (F OP)
        parts = m.group(1).split()
        return parts[1] if len(parts) >= 2 else None
    parts = h.split()
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0]
    if len(parts) == 3:
        return parts[1]
    return None


def _refers_to_numbers(header: str, body: list[str]) -> bool:
    """The definition's text refers to its own line numbers."""
    name = _function_name(header)
    for text in body:
        code = _code_only(text)
        for stmt in code.split("⋄"):
            if "→" in stmt:
                expr = stmt.split("→", 1)[1]
                if any(float(n) != 0 for n in _BRANCH_NUMBER_RE.findall(expr)):
                    return True
        if name and re.search(rf"(?<![\w∆⍙_.]){re.escape(name)}\[\d+\]", text):
            return True
    return False


def _unnumber(
    body: list[str], start: int, listed: bool, keep_all: bool
) -> tuple[list[str], int, str | None] | None:
    """Rewrite the ∇ definition whose header is body[start].

    Returns (new lines, index after the definition, keep reason) or None
    when body[start] does not begin a numbered definition. `listed` means
    it is output (after ⎕VR and the like): its lines are returned as they
    are. With a keep reason ("line-numbers": the text refers to its
    numbers, or `keep_all`, the block uses ⎕STOP and the like) the lines
    are also unchanged.
    """
    head = _HEADER_RE.match(body[start])
    if not head:
        return None
    numbered: list[tuple[int, str]] = []  # (column of the text, text)
    j = start + 1
    closed = False
    while j < len(body):
        ln = body[j].rstrip()
        m = _NUMBERED_RE.match(ln)
        if m and int(m.group(1)) == len(numbered) + 1:
            text = m.group(2)
            if not text.strip():
                return None  # an empty line: leave the definition alone
            if text.strip() == "∇":
                closed = True
                j += 1
                break
            numbered.append((len(ln) - len(text.lstrip()), text.strip()))
            j += 1
            continue
        if _CLOSE_DEL_RE.match(ln):
            closed = True
            j += 1
        break
    if not closed or not numbered:
        return None
    original = body[start:j]
    if listed:
        return original, j, None
    header = head.group(1).rstrip()
    texts = [t for _, t in numbered]
    if keep_all or _refers_to_numbers(header, texts):
        return original, j, "line-numbers"
    # The body's own indent: the leftmost code line (labels and comments
    # aside); deeper lines (control structures) keep their extra indent.
    code = [c for c, t in numbered if not (_LABEL_RE.match(t) or t.startswith("⍝"))]
    base = min(code or [c for c, _ in numbered])
    lines = [f"      ∇ {header}" if body[start].lstrip().startswith("∇ ") else f"      ∇{header}"]
    for col, text in numbered:
        if _LABEL_RE.match(text):
            lines.append("      " + text)
        else:
            lines.append("        " + " " * max(0, col - base) + text)
    lines.append("      ∇")
    return lines, j, None


def normalise(block: Block) -> Normalised:
    """Rewrite shy displays to ⎕← and remove typed definitions' line numbers."""
    body: list[str] = []
    rewrites: list[str] = []
    plus: list[int] = []
    keep: str | None = None
    keep_all = any(
        _SESSION_INDENT_RE.match(ln) and _LINE_NUMBER_QUADS_RE.search(_code_only(ln))
        for ln in block.body
    )
    listed = False  # the previous definition was output
    prev = ""  # the previous non-blank line
    i = 0
    while i < len(block.body):
        raw = block.body[i]
        if _HEADER_RE.match(raw):
            if prev.lstrip().startswith("∇") or _NUMBERED_RE.match(prev):
                is_listed = listed  # one listing after another (⎕VR¨)
            else:
                # Right after an input line it is that line's output (⎕VR,
                # or a variable holding ⎕OR's result, say).
                is_listed = _leading(prev) >= 6
            done = _unnumber(block.body, i, is_listed, keep_all)
            if done is not None:
                lines, end, why = done
                if lines != block.body[i:end]:
                    if "unnumbered" not in rewrites:
                        rewrites.append("unnumbered")
                keep = keep or why
                body.extend(lines)
                prev, listed, i = block.body[end - 1], is_listed, end
                continue
        m = _SHY_RE.match(raw.rstrip("\r"))
        if m:
            if m.group("fn") == "+":
                plus.append(len(body))
            raw = f"{m.group('indent')}⎕←{m.group('rest')}"
            if "shy" not in rewrites:
                rewrites.append("shy")
        body.append(raw)
        if raw.strip():
            prev = raw
        i += 1
    if not rewrites:
        return Normalised(block, (), (), keep)
    return Normalised(_with_body(block, body), tuple(rewrites), tuple(plus), keep)


def _with_body(b: Block, body: list[str]) -> Block:
    """`b` with another body; text and sha256 stay the original's."""
    return Block(b.page, b.line, b.sha256, b.text, body, b.nested)


# --- Running examples in Docker -----------------------------------------

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
    # Input units: a string is one session line; a list of strings is several
    # lines the harness runs as source (one result for the unit).
    lines: list[str | list[str]]


DOCKER = shutil.which("docker") or "docker"
DOCKERFILE = Path(__file__).with_name("docker") / "Dockerfile"
# The Dockerfile's base: Dyalog 21.0, pinned by digest (arm64 and amd64).
BASE_IMAGE = (
    "dyalog/dyalog@sha256:"
    "d9c0dda7c53b3505b726a41709d5136ddcf15a441fdf572e2c439c69602ceb32"
)


def default_image() -> str:
    """The tag of the image built from DOCKERFILE: its content decides the tag."""
    digest = hashlib.sha256(DOCKERFILE.read_bytes()).hexdigest()
    return f"code-blocks-dyalog:{digest[:12]}"


def _docker(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [DOCKER, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def prepare_image(image: str | None) -> str | None:
    """The image to run examples in, or None (with a note on stderr).

    Docker must be running. The default image is built from DOCKERFILE when
    missing (the only time the network is used); an --image is pulled when
    missing.
    """
    image = image or default_image()
    try:
        if _docker("info", timeout=60).returncode:
            raise OSError("docker info failed")
    except (OSError, subprocess.TimeoutExpired):
        print("note: Docker is not available; not running examples", file=sys.stderr)
        return None
    if _docker("image", "inspect", image).returncode == 0:
        return image
    if image == default_image():
        print(f"note: building {image}", file=sys.stderr)
        made = _docker(
            "build", "-q", "-t", image, "-f", str(DOCKERFILE), str(DOCKERFILE.parent),
            timeout=1800,
        )
    else:
        print(f"note: pulling {image}", file=sys.stderr)
        made = _docker("pull", "-q", image, timeout=1800)
    if made.returncode:
        print(
            f"note: no image {image} ({made.stderr.strip()}); not running examples",
            file=sys.stderr,
        )
        return None
    return image


def image_meta(image: str) -> dict:
    """The image's tag, id and platform (the container's, e.g. linux/arm64)."""
    out = _docker(
        "image", "inspect", "--format",
        "{{.Id}} {{.Os}}/{{.Architecture}}",
        image,
    )
    if out.returncode:
        raise RuntimeError(f"docker image inspect {image}: {out.stderr.strip()}")
    image_id, plat = out.stdout.split()
    return {"image": image, "image_id": image_id, "platform": plat}


# Seconds the interpreter may outlive the page's time-out. Its own deadline
# (coreutils timeout inside the container) ends it even if the tool hangs.
CONTAINER_GRACE = 15
# The container's keeper exits, taking the container with it, once the
# heartbeat file in the run's /cb directory has not changed for this many
# seconds (or is gone): a tool killed outright leaves nothing running for
# long. The tool touches the file every HEARTBEAT seconds.
KEEPER_STALE = 30
HEARTBEAT = 5

# PID 2 under docker-init: exits when /cb/alive stops changing or vanishes.
# Comparing mtimes with each other, not with the clock, is immune to skew
# between the host and Docker's VM.
_KEEPER = (
    'n=0; old=; while :; do m=$(stat -c %Y /cb/alive) || exit 0; '
    'if [ "$m" = "$old" ]; then n=$((n+2)); else n=0; old=$m; fi; '
    '[ "$n" -ge "$1" ] && exit 0; sleep 2; done'
)

# Between pages: kill every process but docker-init, the keeper (whose
# command line carries cb-keeper), this script and their children (not
# docker-init's: a page's orphaned background jobs end up there); then
# empty the writable mounts. Each page thus starts as in a fresh container.
_RESET = r"""
parents="$$"
for d in /proc/[0-9]*; do
  [ "$d" = /proc/1 ] && continue  # docker-init's command line names it too
  case "$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)" in
    *" cb-keeper "*) parents="$parents ${d#/proc/}";;
  esac
done
for d in /proc/[0-9]*; do
  p=${d#/proc/}
  pp=$(sed -n 's/^PPid:[[:space:]]*//p' "$d/status" 2>/dev/null) || continue
  case " 1 $parents " in *" $p "*) continue;; esac
  case " $parents " in *" $pp "*) continue;; esac
  kill -9 "$p" 2>/dev/null
done
for m in /work /tmp /home/dyalog; do
  find "$m" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null
done
exit 0
"""


def _container_argv(image: str, name: str, mount: str) -> list[str]:
    """docker run argv: the run's one locked-down container, detached.

    No network, no capabilities, a read-only root; only /work (the working
    directory), /tmp and the home directory are writable, as small tmpfs
    mounts. `mount` (the harness and each page's request) is read-only at
    /cb. Everything runs as uid 1000. docker-init (--init) is PID 1 and
    reaps; the keeper (_KEEPER) is what keeps the container alive.
    """
    return [
        DOCKER, "run", "-d", "--rm", "--init", "--name", name,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "64",
        "--memory", "512m", "--cpus", "1",
        "--tmpfs", "/work:rw,size=16m,mode=1777",
        "--tmpfs", "/tmp:rw,size=64m",  # the .NET runtime needs room here
        "--tmpfs", "/home/dyalog:rw,size=16m,uid=1000,gid=1000",
        "--user", "1000:1000", "-w", "/work", "-v", f"{mount}:/cb:ro",
        "--entrypoint", "sh", image,
        "-c", _KEEPER, "cb-keeper", str(KEEPER_STALE),
    ]


def _exec_argv(name: str, args: list[str], timeout: float) -> list[str]:
    """docker exec argv: dyalogscript `args` under its own deadline."""
    deadline = math.ceil(timeout) + CONTAINER_GRACE
    return [
        DOCKER, "exec", "-w", "/work", name,
        "timeout", "--signal=KILL", f"{deadline}s", "dyalogscript", *args,
    ]


class ContainerError(RuntimeError):
    """The run's container could not be started, reset or removed."""


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _left(name: str) -> bool:
    """True if the container may still exist (also when docker cannot say)."""
    try:
        out = _docker("ps", "-a", "-q", "--filter", f"name=^/{name}$", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return True
    return out.returncode != 0 or bool(out.stdout.strip())


def _remove_container(name: str) -> None:
    """Make sure the container `name` is gone; raise ContainerError if not.

    --rm removes a container asynchronously, so poll for a while.
    """
    for args in (("kill", name), ("rm", "-f", name)):
        try:
            _docker(*args, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass
    for _ in range(50):
        if not _left(name):
            return
        time.sleep(0.2)
    try:
        _docker("rm", "-f", name, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if _left(name):
        raise ContainerError(f"container {name} is still there")


def _sweep_stale_dirs() -> None:
    """Delete /cb directories that runs killed outright left behind.

    A directory is stale once its heartbeat is older than KEEPER_STALE
    seconds by a wide margin: its container's keeper has exited by then.
    """
    limit = time.time() - 4 * KEEPER_STALE
    for d in Path(tempfile.gettempdir()).glob("code-blocks-*"):
        try:
            if (d / "alive").stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


class Sandbox:
    """The run's one container, named cb-<token>; pages run in it in turn.

    Use it as a context manager: on leaving (normally, on an exception, or
    on SIGTERM/SIGHUP, which main() turns into SystemExit) the container is
    removed and the host directory mounted at /cb deleted. The container
    starts on the first page, so a run that executes nothing starts none. If
    it dies (an example killed its keeper, say), the next page starts a new
    one; there is never more than one.
    """

    def __init__(self, image: str) -> None:
        self.image = image
        self.name: str | None = None
        self._dir: str | None = None
        self._stop = threading.Event()
        self._beat: threading.Thread | None = None

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _touch(self) -> None:
        if self._dir:
            Path(self._dir, "alive").touch()

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT):
            try:
                self._touch()
            except OSError:
                return

    def _running(self) -> bool:
        if self.name is None:
            return False
        try:
            out = _docker("inspect", "-f", "{{.State.Running}}", self.name, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return out.returncode == 0 and out.stdout.strip() == "true"

    def _start(self) -> None:
        if self._dir is None:
            _sweep_stale_dirs()
            self._dir = os.path.realpath(tempfile.mkdtemp(prefix="code-blocks-"))
            os.chmod(self._dir, 0o755)  # the container's user is uid 1000
            Path(self._dir, HARNESS.name).write_text(
                HARNESS.read_text(encoding="utf-8"), encoding="utf-8"
            )
            os.chmod(Path(self._dir, HARNESS.name), 0o644)
            self._touch()
            os.chmod(Path(self._dir, "alive"), 0o644)
            self._beat = threading.Thread(target=self._heartbeat, daemon=True)
            self._beat.start()
        if self.name is not None:
            _remove_container(self.name)
        self.name = f"cb-{secrets.token_hex(8)}"
        out = _docker(*_container_argv(self.image, self.name, self._dir)[1:], timeout=120)
        if out.returncode:
            name, self.name = self.name, None
            _remove_container(name)
            raise ContainerError(f"docker run {self.image}: {out.stderr.strip()}")

    def _reset(self) -> None:
        """Kill what the last page left running and empty the writable mounts."""
        assert self.name is not None
        try:
            out = _docker("exec", self.name, "sh", "-c", _RESET, timeout=30)
            ok = out.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:  # start afresh on the next page
            name, self.name = self.name, None
            _remove_container(name)

    def run(
        self, files: dict[str, str], args: list[str], timeout: float
    ) -> tuple[str, bool]:
        """Run dyalogscript `args` with `files` at /cb; returns (stdout, timed_out).

        CR and CRLF in stdout become LF. On a time-out, an exception or a
        normal end the container is reset, so nothing the page started is
        left running.
        """
        if not self._running():
            self._start()
        assert self.name is not None and self._dir is not None
        self._touch()
        for fname, text in files.items():
            Path(self._dir, fname).write_text(text, encoding="utf-8")
            os.chmod(Path(self._dir, fname), 0o644)
        proc = subprocess.Popen(
            _exec_argv(self.name, args, timeout),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        timed_out = False
        out = b""
        try:
            try:
                out, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._reset()  # ends the interpreter, so the client ends too
                try:
                    out, _ = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    _kill_group(proc.pid)
                    out, _ = proc.communicate()
        finally:
            if proc.poll() is None:
                _kill_group(proc.pid)
                proc.wait()
            for fname in files:
                Path(self._dir, fname).unlink(missing_ok=True)
            if self.name is not None:
                self._reset()
        text = out.decode("utf-8", errors="replace")
        return text.replace("\r\n", "\n").replace("\r", "\n"), timed_out

    def close(self) -> None:
        """Remove the container and the /cb directory. Safe to call twice."""
        self._stop.set()
        try:
            if self.name is not None:
                name, self.name = self.name, None
                _remove_container(name)
        finally:
            if self._dir is not None:
                shutil.rmtree(self._dir, ignore_errors=True)
                self._dir = None


def dyalog_version(box: Sandbox) -> str:
    """The container interpreter's version field, e.g. "21.0.54424.0"."""
    out, _ = box.run(
        {"version.apls": "⎕←2⊃#⎕WG'APLVersion'\n⎕OFF\n"},
        ["/cb/version.apls"],
        timeout=60,
    )
    return out.strip()


def run_page(
    blocks: list[RunBlock], box: Sandbox, timeout: float = 60.0
) -> tuple[dict[int, list[LineResult]], str | None]:
    """Run each block's input lines in `box` with one fresh interpreter.

    Results are keyed by block id; blocks not reached are absent. The second
    value is "timeout" if the run timed out, "exited" if the interpreter
    stopped before the last line, else None.
    """
    token = secrets.token_hex(8)
    request = {
        "pw": 80,
        "token": token,
        "blocks": [
            {"id": b.id, "style": b.style, "lines": b.lines} for b in blocks
        ],
    }
    req = f"request-{token}.json"
    out, timed_out = box.run(
        {req: json.dumps(request, ensure_ascii=False)},
        ["DYALOG_INITSESSION=1", f"/cb/{HARNESS.name}", f"/cb/{req}"],
        timeout,
    )
    results, done = _parse_output(out, token)
    expected = sum(len(b.lines) for b in blocks)
    if timed_out:
        return results, "timeout"
    return results, "exited" if done < expected else None


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
        # Harness artefacts: errors and the stack show the harness.
        "⎕DM", "⎕EM", "⎕EN", "⎕STACK", "⎕XSI", "⎕NSI", "⎕RSI",
    }
)
# Results that depend on the machine: its user and screen. (File reads are
# judged: the container starts empty, so a page reads what it wrote.)
ENVIRONMENT = frozenset({"⎕FLIB", "⎕TCNUMS", "⎕WSID", "⎕AN", "⎕SD"})

# System names allowed to run in the Docker jail (Dyalog 21.0). The jail has
# no network and no writable place outside /work, /tmp and the home
# directory, and vanishes with the page, so files, workspaces, .NET, shell
# commands and ⎕NA may run. Any other ⎕ name is blocked, so a name this list
# does not know (a new system function, a typo, a Classic ⎕U name) never
# runs. Left out on purpose, because they break or escape the run rather
# than touch the host: ⎕SE and the session (⎕ED ⎕SM ⎕SR ⎕PFKEY), shared
# variables (⎕SV*), the GUI (⎕W* ⎕NQ ⎕DQ), ⎕CLEAR ⎕LOAD ⎕OFF, ⎕ARBIN and
# ⎕ARBOUT, ⎕CS and ⎕PATH (they move or widen name lookup, towards ⎕SE),
# ⎕TGET ⎕TKILL (they can hang or kill the harness thread), and ⎕RSI ⎕NSI
# ⎕XSI ⎕STACK, which hand user code a way back into the harness in ⎕SE.
# ⎕SAVE is left out too: after it, every later line's execute (85⌶) fails
# with error 85, so nothing more on the page would show.
# Volatile and environment names stay here: those blocks run, and judging
# then reports them as not run.
SAFE_QUADS = frozenset(
    "⎕" + n
    for n in (
        "A AI AN AT ATX AV AVU BASE C CLASS CR CT D DCT DF DIV DL DM DMX DR "
        "DT EM EN EX FMT FR FX INSTANCES IO JSON KL LC "
        "LOCK LX ML MONITOR NC NL NR NS NULL OPT "
        "OR PP PROFILE PW R REFS RL RTL S SD SHADOW SI SIGNAL SIZE SRC STATE "
        "STOP TALLOC TC TCNUMS THIS TID TNAME TNUMS TPOOL TRACE TRAP TREQ TS "
        "TSYNC UCS VFI VGET VR VSET WA WSID XML "
        # Native files.
        "NAPPEND NCOPY NCREATE NDELETE NERASE NEXISTS NGET NINFO NLOCK NMOVE "
        "NNAMES NNUMS NPARTS NPUT NREAD NRENAME NREPLACE NRESIZE NSIZE NTIE "
        "NUNTIE NXLATE MKDIR CSV MAP "
        # Component files.
        "FAPPEND FAVAIL FCHK FCOPY FCREATE FDROP FERASE FHIST FHOLD FLIB "
        "FNAMES FNUMS FPROPS FRDAC FRDCI FREAD FRENAME FREPLACE FRESIZE FSIZE "
        "FSTAC FSTIE FTIE FUNTIE "
        # Workspaces and scripts, external variables.
        "FIX CY EXPORT XT "
        # .NET, shell commands and native code.
        "USING NEW EXCEPTION SH CMD SHELL NA"
    ).split()
)
# I-beams known to be pure (no files, processes or workspace changes). 85⌶
# (execute) is handled like ⍎.
PURE_IBEAMS = frozenset({8, 181, 201, 219, 220, 1200, 1500, 5581, 7162, 16808})
_IBEAM_NUM_RE = re.compile(r"(?<![\w∆⍙_.¯])(\d+)\s*$")
_SAFE_COMMANDS = frozenset({"box", "boxing", "display", "disp", "rows"})

_QUAD_NAME_RE = re.compile(r"⎕[A-Za-z0-9_]*")
_NAME_CHAR_RE = re.compile(r"[A-Za-z_∆⍙⍺⍵⎕#]")
# What may follow the literal argument of ⍎, ⎕FX or 85⌶: the end of the
# statement or a closing bracket. Anything else (`,`, a name, `¨`) means the
# argument is computed.
_ARG_END = frozenset(")]};⋄")


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


def _string_contents(text: str) -> list[str]:
    """The contents of each '…' string in a comment-free line, '' undoubled.

    An unterminated string runs to the end of the line.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] != "'":
            i += 1
            continue
        i += 1
        buf: list[str] = []
        while i < len(text):
            if text[i] == "'":
                if text[i + 1 : i + 2] == "'":
                    buf.append("'")
                    i += 2
                    continue
                break
            buf.append(text[i])
            i += 1
        out.append("".join(buf))
        i += 1
    return out


def _literal_end(text: str, i: int) -> int | None:
    """Index after the '…' string starting at text[i], or None if none does."""
    if text[i : i + 1] != "'":
        return None
    i += 1
    while i < len(text):
        if text[i] == "'":
            if text[i + 1 : i + 2] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return None


def _literal_arg(text: str, i: int, strand: bool) -> bool:
    """True if the argument starting at text[i] is a literal string.

    With `strand`, a strand of literal strings ('a' 'b') also counts. The
    literal must be followed by the end of the statement or a closing bracket.
    """
    n = 0
    while True:
        while i < len(text) and text[i] == " ":
            i += 1
        end = _literal_end(text, i)
        if end is None:
            break
        n += 1
        i = end
        if not strand:
            break
    while i < len(text) and text[i] == " ":
        i += 1
    return n > 0 and (i == len(text) or text[i] in _ARG_END)


# Before ⍎, these mean ⍎ is monadic: the start of a statement or an
# expression, a function or a monadic operator.
_MONADIC_BEFORE = frozenset(
    "←⋄{([;:"
    "+-×÷*⍟⌹○!?|⌈⌊⊥⊤⊣⊢=≠≤<>≥≡≢∨∧⍲⍱↑↓⊂⊃⊆⌷⍋⍒⍳⍸∊⍷∪∩~,⍪⍴⌽⊖⍉⍎⍕⊇"
    "¨⍨/⌿\\⍀&⌸}"
)
_PLAIN_NAME_END_RE = re.compile(r"(?<![\w∆⍙_.⎕¯#])[A-Za-z_∆⍙][\w∆⍙¯]*$")


def _plain_left(text: str, code: str, i: int) -> bool:
    """True unless ⍎ at code[i] is dyadic with a computed left argument.

    A dyadic ⍎ may take a plain name, # or a literal string on its left;
    anything else (a parenthesised expression, a dotted or system name, ⍺,
    a dyadic operator binding a left argument) could be a ref into ⎕SE.
    """
    if text[:i].rstrip(" ").endswith("'"):
        return True  # a literal string (its content is checked as code)
    before = code[:i].rstrip(" ")
    if not before or before[-1] in _MONADIC_BEFORE:
        return True
    if before.endswith("#"):
        return not re.search(r"[#.\w∆⍙]#$", before)
    return bool(_PLAIN_NAME_END_RE.search(before)) and not before[-1].isdigit()


def _executes_computed(text: str, code: str) -> bool:
    """True if ⍎, ⎕FX or 85⌶ in code (outside strings) gets a computed argument."""
    for m in re.finditer("⍎", code):
        if not _literal_arg(text, m.end(), strand=False):
            return True
        if not _plain_left(text, code, m.start()):
            return True
    for m in re.finditer(r"⎕FX(?![A-Za-z0-9_])", code, re.I):
        if not _literal_arg(text, m.end(), strand=True):
            return True
    for m in re.finditer("⌶", code):
        num = _IBEAM_NUM_RE.search(code[: m.start()])
        if num and int(num.group(1)) == 85:
            j = m.end()
            if code[j : j + 1] == ")":
                j += 1
            if not _literal_arg(text, j, strand=False):
                return True
    return False


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
    # A unique native file name is random.
    if "⎕NCREATE" in found and re.search(r"⍠.*'unique'", _strip_comment(line), re.I):
        return True
    # ⎕DT is volatile only when it converts the current time ('Z').
    return "⎕DT" in found and "'Z'" in _strip_comment(line)


# Differences these cause come from the platform (Linux in the container,
# not the Windows or macOS of the guide's examples): shell and native code,
# .NET, file metadata and line endings, and Windows paths.
_PLATFORM_QUADS = frozenset(
    {"⎕SH", "⎕CMD", "⎕SHELL", "⎕NA", "⎕USING", "⎕NEW", "⎕NINFO", "⎕NPUT",
     "⎕NAPPEND", "⎕NREPLACE"}
)
_DOTNET_NL_RE = re.compile(r"[\w∆⍙]\.(?:\(.*)?⎕NL(?![A-Za-z0-9_])", re.I)
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\|\\\\")


def _is_platform(line: str) -> bool:
    """True if what the line prints may depend on the platform."""
    text = _strip_comment(line)
    code = _code_only(text)
    found = {m.upper() for m in _QUAD_NAME_RE.findall(code)}
    return bool(
        found & _PLATFORM_QUADS
        or _DOTNET_NL_RE.search(code)
        or _WINDOWS_PATH_RE.search(text)
    )


def _is_environment(line: str) -> bool:
    found = {m.upper() for m in _QUAD_NAME_RE.findall(_code_only(line))}
    return bool(found & ENVIRONMENT)


def _top_level_diamond(line: str) -> bool:
    """True if ⋄ separates statements (outside strings, comments, brackets)."""
    depth = 0
    for ch in _code_only(line):
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth = max(0, depth - 1)
        elif ch == "⋄" and depth == 0:
            return True
    return False


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


def has_side_effect(line: str, nested: bool = False) -> bool:
    """True if an input line may have a side effect.

    Allowlist-based: every ⎕ name must be in SAFE_QUADS, every I-beam in
    PURE_IBEAMS (or 85⌶ with a literal argument), ⍎ and ⎕FX may only take
    literal strings, and dyadic ⍎ only a plain name, # or a literal string
    on its left.
    Every string on the line is checked by the same rules as if it were code
    (it may be executed by ⍎, ⎕FX, 85⌶ or a ⎕TRAP action), so a string that
    merely mentions a facility is an accepted false positive. `nested` marks
    such string content: there `]` is not a user command and `)` starts a
    system command only when a name follows.
    """
    text = _strip_comment(line)
    code = _code_only(text)
    if not nested and text.lstrip().startswith("]"):
        parts = text.lstrip()[1:].split()
        if not parts or parts[0].lower() not in _SAFE_COMMANDS:
            return True
        # An allowed command still evaluates its argument: check the rest.
        cmd = text.lstrip()[1:].lstrip()[len(parts[0]) :]
        return has_side_effect(cmd)
    for st in _statements(text):
        s = st.lstrip()
        if s.startswith(")") and (not nested or re.match(r"\)\s*[A-Za-z]", s)):
            return True
    for m in _QUAD_NAME_RE.finditer(code):
        name = m.group().upper()
        if name == "⎕":
            # Bare ⎕ is evaluated input unless it is the ⎕← output.
            if not re.match(r"\s*←", code[m.end() :]):
                return True
        elif name not in SAFE_QUADS:
            return True
    for m in re.finditer("⌶", code):
        num = _IBEAM_NUM_RE.search(code[: m.start()])
        if not num or int(num.group(1)) not in PURE_IBEAMS | {85}:
            return True
    if _executes_computed(text, code):
        return True
    # A ⎕TRAP 'E' action is executed: it must not be built at run time.
    for st in _statements(code):
        if re.search("⎕TRAP", st, re.I):
            rest = re.sub("⎕TRAP", "", st, flags=re.I)
            if _NAME_CHAR_RE.search(rest):
                return True
    return any(has_side_effect(s, nested=True) for s in _string_contents(text))


def expected_outputs(block: Block, cls: Classification) -> list[list[str]]:
    """Printed output lines (verbatim) after each input line."""
    groups: list[list[str]] = []
    for raw, role in zip(block.body, cls.roles):
        if role == "input":
            groups.append([])
        elif role == "cont":
            continue
        elif groups and not raw.startswith("⍝"):  # annotations are not output
            groups[-1].append(raw)
    return groups


def _input_texts(block: Block, cls: Classification) -> list[str]:
    """Each input's text without the session indent; a typed definition's
    lines are joined with newlines (one input unit)."""
    texts: list[str] = []
    for raw, role in zip(block.body, cls.roles):
        text = strip_indent(raw.rstrip("\r"))
        if role == "input":
            texts.append(text)
        elif role == "cont":
            texts[-1] += "\n" + text
    return texts


def _normalise(lines: list[str]) -> list[str]:
    out = [ln.rstrip() for ln in lines]
    while out and not out[-1]:
        out.pop()
    return out


def _session(inputs: list[str], results: list[LineResult]) -> list[str]:
    out: list[str] = []
    for text, res in zip(inputs, results):
        out.extend("      " + ln for ln in text.split("\n"))
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
    stop: str | None,
) -> tuple[str, str | None, str | None]:
    """Decide `(verdict, reason, actual)` for a block.

    Rules, in order:
    1. block not runnable: not-run with the classification's reason;
    2. no results, or fewer than input lines, and timed out: timeout
       (no results and not timed out: not-run/not-run);
       fewer results than input lines without timeout: not-run/incomplete;
    3. any skipped line: not-run with the skipped value as reason;
    4. a volatile facility in an input line: not-run/volatile; a
       machine-dependent one: not-run/environment (either: not-run/
       suspect-input if a line with no printed output printed something);
    5. an error the printed output does not show: not-run/suspect-input
       (SYNTAX/VALUE ERROR, no prior unrun block), dependency (prior
       unrun block) or error;
    6. every line's output equal after normalisation: match (reason
       annotation if the block has column-0 ⍝ lines, which are not compared);
    7. otherwise: differs, with the first reason that applies:
       suspect-input (a line with no printed output printed something),
       after-unrun (an earlier block on the page did not run), platform (a
       line uses shell, native code, .NET, file metadata or writes, or a
       Windows path), diamond (an
       input line has statements the harness runs one by one), annotation.

    `actual` is the session-style actual output (each input at six-space
    indent, then its output, newline-terminated) for differs and for the
    not-run reasons error, dependency and suspect-input; else None. A
    suspect line is one with no printed output that printed something
    (not an error line, not a user command).
    """
    if not cls.runnable:
        return "not-run", cls.reason, None
    inputs = _input_texts(block, cls)
    if results is None:
        return _stopped(stop) if stop else ("not-run", "not-run", None)
    if len(results) < len(inputs):
        return _stopped(stop) if stop else ("not-run", "incomplete", None)
    for res in results:
        if res.skipped:
            return "not-run", res.skipped, None
    expected = expected_outputs(block, cls)
    actual = "".join(ln + "\n" for ln in _session(inputs, results))
    # A line with no printed output printed something: it may be a folded
    # output row read as input. (A user command line cannot be output.)
    suspect = any(
        not res.error
        and not text.lstrip().startswith("]")
        and not _normalise(exp)
        and _normalise(res.output)
        for text, exp, res in zip(inputs, expected, results)
    )
    for unjudged, test in (("volatile", _is_volatile), ("environment", _is_environment)):
        if any(test(t) for t in inputs):
            return ("not-run", "suspect-input", actual) if suspect else ("not-run", unjudged, None)

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
        return "match", ("annotation" if cls.annotation else None), None
    if suspect:
        reason = "suspect-input"
    elif prior_unrun:
        reason = "after-unrun"
    elif any(_is_platform(t) for t in inputs):
        reason = "platform"
    elif any(_top_level_diamond(t) for t in inputs):
        reason = "diamond"
    elif cls.annotation:
        reason = "annotation"
    else:
        reason = None
    return "differs", reason, actual


def _stopped(stop: str) -> tuple[str, str | None, None]:
    return "timeout", ("exited" if stop == "exited" else None), None


class LogError(Exception):
    """The log could not be written faithfully."""


_HEADER = (
    "# Code-blocks review log. Review each entry and set `decision` to\n"
    "# `apply` or `skip` (it starts as `pending`); only `apply` entries are\n"
    "# applied. Text fields are exact; do not re-indent or strip them.\n"
    "# Fields, verdicts, reasons and splits: the docstring of\n"
    "# tools/cutover/code-blocks.py.\n"
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


DECISIONS = ("pending", "apply", "skip")


def read_log(path: Path) -> tuple[dict, list[dict]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for e in data["entries"]:
        if e.get("decision") not in DECISIONS:
            raise LogError(
                f"{path}: {e.get('page')} (block at page line {e.get('line')}): "
                f"decision {e.get('decision')!r} is not one of {', '.join(DECISIONS)}"
            )
    return data["meta"], data["entries"]


_SPLITS = frozenset({"confirmed", "unambiguous"})


def carry_decisions(
    old: list[dict], new: list[dict], stats: Counter | None = None
) -> list[dict]:
    """Keep earlier decisions for blocks whose proposal is unchanged.

    Entries match by page and sha256 (duplicates in order). A decision is
    kept when `proposed` is equal and the split is equal or both splits
    propose (confirmed or unambiguous: a --no-run rerun or a timeout turns
    confirmed into unambiguous without changing the proposal). `stats`, if
    given, counts the decisions other than pending that were `reset` (the
    block is unchanged but its proposal changed) or `dropped` (the block
    changed or is gone), and those carried onto a block that matched before
    and does not now (`now_differs`).
    """
    prior: dict[tuple, list[dict]] = {}
    for e in old:
        prior.setdefault((e["page"], e["sha256"]), []).append(e)
    out = []
    for entry in new:
        entry = dict(entry)
        queue = prior.get((entry["page"], entry["sha256"]))
        before = queue.pop(0) if queue else None
        if before is None:
            entry["decision"] = "pending"
        elif before["proposed"] == entry["proposed"] and (
            before["split"] == entry["split"]
            or {before["split"], entry["split"]} <= _SPLITS
        ):
            entry["decision"] = before["decision"]
            if (
                stats is not None
                and before["decision"] != "pending"
                and before.get("verdict") == "match"
                and entry.get("verdict") != "match"
            ):
                stats["now_differs"] += 1
        else:
            entry["decision"] = "pending"
            if stats is not None and before["decision"] != "pending":
                stats["reset"] += 1
        out.append(entry)
    if stats is not None:
        stats["dropped"] += sum(
            e["decision"] != "pending" for q in prior.values() for e in q
        )
    return out


# --- Defining shown values (--define-shown) --------------------------------

_SHOWN_NAME_RE = re.compile(r"[A-Za-z_∆⍙][A-Za-z0-9_∆⍙¯]*")
_NUM = r"¯?(?:\d+(?:\.\d*)?|\.\d+)(?:E¯?\d+)?"
_NUMBER_RE = re.compile(rf"{_NUM}(?:J{_NUM})?")
_BOX_DRAWING_RE = re.compile("[\u2500-\u257f]")


class Unsupported(ValueError):
    """A shown value that --define-shown does not write in array notation."""


def aplan(name: str, rows: list[str]) -> tuple[str, list[str]]:
    """Array notation defining `name` as the value printed as `rows`.

    Returns (one line, with ⋄ between rows; the proposal's lines, rows
    aligned under the first element after `[`). One row of numbers: a
    numeric vector, single-spaced (a scalar for one number); several: a
    numeric matrix, each row spaced as printed.
    One row of other text: a character vector; several rows, not all
    numeric: a character matrix (rows without trailing spaces; array
    notation pads them). Raises Unsupported for anything else.
    """
    rows = _normalise(rows)
    if not rows:
        raise Unsupported("no output")
    if any(_BOX_DRAWING_RE.search(r) for r in rows):
        raise Unsupported("box drawing")
    if any(not r.strip() for r in rows):
        raise Unsupported("blank line inside")
    numeric = []
    for r in rows:
        kinds = {bool(_NUMBER_RE.fullmatch(t)) for t in r.split()}
        if len(kinds) > 1:
            raise Unsupported(f"numbers and text in one row: {r.strip()}")
        numeric.append(kinds == {True})
    if all(numeric) and len(rows) == 1:
        items = [" ".join(rows[0].split())]
    elif all(numeric):
        items = rows  # each row's spacing as printed
    else:
        items = ["'" + r.replace("'", "''") + "'" for r in rows]
    head = f"{name}←"
    if len(items) == 1:
        return head + items[0], [head + items[0]]
    pad = " " * (len(head) + 1)
    lines = [f"{head}[{items[0]}"] + [pad + it for it in items[1:]]
    lines[-1] += "]"
    return f"{head}[{' ⋄ '.join(items)}]", lines


@dataclass(frozen=True)
class Shown:
    """A bare name whose value a block shows but the page never defines."""

    block: int  # index of the block on the page
    line: int  # index among the block's input lines
    name: str
    oneline: str | None  # the definition on one line (⋄ between rows)
    lines: tuple[str, ...]  # the definition as proposed; () if unsupported
    unsupported: str | None  # why there is no definition


def _assigns(line: str, name: str) -> bool:
    """The line assigns `name` (`name←`) or is a ∇ line naming it."""
    if re.search(rf"(?<![\w∆⍙_.⎕]){re.escape(name)}\s*←", _code_only(line)):
        return True
    return line.strip().startswith("∇") and _uses_name(line.strip(), name)


def shown_candidates(
    blocks: list[Block],
    classes: dict[int, Classification],
    results: dict[int, list[LineResult]],
    suspect: frozenset[int] = frozenset(),
) -> dict[int, list[Shown]]:
    """The page's shown-but-undefined names, by block index.

    An input line is a candidate when it is exactly a name N (trailing
    whitespace aside), no earlier line of an `apl` block on the page assigns
    N (N←, or a ∇ line naming it), N is not an earlier candidate, the run
    (`results`) gave `VALUE ERROR: Undefined name: N` for it, and its printed
    output is not an error. A candidate is returned with `unsupported` set
    when aplan cannot write its printed output, an output row mentions N (an
    input line read as output, say), a column-0 ⍝ note is among its output,
    the block is ambiguous, or the block's plain run was suspect-input (its
    index is in `suspect`).
    """
    found: dict[int, list[Shown]] = {}
    seen: set[str] = set()
    earlier: list[str] = []
    for i, b in enumerate(blocks):
        cls = classes.get(i)
        res = results.get(i)
        if cls is None or cls.kind == "listing" or not cls.runnable or res is None:
            earlier.extend(b.body)
            continue
        printed = expected_outputs(b, cls)
        # Each input line's following lines, ⍝ notes included.
        spans: list[list[str]] = []
        for raw, role in zip(b.body, cls.roles):
            if role == "input":
                spans.append([])
            elif spans:
                spans[-1].append(raw)
        k = -1
        for raw, role in zip(b.body, cls.roles):
            if role == "input":
                k += 1
                name = strip_indent(raw).rstrip()
                if (
                    _SHOWN_NAME_RE.fullmatch(name)
                    and name not in seen
                    and k < len(res)
                    and res[k].error
                    and res[k].output[:1] == [f"VALUE ERROR: Undefined name: {name}"]
                    and not any(_ERROR_RE.match(ln.rstrip()) for ln in printed[k])
                    and not any(_assigns(ln, name) for ln in earlier)
                ):
                    seen.add(name)
                    try:
                        if cls.kind == "ambiguous":
                            raise Unsupported(f"ambiguous block: {cls.reason}")
                        if i in suspect:
                            raise Unsupported("the block's run was suspect-input")
                        if any(ln.startswith("⍝") for ln in spans[k]):
                            # The rewrite drops the output: a note would go too.
                            raise Unsupported("annotation in the output")
                        if any(_uses_name(ln, name) for ln in printed[k]):
                            raise Unsupported(f"the output mentions {name}")
                        oneline, lines = aplan(name, printed[k])
                        shown = Shown(i, k, name, oneline, tuple(lines), None)
                    except Unsupported as e:
                        shown = Shown(
                            i, k, name, None, (), f"define-shown: unsupported ({e})"
                        )
                    found.setdefault(i, []).append(shown)
            earlier.append(raw)
    return found


def _units(inputs: list[str], shown: list[Shown]) -> list[str | list[str]]:
    """Input units to send: each shown name's line becomes its definition as
    proposed (a multi-line unit if it spans lines), followed by a hidden
    check, the bare name."""
    at = {s.line: s for s in shown}
    units: list[str | list[str]] = []
    for k, text in enumerate(inputs):
        s = at.get(k)
        if s is None:
            units.append(list(text.split("\n")) if "\n" in text else text)
        else:
            units.append(s.lines[0] if len(s.lines) == 1 else list(s.lines))
            units.append(s.name)
    return units


def _unhide(
    results: list[LineResult], shown: list[Shown]
) -> tuple[list[LineResult], dict[str, LineResult]]:
    """Split a rewritten block's results into the judged ones and the
    hidden checks, by name."""
    checks = {
        s.line + n + 1: s.name
        for n, s in enumerate(sorted(shown, key=lambda s: s.line))
    }
    judged: list[LineResult] = []
    hidden: dict[str, LineResult] = {}
    for pos, r in enumerate(results):
        if pos in checks:
            hidden[checks[pos]] = r
        else:
            judged.append(r)
    return judged, hidden


def _rewritten(b: Block, cls: Classification, shown: list[Shown]) -> Block:
    """The block with each shown name's line replaced by its one-line
    definition and the name's printed output removed. It stands in for the
    rewrite when judging and splitting (_proposal restores the line breaks)."""
    at = {s.line: s for s in shown}
    body: list[str] = []
    k = -1
    drop = False
    for raw, role in zip(b.body, cls.roles):
        if role == "input":
            k += 1
            drop = k in at
            body.append("      " + at[k].oneline if drop else raw)
        elif not drop:
            body.append(raw)
    text = "```apl\n" + "".join(ln + "\n" for ln in body) + "```\n"
    return Block(b.page, b.line, b.sha256, text, body, b.nested)


def _proposal(b: Block, cls: Classification, shown: list[Shown]) -> str:
    """split_block, with each one-line definition in its proposed form."""
    text = split_block(b, cls)
    for s in shown:
        if len(s.lines) > 1:
            one = f"\n{s.oneline}\n"
            if text.count(one) != 1:
                raise SplitError(f"{b.page}:{b.line}: cannot place {s.name}")
            text = text.replace(one, "\n" + "\n".join(s.lines) + "\n")
    return text


def _disproof(
    s: Shown,
    check: LineResult | None,
    printed: list[str],
    verdict: str,
    reason: str | None,
) -> str | None:
    """Why the definition is not proved, or None: the hidden check must
    display the printed output and the rewritten block must match."""
    if check is None:
        return "the check did not run"
    if check.error or _normalise(check.output) != _normalise(printed):
        return f"{s.name} displays otherwise: " + " / ".join(_normalise(check.output))
    if verdict != "match":
        return f"rewritten block: {verdict}" + (f" ({reason})" if reason else "")
    return None


# --- Command line -----------------------------------------------------------

_BOX_RE = re.compile(r"\]box(ing)?\b", re.I)


def _run_plan(
    blocks: list[Block],
    classes: dict[int, Classification],
    box: "Sandbox | None",
    timeout: float,
    applied: dict[int, list["Shown"]],
) -> tuple[dict[int, list[LineResult]], str | None, set[int], dict[int, int]]:
    """Run a page: (results, stop, side-effect blocks, units sent per block).

    `applied` maps a block index to the shown names whose definitions replace
    their bare-name lines (see _units). Without a container nothing runs.
    """
    results: dict[int, list[LineResult]] = {}
    stop: str | None = None
    side: set[int] = set()
    sent: dict[int, int] = {}  # block index -> number of units sent
    if not box:
        return results, stop, side, sent
    boxed = False
    to_run: list[RunBlock] = []
    for i, b in enumerate(blocks):
        cls = classes.get(i)
        if cls is None or cls.kind == "listing" or not cls.runnable:
            continue
        units = _units(_input_texts(b, cls), applied.get(i, []))
        flat = [ln for u in units for ln in ([u] if isinstance(u, str) else u)]
        if any(has_side_effect(t) for t in flat):
            side.add(i)
            continue
        boxed = boxed or any(_BOX_RE.match(t) for t in flat)
        to_run.append(RunBlock(i, None if boxed else cls.style, units))
        sent[i] = len(units)
    if to_run:
        results, stop = run_page(to_run, box, timeout)
    return results, stop, side, sent


def _judge_page(
    blocks: list[Block],
    classes: dict[int, Classification],
    box: "Sandbox | None",
    run: tuple[dict[int, list[LineResult]], str | None, set[int], dict[int, int]],
    applied: dict[int, list["Shown"]],
) -> tuple[list[dict], int, dict[tuple[int, str], str]]:
    """Entries for a page's run, the listing count, and the applied
    definitions that failed their proof, as {(block index, name): why}."""
    results, stop, side, sent = run
    entries: list[dict] = []
    failed: dict[tuple[int, str], str] = {}
    listings = 0
    prior_unrun = False
    hung_seen = False
    ran: dict[int, list[LineResult]] = {}  # judged results, hidden checks removed
    for i, b in enumerate(blocks):
        cls = classes.get(i)
        if cls is None:
            prior_unrun = True
            entries.append(_entry(b, "not-run", "nested", "none", False, None, None))
            continue
        if cls.kind == "listing":
            listings += 1
            continue
        shown = applied.get(i, [])
        jb, jcls, res = b, cls, results.get(i)
        hidden: dict[str, LineResult] = {}
        if shown:
            jb = _rewritten(b, cls, shown)
            jcls = classify(jb)
            if res is not None:
                res, hidden = _unhide(res, shown)
        if res is not None:
            ran[i] = res
        if i in side:
            verdict, reason, actual = "not-run", "side-effect", None
        elif stop and i in sent and len(results.get(i, [])) < sent[i] and hung_seen:
            verdict, reason, actual = "not-run", "unreached", None
        elif box:
            if stop and i in sent and len(results.get(i, [])) < sent[i]:
                hung_seen = True  # the first block with a missing line
            verdict, reason, actual = judge(jb, jcls, res, prior_unrun, stop)
        else:
            verdict, reason, actual = judge(jb, jcls, None, prior_unrun, None)
        if reason is None and jcls.kind == "ambiguous":
            reason = jcls.reason
        printed = expected_outputs(b, cls)
        for s in shown:
            why = _disproof(s, hidden.get(s.name), printed[s.line], verdict, reason)
            if why:
                failed[(i, s.name)] = why
        split = split_kind(jcls, verdict, reason)
        proposed = None
        if split in ("confirmed", "unambiguous"):
            try:
                proposed = _proposal(jb, jcls, shown)
            except SplitError:
                split, reason, proposed = "ambiguous", "split-check", None
        if (cls.kind == "ambiguous" and not cls.runnable) or verdict == "not-run":
            prior_unrun = True
        entry = _entry(b, verdict, reason, split, cls.nbsp, proposed, actual)
        if shown:
            entry["rewrites"] = ["aplan"]
            entry["defines"] = [s.name for s in shown]
        entries.append(entry)
    for i, shown in applied.items():
        for s in shown:
            if (i, s.name) not in failed and (why := _later_uses(blocks, classes, ran, s)):
                failed[(i, s.name)] = why
    return entries, listings, failed


def _later_uses(
    blocks: list[Block],
    classes: dict[int, Classification],
    ran: dict[int, list[LineResult]],
    s: Shown,
) -> str | None:
    """Why the page's later input lines do not back the definition, or None.

    Every later input line that uses the name and ran must not raise an
    error the page does not show, and at least one must have run.
    """
    used = False
    for j in range(s.block, len(blocks)):
        res = ran.get(j)
        if res is None:
            continue
        b, cls = blocks[j], classes[j]
        printed = expected_outputs(b, cls)
        for k, text in enumerate(_input_texts(b, cls)):
            if (j == s.block and k <= s.line) or k >= len(res):
                continue
            if not _uses_name(text, s.name):
                continue
            used = True
            shows_error = any(_ERROR_RE.match(ln.rstrip()) for ln in printed[k])
            if res[k].error and not shows_error:
                head = res[k].output[0] if res[k].output else "error"
                return f"a later use fails: line {b.line}: {text.strip()}: {head}"
    return None if used else f"nothing on the page uses {s.name}"


def _process_page(
    page: str,
    markdown: str,
    box: "Sandbox | None",
    timeout: float,
    define_shown: bool = False,
    stats: Counter | None = None,
) -> tuple[list[dict], int, int]:
    """Entries for one page, plus (block count, listing count).

    With `define_shown` (and a container), shown-but-undefined names get their
    definitions proved by further runs (see "Defining shown values" in the
    design); `stats`, if given, counts accepted, rejected and unsupported.
    """
    originals = find_blocks(page, markdown)
    norms = [normalise(b) for b in originals]
    blocks, classes = _normalised_page(originals, norms, set())
    run = _run_plan(blocks, classes, box, timeout, {})
    # Prove the + rewrites (see "Normalising input" in the design): revert
    # the ones whose line did not display the printed output, and run again.
    reverted = _unproved_plus(blocks, classes, norms, run[0]) if box else {
        (i, k): "not run" for i, n in enumerate(norms) for k in n.plus
    }
    if reverted:
        blocks, classes = _normalised_page(originals, norms, set(reverted))
        if box:
            run = _run_plan(blocks, classes, box, timeout, {})
    applied: dict[int, list[Shown]] = {}
    rejected: dict[int, dict[str, str]] = {}
    if define_shown and box:
        plain = _judge_page(blocks, classes, box, run, {})[0]
        at = {b.line: i for i, b in enumerate(blocks)}
        suspect = frozenset(
            at[e["line"]] for e in plain if e["reason"] == "suspect-input"
        )
        for i, found in shown_candidates(blocks, classes, run[0], suspect).items():
            for s in found:
                if s.unsupported:
                    rejected.setdefault(i, {})[s.name] = s.unsupported
                else:
                    applied.setdefault(i, []).append(s)
        # Prove the definitions in a run with all of them applied; drop the
        # failures and prove the rest once more. If that still fails, none
        # is applied and the first run stands.
        for _ in range(2):
            if not applied:
                break
            trial = _run_plan(blocks, classes, box, timeout, applied)
            failed = _judge_page(blocks, classes, box, trial, applied)[2]
            if not failed:
                run = trial
                break
            for (i, name), why in failed.items():
                rejected.setdefault(i, {})[name] = why
            applied = {
                i: kept
                for i, found in applied.items()
                if (kept := [s for s in found if (i, s.name) not in failed])
            }
        else:
            for i, found in applied.items():
                for s in found:
                    rejected.setdefault(i, {})[s.name] = (
                        "not proved: another definition on the page failed"
                    )
            applied = {}
    entries, listings, _ = _judge_page(blocks, classes, box, run, applied)
    lines = {b.line: i for i, b in enumerate(blocks)}
    for e in entries:
        i = lines[e["line"]]
        why = rejected.get(i)
        if why:
            e["rewrite_rejected"] = why
        done = _applied_rewrites(originals[i], blocks[i], norms[i])
        if done:
            e["rewrites"] = done + e.get("rewrites", [])
            if e["proposed"] is None:  # no split: the rewritten block as one
                e["proposed"] = _fence("apl", [ln.rstrip("\r") for ln in blocks[i].body])
        for (j, k), text in reverted.items():
            if j == i:
                key = originals[i].body[k].strip()
                e.setdefault("rewrite_rejected", {})[key] = f"shy: + kept: {text}"
    if stats is not None:
        stats["accepted"] += sum(len(found) for found in applied.values())
        for why in rejected.values():
            for text in why.values():
                unsupported = text.startswith("define-shown: unsupported")
                stats["unsupported" if unsupported else "rejected"] += 1
    return entries, len(blocks), listings


def _normalised_page(
    originals: list[Block], norms: list[Normalised], reverted: set[tuple[int, int]]
) -> tuple[list[Block], dict[int, Classification]]:
    """The page's blocks as normalised, with the `reverted` (block, body
    line) + rewrites undone, and their classifications. A block whose
    definition keeps its numbers is not run (reason line-numbers)."""
    blocks: list[Block] = []
    for i, (b, n) in enumerate(zip(originals, norms)):
        body = list(n.block.body)
        for j, k in reverted:
            if j == i:
                body[k] = b.body[k]
        blocks.append(_with_body(b, body) if body != b.body else b)
    classes: dict[int, Classification] = {}
    for i, b in enumerate(blocks):
        if b.nested:
            continue
        cls = classify(b)
        if norms[i].keep and cls.kind != "listing":
            cls = dataclasses.replace(
                cls, kind="ambiguous", reason=norms[i].keep, runnable=False
            )
        classes[i] = cls
    return blocks, classes


def _unproved_plus(
    blocks: list[Block],
    classes: dict[int, Classification],
    norms: list[Normalised],
    results: dict[int, list[LineResult]],
) -> dict[tuple[int, int], str]:
    """The + rewrites (block, body line) not proved, with why: the rewritten
    line must have run without error and displayed its printed output."""
    out: dict[tuple[int, int], str] = {}
    for i, n in enumerate(norms):
        cls = classes.get(i)
        for k in n.plus:
            if cls is None or cls.roles[k] != "input":
                out[(i, k)] = "not an input line"
                continue
            idx = sum(1 for r in cls.roles[:k] if r == "input")
            res = results.get(i, [])
            printed = expected_outputs(blocks[i], cls)[idx]
            if idx >= len(res):
                out[(i, k)] = "not run"
            elif res[idx].error or _normalise(res[idx].output) != _normalise(printed):
                out[(i, k)] = "⎕← displays otherwise: " + " / ".join(
                    _normalise(res[idx].output)
                )
    return out


def _applied_rewrites(original: Block, block: Block, n: Normalised) -> list[str]:
    """The rewrites in effect in `block` (some + rewrites may be undone)."""
    done: list[str] = []
    if "unnumbered" in n.rewrites:
        done.append("unnumbered")
    if any(a != o for a, o in zip(block.body, original.body) if _SHY_RE.match(o)):
        done.append("shy")
    return done


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
        description="Review `apl` code fences: propose splits and run them. "
        "See the module docstring for the log format."
    )
    ap.add_argument(
        "--guide",
        default="language-reference-guide",
        help="the guide's directory under --docs (default: %(default)s)",
    )
    ap.add_argument(
        "--log",
        default="code-blocks.yml",
        help="review log to write; an existing one is copied to <log>.bak "
        "and its decisions carried over (default: %(default)s)",
    )
    ap.add_argument(
        "--page",
        metavar="GLOB",
        help="only pages whose path inside the guide matches GLOB; other "
        "pages' entries in the log are kept",
    )
    ap.add_argument(
        "--no-run",
        action="store_true",
        help="do not run examples; propose only splits that need no run",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=60,
        metavar="SECONDS",
        help="time limit for each page's interpreter (default: %(default)s)",
    )
    ap.add_argument(
        "--image",
        help="Docker image to run examples in (default: the image built from "
        "tools/cutover/docker/Dockerfile, built when missing)",
    )
    ap.add_argument(
        "--docs",
        default="zensical/docs",
        help="the documentation root; log paths are relative to it "
        "(default: %(default)s)",
    )
    ap.add_argument(
        "--define-shown",
        action="store_true",
        help="propose array-notation definitions for names a block shows "
        "but the page never defines, when a run proves them",
    )
    args = ap.parse_args(argv)
    # SIGTERM and SIGHUP would end Python without running `finally`, leaving
    # the run's container running: turn them into SystemExit instead.
    old = {
        sig: signal.signal(sig, _exit_on_signal)
        for sig in (signal.SIGTERM, signal.SIGHUP)
    }
    try:
        return _main(args)
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def _exit_on_signal(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


def _main(args: argparse.Namespace) -> int:

    image = None if args.no_run else prepare_image(args.image)
    if args.define_shown and not image:
        print(
            "note: --define-shown needs a run in Docker; no definitions proposed",
            file=sys.stderr,
        )

    docs = Path(args.docs)
    root = docs / args.guide
    if not root.is_dir():
        print(f"error: no guide directory {root}", file=sys.stderr)
        return 2
    pages = sorted(
        (p for p in root.rglob("*.md") if p.is_file()),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    if args.page:
        pages = [
            p for p in pages
            if fnmatch.fnmatch(p.relative_to(root).as_posix(), args.page)
        ]
    if not pages:
        what = f"matching {args.page!r} " if args.page else ""
        print(f"error: no pages {what}under {root}", file=sys.stderr)
        return 2

    entries: list[dict] = []
    total = listings = 0
    shown: Counter = Counter()
    # One container for the whole run, removed when the run ends however
    # it ends.
    with contextlib.ExitStack() as stack:
        box = stack.enter_context(Sandbox(image)) if image else None
        for p in pages:
            found, n, lst = _process_page(
                p.relative_to(docs).as_posix(),
                p.read_text(encoding="utf-8"),
                box,
                args.timeout,
                args.define_shown,
                shown,
            )
            entries += found
            total += n
            listings += lst
        version = dyalog_version(box) if box else None

    log = Path(args.log)
    stats: Counter = Counter()
    had_log = log.exists()
    if had_log:
        old = read_log(log)[1]
        if args.page:
            done = {p.relative_to(docs).as_posix() for p in pages}
            kept = [e for e in old if e["page"] not in done]
            replaced = carry_decisions(
                [e for e in old if e["page"] in done], entries, stats
            )
            entries = sorted(kept + replaced, key=lambda e: (e["page"], e["line"]))
        else:
            entries = carry_decisions(old, entries, stats)
        shutil.copy2(log, log.with_name(log.name + ".bak"))
    by_page: dict[str, Counter] = {}
    for e in entries:
        by_page.setdefault(e["page"], Counter())[e["verdict"]] += 1
    meta = {
        "guide": args.guide,
        "dyalog": version,
        # The container's image and platform; null without a run.
        **(image_meta(image) if image else dict.fromkeys(("image", "image_id", "platform"))),
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        # Verdict counts for each page with at least one differs.
        "pages": {
            page: dict(sorted(c.items()))
            for page, c in by_page.items()
            if c["differs"]
        },
    }
    write_log(log, meta, entries)

    print(f"Blocks: {total} total, {listings} listing (not logged)")
    print(f"Entries: {len(entries)} in {log}")
    if had_log:
        print(
            f"Decisions: {stats['reset']} reset to pending, "
            f"{stats['dropped']} dropped; carried, now differs: "
            f"{stats['now_differs']} (previous log in {log.name}.bak)"
        )
    for key in ("verdict", "split"):
        counts = Counter(e[key] for e in entries)
        print(f"By {key}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if args.define_shown:
        print(
            f"define-shown: {shown['accepted']} rewrites accepted, "
            f"{shown['rejected']} rejected, {shown['unsupported']} unsupported"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
