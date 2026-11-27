"""Runs dyalog-docs-update.sh, the shell installer, against fixture archives through file: URLs."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from offline.build_bundle import package

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX sh installer")

SCRIPT = Path(__file__).parent / "dyalog-docs-update.sh"
META = {"version": "21.0", "build_date": "2026-11-27", "git": "main:abc1234"}


def site(tmp_path: Path, name: str = "site", body: str = "<html></html>") -> Path:
    s = tmp_path / name
    (s / "language-reference-guide").mkdir(parents=True)
    (s / "index.html").write_text(body)
    (s / "language-reference-guide" / "union.html").write_text(body)
    return s


@pytest.fixture
def served(tmp_path):
    """A download directory holding the archive and its .sha256, as docs.dyalog.com/<X.Y>/files does."""
    files = tmp_path / "files"
    archive, checksum = package(site(tmp_path), "21.0", files, META)
    return files, archive, checksum


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [os.environ.get("TEST_SH", "sh"), str(SCRIPT), *args],
        capture_output=True, text=True, timeout=60,
        env=dict(os.environ, **(env or {})),
    )


def leftovers(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.name.startswith("."))


def test_fresh_install(served, tmp_path):
    files, archive, _ = served
    root = tmp_path / "root"
    p = run("--version", "21.0", "--root", str(root), "--base-url", files.as_uri())
    assert p.returncode == 0, p.stderr
    dest = root / "21.0"
    assert (dest / "index.html").exists()
    assert (dest / "language-reference-guide" / "union.html").exists()
    meta = json.loads((dest / "bundle.json").read_text())
    assert meta["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert meta["build_date"] == "2026-11-27"
    assert f"DYALOGHELPDIR=file://{dest}" in p.stdout
    assert leftovers(root) == []


def test_already_current_is_noop(served, tmp_path):
    files, _, _ = served
    root = tmp_path / "root"
    args = ("--version", "21.0", "--root", str(root), "--base-url", files.as_uri())
    assert run(*args).returncode == 0
    before = (root / "21.0").stat().st_mtime_ns
    p = run(*args)
    assert p.returncode == 0, p.stderr
    assert "up to date" in p.stdout
    assert (root / "21.0").stat().st_mtime_ns == before
    assert leftovers(root) == []


def test_recognises_docs_ucmd_install(served, tmp_path):
    """]Docs writes bundle.json with sorted keys, so sha256 is not the last member."""
    files, archive, _ = served
    root = tmp_path / "root"
    dest = root / "21.0"
    dest.mkdir(parents=True)
    (dest / "index.html").write_text("<html></html>")
    meta = dict(META, sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    (dest / "bundle.json").write_text(json.dumps(meta, indent=2, sort_keys=True).replace("\n", "\r\n"))
    p = run("--version", "21.0", "--root", str(root), "--base-url", files.as_uri())
    assert p.returncode == 0, p.stderr
    assert "up to date" in p.stdout


def test_update_replaces_older_copy(served, tmp_path):
    files, _, _ = served
    root = tmp_path / "root"
    newer = tmp_path / "newer"
    package(site(tmp_path, "site2", "<html>new</html>"), "21.0", newer, dict(META, build_date="2026-11-28"))
    assert run("--version", "21.0", "--root", str(root), "--base-url", files.as_uri()).returncode == 0
    p = run("--version", "21.0", "--root", str(root), "--base-url", newer.as_uri())
    assert p.returncode == 0, p.stderr
    assert (root / "21.0" / "index.html").read_text() == "<html>new</html>"
    assert leftovers(root) == []


def test_checksum_mismatch_keeps_old(served, tmp_path):
    files, archive, _ = served
    root = tmp_path / "root"
    assert run("--version", "21.0", "--root", str(root), "--base-url", files.as_uri()).returncode == 0
    installed = (root / "21.0" / "bundle.json").read_text()

    bad = tmp_path / "bad"
    bad.mkdir()
    data = archive.read_bytes()
    (bad / archive.name).write_bytes(data + b"\0")
    claimed = hashlib.sha256(data + b"\1").hexdigest()
    (bad / f"{archive.name}.sha256").write_text(f"{claimed}  {archive.name}\n")

    p = run("--version", "21.0", "--root", str(root), "--base-url", bad.as_uri())
    assert p.returncode == 3, p.stdout + p.stderr
    assert (root / "21.0" / "bundle.json").read_text() == installed
    assert (root / "21.0" / "index.html").exists()
    assert leftovers(root) == []


def test_missing_download_exits_2(tmp_path):
    root = tmp_path / "root"
    empty = tmp_path / "empty"
    empty.mkdir()
    p = run("--version", "21.0", "--root", str(root), "--base-url", empty.as_uri())
    assert p.returncode == 2, p.stdout + p.stderr
    assert not (root / "21.0").exists()
    assert leftovers(root) == []


def test_corrupt_archive_exits_4(tmp_path):
    root = tmp_path / "root"
    bad = tmp_path / "bad"
    bad.mkdir()
    junk = b"not a tar archive"
    (bad / "dyalog-docs-21.0.tar.gz").write_bytes(junk)
    (bad / "dyalog-docs-21.0.tar.gz.sha256").write_text(
        f"{hashlib.sha256(junk).hexdigest()}  dyalog-docs-21.0.tar.gz\n")
    p = run("--version", "21.0", "--root", str(root), "--base-url", bad.as_uri())
    assert p.returncode == 4, p.stdout + p.stderr
    assert not (root / "21.0").exists()
    assert leftovers(root) == []


def test_root_with_spaces(served, tmp_path):
    files, _, _ = served
    root = tmp_path / "Application Support" / "Dyalog" / "docs"
    p = run("--version", "21.0", "--root", str(root), "--base-url", files.as_uri())
    assert p.returncode == 0, p.stderr
    assert (root / "21.0" / "index.html").exists()


def test_from_local_archive(served, tmp_path):
    _, archive, _ = served
    root = tmp_path / "root"
    p = run("--from", str(archive), "--root", str(root))
    assert p.returncode == 0, p.stderr
    assert (root / "21.0" / "index.html").exists()


def test_from_without_checksum_is_refused(served, tmp_path):
    _, archive, checksum = served
    checksum.unlink()
    root = tmp_path / "root"
    p = run("--from", str(archive), "--root", str(root))
    assert p.returncode == 2, p.stdout + p.stderr
    assert not (root / "21.0").exists()


def test_default_user_root(served, tmp_path):
    files, _, _ = served
    home = tmp_path / "home"
    p = run("--version", "21.0", "--base-url", files.as_uri(),
            env={"HOME": str(home), "XDG_DATA_HOME": ""})
    assert p.returncode == 0, p.stderr
    if os.uname().sysname == "Darwin":
        dest = home / "Library" / "Application Support" / "Dyalog" / "docs" / "21.0"
    else:
        dest = home / ".local" / "share" / "dyalog" / "docs" / "21.0"
    assert (dest / "index.html").exists()


@pytest.mark.parametrize("args", [(), ("--bogus",), ("--version",), ("--version", "21")])
def test_usage_errors(args):
    assert run(*args).returncode == 1
