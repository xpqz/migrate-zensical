"""Derive the offline build's config from zensical.toml.

The offline bundle is the published site built with Zensical's `offline`
plugin, minus everything that would make a page load from the network or
from a server: the unpkg copy of the iframe-worker shim, the GitHub
repository facts, the mike version selector and instant navigation (which
fetches pages, refused under file://).
"""

import copy
from pathlib import Path
import tomllib

import tomli_w

# Relative to the docs directory. Zensical's offline plugin adds the unpkg
# shim only when no polyfill path contains "iframe-worker".
SHIM_PATH = "assets/javascripts/iframe-worker-shim.js"
SITE_DIR = "site-offline"
CONFIG_NAME = "offline.toml"

_REMOTE_KEYS = ("repo_url", "edit_uri", "site_url")
_SERVER_FEATURES = ("navigation.instant", "navigation.instant.prefetch", "navigation.instant.progress")


def offline_config(base: dict, *, build_date: str, git_info: str, year: str) -> dict:
    cfg = copy.deepcopy(base)
    project = cfg["project"]

    plugins = project.setdefault("plugins", [])
    if "offline" not in plugins:
        plugins.append("offline")

    for key in _REMOTE_KEYS:
        project.pop(key, None)
    project["site_dir"] = SITE_DIR

    extra = project.setdefault("extra", {})
    extra.pop("version", None)
    extra["polyfills"] = [{"path": SHIM_PATH, "type": "text/javascript", "async": False}]

    theme = project.get("theme", {})
    if "features" in theme:
        theme["features"] = [f for f in theme["features"] if f not in _SERVER_FEATURES]

    if "copyright" in project:
        project["copyright"] = (
            project["copyright"]
            .replace("$BUILD_DATE", build_date)
            .replace("$GIT_INFO", git_info)
            .replace("$CURRENT_YEAR", year)
        )
    return cfg


def write_offline_config(project: Path, *, build_date: str, git_info: str, year: str) -> Path:
    """Write offline.toml beside zensical.toml; Zensical requires its site_dir inside the project."""
    with open(project / "zensical.toml", "rb") as f:
        base = tomllib.load(f)
    out = project / CONFIG_NAME
    cfg = offline_config(base, build_date=build_date, git_info=git_info, year=year)
    out.write_text(tomli_w.dumps(cfg), encoding="utf-8")
    return out
