"""The page model shared by the checks, maintenance scripts and build tools.

(Named docsite, not site: a module called site would shadow the stdlib one.)
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

ASSET_DIR = "documentation-theme-zensical"
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def find_project(repo_root: Path | None = None) -> Path:
    """The directory holding zensical.toml.

    $DOCS_PROJECT wins; else <repo_root>/zensical if it has a zensical.toml;
    else the repo root. repo_root defaults to the parent of tools/.
    """
    env = os.environ.get("DOCS_PROJECT")
    if env:
        return Path(env)
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    sub = repo_root / "zensical"
    if (sub / "zensical.toml").is_file():
        return sub
    return repo_root


def _flatten(nav: list) -> Iterator[str]:
    for entry in nav:
        if isinstance(entry, str):
            yield entry
        else:
            for value in entry.values():
                if isinstance(value, str):
                    yield value
                else:
                    yield from _flatten(value)


@dataclass(frozen=True)
class Site:
    project: Path
    docs: Path
    config: dict

    @classmethod
    def load(cls, project: Path) -> "Site":
        project = Path(project)
        with open(project / "zensical.toml", "rb") as f:
            config = tomllib.load(f)
        docs_dir = config.get("project", {}).get("docs_dir", "docs")
        return cls(project, project / docs_dir, config)

    def _nav(self) -> list:
        return self.config.get("project", {}).get("nav", [])

    def nav_pages(self) -> list[str]:
        return list(dict.fromkeys(_flatten(self._nav())))

    def nav_section(self, title_path: tuple[str, ...]) -> list:
        nav = self._nav()
        for i, title in enumerate(title_path):
            for entry in nav:
                if isinstance(entry, dict) and isinstance(entry.get(title), list):
                    nav = entry[title]
                    break
            else:
                raise KeyError(" > ".join(title_path[: i + 1]))
        return nav

    def pages(self) -> list[str]:
        out = []
        for p in self.docs.rglob("*.md"):
            rel = p.relative_to(self.docs).as_posix()
            if rel.split("/", 1)[0] != ASSET_DIR:
                out.append(rel)
        return sorted(out)

    def read(self, page: str) -> str:
        return (self.docs / page).read_text(encoding="utf-8")


def prose_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield (1-based line number, line) for lines outside fenced code."""
    fence = None  # (char, length) of the open fence
    for n, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                continue
            yield n, line
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                and not line.strip().strip(fence[0]):
            fence = None
