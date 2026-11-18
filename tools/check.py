"""Run every project check and print `path:line: severity: message`.

Exit status is 1 if any problem has severity "error". Internal link and
anchor validation is not re-implemented here: it comes from the Zensical
build (via build_gate) and the known-issues.txt baseline. check_links covers
only the links that build skips (no .md, or to other files).
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Literal

import yaml

import build_gate
import docsite
from docsite import Site

REPO_ROOT = Path(__file__).resolve().parent.parent
UNLISTED_PATH = REPO_ROOT / "unlisted-pages.txt"
BASELINE_PATH = build_gate.BASELINE_PATH

# "<type> @ <file>:<line>:<col>", as produced by build_gate.parse_issues.
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
                runner=build_gate.run_build) -> list[Problem]:
    try:
        baseline = build_gate.load_baseline(baseline_path)
    except FileNotFoundError:
        return [Problem(str(baseline_path), None, "baseline file not found",
                        "error")]
    output, returncode = runner(site.project)
    result = build_gate.assess(output, returncode, baseline)
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


# A hidden keyword block's opening div. maintain/search_keywords.py uses it too.
HIDDEN_DIV = re.compile(
    r"""<div\b[^>]*?\bstyle\s*=\s*["']\s*display\s*:\s*none\s*;?\s*["']""",
    re.IGNORECASE)


def excludes_search(text: str) -> bool:
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
        if not excludes_search(text):
            continue
        for n, line in docsite.prose_lines(text):
            if HIDDEN_DIV.search(line):
                problems.append(Problem(
                    page, n, "page is excluded from search but has hidden "
                    "search keywords; remove one", "error"))
                break
    return problems


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
_MD_TARGET = re.compile(r"\]\(\s*(?:<([^>]*)>|([^)\s]+))")
# Reference definitions; footnote definitions ([^1]: text) are not links.
_REF_DEF = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:\s*(?:<([^>]*)>|(\S+))", re.MULTILINE)
_HTML_TAG = re.compile(r"<([a-zA-Z][\w-]*)\b([^>]*)>")
_HTML_ATTR = re.compile(
    r"""(?<![\w-])(src|srcset|href|poster)\s*=\s*(?:"([^"]*)"|'([^']*)')""",
    re.IGNORECASE)
_CSS_URL = re.compile(r"""url\(\s*["']?([^"')\s]+)["']?\s*\)""", re.IGNORECASE)
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)


def _resolve(base_dir: str, target: str) -> str | None:
    """docs-relative POSIX path of a relative image target, else None."""
    target = target.strip()
    if not target or target.startswith("#") or _EXTERNAL.match(target):
        return None
    target = urllib.parse.unquote(re.split(r"[#?]", target, maxsplit=1)[0])
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(base_dir, target))


def _escaped(text: str, i: int) -> bool:
    """True if text[i] is preceded by an odd number of backslashes."""
    j = i
    while j > 0 and text[j - 1] == "\\":
        j -= 1
    return (i - j) % 2 == 1


def _is_embedded(text: str, close: int) -> bool:
    """Whether the link text closed by text[close] == "]" opens with "![".

    Scans back over nested and escaped brackets, so a thumbnail
    [![t](a.png)](b.png) classifies the inner target as an image and the
    outer one as a plain link.
    """
    depth = 0
    for i in range(close - 1, -1, -1):
        c = text[i]
        if c not in "[]" or _escaped(text, i):
            continue
        if c == "]":
            depth += 1
        elif depth:
            depth -= 1
        else:
            return i > 0 and text[i - 1] == "!" and not _escaped(text, i - 1)
    return False


def _is_image(target: str) -> bool:
    path = re.split(r"[#?]", target, maxsplit=1)[0]
    return posixpath.splitext(path)[1].lower() in IMAGE_SUFFIXES


def _prose_text(site: Site, page: str) -> str:
    """The page with fenced lines blanked, so offsets still give editor line
    numbers."""
    prose = {n: line for n, line in docsite.prose_lines(site.read(page))}
    return "\n".join(prose.get(i, "") for i in range(1, max(prose, default=0) + 1))


def _targets(text: str) -> Iterator[tuple[int, str, bool, str | None]]:
    """(offset, target, embedded, html) for every link or embed target.

    html is "tag.attr" for a raw HTML attribute, else None. Embedded targets
    are Markdown images and <img src/srcset>.
    """
    for m in _MD_TARGET.finditer(text):
        if not _escaped(text, m.start()):
            yield m.start(), m[1] or m[2] or "", _is_embedded(text, m.start()), None
    for m in _REF_DEF.finditer(text):
        yield m.start(), m[1] or m[2] or "", False, None
    for tag in _HTML_TAG.finditer(text):
        name = tag[1].lower()
        for attr in _HTML_ATTR.finditer(tag[2]):
            key, value = attr[1].lower(), attr[2] if attr[2] is not None else attr[3]
            pos = tag.start(2) + attr.start()
            if key == "srcset":
                for cand in value.split(","):
                    if cand.split():
                        yield pos, cand.split()[0], name == "img", f"{name}.{key}"
            else:
                yield pos, value, name == "img" and key == "src", f"{name}.{key}"


def _page_image_refs(site: Site, page: str) -> list[tuple[int, str, bool]]:
    """(line, docs-relative path, embedded) for each local image reference.

    Embedded references (![](), <img src/srcset>) must exist. Other
    references (links, reference definitions, href/poster/srcset) only count
    when they point at an image file, and only mark it as used. Prose only.
    """
    base = posixpath.dirname(page)
    text = _prose_text(site, page)
    found: list[tuple[int, str, bool]] = []
    for pos, target, embedded, _ in _targets(text):
        if not embedded and not _is_image(target):
            continue
        if (path := _resolve(base, target)) is not None:
            found.append((text.count("\n", 0, pos) + 1, path, embedded))
    return found


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

    A reference that differs from the file on disk only in case is an error
    (it breaks on case-sensitive servers) and still marks the file as used.
    With prune=True the unreferenced files are deleted and reported as
    "removed". The house-style submodule is never pruned; its CSS counts as
    a referrer.
    """
    problems = []
    files = {f.relative_to(site.docs).as_posix()
             for f in site.docs.rglob("*") if f.is_file()}
    by_fold = {rel.casefold(): rel for rel in files}
    used: set[str] = set()
    for page in site.pages():
        for n, path, embedded in _page_image_refs(site, page):
            actual = path if path in files else by_fold.get(path.casefold())
            if actual is None:
                if embedded:
                    problems.append(Problem(
                        page, n, f"image not found: {path}", "error"))
                continue
            used.add(actual.casefold())
            if actual != path:
                problems.append(Problem(
                    page, n, f"image path {path!r} differs in case from the "
                    f"file {actual!r}", "error"))
    used |= {p.casefold() for p in _theme_css_refs(site)}
    config_text = (site.project / "zensical.toml").read_text(
        encoding="utf-8").casefold()

    for rel in sorted(files):
        if posixpath.splitext(rel)[1].lower() not in IMAGE_SUFFIXES:
            continue
        if rel.split("/", 1)[0] == docsite.ASSET_DIR:
            continue
        if rel.casefold() in used or rel.casefold() in config_text:
            continue
        if prune:
            (site.docs / rel).unlink()
            problems.append(Problem(rel, None, "removed", "warning"))
        else:
            problems.append(Problem(rel, None, "image is not referenced by "
                                    "any page, the config or the theme CSS",
                                    "warning"))
    return problems


def _page_url_dir(page: str) -> str:
    """The directory a browser resolves a page's relative hrefs against:
    a/b.md is served as a/b/, a/index.md as a/."""
    stem = posixpath.splitext(page)[0]
    if posixpath.basename(stem) == "index":
        return posixpath.dirname(stem)
    return stem


def check_links(site: Site) -> list[Problem]:
    """Links the Zensical build does not validate.

    The build checks links to .md files (Markdown or raw HTML) and their
    anchors. It copies every other relative href into the page unchecked:
    - a link to a file (x.pdf, img/a.png) is resolved against the page's
      source directory; a missing file is an error. Targets outside docs/
      are skipped: the web server's files/ directory, filled from SVN by
      tools/jenkins/get_svn_docbin, is one;
    - a link without an extension (bare `page` or directory-style `page/`)
      is resolved by the browser against the page's URL, which for a.md is
      a/, one level below its source directory. One that lands on no page is
      an error; one that works is a warning, since the build will not notice
      when its target moves, with the .md form to write instead.
    """
    pages = set(site.pages())
    files = {f.relative_to(site.docs).as_posix()
             for f in site.docs.rglob("*") if f.is_file()}
    problems = []
    for page in sorted(pages):
        base, url_dir = posixpath.dirname(page), _page_url_dir(page)
        text = _prose_text(site, page)
        for pos, raw, embedded, html in _targets(text):
            target = raw.strip()
            if (embedded or (html is not None and html != "a.href")
                    or not target or target.startswith("#")
                    or _EXTERNAL.match(target) or "{{" in target):
                continue
            path, _, fragment = target.partition("#")
            path = urllib.parse.unquote(path.split("?", 1)[0])
            if path.rstrip("/").endswith(".md"):
                continue  # the build checks these (x.md/ too)
            line = text.count("\n", 0, pos) + 1
            if posixpath.splitext(path.rstrip("/"))[1] and not path.endswith("/"):
                resolved = _resolve(base, path)
                if resolved.startswith("../"):
                    continue  # outside docs/, e.g. files/ (Jenkins fills it from SVN)
                if resolved not in files:
                    problems.append(Problem(page, line, f"linked file not found: {raw}", "error"))
                continue
            dest = posixpath.normpath(
                path.lstrip("/") if path.startswith("/") else posixpath.join(url_dir, path))
            dest = "" if dest == "." else dest
            found = next((c for c in (f"{dest}.md" if dest else None,
                                      posixpath.join(dest, "index.md"))
                          if c in pages), None)
            if found is None or dest.startswith(".."):
                problems.append(Problem(
                    page, line, f"link does not lead to a page: {raw} (links "
                    "between pages are source-relative paths ending in .md)", "error"))
                continue
            suggestion = posixpath.relpath(found, base or ".")
            if fragment:
                suggestion += "#" + fragment
            problems.append(Problem(
                page, line, f"link written without .md, so the build does not "
                f"check it: {raw} -> write {suggestion}", "warning"))
    return problems


def check_help_urls(site: Site, help_urls: Path,
                    baseline: Path | None = None) -> list[Problem]:
    """HELP_URL targets in help_urls.h (from the interpreter sources) that no
    page would provide in the CHM.

    The same check as tools/chm/checkchm.py, but against the Markdown source,
    so no CHM build is needed. Targets listed in
    tools/chm/known-missing-help-urls.txt are not reported while missing.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent / "chm"))
    import build_chm
    import checkchm

    if baseline is None:
        baseline = build_chm.HERE / "known-missing-help-urls.txt"
    if not help_urls.is_file():
        return [Problem(str(help_urls), None, "help_urls.h not found", "error")]
    exclude = json.loads((build_chm.HERE / "config.json").read_text(
        encoding="utf-8")).get("exclude", [])
    nav = build_chm.load_nav(site, exclude=exclude)
    topics = {out for _, out in build_chm.chm_pages(site, nav, verbose=False)}
    topics.add("welcome.htm")
    entries, _ = checkchm.parse_help_urls(
        help_urls.read_text(encoding="utf-8").splitlines())
    known = checkchm.read_baseline(baseline)
    missing = set()
    problems = []
    for entry in entries:
        found, _ = checkchm.resolve_path(entry.relative_path, topics.__contains__)
        if found is not None:
            continue
        missing.add(entry.relative_path)
        if entry.relative_path not in known:
            problems.append(Problem(
                str(help_urls), entry.source_line,
                f"HELP_URL {entry.key!r} names {entry.relative_path}, which no "
                "page in the CHM provides", "error"))
    problems += [Problem(str(baseline), None, f"{rel} is no longer missing; "
                         "remove it", "warning")
                 for rel in sorted(known - missing)]
    return problems


# Cheap checks that need only the site; the build, unlisted and image checks
# take extra inputs and are wired in main().
CHECKS: list[Callable[[Site], list[Problem]]] = [
    check_nav_targets, check_admonitions, check_search_exclusions, check_links]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true",
                        help="skip the Zensical build (the slow check)")
    parser.add_argument("--prune-images", action="store_true",
                        help="delete unreferenced images")
    parser.add_argument("--project", type=Path, default=None)
    parser.add_argument("--unlisted", type=Path, default=UNLISTED_PATH)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument(
        "--help-urls", type=Path, default=os.environ.get("DYALOG_HELP_URLS"),
        help="also check the HELP_URL targets in this help_urls.h (default: "
        "$DYALOG_HELP_URLS, if set)")
    args = parser.parse_args(argv)

    site = Site.load(args.project or docsite.find_project())
    problems: list[Problem] = []
    for fn in CHECKS:
        problems += fn(site)
    problems += check_unlisted(site, load_list(args.unlisted))
    if args.help_urls:
        problems += check_help_urls(site, args.help_urls)
    if not args.skip_build:
        problems += check_build(site, args.baseline)
    # Pruning deletes files, so only do it when nothing at all is in error.
    image_problems = check_images(site)
    if args.prune_images:
        if any(p.severity == "error" for p in problems + image_problems):
            problems.append(Problem(
                "images", None, "pruning skipped because of errors", "warning"))
        else:
            image_problems = check_images(site, prune=True)
    problems += image_problems

    for p in problems:
        loc = f"{p.path}:{p.line}" if p.line else p.path
        print(f"{loc}: {p.severity}: {p.message}")
    return 1 if any(p.severity == "error" for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
