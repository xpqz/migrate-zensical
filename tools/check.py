"""Run every project check and print `path:line: severity: message`.

Exit status is 1 if any problem has severity "error". Internal link and
anchor validation is not re-implemented here: it comes from the Zensical
build (via check_flatten) and the known-issues.txt baseline.
"""

from __future__ import annotations

import argparse
import re
import posixpath
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import yaml

import check_flatten
import docsite
from docsite import Site

REPO_ROOT = Path(__file__).resolve().parent.parent
UNLISTED_PATH = REPO_ROOT / "unlisted-pages.txt"
BASELINE_PATH = check_flatten.BASELINE_PATH

# "<type> @ <file>:<line>:<col>", as produced by check_flatten.parse_issues.
_ISSUE_KEY = re.compile(r"^(?P<msg>.*?) @ (?P<file>.+?):(?P<line>\d+):\d+$")


@dataclass(frozen=True)
class Problem:
    path: str
    line: int | None
    message: str
    severity: Literal["error", "warning"]


def load_list(path: Path) -> set[str]:
    """Entries of a one-per-line list; blank lines and '#' comments ignored."""
    if not Path(path).is_file():
        return set()
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return {s for line in lines if (s := line.strip()) and not s.startswith("#")}


def check_nav_targets(site: Site) -> list[Problem]:
    return [
        Problem(page, None, "nav entry has no file", "error")
        for page in site.nav_pages()
        if not (site.docs / page).is_file()
    ]


def check_unlisted(site: Site, allow: set[str]) -> list[Problem]:
    nav = set(site.nav_pages())
    pages = set(site.pages())
    problems = [
        Problem(page, None, "page is not in the nav (list it, or add it to "
                "unlisted-pages.txt)", "error")
        for page in sorted(pages - nav - allow)
    ]
    for page in sorted(allow):
        if page in nav:
            problems.append(Problem(page, None, "now in the nav; remove from "
                                    "unlisted-pages.txt", "warning"))
        elif page not in pages:
            problems.append(Problem(page, None, "no longer exists; remove from "
                                    "unlisted-pages.txt", "warning"))
    return problems


def _issue_problem(key: str, message: str, severity) -> Problem:
    m = _ISSUE_KEY.match(key)
    if not m:
        return Problem(key, None, message, severity)
    return Problem(m["file"], int(m["line"]), f"{message}: {m['msg']}", severity)


def check_build(site: Site, baseline_path: Path,
                runner=check_flatten.run_build) -> list[Problem]:
    try:
        baseline = check_flatten.load_baseline(baseline_path)
    except FileNotFoundError:
        return [Problem(str(baseline_path), None, "baseline file not found",
                        "error")]
    output, returncode = runner(site.project)
    result = check_flatten.assess(output, returncode, baseline)
    problems = [_issue_problem(k, "new build issue", "error")
                for k in sorted(result.new)]
    problems += [_issue_problem(k, "baseline issue no longer reported; remove "
                                "from known-issues.txt", "warning")
                 for k in sorted(result.resolved)]
    problems += [Problem("zensical build", None, r, "error")
                 for r in result.other_reasons]
    return problems


# type -> allowed quoted titles. The conversion renamed the MkDocs types:
# Hint -> tip, Legacy -> note, the OS types -> info.
ADMONITIONS = {
    "info": {"Information", "Dyalog on Microsoft Windows", "Dyalog on Linux",
             "Dyalog on Unix", "Dyalog on AIX", "Dyalog on macOS"},
    "warning": {"Warning"},
    "tip": {"Hints and Recommendations"},
    "note": {"Legacy"},
}

_ADMONITION = re.compile(r'^\s*(?:!!!|\?\?\?\+?)\s*(.*)$')
_ADMONITION_HEAD = re.compile(r'^(\S+)(?:\s+"([^"]*)")?\s*$')


def check_admonitions(site: Site) -> list[Problem]:
    problems = []
    for page in site.pages():
        for n, line in docsite.prose_lines(site.read(page)):
            m = _ADMONITION.match(line)
            if not m:
                continue

            def err(msg: str) -> None:
                problems.append(Problem(page, n, msg, "error"))

            head = _ADMONITION_HEAD.match(m[1].strip())
            if not head:
                err(f"malformed admonition header: {m[1].strip()!r}")
                continue
            kind, title = head[1], head[2]
            allowed = ADMONITIONS.get(kind.lower())
            if allowed is None:
                err(f"unknown admonition type {kind!r} (valid: "
                    f"{', '.join(sorted(ADMONITIONS))})")
            elif title is None:
                err(f"admonition {kind!r} is missing its quoted title "
                    f"(expected one of {sorted(allowed)})")
            elif title not in allowed:
                err(f'admonition {kind!r} has title "{title}"; expected one '
                    f"of {sorted(allowed)}")
    return problems


_HIDDEN_DIV = re.compile(
    r"""<div\b[^>]*?\bstyle\s*=\s*["']\s*display\s*:\s*none\s*;?\s*["']""",
    re.IGNORECASE)


def _excludes_search(text: str) -> bool:
    if not text.startswith("---"):
        return False
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return False
    try:
        meta = yaml.safe_load(parts[0][3:])
    except yaml.YAMLError:
        return False
    search = meta.get("search") if isinstance(meta, dict) else None
    return isinstance(search, dict) and search.get("exclude") is True


def check_search_exclusions(site: Site) -> list[Problem]:
    problems = []
    for page in site.pages():
        text = site.read(page)
        if not _excludes_search(text):
            continue
        for n, line in docsite.prose_lines(text):
            if _HIDDEN_DIV.search(line):
                problems.append(Problem(
                    page, n, "page is excluded from search but has hidden "
                    "search keywords; remove one", "error"))
                break
    return problems


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]*)>|([^)\s]+))")
_HTML_IMG = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')""",
                       re.IGNORECASE)
_CSS_URL = re.compile(r"""url\(\s*["']?([^"')\s]+)["']?\s*\)""", re.IGNORECASE)
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)


def _resolve(base_dir: str, target: str) -> str | None:
    """docs-relative POSIX path of a relative image target, else None."""
    target = target.strip()
    if not target or target.startswith("#") or _EXTERNAL.match(target):
        return None
    target = urllib.parse.unquote(re.split(r"[#?]", target, 1)[0])
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(base_dir, target))


def _page_image_refs(site: Site, page: str) -> list[tuple[int, str]]:
    """(line, docs-relative path) for each local image reference in prose."""
    base = posixpath.dirname(page)
    refs = []
    for n, line in docsite.prose_lines(site.read(page)):
        targets = [a or b for a, b in _MD_IMAGE.findall(line)]
        targets += [a or b for a, b in _HTML_IMG.findall(line)]
        for t in targets:
            if (path := _resolve(base, t)) is not None:
                refs.append((n, path))
    return refs


def _theme_css_refs(site: Site) -> set[str]:
    refs = set()
    for css in (site.docs / docsite.ASSET_DIR).rglob("*.css"):
        base = posixpath.dirname(css.relative_to(site.docs).as_posix())
        for t in _CSS_URL.findall(css.read_text(encoding="utf-8")):
            if (path := _resolve(base, t)) is not None:
                refs.add(path)
    return refs


def check_images(site: Site, prune: bool = False) -> list[Problem]:
    """Missing image targets are errors; unreferenced image files warnings.

    With prune=True the unreferenced files are deleted and reported as
    "removed". The house-style submodule is never pruned; its CSS counts as
    a referrer.
    """
    problems = []
    used: set[str] = set()
    for page in site.pages():
        for n, path in _page_image_refs(site, page):
            used.add(path)
            if not (site.docs / path).is_file():
                problems.append(Problem(
                    page, n, f"image not found: {path}", "error"))
    used |= _theme_css_refs(site)
    config_text = (site.project / "zensical.toml").read_text(encoding="utf-8")

    for f in sorted(site.docs.rglob("*")):
        if f.suffix.lower() not in IMAGE_SUFFIXES or not f.is_file():
            continue
        rel = f.relative_to(site.docs).as_posix()
        if rel.split("/", 1)[0] == docsite.ASSET_DIR:
            continue
        if rel in used or rel in config_text:
            continue
        if prune:
            f.unlink()
            problems.append(Problem(rel, None, "removed", "warning"))
        else:
            problems.append(Problem(rel, None, "image is not referenced by "
                                    "any page, the config or the theme CSS",
                                    "warning"))
    return problems


# Cheap checks that need only the site; the build, unlisted and image checks
# take extra inputs and are wired in main().
CHECKS: list[Callable[[Site], list[Problem]]] = [
    check_nav_targets, check_admonitions, check_search_exclusions]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true",
                        help="skip the Zensical build (the slow check)")
    parser.add_argument("--prune-images", action="store_true",
                        help="delete unreferenced images")
    parser.add_argument("--project", type=Path, default=None)
    parser.add_argument("--unlisted", type=Path, default=UNLISTED_PATH)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args(argv)

    site = Site.load(args.project or docsite.find_project())
    problems: list[Problem] = []
    for fn in CHECKS:
        problems += fn(site)
    problems += check_images(site, prune=args.prune_images)
    problems += check_unlisted(site, load_list(args.unlisted))
    if not args.skip_build:
        problems += check_build(site, args.baseline)

    for p in problems:
        loc = f"{p.path}:{p.line}" if p.line else p.path
        print(f"{loc}: {p.severity}: {p.message}")
    return 1 if any(p.severity == "error" for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
