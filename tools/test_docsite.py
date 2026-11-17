import pytest

import docsite

TOML = """\
[project]
site_name = "T"
nav = [
    "index.md",
    { "A" = [
        "a/index.md",
        { "B" = "a/b.md" },
        "index.md",
    ] },
]
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "zensical.toml").write_text(TOML, encoding="utf-8")
    docs = tmp_path / "docs"
    for rel in ("index.md", "a/index.md", "a/b.md", "a/unlisted.md",
                "documentation-theme-zensical/x.md"):
        p = docs / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# ⍳ " + rel + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def site(project):
    return docsite.Site.load(project)


def test_nav_pages_in_order_without_duplicates(site):
    assert site.nav_pages() == ["index.md", "a/index.md", "a/b.md"]


def test_pages_excludes_asset_dir(site):
    pages = site.pages()
    assert "a/unlisted.md" in pages
    assert "documentation-theme-zensical/x.md" not in pages
    assert pages == sorted(pages)


def test_page_keys_are_posix(site):
    assert all("\\" not in p for p in site.pages() + site.nav_pages())


def test_read_is_utf8(site):
    assert site.read("a/b.md") == "# ⍳ a/b.md\n"


def test_nav_section_returns_raw_list(site):
    assert site.nav_section(("A",)) == ["a/index.md", {"B": "a/b.md"}, "index.md"]


def test_nav_section_missing_raises(site):
    with pytest.raises(KeyError):
        site.nav_section(("A", "Nope"))
    with pytest.raises(KeyError):
        site.nav_section(("Nope",))


def test_prose_lines_skips_fences():
    assert list(docsite.prose_lines("a\n```apl\n!!! x\n```\nb")) == [(1, "a"), (5, "b")]


def test_prose_lines_tilde_indented_and_longer_fences():
    text = "a\n  ~~~\nx\n~~~\nb\n````\n```\ny\n````\nc"
    assert list(docsite.prose_lines(text)) == [(1, "a"), (5, "b"), (10, "c")]


def test_find_project_prefers_env_then_zensical_dir_then_root(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCS_PROJECT", raising=False)
    assert docsite.find_project(tmp_path) == tmp_path
    (tmp_path / "zensical").mkdir()
    (tmp_path / "zensical" / "zensical.toml").write_text("", encoding="utf-8")
    assert docsite.find_project(tmp_path) == tmp_path / "zensical"
    other = tmp_path / "other"
    monkeypatch.setenv("DOCS_PROJECT", str(other))
    assert docsite.find_project(tmp_path) == other


def test_prose_lines_numbers_like_an_editor():
    # \x0c and U+2028 are line breaks to splitlines() but not to an editor.
    text = "a\x0cb\nc d\ne"
    assert list(docsite.prose_lines(text)) == [(1, "a\x0cb"), (2, "c d"), (3, "e")]


def test_backtick_info_string_with_backtick_is_not_a_fence():
    text = "a\n```code``` text\nb\n```\nc"
    # The first line is prose; the lone ``` on line 4 then opens a real fence.
    assert list(docsite.prose_lines(text)) == [(1, "a"), (2, "```code``` text"), (3, "b")]
