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
    UPSTREAM_URL,
    build_overlay_tree,
    build_tooling_repo,
    build_upstream_repo,
    git,
    tree_digest,
)

CUTOVER_DIR = Path(cutover.__file__).resolve().parent

VERSION = "22.0"
REPO_URL = "https://github.com/Dyalog/documentation"
COMMIT_B_ARGS = {"version": VERSION, "repo_url": REPO_URL, "theme_url": THEME_URL}


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
    b = cutover.commit_transform(upstream, tooling, **COMMIT_B_ARGS)
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


def test_assert_pure_renames_rejects_a_mode_change(upstream):
    git(upstream, "mv", "docs/conventions.md", "docs/moved.md")
    git(upstream, "update-index", "--chmod=+x", "docs/moved.md")
    git(upstream, "commit", "-q", "-m", "Rename and make executable")
    sha = git(upstream, "rev-parse", "HEAD").strip()
    with pytest.raises(cutover.CutoverError, match="docs/moved.md"):
        cutover.assert_pure_renames(upstream, sha)


def test_assert_pure_renames_rejects_deletions(upstream):
    git(upstream, "rm", "-q", "docs/conventions.md")
    git(upstream, "commit", "-q", "-m", "Delete only")
    sha = git(upstream, "rev-parse", "HEAD").strip()
    with pytest.raises(cutover.CutoverError, match="docs/conventions.md"):
        cutover.assert_pure_renames(upstream, sha)


def test_assert_pure_renames_rejects_copies(upstream):
    (upstream / "docs" / "copy.md").write_bytes((upstream / "docs" / "conventions.md").read_bytes())
    git(upstream, "add", "docs/copy.md")
    git(upstream, "commit", "-q", "-m", "Copy")
    sha = git(upstream, "rev-parse", "HEAD").strip()
    with pytest.raises(cutover.CutoverError, match="docs/copy.md"):
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
    deliberate = ("edit_uri = ", "repo_url = ", "version_")
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


@pytest.mark.parametrize("path, renamed", [
    ("tools/jenkins/Jenkinsfile", "tools/jenkins/Jenkinsfile"),
    ("language-bar/toc.json", "tools/language-bar/toc.json"),
])
def test_commit_b_refuses_to_revert_upstream_owned_files(upstream, tooling, path, renamed):
    """An upstream change after the tooling copied a file it owns stops the
    cutover before commit B, instead of being silently reverted."""
    (upstream / path).write_text("changed upstream\n", encoding="utf-8", newline="\n")
    git(upstream, "commit", "-q", "-am", "Upstream edit after the tooling copied it")
    plan = cutover.rename_plan(upstream, cutover.load_mounts(upstream))
    a = cutover.commit_renames(upstream, plan)
    with pytest.raises(cutover.CutoverError, match=renamed) as raised:
        cutover.commit_transform(upstream, tooling, **COMMIT_B_ARGS)
    assert "re-sync the tooling copy from upstream" in str(raised.value).lower()
    assert git(upstream, "rev-parse", "HEAD").strip() == a
    assert git(upstream, "status", "--porcelain") == ""


def test_upstream_owned_mode_change_is_refused(upstream, tooling):
    git(upstream, "update-index", "--chmod=-x", "tools/jenkins/get_svn_docbin")
    git(upstream, "commit", "-q", "-m", "Upstream drops the exec bit")
    plan = cutover.rename_plan(upstream, cutover.load_mounts(upstream))
    cutover.commit_renames(upstream, plan)
    with pytest.raises(cutover.CutoverError, match="get_svn_docbin"):
        cutover.commit_transform(upstream, tooling, **COMMIT_B_ARGS)


@pytest.mark.parametrize("path, replaced", [
    ("README.md", True),
    ("CONTRIBUTE.md", True),
    (".gitignore", True),
    (".github/dependabot.yml", True),
    (".github/workflows/mkdocs-publish.yml", True),
    (".github/workflows/test-jenkins-simulation.yml", False),
    (".github/workflows/nested/x.yml", False),
    ("tools/buildgui/regen_properties.py", True),
    ("tools/buildgui/ObjectMembers/NetType/PropList.apla", True),
    ("tools/jenkins/Jenkinsfile", False),
    ("tools/language-bar/toc.json", False),
    ("img-src/x.svg", False),
    (".github/ISSUE_TEMPLATE/content_error.md", False),
    ("docs/README.md", False),
])
def test_intentionally_replaced(path, replaced):
    assert cutover.intentionally_replaced(path) is replaced


@pytest.mark.parametrize("file_mode", ["true", "false"])
def test_commit_b_keeps_the_tooling_modes(upstream, tooling, file_mode):
    """Modes come from the tooling commit, also where git ignores the working
    tree's executable bit (core.fileMode false, as on Windows)."""
    git(upstream, "config", "core.fileMode", file_mode)
    run_both_commits(upstream, tooling)
    for path in ("tools/chm/build_chm.py", "tools/jenkins/get_svn_docbin"):
        assert git(upstream, "ls-files", "-s", path).split()[0] == "100755"
    assert git(upstream, "ls-files", "-s", "tools/check.py").split()[0] == "100644"


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
    assert show(upstream, "README.md") == (CUTOVER_DIR / "README.root.md").read_bytes()
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


def test_commit_b_uses_the_given_theme_url(upstream, tooling):
    plan = cutover.rename_plan(upstream, cutover.load_mounts(upstream))
    cutover.commit_renames(upstream, plan)
    cutover.commit_transform(upstream, tooling, version=VERSION, repo_url=REPO_URL,
                             theme_url="https://github.com/Dyalog/documentation-theme-zensical")
    assert git(upstream, "config", "-f", ".gitmodules",
               "submodule.docs/documentation-theme-zensical.url").strip() == \
        "https://github.com/Dyalog/documentation-theme-zensical"


def test_commit_b_installs_the_root_readme(upstream, tooling):
    run_both_commits(upstream, tooling)
    readme = show(upstream, "README.md").decode("utf-8")
    for poc in ("zensical/", "design/", "tooling-port", "convert.py"):
        assert poc not in readme
    assert "CONTRIBUTE.md" in readme and "--recurse-submodules" in readme


def test_commit_b_refuses_a_stale_overlay(upstream, tooling):
    plan = cutover.rename_plan(upstream, cutover.load_mounts(upstream))
    cutover.commit_renames(upstream, plan)
    (upstream / "docs" / "index.md").write_text("# Edited upstream\n")
    with pytest.raises(ValueError, match="index.md"):
        cutover.commit_transform(upstream, tooling, **COMMIT_B_ARGS)


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


@pytest.mark.parametrize("origin", [
    "https://github.com/Dyalog/documentation.git",
    "git@github.com:Dyalog/documentation.git",
])
def test_cutover_points_repo_url_at_the_origin(upstream, tooling, origin):
    git(upstream, "remote", "set-url", "origin", origin)
    cutover.cutover(upstream, "HEAD", tooling, "zensical-cutover", version=VERSION,
                    init_submodule=False, check_command=None)
    with open(upstream / "zensical.toml", "rb") as f:
        assert tomllib.load(f)["project"]["repo_url"] == "https://github.com/Dyalog/documentation"


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/Dyalog/documentation", "https://github.com/Dyalog/documentation"),
    ("https://github.com/Dyalog/documentation.git", "https://github.com/Dyalog/documentation"),
    ("https://github.com/dyalog/documentation/", "https://github.com/dyalog/documentation"),
    ("git@github.com:Dyalog/documentation.git", "https://github.com/Dyalog/documentation"),
    ("ssh://git@github.com/Dyalog/documentation.git", "https://github.com/Dyalog/documentation"),
])
def test_github_repo_url(url, expected):
    assert cutover.github_repo_url(url) == expected


@pytest.mark.parametrize("url", ["/tmp/documentation", "https://gitlab.com/a/b",
                                 "https://github.com/Dyalog"])
def test_github_repo_url_refuses_others(url):
    with pytest.raises(cutover.CutoverError, match="GitHub"):
        cutover.github_repo_url(url)


def test_cutover_refuses_an_existing_release_branch(upstream, tooling):
    git(upstream, "branch", "v21.0")
    with pytest.raises(cutover.CutoverError, match="v21.0.*fresh clone"):
        cutover.cutover(upstream, "HEAD", tooling, "zensical-cutover", version=VERSION,
                        init_submodule=False, check_command=None)


def test_main_prints_the_next_steps(upstream, tooling, monkeypatch, capsys):
    monkeypatch.setattr(cutover, "cutover", lambda *args, **kwargs: ("base", "aaa", "bbb"))
    assert cutover.main(["--upstream", str(upstream), "--freeze", "HEAD",
                         "--tooling", str(tooling), "--version", VERSION]) == 0
    out = capsys.readouterr().out
    assert "version 22.0, repo_url https://github.com/Dyalog/documentation" in out
    assert "TRUNK_VERSION in tools/jenkins/Jenkinsfile is 22.0" in out
    assert THEME_URL in out and ".gitmodules" in out


def test_main_reports_a_failing_git_command(upstream, tooling, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "cat-file"], stderr=b"fatal: bad object")
    monkeypatch.setattr(cutover, "cutover", fail)
    assert cutover.main(["--upstream", str(upstream), "--freeze", "HEAD",
                         "--tooling", str(tooling), "--version", VERSION]) == 1
    err = capsys.readouterr().err
    assert err.startswith("cutover: ") and "fatal: bad object" in err


def test_cutover_reports_a_failing_check(upstream, tooling):
    with pytest.raises(cutover.CutoverError, match="check"):
        cutover.cutover(upstream, "HEAD", tooling, "zensical-cutover", version=VERSION,
                        init_submodule=False, check_command=["git", "no-such-command"])
