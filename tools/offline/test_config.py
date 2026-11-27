import copy
import shutil
import tomllib
from pathlib import Path

from offline.config import SHIM_PATH, offline_config, write_offline_config

REPO = Path(__file__).resolve().parents[2]
STAMP = dict(build_date="2026-11-27", git_info="main:abc1234", year="2026")

BASE = {
    "project": {
        "site_name": "Documentation",
        "repo_url": "https://github.com/example/repo",
        "edit_uri": "edit/main/docs",
        "site_url": "https://docs.dyalog.com/",
        "copyright": "1982-$CURRENT_YEAR ($BUILD_DATE $GIT_INFO)",
        "plugins": ["search"],
        "theme": {"features": ["content.code.copy", "navigation.instant", "navigation.tracking"]},
        "extra": {"version_majmin": "21.0", "version": {"provider": "mike"}},
        "nav": [{"Home": "index.md"}],
    }
}


def test_adds_offline_plugin_once():
    cfg = offline_config(BASE, **STAMP)
    assert cfg["project"]["plugins"] == ["search", "offline"]
    again = offline_config(cfg, **STAMP)
    assert again["project"]["plugins"] == ["search", "offline"]


def test_vendored_shim_replaces_unpkg():
    cfg = offline_config(BASE, **STAMP)
    assert cfg["project"]["extra"]["polyfills"] == [
        {"path": SHIM_PATH, "type": "text/javascript", "async": False}
    ]
    assert "iframe-worker" in SHIM_PATH


def test_remote_keys_removed():
    project = offline_config(BASE, **STAMP)["project"]
    for key in ("repo_url", "edit_uri", "site_url"):
        assert key not in project
    assert project["site_dir"] == "site-offline"


def test_version_selector_removed():
    assert "version" not in offline_config(BASE, **STAMP)["project"]["extra"]


def test_instant_navigation_removed():
    features = offline_config(BASE, **STAMP)["project"]["theme"]["features"]
    assert features == ["content.code.copy", "navigation.tracking"]


def test_copyright_placeholders_stamped():
    c = offline_config(BASE, **STAMP)["project"]["copyright"]
    assert c == "1982-2026 (2026-11-27 main:abc1234)"


def test_base_not_mutated():
    before = copy.deepcopy(BASE)
    offline_config(BASE, **STAMP)
    assert BASE == before


def test_real_project_config(tmp_path):
    src = REPO / "zensical" / "zensical.toml"
    shutil.copy(src, tmp_path / "zensical.toml")
    out = write_offline_config(tmp_path, **STAMP)
    assert out == tmp_path / "offline.toml"
    with open(src, "rb") as f:
        base = tomllib.load(f)
    with open(out, "rb") as f:
        cfg = tomllib.load(f)
    assert cfg["project"]["nav"] == base["project"]["nav"]
    assert "offline" in cfg["project"]["plugins"]
    assert "$" not in cfg["project"]["copyright"]


def test_shim_is_vendored():
    shim = REPO / "zensical" / "docs" / SHIM_PATH
    assert shim.is_file()
    assert "unpkg.com" not in shim.read_text()
