import check
import docsite

TOML = """\
[project]
site_name = "T"
nav = [
    "index.md",
    { "A" = ["a/index.md", "a/missing.md"] },
]
"""


def make(tmp_path, files=("index.md", "a/index.md", "a/extra.md")):
    (tmp_path / "zensical.toml").write_text(TOML, encoding="utf-8")
    for rel in files:
        p = tmp_path / "docs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# " + rel + "\n", encoding="utf-8")
    return docsite.Site.load(tmp_path)


def test_nav_target_missing_is_error(tmp_path):
    problems = check.check_nav_targets(make(tmp_path))
    assert [(p.path, p.severity) for p in problems] == [("a/missing.md", "error")]


def test_unlisted_page_is_error(tmp_path):
    problems = check.check_unlisted(make(tmp_path), set())
    assert [(p.path, p.severity) for p in problems] == [("a/extra.md", "error")]


def test_allow_listed_page_is_ok(tmp_path):
    assert check.check_unlisted(make(tmp_path), {"a/extra.md"}) == []


def test_stale_allow_entry_is_warning(tmp_path):
    site = make(tmp_path)
    problems = check.check_unlisted(site, {"a/extra.md", "index.md", "gone.md"})
    assert sorted((p.path, p.severity) for p in problems) == [
        ("gone.md", "warning"), ("index.md", "warning")]


def test_main_exit_code_reflects_errors(tmp_path, capsys):
    make(tmp_path)
    allow = tmp_path / "allow.txt"
    allow.write_text("# group\na/extra.md\n", encoding="utf-8")
    argv = ["--skip-build", "--project", str(tmp_path), "--unlisted", str(allow)]
    assert check.main(argv) == 1  # a/missing.md is in the nav but absent
    assert "a/missing.md:" in capsys.readouterr().out
    (tmp_path / "docs/a/missing.md").write_text("x", encoding="utf-8")
    assert check.main(argv) == 0


def test_reads_utf8_titles(tmp_path):
    site = make(tmp_path)
    (site.docs / "index.md").write_bytes("# ⎕NA\n".encode("utf-8"))
    assert site.read("index.md") == "# ⎕NA\n"


def test_build_problems_from_assessment(tmp_path):
    site = make(tmp_path)
    base = tmp_path / "known.txt"
    base.write_text("# c\nanchor does not exist @ a/old.md:1:2\n", encoding="utf-8")
    out = ("Warning: anchor does not exist\n ╭─[ a/index.md:3:4 ]\n1 issues found\n")
    problems = check.check_build(site, base, runner=lambda p: (out, 0))
    assert sorted((p.path, p.line, p.severity) for p in problems) == [
        ("a/index.md", 3, "error"), ("a/old.md", 1, "warning")]


def test_failed_build_is_error(tmp_path):
    site = make(tmp_path)
    base = tmp_path / "known.txt"
    base.write_text("", encoding="utf-8")
    problems = check.check_build(site, base, runner=lambda p: ("boom", 2))
    assert any(p.severity == "error" and "exited" in p.message for p in problems)
