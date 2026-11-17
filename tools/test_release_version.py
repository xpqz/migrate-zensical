import pytest

import release_version
from release_version import ReleaseVersionError, resolve

JENKINSFILE = """\
def isTrunk(version) {
    if (version == env.TRUNK_VERSION) {
        return true
    }
}
pipeline {
    environment {
        TRUNK_VERSION = '22.0'
        PRODUCTION_VERSIONS = '20.0 21.0 22.0'
    }
}
"""

TOML = '[project]\nsite_name = "Docs"\n\n[project.extra]\nversion_majmin = "{v}"\n'


def test_trunk_version_reads_the_assignment():
    assert release_version.trunk_version(JENKINSFILE) == "22.0"


def test_trunk_version_missing():
    with pytest.raises(ReleaseVersionError, match="TRUNK_VERSION"):
        release_version.trunk_version("pipeline { }\n")


def test_default_branch_matching_trunk_is_allowed():
    assert resolve("", "22.0", ref_name="main", ref_type="branch",
                   default_branch="main", trunk="22.0") == "22.0"


def test_default_branch_other_version_is_refused():
    with pytest.raises(ReleaseVersionError, match=r"21\.0.*22\.0.*v21\.0"):
        resolve("", "21.0", ref_name="main", ref_type="branch",
                default_branch="main", trunk="22.0")


def test_other_branch_is_not_guarded():
    assert resolve("", "21.0", ref_name="tooling-port", ref_type="branch",
                   default_branch="main", trunk="22.0") == "21.0"


def test_tag_named_like_the_default_branch_is_not_guarded():
    assert resolve("", "21.0", ref_name="main", ref_type="tag",
                   default_branch="main", trunk="22.0") == "21.0"


def test_override_wins_over_the_project():
    assert resolve("22.0", "21.0", ref_name="main", ref_type="branch",
                   default_branch="main", trunk="22.0") == "22.0"


def test_override_is_guarded_too():
    with pytest.raises(ReleaseVersionError, match="21.0"):
        resolve("21.0", "22.0", ref_name="main", ref_type="branch",
                default_branch="main", trunk="22.0")


def test_unknown_default_branch_is_refused():
    with pytest.raises(ReleaseVersionError, match="default branch"):
        resolve("", "22.0", ref_name="main", ref_type="branch",
                default_branch="", trunk="22.0")


@pytest.mark.parametrize("bad", ["22", "v22.0", "22.0.1", " 22.0"])
def test_malformed_version_is_refused(bad):
    with pytest.raises(ReleaseVersionError, match="X.Y"):
        resolve(bad, "22.0", ref_name="x", ref_type="branch",
                default_branch="main", trunk="22.0")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "tools" / "jenkins").mkdir(parents=True)
    (tmp_path / "tools" / "jenkins" / "Jenkinsfile").write_text(
        JENKINSFILE, encoding="utf-8", newline="\n")
    (tmp_path / "zensical.toml").write_text(TOML.format(v="21.0"), encoding="utf-8")
    monkeypatch.setenv("DOCS_PROJECT", str(tmp_path))
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    for name, value in {"GITHUB_REF_NAME": "main", "GITHUB_REF_TYPE": "branch",
                        "DEFAULT_BRANCH": "main", "VERSION_OVERRIDE": "",
                        "GITHUB_OUTPUT": str(out)}.items():
        monkeypatch.setenv(name, value)
    return tmp_path


def test_main_writes_the_version_output(repo, monkeypatch, capsys):
    monkeypatch.setenv("VERSION_OVERRIDE", "22.0")
    assert release_version.main(["--repo-root", str(repo)]) == 0
    assert (repo / "github_output").read_text(encoding="utf-8") == "VERSION=22.0\n"
    assert "22.0" in capsys.readouterr().out


def test_main_refuses_with_a_workflow_error(repo, capsys):
    assert release_version.main(["--repo-root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("::error::")
    assert (repo / "github_output").read_text(encoding="utf-8") == ""


def test_main_off_the_default_branch(repo, monkeypatch):
    monkeypatch.setenv("GITHUB_REF_NAME", "tooling-port")
    assert release_version.main(["--repo-root", str(repo)]) == 0
    assert (repo / "github_output").read_text(encoding="utf-8") == "VERSION=21.0\n"
