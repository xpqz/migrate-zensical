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


# --- review fixes from the admonition and search checks ------------------

def test_hidden_div_with_attributes_before_style(tmp_path):
    div = '<div class="x" style="display:none">'
    problems = excl(tmp_path, EXCLUDE + "# T\n\n" + div + "\n⍳\n</div>\n")
    assert [(p.line, p.severity) for p in problems] == [(7, "error")]


def test_hidden_div_in_fence_is_ignored(tmp_path):
    text = EXCLUDE + '# T\n\n```html\n<div style="display: none;">\n```\n'
    assert excl(tmp_path, text) == []


def test_admonition_without_type_is_malformed(tmp_path):
    problems = adm(tmp_path, "!!!\n")
    assert [(p.line, p.severity) for p in problems] == [(1, "error")]
    assert "malformed" in problems[0].message


def test_admonition_unquoted_title_is_malformed(tmp_path):
    problems = adm(tmp_path, "!!! info Information\n")
    assert [(p.line, p.severity) for p in problems] == [(1, "error")]
    assert "malformed" in problems[0].message


def test_admonition_empty_title_is_error(tmp_path):
    problems = adm(tmp_path, '!!! info ""\n')
    assert [(p.line, p.severity) for p in problems] == [(1, "error")]
    assert "title" in problems[0].message


# --- images --------------------------------------------------------------

def img_site(tmp_path, body="", files=("a/pic.png",), css=""):
    site = make(tmp_path)
    page(site, "index.md", body)
    for rel in files:
        f = site.docs / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    if css:
        d = site.docs / docsite.ASSET_DIR / "css"
        d.mkdir(parents=True, exist_ok=True)
        (d / "dyalog.css").write_text(css, encoding="utf-8")
    return site


def img_problems(site, severity, **kw):
    return [p for p in check.check_images(site, **kw) if p.severity == severity]


def test_missing_image_is_error(tmp_path):
    site = img_site(tmp_path, "# T\n\n![x](a/nope.png)\n")
    errs = img_problems(site, "error")
    assert [(p.path, p.line) for p in errs] == [("index.md", 3)]


def test_existing_image_and_decoding(tmp_path):
    site = img_site(tmp_path, "![x](a/my%20pic.png#f?v=1 \"title\")\n",
                    files=("a/my pic.png",))
    assert check.check_images(site) == []


def test_html_img_resolved(tmp_path):
    site = img_site(tmp_path, '<img alt="" src="a/pic.png">\n<img src=\'a/no.png\'>\n')
    errs = img_problems(site, "error")
    assert [p.line for p in errs] == [2]
    assert img_problems(site, "warning") == []


def test_relative_to_page_directory(tmp_path):
    site = img_site(tmp_path, "", files=("a/pic.png",))
    page(site, "a/index.md", "![x](pic.png) ![y](../a/pic.png)\n")
    assert check.check_images(site) == []


def test_image_in_fence_ignored(tmp_path):
    site = img_site(tmp_path, "```\n![x](a/nope.png)\n```\n")
    assert img_problems(site, "error") == []
    # and a fenced reference does not make an image "used"
    (tmp_path / "b").mkdir()
    site2 = img_site(tmp_path / "b", "```\n![x](a/pic.png)\n```\n")
    assert [p.path for p in img_problems(site2, "warning")] == ["a/pic.png"]


def test_external_image_ignored(tmp_path):
    site = img_site(tmp_path, "![x](https://e.com/a.png)\n"
                    '<img src="http://e.com/b.png">\n![d](data:image/png;base64,AA)\n')
    assert img_problems(site, "error") == []


def test_unused_image_is_warning(tmp_path):
    site = img_site(tmp_path, "# T\n", files=("a/pic.png", "a/doc.md.txt"))
    warns = img_problems(site, "warning")
    assert [p.path for p in warns] == ["a/pic.png"]
    assert (site.docs / "a/pic.png").exists()


def test_prune_removes_only_unused(tmp_path):
    site = img_site(tmp_path, "![x](a/used.png)\n",
                    files=("a/used.png", "a/unused.PNG", "a/notes.txt"),
                    css="")
    theme_img = site.docs / docsite.ASSET_DIR / "images" / "t.svg"
    theme_img.parent.mkdir(parents=True)
    theme_img.write_bytes(b"x")
    probs = check.check_images(site, prune=True)
    assert [(p.path, p.severity, p.message) for p in probs] == [
        ("a/unused.PNG", "warning", "removed")]
    assert (site.docs / "a/used.png").exists()
    assert (site.docs / "a/notes.txt").exists()
    assert theme_img.exists()
    assert not (site.docs / "a/unused.PNG").exists()


def test_image_referenced_from_css_is_used(tmp_path):
    site = img_site(
        tmp_path, "", files=("bg/hero.png",),
        css="a{background:url('../../bg/hero.png')}")
    assert check.check_images(site) == []


def test_image_referenced_from_config_is_used(tmp_path):
    site = img_site(tmp_path, "", files=("bg/hero.png",))
    (site.project / "zensical.toml").write_text(
        TOML + '\n[project.theme]\nlogo = "bg/hero.png"\n', encoding="utf-8")
    site = docsite.Site.load(site.project)
    assert check.check_images(site) == []


def test_main_prune_flag(tmp_path):
    site = img_site(tmp_path, "# T\n", files=("a/pic.png",))
    (tmp_path / "zensical.toml").write_text(
        '[project]\nsite_name = "T"\nnav = ["index.md", "a/index.md"]\n',
        encoding="utf-8")
    (site.docs / "a/extra.md").unlink()
    check.main(["--project", str(tmp_path), "--skip-build",
                "--unlisted", str(tmp_path / "u.txt"), "--prune-images"])
    assert not (site.docs / "a/pic.png").exists()


# --- image check hardening ----------------------------------------------

def test_prune_skipped_when_any_check_errors(tmp_path):
    # make() has a nav entry with no file: an error from another check.
    site = img_site(tmp_path, "# T\n", files=("a/pic.png",))
    check.main(["--project", str(tmp_path), "--skip-build",
                "--unlisted", str(tmp_path / "u.txt"), "--prune-images"])
    assert (site.docs / "a/pic.png").exists()


def test_prune_skipped_message(tmp_path, capsys):
    img_site(tmp_path, "# T\n", files=("a/pic.png",))
    check.main(["--project", str(tmp_path), "--skip-build",
                "--unlisted", str(tmp_path / "u.txt"), "--prune-images"])
    assert "pruning skipped because of errors" in capsys.readouterr().out


def used_by(tmp_path, body, files=("a/pic.png",)):
    site = img_site(tmp_path, body, files=files)
    return check.check_images(site)


def test_link_to_image_counts_as_use(tmp_path):
    assert used_by(tmp_path, "[big](a/pic.png)\n") == []


def test_reference_definition_counts_as_use(tmp_path):
    assert used_by(tmp_path, "![x][p]\n\n[p]: a/pic.png \"t\"\n") == []


def test_href_poster_srcset_count_as_use(tmp_path):
    files = ("a/1.png", "a/2.png", "a/3.png", "a/4.png")
    body = ('<a href="a/1.png">x</a>\n<video poster="a/2.png"></video>\n'
            '<img src="a/1.png" srcset="a/3.png 1x, a/4.png 2x">\n')
    assert used_by(tmp_path, body, files) == []


def test_srcset_missing_candidate_is_error(tmp_path):
    body = '<img src="a/pic.png" srcset="a/pic.png 1x, a/gone.png 2x">\n'
    assert [p.severity for p in used_by(tmp_path, body)] == ["error"]


def test_multiline_img_tag(tmp_path):
    body = '# T\n\n<img\n  alt="x"\n  src="a/pic.png">\n<img\n  src="a/no.png">\n'
    probs = used_by(tmp_path, body)
    assert [(p.severity, p.line) for p in probs] == [("error", 7)]


def test_root_relative_image(tmp_path):
    site = img_site(tmp_path, "", files=("img.png",))
    page(site, "a/index.md", "![x](/img.png)\n")
    assert check.check_images(site) == []


def test_two_images_on_one_line(tmp_path):
    body = "![a](a/1.png) text ![b](a/2.png) <img src=\"a/3.png\">\n"
    assert used_by(tmp_path, body, ("a/1.png", "a/2.png", "a/3.png")) == []


def test_case_mismatch_is_error_and_not_pruned(tmp_path):
    site = img_site(tmp_path, "![x](a/Pic.PNG)\n", files=("a/pic.png",))
    probs = check.check_images(site, prune=True)
    assert [(p.path, p.severity) for p in probs] == [("index.md", "error")]
    assert "case" in probs[0].message
    assert (site.docs / "a/pic.png").exists()


def test_fenced_multiline_img_ignored(tmp_path):
    body = "```html\n<img\n src=\"a/no.png\">\n```\n"
    assert [p.severity for p in used_by(tmp_path, body)] == ["warning"]


def test_thumbnail_link_marks_both_used(tmp_path):
    body = "[![t](a/thumb.png)](a/full.png)\n"
    assert used_by(tmp_path, body, ("a/thumb.png", "a/full.png")) == []


def test_thumbnail_missing_inner_is_error(tmp_path):
    body = "[![t](a/gone.png)](a/full.png)\n"
    probs = used_by(tmp_path, body, ("a/full.png",))
    assert [(p.severity, p.line) for p in probs] == [("error", 1)]
    assert "gone.png" in probs[0].message


def test_thumbnail_missing_outer_is_not_error(tmp_path):
    body = "[![t](a/pic.png)](a/gone.png)\n"
    assert used_by(tmp_path, body) == []


def test_escaped_bracket_in_alt_text(tmp_path):
    assert used_by(tmp_path, "![a \\] b](a/pic.png)\n") == []
    probs = used_by(tmp_path, "![a \\] b](a/gone.png)\n")
    assert [p.severity for p in probs if p.severity == "error"] == ["error"]


def test_excludes_search_is_public():
    assert check.excludes_search("---\nsearch:\n  exclude: true\n---\n\n# T\n")
    assert not check.excludes_search("---\nsearch:\n  boost: 2\n---\n\n# T\n")
    assert not check.excludes_search("# T\n")
