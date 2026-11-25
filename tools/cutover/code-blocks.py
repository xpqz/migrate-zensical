"""Review tool for `apl` code fences in the Markdown documentation.

It finds `apl` fences, proposes splitting session input from output, and
validates examples against Dyalog APL. This module holds the block finder.
The tool only reads the corpus; it never writes to it.
"""

import hashlib
import re
from dataclasses import dataclass

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
    lines = markdown.splitlines()
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
