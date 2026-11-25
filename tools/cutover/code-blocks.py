"""Review the `apl` code examples of a guide: propose splits, run them.

    uv run python tools/cutover/code-blocks.py [--guide language-reference-guide]
        [--log code-blocks.yml] [--page GLOB] [--no-run] [--timeout SECONDS]
        [--image IMAGE] [--docs zensical/docs]

The `apl` fences mix session input (the six-space indent) with output. For
each fence of the guide under --docs, the tool proposes splitting it into an
`apl` block of input followed by a `text` block of output, runs the input
in Docker (one locked-down container and interpreter per page, through
code-blocks-harness.apls) and compares what Dyalog prints with the printed
output. Examples never run on the host. It writes a YAML log for people to review entry by entry; the
approved entries are applied later, during the cutover. The tool only reads
the corpus; it never writes to it. Design:
design/superpowers/specs/2026-11-25-code-blocks-design.md.

--guide is the guide's directory under --docs, --log the log to write (an
existing one is copied to <log>.bak first), --page a glob, relative to the
guide, that limits the run to matching pages (their entries replace those
pages' entries in an existing log; other pages' entries are kept),
--no-run proposes splits without running anything, --timeout is the limit
in seconds for each page's container, --image the Docker image to run in.
The tool exits
with status 2, leaving the log alone, if the guide does not exist or no
page matches.

Docker: Docker must be running; without it the tool says so on stderr and
runs as --no-run. The default image is built from docker/Dockerfile next to
this file (Dyalog 21.0, pinned by digest, plus the .NET runtime) and tagged
code-blocks-dyalog:<first 12 hex of the Dockerfile's sha256>; it is built
when missing, the only time the network is used. An --image is pulled when
missing. Each page runs in a fresh container with no network, no
capabilities, no new privileges, a read-only root, at most 64 processes,
512 MB and one CPU; only /work (the working directory), /tmp and the home
directory are writable, as small tmpfs mounts, and the harness and request
are mounted read-only. Files a page writes persist across its blocks and
vanish with the container. A timed-out or interrupted container is killed
and removed. Results are those of Linux Dyalog in that image.

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

    del-definition  not run: a ∇ definition in session-editor notation
    script          not run: a :Class, :Namespace or :Interface script
    indent          not run: input at an odd indent
    nested          not run: the fence is indented (in a list, say)
    side-effect     not run: a side effect, or after ⎕USING (above)
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

Decisions are carried to the new log by page and sha256 (duplicate blocks
in order): an entry keeps its decision while `proposed` is unchanged and
its split is unchanged or both splits are confirmed or unambiguous;
otherwise it is reset to pending. The tool prints how many decisions were
reset, how many were dropped because their block changed or went away, and
how many were carried onto a block that matched before and does not now
("carried, now differs"), which deserve a second look.
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
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
    return Classification(kind, reason, roles, nbsp, runnable, style, annotation)


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
    lines: list[str]


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


def _container_argv(image: str, name: str, mount: str, args: list[str]) -> list[str]:
    """docker run argv: one locked-down, throw-away container.

    No network, no capabilities, a read-only root; only /work (the working
    directory), /tmp and the home directory are writable, as small tmpfs
    mounts that vanish with the container. `mount` (the harness and the
    request) is read-only at /cb.
    """
    return [
        DOCKER, "run", "--rm", "-i", "--name", name,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "64",
        "--memory", "512m", "--cpus", "1",
        "--tmpfs", "/work:rw,size=16m,mode=1777",
        "--tmpfs", "/tmp:rw,size=64m",  # the .NET runtime needs room here
        "--tmpfs", "/home/dyalog:rw,size=16m,uid=1000,gid=1000",
        "-w", "/work", "-v", f"{mount}:/cb:ro",
        "--entrypoint", "dyalogscript", image, *args,
    ]


class ContainerError(RuntimeError):
    """A container outlived its run."""


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _left(name: str) -> bool:
    out = _docker("ps", "-a", "-q", "--filter", f"name=^/{name}$", timeout=30)
    return bool(out.stdout.strip())


def _remove_container(name: str, force: bool) -> None:
    """Make sure the container `name` is gone; raise ContainerError if not.

    --rm removes a container asynchronously, so poll for a while.
    """
    if force:
        _docker("kill", name, timeout=30)
        _docker("rm", "-f", name, timeout=30)
    for _ in range(50):
        if not _left(name):
            return
        time.sleep(0.2)
    _docker("rm", "-f", name, timeout=30)
    if _left(name):
        raise ContainerError(f"container {name} is still there")


def _in_container(
    image: str, token: str, files: dict[str, str], args: list[str], timeout: float
) -> tuple[str, bool]:
    """Run dyalogscript `args` in a fresh container, `files` mounted at /cb.

    The container is named cb-<token>.

    Returns (stdout with CR/CRLF turned into LF, timed_out). On a time-out or
    any exception the container is killed and removed and the local docker
    client's process group is killed; either way the container is gone when
    this returns.
    """
    name = f"cb-{token}"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        os.chmod(tmp, 0o755)  # the container's user is uid 1000
        for fname, text in files.items():
            Path(tmp, fname).write_text(text, encoding="utf-8")
            os.chmod(Path(tmp, fname), 0o644)
        proc = subprocess.Popen(
            _container_argv(image, name, tmp, args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        timed_out = False
        clean = False
        try:
            try:
                out, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _docker("kill", name, timeout=30)
                try:
                    out, _ = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    _kill_group(proc.pid)
                    out, _ = proc.communicate()
            clean = not timed_out
        finally:
            if proc.poll() is None:
                _docker("kill", name, timeout=30)
            _kill_group(proc.pid)
            proc.wait()
            _remove_container(name, force=not clean)
    text = out.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n"), timed_out


def dyalog_version(image: str) -> str:
    """The container interpreter's version field, e.g. "21.0.54424.0"."""
    out, _ = _in_container(
        image,
        secrets.token_hex(8),
        {"version.apls": "⎕←2⊃#⎕WG'APLVersion'\n⎕OFF\n"},
        ["/cb/version.apls"],
        timeout=60,
    )
    return out.strip()


def run_page(
    blocks: list[RunBlock], image: str, timeout: float = 60.0
) -> tuple[dict[int, list[LineResult]], str | None]:
    """Run each block's input lines in one fresh container (one interpreter).

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
    out, timed_out = _in_container(
        image,
        token,
        {
            HARNESS.name: HARNESS.read_text(encoding="utf-8"),
            "request.json": json.dumps(request, ensure_ascii=False),
        },
        ["DYALOG_INITSESSION=1", f"/cb/{HARNESS.name}", "/cb/request.json"],
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
    # ⎕DT is volatile only when it converts the current time ('Z').
    return "⎕DT" in found and "'Z'" in _strip_comment(line)


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
        elif groups and not raw.startswith("⍝"):  # annotations are not output
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
       after-unrun (an earlier block on the page did not run), diamond (an
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


# --- Command line -----------------------------------------------------------

_BOX_RE = re.compile(r"\]box(ing)?\b", re.I)


def _process_page(
    page: str, markdown: str, image: str | None, timeout: float
) -> tuple[list[dict], int, int]:
    """Entries for one page, plus (block count, listing count)."""
    blocks = find_blocks(page, markdown)
    classes = {
        i: classify(b) for i, b in enumerate(blocks) if not b.nested
    }
    results: dict[int, list[LineResult]] = {}
    stop: str | None = None
    side: set[int] = set()
    sent: dict[int, int] = {}  # block index -> number of lines sent
    if image:
        boxed = False
        to_run: list[RunBlock] = []
        for i, b in enumerate(blocks):
            cls = classes.get(i)
            if cls is None or cls.kind == "listing":
                continue
            if not cls.runnable:
                continue
            lines = _input_texts(b, cls)
            if any(has_side_effect(t) for t in lines):
                side.add(i)
                continue
            boxed = boxed or any(_BOX_RE.match(t) for t in lines)
            to_run.append(RunBlock(i, None if boxed else cls.style, lines))
            sent[i] = len(lines)
        if to_run:
            results, stop = run_page(to_run, image, timeout)

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
        elif stop and i in sent and len(results.get(i, [])) < sent[i] and hung_seen:
            verdict, reason, actual = "not-run", "unreached", None
        elif image:
            if stop and i in sent and len(results.get(i, [])) < sent[i]:
                hung_seen = True  # the first block with a missing line
            verdict, reason, actual = judge(
                b, cls, results.get(i), prior_unrun, stop
            )
        else:
            verdict, reason, actual = judge(b, cls, None, prior_unrun, None)
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
    args = ap.parse_args(argv)

    image = None if args.no_run else prepare_image(args.image)

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
    for p in pages:
        found, n, lst = _process_page(
            p.relative_to(docs).as_posix(),
            p.read_text(encoding="utf-8"),
            image,
            args.timeout,
        )
        entries += found
        total += n
        listings += lst

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
        "dyalog": dyalog_version(image) if image else None,
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
