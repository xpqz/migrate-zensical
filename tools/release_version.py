"""The X.Y version a publish or release run deploys, and the production guard.

    uv run python tools/release_version.py

publish.yml and chm.yml both run this. The version is $VERSION_OVERRIDE if
set, else extra.version_majmin in the project's zensical.toml. On the
repository's default branch it must also equal TRUNK_VERSION in
tools/jenkins/Jenkinsfile on the same commit: the default branch documents
the trunk version, and an older version is published from its own vM.0
release branch, so a default-branch run deploying, say, 21.0 would overwrite
production's 21.0 with trunk content. Other branches (staging) are not
guarded.

Reads GITHUB_REF_NAME, GITHUB_REF_TYPE, DEFAULT_BRANCH and VERSION_OVERRIDE
from the environment. Prints the version and appends VERSION=<X.Y> to
$GITHUB_OUTPUT; on refusal prints a ::error:: workflow command and exits 1.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

import docsite

REPO_ROOT = Path(__file__).resolve().parent.parent
JENKINSFILE = Path("tools") / "jenkins" / "Jenkinsfile"

_VERSION_RE = re.compile(r"\d+\.\d+")
_TRUNK_RE = re.compile(r"""^\s*TRUNK_VERSION\s*=\s*['"]([^'"]*)['"]""", re.MULTILINE)


class ReleaseVersionError(Exception):
    """The version cannot be released from this ref."""


def trunk_version(jenkinsfile_text: str) -> str:
    """The value of the TRUNK_VERSION assignment in a Jenkinsfile."""
    match = _TRUNK_RE.search(jenkinsfile_text)
    if not match:
        raise ReleaseVersionError(f"no TRUNK_VERSION assignment in {JENKINSFILE.as_posix()}")
    return match.group(1)


def resolve(override: str, project_version: str, *, ref_name: str, ref_type: str,
            default_branch: str, trunk: str) -> str:
    """The version to release: override if given, else project_version.
    Raises ReleaseVersionError if it is not X.Y, or if the run is on the
    default branch and the version is not trunk."""
    version = override or project_version
    if not _VERSION_RE.fullmatch(version):
        raise ReleaseVersionError(f"invalid version {version!r} (expected X.Y)")
    if ref_type != "branch":
        return version
    if not default_branch:
        raise ReleaseVersionError("the repository's default branch is unknown; "
                                  "cannot tell whether this is a production run")
    if ref_name == default_branch and version != trunk:
        raise ReleaseVersionError(
            f"{version} cannot be released from {default_branch}, which documents the "
            f"trunk version {trunk} (TRUNK_VERSION in {JENKINSFILE.as_posix()}). "
            f"Release-branch versions are published from their vM.0 branch: run this "
            f"workflow on v{version}. If {default_branch} now documents {version}, "
            f"update TRUNK_VERSION first.")
    return version


def project_version(project: Path) -> str:
    with open(project / "zensical.toml", "rb") as f:
        return str(tomllib.load(f)["project"]["extra"]["version_majmin"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    env = os.environ
    try:
        version = resolve(
            env.get("VERSION_OVERRIDE", "").strip(),
            project_version(docsite.find_project(args.repo_root)),
            ref_name=env.get("GITHUB_REF_NAME", ""),
            ref_type=env.get("GITHUB_REF_TYPE", ""),
            default_branch=env.get("DEFAULT_BRANCH", ""),
            trunk=trunk_version((args.repo_root / JENKINSFILE).read_text(encoding="utf-8")),
        )
    except (ReleaseVersionError, OSError, KeyError) as error:
        print(f"::error::release_version: {error}")
        return 1
    print(f"Version: {version}")
    output = env.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"VERSION={version}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
