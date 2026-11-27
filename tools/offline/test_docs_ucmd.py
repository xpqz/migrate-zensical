"""Runs test_docs_ucmd.apls, the ]Docs user command's install tests, under dyalogscript."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from offline.build_bundle import package

HERE = Path(__file__).parent
DYALOGSCRIPT = shutil.which("dyalogscript") or ""
META = {"version": "21.0", "build_date": "2026-11-27", "git": "main:abc1234"}

pytestmark = pytest.mark.skipif(not DYALOGSCRIPT, reason="dyalogscript not on PATH")


@pytest.fixture
def fixtures(tmp_path):
    site = tmp_path / "site"
    (site / "language-reference-guide").mkdir(parents=True)
    (site / "index.html").write_text("<html></html>")
    (site / "language-reference-guide" / "union.html").write_text("<html></html>")
    archive, _ = package(site, "21.0", tmp_path / "download dir", META)

    bad_dir = tmp_path / "tampered"
    bad_dir.mkdir()
    bad = bad_dir / archive.name
    data = archive.read_bytes()
    bad.write_bytes(data + b"\0")
    claimed = hashlib.sha256(data + b"\1").hexdigest()
    (bad_dir / f"{archive.name}.sha256").write_text(f"{claimed}  {archive.name}\n")

    root = tmp_path / "root"
    root.mkdir()
    return archive, bad, root


def test_docs_user_command(fixtures):
    archive, bad, root = fixtures
    env = dict(
        os.environ,
        DOCS_UCMD=str(HERE / "Docs.dyalog"),
        DOCS_ARCHIVE=str(archive),
        DOCS_TAMPERED=str(bad),
        DOCS_ROOT=str(root),
    )
    proc = subprocess.run(
        [DYALOGSCRIPT, str(HERE / "test_docs_ucmd.apls")],
        env=env, capture_output=True, text=True, timeout=120,
    )
    lines = [ln for ln in proc.stdout.replace("\r", "\n").splitlines() if ln.strip()]
    failed = [ln for ln in lines if not ln.startswith("PASS ")]
    assert failed == [] and proc.returncode == 0, "\n".join(lines + [proc.stderr])
    # dyalogscript exits 0 with no output when the script itself breaks.
    assert len(lines) == 12, "\n".join(lines + [proc.stderr])
