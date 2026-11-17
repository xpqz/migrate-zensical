import search_keywords as sk

BLOCK = '<!-- Hidden search keywords -->\n<div style="display: none;">\n  {}\n</div>\n'


def page(syntax, body="Body text.\n"):
    return f'---\nsearch:\n  boost: 2\n---\n\n# <span class="title-name">Name</span> `{syntax}`\n\n{body}'


def test_add_keywords_inserts_block():
    md = page("R←⎕AT Y")
    out = sk.add_keywords(md)
    assert out == md.rstrip("\n") + "\n\n" + BLOCK.format("⎕AT AT")


def test_add_keywords_variants():
    assert BLOCK.format("⎕A") in sk.add_keywords(page("R←⎕A"))
    assert BLOCK.format("8⌶") in sk.add_keywords(page("R←X(8⌶)Y"))
    assert BLOCK.format("⍒") in sk.add_keywords(page("R←⍒Y"))
    assert sk.add_keywords(page("R←X[Y]")) == page("R←X[Y]")
    multi = '# <span class="title-name">N</span> <code>R←⎕FMT Y<br>R←X ⎕FMT Y</code>\n\ntext\n'
    assert BLOCK.format("⎕FMT FMT") in sk.add_keywords(multi)


def test_add_keywords_idempotent():
    once = sk.add_keywords(page("R←⎕AT Y"))
    assert sk.add_keywords(once) == once


def test_add_keywords_skips_pages_without_syntax_title():
    for md in ("# Plain heading\n\ntext\n",
               '# <span class="title-name">N</span>\n\ntext\n',
               "```\n# <span class=\"title-name\">N</span> `⎕A`\n```\n",
               page(")SI {n}")):
        assert sk.add_keywords(md) == md


def test_add_keywords_keeps_existing_block():
    md = page("R←⎕A") + "\n" + BLOCK.format("⎕A custom")
    assert sk.add_keywords(md) == md


def test_add_keywords_leaves_search_excluded_page_alone():
    md = page("R←⎕A").replace("boost: 2", "exclude: true")
    assert sk.add_keywords(md) == md


def test_add_keywords_uses_crlf_when_file_does():
    md = page("R←⎕AT Y").replace("\n", "\r\n")
    out = sk.add_keywords(md)
    assert out.endswith("\r\n</div>\r\n")
    assert "\n" not in out.replace("\r\n", "")
    assert out == md.rstrip("\r\n") + "\r\n\r\n" + BLOCK.format("⎕AT AT").replace("\n", "\r\n")


def test_cli_accepts_cwd_relative_docs_relative_and_absolute(tmp_path, monkeypatch, capsys):
    (tmp_path / "zensical.toml").write_text('[project]\nsite_name = "T"\nnav = []\n', encoding="utf-8")
    docs = tmp_path / "docs" / "guide"
    docs.mkdir(parents=True)
    for n in "abc":
        (docs / f"{n}.md").write_text(page("R←⎕AT Y"), encoding="utf-8")
    monkeypatch.setenv("DOCS_PROJECT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert sk.main(["docs/guide/a.md", "guide/b.md", str(docs / "c.md")]) == 0
    assert "3 page(s) changed" in capsys.readouterr().out
    for n in "abc":
        assert "Hidden search keywords" in (docs / f"{n}.md").read_text(encoding="utf-8")
