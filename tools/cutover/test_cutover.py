"""Behavioural surface for the cutover script.

Every test runs against git repositories built from the miniature monorepo
(fixture_tree.build_upstream_repo, a clone with an origin) and a miniature
of this repository (fixture_tree.build_tooling_repo). Nothing touches the
network: the house-style submodule is recorded as a gitlink, never fetched.
"""

import subprocess
import tomllib
from pathlib import Path

import convert
import cutover
import pytest
from fixture_tree import (
    JENKINS_FILES,
    STUB_WORKFLOWS,
    THEME_GITLINK,
    THEME_URL,
    build_overlay_tree,
    build_tooling_repo,
    build_upstream_repo,
    git,
    tree_digest,
)

CUTOVER_DIR = Path(cutover.__file__).resolve().parent

VERSION = "22.0"


@pytest.fixture
def upstream(tmp_path):
    """A fresh clone of the miniature upstream, on main."""
    return build_upstream_repo(tmp_path / "up")


@pytest.fixture
def tooling(tmp_path, upstream):
    overlay = build_overlay_tree(tmp_path / "overlay", tmp_path / "up" / "documentation")
    return build_tooling_repo(tmp_path, overlay, CUTOVER_DIR)


def committed(repo, rev="HEAD"):
    return set(git(repo, "ls-tree", "-r", "--name-only", rev).splitlines())


def show(repo, path, rev="HEAD"):
    return subprocess.run(
        ["git", "-C", str(repo), "show", f"{rev}:{path}"], check=True, capture_output=True
    ).stdout


def run_both_commits(upstream, tooling):
    plan = cutover.rename_plan(upstream, cutover.load_mounts(upstream))
    a = cutover.commit_renames(upstream, plan)
    b = cutover.commit_transform(upstream, tooling, version=VERSION)
    return a, b


# --- commit A: renames ---------------------------------------------------


def test_rename_plan_maps_net_guides_to_aliases(upstream):
    plan = dict(cutover.rename_plan(upstream, cutover.load_mounts(upstream)))
    assert plan["dotnet-interface-guide/docs/index.md"] == "docs/net-interface-guide/index.md"
    assert plan["compiler-user-guide/docs/basic-usage.md"] == "docs/compiler-user-guide/basic-usage.md"
    # Images and other assets move with the pages.
    assert plan["release-notes/docs/images/logo.png"] == "docs/release-notes/images/logo.png"
    # The root project's docs/ is already at its target.
    assert not any(src.startswith("docs/") for src in plan)
    # Configs are not moved; they are deleted in the second commit.
    assert not any(src.endswith("mkdocs.yml") for src in plan)
    # The language bar keeps its history under tools/.
    assert plan["language-bar/toc.json"] == "tools/language-bar/toc.json"


def test_rename_plan_rejects_collisions(upstream):
    (upstream / "docs" / "compiler-user-guide").mkdir()
    (upstream / "docs" / "compiler-user-guide" / "index.md").write_text("# Clash\n")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-q", "-m", "A root page where a guide will land")
    with pytest.raises(cutover.CutoverError, match="docs/compiler-user-guide/index.md"):
        cutover.rename_plan(upstream, cutover.load_mounts(upstream))


def test_commit_a_is_pure_renames(upstream):
    before = git(upstream, "rev-parse", "HEAD").strip()
    plan = cutover.rename_plan(upstream, cutover.load_mounts(upstream))
    sha = cutover.commit_renames(upstream, plan)

    assert git(upstream, "rev-parse", f"{sha}^").strip() == before
    status = git(upstream, "diff", "-M100%", "--name-status", f"{sha}^", sha).splitlines()
    assert len(status) == len(plan)
    assert all(line.startswith("R100\t") for line in status)
    cutover.assert_pure_renames(upstream, sha)

    for src, dst in plan:
        assert show(upstream, dst, sha) == show(upstream, src, before)
        assert (upstream / dst).is_file() and not (upstream / src).exists()
    # The working tree matches the commit: nothing left to stage.
    assert git(upstream, "status", "--porcelain") == ""


def test_commit_a_keeps_page_history(upstream):
    plan = cutover.rename_plan(upstream, cutover.load_mounts(upstream))
    cutover.commit_renames(upstream, plan)
    log = git(upstream, "log", "--follow", "--format=%s", "--",
              "docs/compiler-user-guide/basic-usage.md").splitlines()
    assert "Edit a page before the freeze" in log
    assert "Upstream history before the cutover" in log


def test_assert_pure_renames_rejects_edits(upstream):
    git(upstream, "mv", "docs/conventions.md", "docs/moved.md")
    (upstream / "docs" / "moved.md").write_text("# Conventions\n\nEdited.\n")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-q", "-m", "Rename and edit")
    sha = git(upstream, "rev-parse", "HEAD").strip()
    with pytest.raises(cutover.CutoverError, match="docs/conventions.md"):
        cutover.assert_pure_renames(upstream, sha)


def test_assert_pure_renames_rejects_additions(upstream):
    git(upstream, "mv", "docs/conventions.md", "docs/moved.md")
    (upstream / "docs" / "new.md").write_text("# New\n")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-q", "-m", "Rename and add")
    sha = git(upstream, "rev-parse", "HEAD").strip()
    with pytest.raises(cutover.CutoverError, match="docs/new.md"):
        cutover.assert_pure_renames(upstream, sha)


# --- commit B: transforms and tooling --------------------------------------


def test_commit_b_content_matches_the_conversion(upstream, tooling, tmp_path):
    """Commit B reproduces, in place, what convert.py generates out of place."""
    source = tmp_path / "up" / "documentation"
    oracle = tmp_path / "oracle"
    convert.convert(
        source, oracle,
        subprojects=cutover.subprojects(upstream),
        overlay=tooling / "tools" / "cutover" / "content",
        template=tooling / "tools" / "cutover" / "zensical.toml.template",
    )
    run_both_commits(upstream, tooling)

    docs = tree_digest(upstream / "docs")
    assert docs == tree_digest(oracle / "docs")
    assert tree_digest(upstream / "overrides") == tree_digest(oracle / "overrides")
    # zensical.toml is the conversion's, apart from the lines the cutover sets
    # deliberately: edit_uri and the version (asserted on their own below).
    deliberate = ("edit_uri = ", "version_")
    expected = [line for line in (oracle / "zensical.toml").read_text(encoding="utf-8")
                .splitlines() if not line.startswith(deliberate)]
    actual = [line for line in (upstream / "zensical.toml").read_text(encoding="utf-8")
              .splitlines() if not line.startswith(deliberate)]
    assert actual == expected
    assert 'edit_uri = "edit/main/docs"' in (upstream / "zensical.toml").read_text(
        encoding="utf-8")
    assert git(upstream, "status", "--porcelain") == ""


def test_commit_b_sets_the_version(upstream, tooling):
    run_both_commits(upstream, tooling)
    with open(upstream / "zensical.toml", "rb") as f:
        extra = tomllib.load(f)["project"]["extra"]
    assert extra["version_maj"] == 22
    assert extra["version_min"] == 0
    assert extra["version_majmin"] == "22.0"
    assert extra["version_condensed"] == 220
    # Other variables are carried over as converted.
    assert extra["generator"] is False


@pytest.mark.parametrize("version, expected", [
    ("22.0", (22, 0, "22.0", 220)),
    ("21.1", (21, 1, "21.1", 211)),
])
def test_version_variables(version, expected):
    assert cutover.version_variables(version) == dict(zip(
        ("version_maj", "version_min", "version_majmin", "version_condensed"), expected))


@pytest.mark.parametrize("bad", ["22", "v22.0", "22.0.1", "22.x", ""])
def test_version_variables_reject_malformed(bad):
    with pytest.raises(ValueError, match="X.Y"):
        cutover.version_variables(bad)


def test_main_requires_a_version(upstream, tooling, capsys):
    with pytest.raises(SystemExit):
        cutover.main(["--upstream", str(upstream), "--freeze", "HEAD",
                      "--tooling", str(tooling)])
    assert "--version" in capsys.readouterr().err


def test_jenkins_files_survive(upstream, tooling):
    before = {path: show(upstream, path) for path in JENKINS_FILES}
    run_both_commits(upstream, tooling)
    for path, content in before.items():
        assert show(upstream, path) == content
    mode = git(upstream, "ls-files", "-s", "tools/jenkins/get_svn_docbin").split()[0]
    assert mode == "100755"


def test_stub_workflows_present(upstream, tooling):
    run_both_commits(upstream, tooling)
    cutover.assert_stub_workflows(upstream)
    for name in STUB_WORKFLOWS:
        assert show(upstream, f".github/workflows/{name}") == f"name: stub {name}\n".encode()

    git(upstream, "rm", "-q", ".github/workflows/mkdocs-pdf.yml")
    with pytest.raises(cutover.CutoverError, match="mkdocs-pdf.yml"):
        cutover.assert_stub_workflows(upstream)


def test_commit_b_layout(upstream, tooling):
    run_both_commits(upstream, tooling)
    files = committed(upstream)
    top = {path.split("/")[0] for path in files}
    assert top == {
        ".gitattributes", ".github", ".gitignore", ".gitmodules", ".python-version",
        ".vscode", "CONTRIBUTE.md", "LICENCE", "README.md", "docs", "known-issues.txt",
        "overrides", "pyproject.toml", "tools", "unlisted-pages.txt", "uv.lock",
        "zensical.toml",
    }
    # MkDocs-only and Docker files are gone, the guide directories with them.
    for gone in ("mkdocs.yml", "chm/mkdocs2chm.py", "pdf/mkdocs2pdf.py",
                 "tools/docker-compose.yml", "tools/nginx/nginx.conf",
                 "tools/utils/doc_utils.py", "tools/.env.template",
                 "tools/mkdocs/Dockerfile", "tools/one_shot_transform.py",
                 "tools/requirements-docs.txt", "tools/README.md",
                 "documentation-assets", "compiler-user-guide/mkdocs.yml"):
        assert gone not in files
    assert not any(path.startswith("compiler-user-guide/") for path in files)
    # This repository's tooling replaces upstream's, minus the cutover itself,
    # the PoC project, planning notes and untracked files.
    assert show(upstream, "tools/buildgui/regen_properties.py") == b"# ported version\n"
    assert show(upstream, "README.md") == b"# Zensical readme\n"
    assert "tools/check.py" in files and "uv.lock" in files
    assert not any(path.startswith(("tools/cutover/", "zensical/", "design/")) for path in files)
    assert "tools/scratch.txt" not in files
    # The MathJax loader is not carried over.
    assert "docs/javascripts/mathjax.js" not in files


def test_commit_b_mounts_the_house_style(upstream, tooling):
    run_both_commits(upstream, tooling)
    entry = git(upstream, "ls-files", "-s", "docs/documentation-theme-zensical").split()
    assert entry[:2] == ["160000", THEME_GITLINK]
    path = "submodule.docs/documentation-theme-zensical"
    assert git(upstream, "config", "-f", ".gitmodules", f"{path}.path").strip() == \
        "docs/documentation-theme-zensical"
    assert git(upstream, "config", "-f", ".gitmodules", f"{path}.url").strip() == THEME_URL
    assert "documentation-assets" not in (upstream / ".gitmodules").read_text()


def test_commit_b_refuses_a_stale_overlay(upstream, tooling):
    plan = cutover.rename_plan(upstream, cutover.load_mounts(upstream))
    cutover.commit_renames(upstream, plan)
    (upstream / "docs" / "index.md").write_text("# Edited upstream\n")
    with pytest.raises(ValueError, match="index.md"):
        cutover.commit_transform(upstream, tooling, version=VERSION)


# --- the whole run -----------------------------------------------------------


def test_push_url_disabled(upstream):
    git(upstream, "remote", "add", "mirror", str(upstream.parent / "documentation"))
    cutover.disable_push(upstream)
    for remote in ("origin", "mirror"):
        assert git(upstream, "remote", "get-url", "--push", remote).strip() == "no_push"
    pushed = subprocess.run(["git", "-C", str(upstream), "push", "origin", "main"],
                            capture_output=True, check=False)
    assert pushed.returncode != 0


def test_disable_push_refuses_when_it_cannot(tmp_path):
    with pytest.raises(cutover.CutoverError):
        cutover.disable_push(tmp_path)


def test_cutover_branches(upstream, tooling, tmp_path):
    origin = tmp_path / "up" / "documentation"
    origin_refs = git(origin, "show-ref")
    freeze = git(upstream, "rev-parse", "HEAD~1").strip()

    cutover.cutover(upstream, freeze, tooling, "zensical-cutover", version=VERSION,
                    init_submodule=False, check_command=None)

    assert git(upstream, "rev-parse", "v21.0").strip() == freeze
    assert git(upstream, "rev-parse", "zensical-cutover~2").strip() == freeze
    assert git(upstream, "branch", "--show-current").strip() == "zensical-cutover"
    assert git(upstream, "remote", "get-url", "--push", "origin").strip() == "no_push"
    # Nothing reached the origin.
    assert git(origin, "show-ref") == origin_refs


def test_cutover_refuses_an_existing_release_branch(upstream, tooling):
    git(upstream, "branch", "v21.0")
    with pytest.raises(cutover.CutoverError, match="v21.0"):
        cutover.cutover(upstream, "HEAD", tooling, "zensical-cutover", version=VERSION,
                        init_submodule=False, check_command=None)


def test_cutover_reports_a_failing_check(upstream, tooling):
    with pytest.raises(cutover.CutoverError, match="check"):
        cutover.cutover(upstream, "HEAD", tooling, "zensical-cutover", version=VERSION,
                        init_submodule=False, check_command=["git", "no-such-command"])
