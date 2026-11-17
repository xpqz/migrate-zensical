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
