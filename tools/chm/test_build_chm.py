"""Tests for the CHM builder.

build_chm needs the `chm` dependency group (pygments, cssutils, htmlmin,
csscompressor), one of uv's default groups; in an environment without it
these tests are skipped rather than failing the suite.
"""

import textwrap

import pytest

for _module in ("pygments", "cssutils", "htmlmin", "csscompressor"):
    pytest.importorskip(_module, reason="needs the chm dependency group")

import build_chm  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402
from docsite import Site  # noqa: E402

TOML = textwrap.dedent('''\
    [project]
    site_name = "Documentation"
    markdown_extensions = [
        "admonition",
        "pymdownx.superfences",
        { "pymdownx.highlight" = { use_pygments = false, pygments_lang_class = true } },
        "attr_list",
        { toc = { title = "On this page" } },
        "dyalog_caption",
        "dyalog_vars",
    ]

    [project.extra]
    version_majmin = "21.0"

    [[project.nav]]
    "Core Reference" = [
        { "Dyalog APL Language" = [
            "language-reference-guide/index.md",
            { "<code>⎕AI</code> Account Information" = "language-reference-guide/ai.md" },
        ] },
    ]

    [[project.nav]]
    "Code Tooling" = [
        { ".NET Interface" = [
            "net-interface-guide/index.md",
            { Intro = "net-interface-guide/x.md" },
        ] },
    ]

    [[project.nav]]
    "Dyalog for Unix" = [
        { "User Interface" = [
            "unix-user-guide/index.md",
        ] },
    ]

    [[project.nav]]
    About = [
        { Conventions = "conventions.md" },
    ]
    ''')


@pytest.fixture
def site(tmp_path):
    project = tmp_path / "zensical"
    docs = project / "docs"
    for page in (
        "language-reference-guide/index.md",
        "language-reference-guide/ai.md",
        "net-interface-guide/index.md",
        "net-interface-guide/x.md",
        "unix-user-guide/index.md",
        "conventions.md",
    ):
        (docs / page).parent.mkdir(parents=True, exist_ok=True)
        (docs / page).write_text("# Page\n", encoding="utf-8")
    (project / "zensical.toml").write_text(TOML, encoding="utf-8")
    return Site.load(project)


def test_load_nav_matches_toc_shape(site):
    nav = build_chm.load_nav(site, exclude=[])
    assert nav == {
        "nav": [
            {"Core Reference": [
                {"Dyalog APL Language": [
                    "language-reference-guide/index.md",
                    {"AI Account Information": "language-reference-guide/ai.md"},
                ]},
            ]},
            {"Code Tooling": [
                {".NET Interface": [
                    "dotnet-interface-guide/index.md",
                    {"Intro": "dotnet-interface-guide/x.md"},
                ]},
            ]},
            {"Dyalog for Unix": [
                {"User Interface": ["unix-user-guide/index.md"]},
            ]},
            {"About": [{"Conventions": "conventions.md"}]},
        ]
    }


def test_net_guides_use_source_dir_names(site):
    assert build_chm.chm_path("net-interface-guide/x.md") == "dotnet-interface-guide/x.htm"
    assert (
        build_chm.chm_path("net-framework-interface-guide/a/b.md")
        == "dotnet-framework-interface-guide/a/b.htm"
    )
    assert build_chm.chm_path("conventions.md") == "conventions.htm"
    pages = dict(build_chm.chm_pages(site, build_chm.load_nav(site, exclude=[])))
    assert pages["net-interface-guide/x.md"] == "dotnet-interface-guide/x.htm"


def test_links_into_net_guides_use_source_dir_names():
    html = (
        '<a href="../net-interface-guide/x.md#a">x</a>'
        '<a href="https://docs.dyalog.com/21.0/net-interface-guide/x/">web</a>'
    )
    out = build_chm.fix_links_html(html)
    assert 'href="../dotnet-interface-guide/x.htm#a"' in out
    assert 'href="https://docs.dyalog.com/21.0/net-interface-guide/x/"' in out


def test_excluded_sections_absent(site):
    nav = build_chm.load_nav(site, exclude=["Dyalog for Unix"])
    titles = [next(iter(entry)) for entry in nav["nav"]]
    assert titles == ["Core Reference", "Code Tooling", "About"]
    pages = [src for src, _ in build_chm.chm_pages(site, nav)]
    assert "unix-user-guide/index.md" not in pages
    assert sorted(pages) == [
        "conventions.md",
        "language-reference-guide/ai.md",
        "language-reference-guide/index.md",
        "net-interface-guide/index.md",
        "net-interface-guide/x.md",
    ]


def test_extract_h1_title_name_form():
    html = '<h1 id="x"><span class="title-name">Account Information</span> <code>R←⎕AI</code></h1>'
    assert build_chm.extract_h1(html, for_title=True) == "Account Information"
    assert build_chm.extract_h1(html) == "Account Information R←⎕AI"
    # Legacy and plain forms still work.
    legacy = '<h1><span class="name">Name</span> <span class="command">C</span></h1>'
    assert build_chm.extract_h1(legacy, for_title=True) == "Name"
    assert build_chm.extract_h1(legacy) == "Name C"
    assert build_chm.extract_h1('<h1>Plain <code>x</code></h1>') == "Plain"


def test_highlighting_forced_on(site):
    extensions, configs = build_chm.markdown_extensions(site)
    assert configs["pymdownx.highlight"]["use_pygments"] is True
    assert "dyalog_caption" in extensions
    assert "dyalog_vars" not in extensions
    html = build_chm.render_markdown("```apl\nX←1\n```\n", site)
    assert '<span class="' in html  # Pygments token spans


def test_os_admonitions_get_os_class():
    html = (
        '<div class="admonition info"><p class="admonition-title">Dyalog on Linux</p></div>'
        '<div class="admonition info"><p class="admonition-title">Information</p></div>'
    )
    out = build_chm.mark_os_admonitions(html)
    assert '<div class="admonition info os"><p class="admonition-title">Dyalog on Linux' in out
    assert '<div class="admonition info"><p class="admonition-title">Information' in out


def test_example_headings_get_example_class():
    html = (
        '<h2 id="example">Example</h2><h4 id="e">Examples</h4>'
        '<h2 id="x">Example 1</h2><h1>Example</h1>'
    )
    soup = BeautifulSoup(build_chm.mark_example_headings(html), "html.parser")
    assert soup.find("h2", id="example")["class"] == ["example"]
    assert soup.find("h4", id="e")["class"] == ["example"]
    assert not soup.find("h2", id="x").has_attr("class")
    assert not soup.find("h1").has_attr("class")


def test_expand_macros_substitutes_like_the_site():
    macros = {"version_majmin": "22.0", "build_date": "2026-11-17", "git_info": "main:abc",
              "version": {"provider": "mike"}}
    md = ("v{{ version_majmin }} {{build_date}} {{ git_info }} "
          "{{ version }} {{ no_such_name }} {{ a-b }}")
    assert build_chm.expand_macros(md, macros) == (
        "v22.0 2026-11-17 main:abc {{ version }} {{ no_such_name }} {{ a-b }}")


def test_titles_keep_plain_h1():
    html = '<h1 id="x"><span class="title-name">N</span> <code>S</code></h1>'
    assert build_chm.mark_example_headings(html) == html


def test_title_syntax_gets_the_pre_july_float_classes():
    html = '<h1 id="x"><span class="title-name">N</span> <code>S</code></h1><p><code>c</code></p>'
    soup = BeautifulSoup(build_chm.mark_title_syntax(html), "html.parser")
    h1 = soup.find("h1")
    assert h1["class"] == ["syntax"]
    # The old IE engine drops a right float that follows inline text onto the
    # next line, so the name floats left as it did before July.
    assert h1.find("span")["class"] == ["title-name", "name"]
    assert h1.find("code")["class"] == ["command"]
    assert not soup.find("p").find("code").has_attr("class")


def test_title_syntax_multiline_and_untitled_pages():
    html = '<h1><span class="title-name">Bind</span> <code>A<br>B</code></h1>'
    assert '<code class="command">A<br/>B</code>' in build_chm.mark_title_syntax(html)
    plain = "<h1>Basic Usage</h1>"
    assert build_chm.mark_title_syntax(plain) == plain


def test_title_syntax_keeps_title_extraction():
    out = build_chm.mark_title_syntax('<h1><span class="title-name">N</span> <code>S</code></h1>')
    assert build_chm.extract_h1(out, for_title=True) == "N"


def test_images_come_from_included_guides(site):
    for image in ("net-interface-guide/img/a.png", "unix-user-guide/img/b.png"):
        (site.docs / image).parent.mkdir(parents=True, exist_ok=True)
        (site.docs / image).write_bytes(b"")
    nav = build_chm.load_nav(site, exclude=["Dyalog for Unix"])
    images = [out for _, out in build_chm.chm_images(site, build_chm.chm_pages(site, nav))]
    assert images == ["dotnet-interface-guide/img/a.png"]


def test_classifier_below_title_is_part_of_it(tmp_path):
    html = "<h1>APLVersion</h1><p>Property</p><p>Text</p>"
    assert build_chm.extract_h1(html, for_title=True) == "APLVersion Property"
    assert build_chm.extract_h1("<h1>Calendar</h1><p>Example 1</p>") == "Calendar Example 1"
    assert build_chm.extract_h1("<h1>Notes</h1><p>Property values are</p>") == "Notes"
    page = tmp_path / "abort.md"
    page.write_text("---\nsearch:\n  boost: 2\n---\n\n# Abort\n\nMethod 103\n\nText\n", encoding="utf-8")
    assert build_chm.extract_headers(str(page)) == ["Abort Method 103"]
    page.write_text("# Abort\n\nAborts things.\n", encoding="utf-8")
    assert build_chm.extract_headers(str(page)) == ["Abort"]
