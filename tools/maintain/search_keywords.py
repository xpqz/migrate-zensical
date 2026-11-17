"""Add the hidden search-keyword block to reference pages that lack one.

    search_keywords.py [--dry-run] PAGE...

The block goes at the end of the page, in the format already used across the
corpus. Pages with a block, or without a `# <span class="title-name">` title
carrying a syntax, or excluded from search, are left alone, so running it twice changes nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check  # noqa: E402
import docsite  # noqa: E402

MARKER = "<!-- Hidden search keywords -->"
_H1 = re.compile(r'^# <span class="title-name">.*?</span>\s+(?P<syntax>`.+`|<code>.*</code>)\s*$')
_IBEAM = re.compile(r"(\d+)⌶")
_QUAD = re.compile(r"⎕([^\W\d_]+)")
_IGNORED = set("{}()[]'\" ")


def _syntax(md: str) -> str | None:
    """The h1's syntax text, markup removed, or None if the h1 has none."""
    for _, line in docsite.prose_lines(md):
        if line.startswith("# "):
            m = _H1.match(line)
            if not m:
                return None
            s = m.group("syntax")
            if s.startswith("`"):
                return s.strip("`")
            return re.sub(r"<[^>]+>", " ", s)
    return None


def keywords(syntax: str) -> str | None:
    """The keyword text for a syntax string, or None if there is none.

    I-beams give `N⌶`; system functions `⎕NAME NAME` (just `⎕X` for one
    letter); other primitives their last symbol, ignoring brackets and blanks (and
    a bare assignment arrow).
    System commands, which start with `)` or `]`, get none.
    """
    s = syntax.strip()
    if s[:1] in ")]":
        return None
    m = _IBEAM.search(s)
    if m:
        return m.group(0)
    m = _QUAD.search(s)
    if m:
        name = m.group(1)
        return f"⎕{name}" if len(name) == 1 else f"⎕{name} {name}"
    symbols = [c for c in s if not c.isalnum() and c not in _IGNORED]
    # A trailing assignment arrow (R←X[Y]) names no primitive.
    return symbols[-1] if symbols and symbols[-1] != "←" else None


def add_keywords(md: str) -> str:
    if MARKER in md or '<div style="display: none;">' in md:
        return md
    if check._excludes_search(md):  # a keyword div there is a check.py error
        return md
    syntax = _syntax(md)
    k = keywords(syntax) if syntax else None
    if not k:
        return md
    block = f'{MARKER}\n<div style="display: none;">\n  {k}\n</div>\n'
    return md.rstrip("\n") + "\n\n" + block


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pages", nargs="+", help="page paths, relative to the docs directory (or absolute)")
    ap.add_argument("--dry-run", action="store_true", help="list pages that would change, write nothing")
    args = ap.parse_args(argv)
    site = docsite.Site.load(docsite.find_project())
    changed = 0
    for page in args.pages:
        path = Path(page)
        if not path.is_absolute():
            path = site.docs / page
        with open(path, encoding="utf-8", newline="") as f:
            md = f.read()
        new = add_keywords(md)
        if new == md:
            continue
        changed += 1
        print(("would add: " if args.dry_run else "added: ") + page)
        if not args.dry_run:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
    print(f"{changed} page(s) {'would change' if args.dry_run else 'changed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
