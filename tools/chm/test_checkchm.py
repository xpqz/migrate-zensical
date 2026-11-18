import checkchm

HELP_URLS = '''\
#define OR "object-reference/properties"
HELP_URL("A", OR "/a")
HELP_URL("B", OR "/b")
HELP_URL("C", "language-reference-guide/c")
'''


def run(tmp_path, present, baseline):
    (tmp_path / "help_urls.h").write_text(HELP_URLS, encoding="utf-8")
    project = tmp_path / "out" / "chm"
    for page in present:
        (project / page).parent.mkdir(parents=True, exist_ok=True)
        (project / page).write_text("", encoding="utf-8")
    (tmp_path / "baseline.txt").write_text(baseline, encoding="utf-8")
    return checkchm.main([
        "--help_urls", str(tmp_path / "help_urls.h"),
        "--project-root", str(tmp_path / "out"),
        "--baseline", str(tmp_path / "baseline.txt"),
    ])


def test_known_missing_passes(tmp_path, capsys):
    rc = run(tmp_path, ["language-reference-guide/c.htm"],
             "# known\nobject-reference/properties/a.htm\nobject-reference/properties/b.htm\n")
    assert rc == 0
    assert "0 missing; 2 known missing" in capsys.readouterr().out


def test_new_missing_fails(tmp_path, capsys):
    rc = run(tmp_path, ["language-reference-guide/c.htm"], "object-reference/properties/a.htm\n")
    assert rc == 1
    assert "object-reference/properties/b.htm" in capsys.readouterr().err


def test_resolved_baseline_entries_are_reported(tmp_path, capsys):
    rc = run(tmp_path, ["language-reference-guide/c.htm", "object-reference/properties/a.htm"],
             "object-reference/properties/a.htm\nobject-reference/properties/b.htm\n")
    assert rc == 0
    out = capsys.readouterr().out
    assert "now resolved" in out and "  object-reference/properties/a.htm" in out


def test_resolve_path_drops_leading_segments():
    have = {"a/b.htm", "c.htm"}.__contains__
    assert checkchm.resolve_path("a/b.htm", have) == ("a/b.htm", 0)
    assert checkchm.resolve_path("x/y/a/b.htm", have) == ("a/b.htm", 2)
    assert checkchm.resolve_path("x/d.htm", have) == (None, 2)
