"""Replay the move to Zensical on a fresh local clone of Dyalog/documentation.

    uv run python tools/cutover/cutover.py --upstream PATH --freeze REF \\
        --tooling PATH --version X.Y [--branch NAME] [--theme-url URL] \\
        [--uv "uvx uv@0.13.9"]

--upstream is a fresh clone of the documentation repository, --freeze the
commit that becomes the v21.0 release branch and the base of the cutover,
--tooling this repository (its committed files are what gets copied),
--version the version main documents from the cutover on (the trunk version,
TRUNK_VERSION in tools/jenkins/Jenkinsfile, e.g. 22.0), --theme-url the
house-style repository the root .gitmodules names. The clone's origin must
be the GitHub repository: zensical.toml's repo_url is derived from it.

On the clone, in this order:

1. Sets the push URL of every remote to `no_push`, and refuses to continue
   if that fails. This script never pushes.
2. Creates the local branch v21.0 at the freeze commit, and the cutover
   branch (default zensical-cutover) from it, checked out.
3. Commit A: moves every guide's files from <guide>/docs/ to
   docs/<alias>/, the path the guide is served at (the root project's docs/
   is already in place), and language-bar/ to tools/language-bar/. Renames
   only, with the blobs unchanged, so `git log --follow` keeps each page's
   history; assert_pure_renames verifies it.
4. Commit B: first refuses to continue if the tooling's copy of any file
   upstream owns (the Jenkins files, language bar, image sources, issue
   templates: every file both trees hold, except INTENTIONALLY_REPLACED)
   differs from upstream's, since copying it would silently revert upstream.
   Then the content transforms, the content overlay, zensical.toml (with
   --version as its version variables and repo_url from the origin) and
   overrides/ at the root, the house-style submodule at
   docs/documentation-theme-zensical, this repository's tooling (minus
   tools/cutover/, the PoC project under zensical/ and design/), and the
   MkDocs-only and Docker files deleted, and tools/cutover/README.root.md
   as README.md. tools/jenkins/* stays.
5. Asserts the workflow files old release branches dispatch exist, fetches
   the house-style submodule and runs `uv run python tools/check.py`.
6. Prints the manual steps that remain, pushing among them.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # tools/, for docsite

import convert  # noqa: E402
import docsite  # noqa: E402

RELEASE_BRANCH = "v21.0"
DEFAULT_BRANCH = "zensical-cutover"

# Top-level upstream paths that move whole in commit A, so their history
# follows them. Upstream language-bar/ is byte-identical to this repository's
# tools/language-bar/.
RELOCATED = {"language-bar": "tools/language-bar"}

# Upstream paths commit B deletes (besides each guide's mkdocs.yml). chm/ is
# replaced by tools/chm/; everything else is MkDocs-only or Docker tooling.
DELETED = (
    "mkdocs.yml",
    "chm",
    "pdf",
    "documentation-assets",
    "tools/docker-compose.yml",
    "tools/nginx",
    "tools/utils",
    "tools/.env.template",
    "tools/mkdocs",
    "tools/one_shot_transform.py",
    "tools/requirements-docs.txt",
    "tools/README.md",
)

# Paths of the tooling repository that are not copied: the cutover tooling
# itself, the PoC project (regenerated in place by commit B), planning notes,
# and .gitmodules (written for the root layout).
NOT_COPIED = ("tools/cutover/", "zensical/", "design/", ".gitmodules")

# Upstream files the tooling deliberately replaces with its own version: the
# README and contributing guide, .gitignore (upstream's ignores *.toml and
# *.lock), dependabot, the workflows and the GUI audit tools (ported). "*"
# matches within one path segment, a trailing "/**" a whole directory. The
# UPSTREAM_OWNED paths are carved out of these patterns. Every other file both
# trees hold must be byte-identical, mode included: see upstream_owned_drift.
INTENTIONALLY_REPLACED = (
    "README.md",
    "CONTRIBUTE.md",
    ".gitignore",
    ".github/dependabot.yml",
    ".github/workflows/*.yml",
    "tools/buildgui/**",
)
UPSTREAM_OWNED = (".github/workflows/test-jenkins-simulation.yml",)

# Installed as README.md: the README for the repository after the cutover.
ROOT_README = "README.root.md"

THEME = docsite.ASSET_DIR
DEFAULT_THEME_URL = "https://github.com/xpqz/documentation-theme-zensical"
THEME_PATH = f"docs/{THEME}"
THEME_PATHS_IN_TOOLING = (f"zensical/docs/{THEME}", THEME_PATH)

# The workflow names release branches dispatch, which GitHub only offers when
# a file of that name exists on the default branch.
STUB_WORKFLOWS = ("mkdocs-publish.yml", "mkdocs-chm.yml", "mkdocs-pdf.yml", "full-release.yml")

# The edit link points into the content tree, which moves from zensical/docs
# to docs.
POC_EDIT_URI = 'edit_uri = "edit/main/zensical/docs"'
ROOT_EDIT_URI = 'edit_uri = "edit/main/docs"'

NO_PUSH = "no_push"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)")
_GITHUB_URL_RE = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([^/\s]+)/([^/\s]+?)(?:\.git)?/?")
_REPO_URL_RE = re.compile(r'^repo_url = ".*"$', re.MULTILINE)


class CutoverError(Exception):
    """A cutover precondition or verification failed."""


def git(repo: Path, *args: str, input: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", input=input, check=False,
    )
    if result.returncode != 0:
        raise CutoverError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def disable_push(repo: Path) -> None:
    """Set every remote's push URL to no_push and verify it took."""
    for remote in git(repo, "remote").split():
        git(repo, "remote", "set-url", "--push", remote, NO_PUSH)
        if git(repo, "remote", "get-url", "--push", remote).strip() != NO_PUSH:
            raise CutoverError(f"could not disable pushing to remote {remote}")


def tracked_files(repo: Path) -> list[str]:
    return git(repo, "ls-files", "-z").split("\0")[:-1]


def tree_blobs(repo: Path, rev: str = "HEAD") -> dict[str, tuple[str, str]]:
    """{path: (mode, sha)} for every blob committed at rev (no gitlinks)."""
    blobs = {}
    for line in git(repo, "ls-tree", "-r", "-z", rev).split("\0")[:-1]:
        meta, path = line.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "blob":
            blobs[path] = (mode, sha)
    return blobs


def github_repo_url(url: str) -> str:
    """https://github.com/<owner>/<repo> for a GitHub clone URL (https or
    ssh, with or without .git). Raises CutoverError for anything else."""
    match = _GITHUB_URL_RE.fullmatch(url.strip())
    if not match:
        raise CutoverError(f"origin {url!r} is not a GitHub repository URL; repo_url "
                           "derives from it, so clone the repository from GitHub")
    return f"https://github.com/{match.group(1)}/{match.group(2)}"


# --- commit A ------------------------------------------------------------------


def subprojects(repo: Path) -> list[str]:
    """The guide directories, in the order the root nav includes them. A
    guide directory the root nav does not include is an error."""
    names = []

    def walk(nav):
        if isinstance(nav, str):
            match = convert._INCLUDE_RE.match(nav)
            if match:
                names.append(match.group(1))
        elif isinstance(nav, list):
            for entry in nav:
                walk(entry)
        elif isinstance(nav, dict):
            for value in nav.values():
                walk(value)

    walk(convert.load_yaml(repo / "mkdocs.yml").get("nav", []))
    configured = {p.parent.name for p in repo.glob("*/mkdocs.yml")}
    if configured - set(names):
        raise CutoverError(
            f"guides not in the root nav: {', '.join(sorted(configured - set(names)))}")
    return names


def load_configs(repo: Path) -> tuple[dict, dict]:
    """The root config and the ordered {guide: config} sub-configs."""
    root_config = convert.load_yaml(repo / "mkdocs.yml")
    return root_config, {name: convert.load_yaml(repo / name / "mkdocs.yml")
                         for name in subprojects(repo)}


def load_mounts(repo: Path) -> dict[str, str]:
    """{alias: guide directory} for the upstream clone."""
    return convert.mount_points(load_configs(repo)[1])


def rename_plan(upstream: Path, mounts: dict[str, str]) -> list[tuple[str, str]]:
    """(source, destination) for every tracked file commit A moves:
    <guide>/docs/x → docs/<alias>/x, plus the RELOCATED directories. The root
    project's docs/ is already at its destination and is not listed. Raises
    CutoverError if a destination is already taken."""
    files = tracked_files(upstream)
    prefixes = {f"{guide}/docs/": f"docs/{alias}/" for alias, guide in mounts.items()}
    prefixes.update({f"{src}/": f"{dst}/" for src, dst in RELOCATED.items()})
    plan = []
    for path in files:
        for src_prefix, dst_prefix in prefixes.items():
            if path.startswith(src_prefix):
                plan.append((path, dst_prefix + path[len(src_prefix):]))
                break
    taken = set(files) - {src for src, _ in plan}
    seen = set()
    clashes = []
    for _, dst in plan:
        if dst in taken or dst in seen:
            clashes.append(dst)
        seen.add(dst)
    if clashes:
        raise CutoverError("rename destinations already taken: " + ", ".join(clashes))
    return plan


def commit_renames(repo: Path, plan: list[tuple[str, str]]) -> str:
    """Commit A: move each planned file, reusing its blob and mode, and
    return the commit's sha."""
    staged = {}
    for line in git(repo, "ls-files", "-s", "-z").split("\0")[:-1]:
        meta, path = line.split("\t", 1)
        mode, sha, _stage = meta.split()
        staged[path] = (mode, sha)
    index_info = []
    for src, dst in plan:
        mode, sha = staged[src]
        index_info.append(f"0 {'0' * 40}\t{src}")
        index_info.append(f"{mode} {sha}\t{dst}")
    git(repo, "update-index", "-z", "--index-info", input="\0".join(index_info) + "\0")
    for src, dst in plan:
        os.renames(repo / src, repo / dst)
    git(repo, "update-index", "-q", "--refresh")
    git(repo, "commit", "-q", "-m", COMMIT_A_MESSAGE)
    return git(repo, "rev-parse", "HEAD").strip()


def assert_pure_renames(repo: Path, sha: str) -> None:
    """Raise unless every change in sha is a rename with unchanged content
    and mode."""
    raw = git(repo, "-c", "diff.renameLimit=0", "-c", "diff.renames=true",
              "diff", "-M100%", "-l0", "--raw", "-z", "--no-color", f"{sha}^", sha)
    fields = raw.split("\0")[:-1]
    bad = []
    i = 0
    while i < len(fields):
        old_mode, new_mode, _old, _new, status = fields[i].lstrip(":").split()
        paths = fields[i + 1:i + (3 if status[0] in "RC" else 2)]
        i += 1 + len(paths)
        if status != "R100" or old_mode != new_mode:
            bad.append(f"{status} {old_mode}->{new_mode} " + " -> ".join(paths))
    if bad:
        raise CutoverError(f"{sha} is not renames only:\n  " + "\n  ".join(bad))


# --- commit B ------------------------------------------------------------------


def version_variables(version: str) -> dict:
    """The [project.extra] version variables for an X.Y version, e.g. 22.0 →
    version_maj 22, version_min 0, version_majmin "22.0", version_condensed
    220. Raises ValueError for anything but X.Y."""
    match = _VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError(f"version {version!r} is not X.Y (for example 22.0)")
    maj, minor = match.groups()
    return {"version_maj": int(maj), "version_min": int(minor),
            "version_majmin": version, "version_condensed": int(maj + minor)}


def intentionally_replaced(path: str) -> bool:
    """Whether path matches INTENTIONALLY_REPLACED (and is not UPSTREAM_OWNED)."""
    if path in UPSTREAM_OWNED:
        return False
    posix = PurePosixPath(path)
    for pattern in INTENTIONALLY_REPLACED:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif "*" in pattern:
            glob = PurePosixPath(pattern)
            if posix.parent == glob.parent and fnmatch.fnmatchcase(posix.name, glob.name):
                return True
        elif path == pattern:
            return True
    return False


def upstream_owned_drift(repo: Path, tooling: Path) -> list[str]:
    """The files both the tooling commit and repo's HEAD (commit A, so
    upstream's language-bar/ is at tools/language-bar/) hold, outside
    NOT_COPIED and INTENTIONALLY_REPLACED, whose content or mode differ."""
    ours = tree_blobs(tooling)
    theirs = tree_blobs(repo)
    return sorted(path for path, blob in ours.items()
                  if path in theirs and blob != theirs[path]
                  and not path.startswith(NOT_COPIED) and not intentionally_replaced(path))


def _theme_gitlink(tooling: Path) -> str:
    """The commit of the tooling repository's house-style submodule."""
    for path in THEME_PATHS_IN_TOOLING:
        entry = git(tooling, "ls-tree", "HEAD", path).split()
        if entry and entry[0] == "160000":
            return entry[2]
    raise CutoverError(f"{tooling} has no {THEME} submodule")


def _copy_tooling(tooling: Path, repo: Path) -> dict[str, str]:
    """Write the tooling repository's committed files into repo, replacing
    what is there, except the NOT_COPIED paths and submodules. Returns
    {path: mode} for what was written."""
    copied = {}
    for path, (mode, sha) in tree_blobs(tooling).items():
        if path.startswith(NOT_COPIED):
            continue
        copied[path] = mode
        if mode not in ("100644", "100755"):
            raise CutoverError(f"{tooling}: {path} has unsupported mode {mode}")
        content = subprocess.run(["git", "-C", str(tooling), "cat-file", "blob", sha],
                                 check=True, capture_output=True).stdout
        dest = repo / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        dest.chmod(0o755 if mode == "100755" else 0o644)
    return copied


def _stage_modes(repo: Path, modes: dict[str, str]) -> None:
    """Set each path's staged mode to the given one. The working tree's
    executable bit carries no information where git does not track it
    (core.fileMode false, as on Windows)."""
    staged = {}
    for line in git(repo, "ls-files", "-s", "-z").split("\0")[:-1]:
        meta, path = line.split("\t", 1)
        staged[path] = meta.split()[0]
    for flag, mode in (("--chmod=+x", "100755"), ("--chmod=-x", "100644")):
        paths = [path for path, want in modes.items() if want == mode and staged[path] != mode]
        if paths:
            git(repo, "update-index", flag, "--", *paths)


def _write_toml(repo: Path, merged: dict, template: Path, version: str,
                repo_url: str) -> None:
    merged["extra"].update(version_variables(version))
    text = convert.render_zensical_toml(merged, template.read_text(encoding="utf-8"))
    if text.count(POC_EDIT_URI) != 1:
        raise CutoverError(f"{template} must contain {POC_EDIT_URI} exactly once")
    text, count = _REPO_URL_RE.subn(lambda _: f'repo_url = "{repo_url}"', text)
    if count != 1:
        raise CutoverError(f"{template} must contain one repo_url line")
    (repo / "zensical.toml").write_text(text.replace(POC_EDIT_URI, ROOT_EDIT_URI),
                                        encoding="utf-8")


def commit_transform(repo: Path, tooling: Path, *, version: str, repo_url: str,
                     theme_url: str = DEFAULT_THEME_URL) -> str:
    """Commit B on top of commit A: convert the content in place, swap the
    tooling, set the version variables to version and repo_url, mount the
    house style from theme_url, and return the commit's sha. The overlay,
    zensical.toml template, theme override and root README come from the
    tooling's tools/cutover/. Raises CutoverError, before writing anything,
    if the tooling would revert an upstream-owned file."""
    repo, tooling = Path(repo), Path(tooling)
    cut = tooling / "tools" / "cutover"
    overlay = cut / "content"
    docs = repo / "docs"

    version_variables(version)  # refuse a malformed version before writing
    drift = upstream_owned_drift(repo, tooling)
    if drift:
        raise CutoverError(
            "the tooling's copies of these upstream-owned files differ from upstream "
            "at the freeze commit, and copying them would revert upstream's changes:\n  "
            + "\n  ".join(drift)
            + "\nRe-sync the tooling copy from upstream (commit it), then rerun.")
    root_config, sub_configs = load_configs(repo)
    mounts = convert.mount_points(sub_configs)
    # The guides are already at their aliases, so docs/<rel> is the source page.
    convert.check_overlay(overlay, repo, {})
    theme_sha = _theme_gitlink(tooling)

    convert.remove_mathjax(docs, mounts)
    convert.transform_pages(docs, overlay)
    convert.apply_overlay(overlay, docs)
    (repo / convert.OVERRIDES_DIR).mkdir(exist_ok=True)
    shutil.copy2(cut / convert.OVERRIDES_DIR / "main.html",
                 repo / convert.OVERRIDES_DIR / "main.html")
    _write_toml(repo, convert.merge_configs(root_config, sub_configs),
                cut / "zensical.toml.template", version, repo_url)

    tracked = tracked_files(repo)
    doomed = [f"{guide}/mkdocs.yml" for guide in sub_configs] + [
        path for path in DELETED
        if any(f == path or f.startswith(path + "/") for f in tracked)]
    git(repo, "rm", "-r", "-q", "--", *doomed)

    copied = _copy_tooling(tooling, repo)
    shutil.copyfile(cut / ROOT_README, repo / "README.md")
    (repo / ".gitmodules").write_text(
        f'[submodule "{THEME_PATH}"]\n\tpath = {THEME_PATH}\n\turl = {theme_url}\n',
        encoding="utf-8")
    (repo / THEME_PATH).mkdir(exist_ok=True)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{theme_sha},{THEME_PATH}")
    git(repo, "add", "-A")
    _stage_modes(repo, copied)
    git(repo, "commit", "-q", "-m", COMMIT_B_MESSAGE)
    return git(repo, "rev-parse", "HEAD").strip()


def assert_stub_workflows(repo: Path) -> None:
    """Raise unless every workflow release branches dispatch is committed."""
    missing = [name for name in STUB_WORKFLOWS
               if not git(repo, "ls-files", f".github/workflows/{name}").strip()]
    if missing:
        raise CutoverError("missing dispatch workflows: " + ", ".join(missing))


# --- the whole run ---------------------------------------------------------------


def cutover(upstream: Path, freeze: str, tooling: Path, branch: str = DEFAULT_BRANCH,
            *, version: str, theme_url: str = DEFAULT_THEME_URL,
            init_submodule: bool = True,
            check_command: Sequence[str] | None = ("uv", "run", "python", "tools/check.py"),
            ) -> tuple[str, str, str]:
    """Run the cutover on the clone at upstream; return the freeze commit,
    commit A and commit B."""
    upstream, tooling = Path(upstream).resolve(), Path(tooling).resolve()
    disable_push(upstream)
    repo_url = github_repo_url(git(upstream, "remote", "get-url", "origin"))
    if git(upstream, "status", "--porcelain").strip():
        raise CutoverError(f"{upstream} has uncommitted changes")
    base = git(upstream, "rev-parse", "--verify", f"{freeze}^{{commit}}").strip()
    for name in (RELEASE_BRANCH, branch):
        if git(upstream, "branch", "--list", name).strip():
            raise CutoverError(
                f"branch {name} already exists in {upstream} (from an earlier run?). "
                f"Start again from a fresh clone, or check out the original branch and "
                f"delete it: git branch -D {RELEASE_BRANCH} {branch}")
    git(upstream, "branch", RELEASE_BRANCH, base)
    git(upstream, "checkout", "-q", "-b", branch, base)

    a = commit_renames(upstream, rename_plan(upstream, load_mounts(upstream)))
    assert_pure_renames(upstream, a)
    b = commit_transform(upstream, tooling, version=version, repo_url=repo_url,
                         theme_url=theme_url)
    assert_stub_workflows(upstream)

    if init_submodule:
        git(upstream, "submodule", "update", "--init", THEME_PATH)
    if check_command:
        env = {k: v for k, v in os.environ.items() if k not in ("DOCS_PROJECT", "VIRTUAL_ENV")}
        result = subprocess.run(list(check_command), cwd=upstream, env=env, check=False)
        if result.returncode != 0:
            raise CutoverError(f"the check ({shlex.join(check_command)}) exited "
                               f"{result.returncode} on {upstream}")
    return base, a, b


COMMIT_A_MESSAGE = """\
Move every guide's files to the path it is served at

Each guide's docs/ moves to docs/<alias>/, the mount point the site gave
it (for the two .NET guides that is their slugified site name, not their
directory name), and language-bar/ moves to tools/language-bar/. Renames
only, no content changes, so the history of every page follows it.
"""

COMMIT_B_MESSAGE = """\
Build the documentation with Zensical

The guides are one Zensical project now: zensical.toml at the root, the
content transformed from the MkDocs house markup into plain Markdown, and
the house style mounted as a submodule at docs/documentation-theme-zensical.
The MkDocs configuration, the Docker tooling, chm/ and pdf/ give way to the
checks, maintenance scripts, CHM build and workflows under tools/ and
.github/. tools/jenkins/ is unchanged.
"""

NEXT_STEPS = """\
Cutover branch {branch} is ready in {upstream}:
  {a}  commit A (renames only, verified)
  {b}  commit B (Zensical)
  {release} at {base}

zensical.toml: version {version}, repo_url {repo_url}
House style: {theme_url}

Nothing has been pushed; every remote's push URL is no_push.
Remaining manual steps, each needing explicit approval:
  - review the two commits;
  - push {release} and {branch}, and open the pull request into main;
  - on {release}, pin the documentation-assets submodule in mkdocs-publish.yml
    (tools/jenkins/RELEASE.md procedure E);
  - check that TRUNK_VERSION in tools/jenkins/Jenkinsfile is {version}, the
    version zensical.toml now carries: publish.yml and chm.yml refuse to
    release any other version from the default branch;
  - move the house-style repository ({theme_url}) to the Dyalog
    organisation, then point .gitmodules at its new URL and run
    `git submodule sync`.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--upstream", type=Path, required=True,
                        help="fresh local clone of the documentation repository")
    parser.add_argument("--freeze", required=True, help="the content-freeze commit")
    parser.add_argument("--tooling", type=Path, required=True,
                        help="this repository (its committed files are copied)")
    parser.add_argument("--version", required=True,
                        help="the version main documents from now on, X.Y (TRUNK_VERSION)")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--theme-url", default=DEFAULT_THEME_URL,
                        help="the house-style repository for the root .gitmodules "
                             f"(default {DEFAULT_THEME_URL})")
    parser.add_argument("--uv", default="uv", help='the uv command, e.g. "uvx uv@0.13.9"')
    args = parser.parse_args(argv)
    check = [*shlex.split(args.uv), "run", "python", "tools/check.py"]
    try:
        base, a, b = cutover(args.upstream, args.freeze, args.tooling, args.branch,
                             version=args.version, theme_url=args.theme_url,
                             check_command=check)
    except (CutoverError, ValueError, FileNotFoundError) as error:
        print(f"cutover: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.decode("utf-8", "replace") if isinstance(error.stderr, bytes) \
            else (error.stderr or "")
        print(f"cutover: {shlex.join(map(str, error.cmd))} exited {error.returncode}: "
              f"{stderr.strip()}", file=sys.stderr)
        return 1
    print(NEXT_STEPS.format(branch=args.branch, upstream=args.upstream.resolve(), a=a, b=b,
                            release=RELEASE_BRANCH, base=base, version=args.version,
                            repo_url=github_repo_url(
                                git(args.upstream, "remote", "get-url", "origin")),
                            theme_url=args.theme_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
