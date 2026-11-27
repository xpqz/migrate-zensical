"""Build the offline documentation bundle and package it for release.

    uv run python tools/offline/build_bundle.py --out out/offline \\
        --build-date 2026-11-27 --git-info main:abc1234

Writes dyalog-docs-<X.Y>.tar.gz and dyalog-docs-<X.Y>.tar.gz.sha256 to --out.
"""

import argparse
import datetime
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import docsite  # noqa: E402
from offline.config import SITE_DIR, write_offline_config  # noqa: E402
from release_version import project_version  # noqa: E402

# 404.html uses root-absolute asset paths and is unreachable from disk.
EXCLUDE = {"404.html"}


def _mtime(meta: dict) -> int:
    day = datetime.date.fromisoformat(meta["build_date"])
    return int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.UTC).timestamp())


def package(site: Path, version: str, out: Path, meta: dict) -> tuple[Path, Path]:
    """Archive site under one top-level directory, with bundle.json, plus a sha256sum file.

    Members are sorted and stamped with the build date, so the same site and
    meta give a byte-identical archive.
    """
    top = f"dyalog-docs-{version}"
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"{top}.tar.gz"
    mtime = _mtime(meta)

    def info(name: str, *, directory: bool = False, size: int = 0) -> tarfile.TarInfo:
        ti = tarfile.TarInfo(name)
        ti.mtime = mtime
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        ti.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
        ti.mode = 0o755 if directory else 0o644
        ti.size = size
        return ti

    paths = sorted(p for p in site.rglob("*") if p.relative_to(site).as_posix() not in EXCLUDE)
    with open(archive, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=mtime) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        tar.addfile(info(top, directory=True))
        for p in paths:
            name = f"{top}/{p.relative_to(site).as_posix()}"
            if p.is_dir():
                tar.addfile(info(name, directory=True))
            else:
                with open(p, "rb") as f:
                    tar.addfile(info(name, size=p.stat().st_size), f)
        data = (json.dumps(meta, indent=2) + "\n").encode()
        tar.addfile(info(f"{top}/bundle.json", size=len(data)), io.BytesIO(data))

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = out / f"{archive.name}.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n")
    return archive, checksum


def build(project: Path, *, build_date: str, git_info: str) -> Path:
    """Run the offline Zensical build; return the site directory."""
    config = write_offline_config(project, build_date=build_date, git_info=git_info, year=build_date[:4])
    site = project / SITE_DIR
    shutil.rmtree(site, ignore_errors=True)
    try:
        subprocess.run(["zensical", "build", "--clean", "-f", config.name], cwd=project, check=True)
    finally:
        config.unlink(missing_ok=True)
    return site


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--build-date", default=datetime.date.today().isoformat())
    ap.add_argument("--git-info", required=True)
    args = ap.parse_args(argv)

    project = docsite.find_project()
    version = project_version(project)
    site = build(project, build_date=args.build_date, git_info=args.git_info)
    meta = {"version": version, "build_date": args.build_date, "git": args.git_info}
    archive, checksum = package(site, version, args.out, meta)
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    sys.exit(main())
