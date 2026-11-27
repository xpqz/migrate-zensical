import hashlib
import json
import tarfile

import pytest

from offline.build_bundle import package

META = {"version": "21.0", "build_date": "2026-11-27", "git": "main:abc1234"}


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    (root / "language-reference-guide").mkdir(parents=True)
    (root / "index.html").write_text("<html></html>")
    (root / "404.html").write_text("<html></html>")
    (root / "language-reference-guide" / "union.html").write_text("<html></html>")
    return root


def _members(archive):
    with tarfile.open(archive, "r:gz") as t:
        return t.getnames()


def test_archive_and_checksum_names(site, tmp_path):
    archive, checksum = package(site, "21.0", tmp_path / "out", META)
    assert archive.name == "dyalog-docs-21.0.tar.gz"
    assert checksum.name == "dyalog-docs-21.0.tar.gz.sha256"


def test_archive_has_single_top_dir(site, tmp_path):
    archive, _ = package(site, "21.0", tmp_path / "out", META)
    names = _members(archive)
    assert "dyalog-docs-21.0/index.html" in names
    assert all(n == "dyalog-docs-21.0" or n.startswith("dyalog-docs-21.0/") for n in names)


def test_404_left_out(site, tmp_path):
    archive, _ = package(site, "21.0", tmp_path / "out", META)
    assert "dyalog-docs-21.0/404.html" not in _members(archive)


def test_bundle_json_fields(site, tmp_path):
    archive, _ = package(site, "21.0", tmp_path / "out", META)
    with tarfile.open(archive, "r:gz") as t:
        member = t.extractfile("dyalog-docs-21.0/bundle.json")
        assert member is not None
        data = json.load(member)
    assert data == META


def test_sha256_file_format(site, tmp_path):
    archive, checksum = package(site, "21.0", tmp_path / "out", META)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert checksum.read_text() == f"{digest}  dyalog-docs-21.0.tar.gz\n"


def test_reproducible(site, tmp_path):
    a, _ = package(site, "21.0", tmp_path / "one", META)
    b, _ = package(site, "21.0", tmp_path / "two", META)
    assert a.read_bytes() == b.read_bytes()
