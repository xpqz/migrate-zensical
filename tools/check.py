"""Run every project check and print `path:line: severity: message`.

Exit status is 1 if any problem has severity "error". Internal link and
anchor validation is not re-implemented here: it comes from the Zensical
build (via check_flatten) and the known-issues.txt baseline.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

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
    output, returncode = runner(site.project)
    baseline = check_flatten.load_baseline(baseline_path)
    result = check_flatten.assess(output, returncode, baseline)
    problems = [_issue_problem(k, "new build issue", "error")
                for k in sorted(result.new)]
    problems += [_issue_problem(k, "baseline issue no longer reported; remove "
                                "from known-issues.txt", "warning")
                 for k in sorted(result.resolved)]
    # Reasons other than the new-issue tally (which the entries above cover).
    problems += [Problem("zensical build", None, r, "error")
                 for r in result.reasons if "outside the baseline" not in r]
    return problems


# Cheap checks that need only the site; the build and unlisted checks take
# extra inputs and are wired in main().
CHECKS: list[Callable[[Site], list[Problem]]] = [check_nav_targets]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true",
                        help="skip the Zensical build (the slow check)")
    parser.add_argument("--prune-images", action="store_true",
                        help="remove unreferenced images (not yet implemented)")
    parser.add_argument("--project", type=Path, default=None)
    parser.add_argument("--unlisted", type=Path, default=UNLISTED_PATH)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args(argv)

    site = Site.load(args.project or docsite.find_project())
    problems: list[Problem] = []
    for fn in CHECKS:
        problems += fn(site)
    problems += check_unlisted(site, load_list(args.unlisted))
    if not args.skip_build:
        problems += check_build(site, args.baseline)

    for p in problems:
        loc = f"{p.path}:{p.line}" if p.line else p.path
        print(f"{loc}: {p.severity}: {p.message}")
    return 1 if any(p.severity == "error" for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
