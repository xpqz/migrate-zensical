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


# --- admonitions ---------------------------------------------------------

def page(site, rel, text):
    (site.docs / rel).write_text(text, encoding="utf-8")


def adm(tmp_path, text):
    site = make(tmp_path)
    page(site, "index.md", text)
    return check.check_admonitions(site)


def test_valid_admonitions_pass(tmp_path):
    lines = [
        '!!! info "Information"',
        '!!! Info "Dyalog on Microsoft Windows"',
        '!!! info "Dyalog on Linux"',
        '!!! info "Dyalog on Unix"',
        '!!! info "Dyalog on AIX"',
        '!!! info "Dyalog on macOS"',
        '!!! warning "Warning"',
        '??? tip "Hints and Recommendations"',
        '???+ note "Legacy"',
        '    !!! Warning "Warning"',
    ]
    assert adm(tmp_path, "\n".join(lines) + "\n") == []


def test_unknown_type_is_error(tmp_path):
    problems = adm(tmp_path, '# T\n\n!!! hint "Information"\n')
    assert [(p.path, p.line, p.severity) for p in problems] == [
        ("index.md", 3, "error")]
    assert "hint" in problems[0].message


def test_wrong_title_is_error(tmp_path):
    problems = adm(tmp_path, '!!! warning "Information"\n')
    assert [(p.line, p.severity) for p in problems] == [(1, "error")]


def test_missing_title_is_error(tmp_path):
    problems = adm(tmp_path, "!!! info\n")
    assert [(p.line, p.severity) for p in problems] == [(1, "error")]
    assert "title" in problems[0].message


def test_admonition_in_fence_is_ignored(tmp_path):
    assert adm(tmp_path, '```\n!!! bogus "x"\n```\n') == []


def test_trailing_whitespace_ok(tmp_path):
    assert adm(tmp_path, '!!! info "Information"  \n') == []


# --- search exclusions ---------------------------------------------------

EXCLUDE = "---\nsearch:\n  exclude: true\n---\n"


def excl(tmp_path, text):
    site = make(tmp_path)
    page(site, "index.md", text)
    return check.check_search_exclusions(site)


def test_search_exclusion_with_keywords_is_error(tmp_path):
    for div in ('<div style="display: none;">', "<div style='display:none'>"):
        problems = excl(tmp_path, EXCLUDE + "# T\n\n" + div + "\n⍳\n</div>\n")
        assert [(p.path, p.line, p.severity) for p in problems] == [
            ("index.md", 7, "error")]


def test_search_exclusion_alone_ok(tmp_path):
    assert excl(tmp_path, EXCLUDE + "# T\n") == []
    assert excl(tmp_path, '# T\n\n<div style="display: none;">\n⍳\n</div>\n') == []


# --- build check: review fixes ------------------------------------------

def test_missing_baseline_is_error_problem(tmp_path):
    site = make(tmp_path)
    problems = check.check_build(site, tmp_path / "nope.txt",
                                 runner=lambda p: ("", 0))
    assert [(p.path, p.severity) for p in problems] == [
        (str(tmp_path / "nope.txt"), "error")]
    assert "baseline" in problems[0].message


def test_new_issue_tally_not_duplicated(tmp_path):
    site = make(tmp_path)
    base = tmp_path / "known.txt"
    base.write_text("", encoding="utf-8")
    out = "Warning: anchor does not exist\n ╭─[ a/index.md:3:4 ]\n1 issues found\n"
    problems = check.check_build(site, base, runner=lambda p: (out, 0))
    assert len(problems) == 1


def test_assess_separates_other_reasons():
    import check_flatten
    r = check_flatten.assess("boom", 2, set())
    assert r.other_reasons and all("baseline" not in x for x in r.other_reasons)
    out = "Warning: x\n ╭─[ a.md:1:1 ]\n1 issues found\n"
    r = check_flatten.assess(out, 0, set())
    assert r.other_reasons == [] and r.reasons and r.new
