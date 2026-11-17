"""Reorder one reference nav section of zensical.toml in place.

    order_nav.py {sysfns|syscmds|ibeams}

Only the entry lines of the chosen section change; the rest of the file
(comments, layout) stays byte-identical. The section's index entry stays
first, then any entries that carry no sort key, then the sorted entries.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import docsite  # noqa: E402

_ENTRY = re.compile(r'^\s*\{\s*("(?:[^"\\]|\\.)*"|[^\s=]+)\s*=')


def _title(line: str) -> str | None:
    m = _ENTRY.match(line)
    if not m:
        return None
    key = m.group(1)
    return json.loads(key) if key.startswith('"') else key


def _name(title: str) -> str:
    """The part before ':' with markup, quad and right bracket removed."""
    head = title.split(":", 1)[0]
    return re.sub(r"</?code>|[⎕\]]", "", head).strip().casefold()


def _sysname_key(title: str):
    return _name(title) if ":" in title else None


def _ibeam_key(title: str):
    head = title.split(":", 1)[0].strip()
    return int(head) if ":" in title and head.isdigit() else None


def _sysfn_key(title: str):
    return _name(title) if "<code>" in title else None


KEYS: dict[str, Callable[[str], object]] = {
    "sysfns": _sysfn_key,
    "syscmds": _sysname_key,
    "ibeams": _ibeam_key,
}
SECTIONS = {
    "sysfns": "System Functions (A-Z)",
    "syscmds": "System Commands (A-Z)",
    "ibeams": "I-Beam",
}


def reorder(toml_text: str, section_title: str, key: Callable[[str], object]) -> str:
    """Return toml_text with the section's entries sorted by key(title).

    key returns None for entries that must stay where they are (pinned,
    ahead of the sorted ones). The index entry (title equal to the section's)
    is always first.
    """
    lines = toml_text.split("\n")
    for start, line in enumerate(lines):
        if _title(line) == section_title and line.rstrip().endswith("= ["):
            break
    else:
        raise KeyError(section_title)
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("]"):
        if lines[end].rstrip().endswith("= ["):
            raise ValueError(f"{section_title}: nested sections are not supported")
        end += 1
    if end == len(lines):
        raise ValueError(f"{section_title}: no closing bracket")

    index, pinned, sortable = [], [], []
    for line in lines[start + 1:end]:
        title = _title(line)
        if title is None:
            raise ValueError(f"{section_title}: cannot parse entry {line!r}")
        k = key(title)
        if title == section_title:
            index.append(line)
        elif k is None:
            pinned.append(line)
        else:
            sortable.append((k, line))
    sortable.sort(key=lambda kv: kv[0])
    lines[start + 1:end] = index + pinned + [line for _, line in sortable]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("section", choices=sorted(KEYS))
    args = ap.parse_args(argv)
    site = docsite.Site.load(docsite.find_project())
    path = site.project / "zensical.toml"
    # newline="" keeps the file's line endings untouched.
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    new = reorder(text, SECTIONS[args.section], KEYS[args.section])
    if new != text:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        print(f"{path}: reordered {SECTIONS[args.section]}")
    else:
        print(f"{path}: {SECTIONS[args.section]} already in order")
    return 0


if __name__ == "__main__":
    sys.exit(main())
