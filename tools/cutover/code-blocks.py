"""Review tool for `apl` code fences in the Markdown documentation.

It finds `apl` fences, proposes splitting session input from output, and
validates examples against Dyalog APL. This module holds the block finder.
The tool only reads the corpus; it never writes to it.
"""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

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
_CARET_RE = re.compile(r"^ {6,}[∧^ ]*[∧^][∧^ ]*$")
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
