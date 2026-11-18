"""
build_chm.py

Build the Windows CHM help file from the Zensical project: the nav in
zensical.toml (already merged and alias-prefixed) and the flat docs/ tree.

    uv run python tools/chm/build_chm.py --project zensical --project-dir out/chm

writes the CHM project (pages, images, _table_of_contents.hhc, _index.hhk,
dyalog.hfp) into out/chm/ and compiles it to out/chm/dyalog.chm, unless
--no-compile is given.

config.json names nav sections to leave out, by their nav titles:

{
    "exclude": [
        "Dyalog for Unix"
    ]
}

Topic paths in the CHM must stay as they were in v21: the IDE's F1 help
(help_urls.h) addresses topics by path. They use the guides' source
directory names, so the two .NET guides, whose site aliases differ, are
written under their v21 names (CHM_DIR_FOR_ALIAS).

Notes:

You need the tool "chmcmd" installed, which is part of Free Pascal
(brew install fpc; apt install fpc; or the Windows installer). See

    https://wiki.freepascal.org/htmlhelp_compiler

The Python dependencies are the `chm` dependency group (uv sync --all-groups).

Any .css files in the assets directory are inlined into each page; other
files there are copied into the project.
"""

import argparse
from dataclasses import dataclass, field
import itertools
import json
import logging
import os
import posixpath
from csscompressor import compress as css_compress
import cssutils
from htmlmin import minify as html_minify
from pathlib import Path
import re
import shutil
from subprocess import Popen
import sys
from typing import IO, List, Tuple
import warnings
from xml.dom.minidom import getDOMImplementation

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from bs4.element import NavigableString, Tag
import markdown
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # tools/, for docsite

import dyalog_vars  # noqa: E402
from docsite import Site, find_project  # noqa: E402

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

cssutils.log.setLevel(logging.CRITICAL)


HEADER = """
<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">
<html>
<head>
<meta name="GENERATOR" content="Microsoft&reg; HTML Help Workshop 4.1">
</head><body>
<object type="text/site properties">
    <param name="SiteType" value="toc">
    <param name="Window Styles" value="0x800401">
    <param name="ExWindow Styles" value="0x200">
</object>
"""

DIR_ENTRY = """<li><object type="text/sitemap">
        <param name="Name" value="{name}">
    </object>
"""

FILE_ENTRY = """<li><object type="text/sitemap">
        <param name="Name" value="{name}">
        <param name="Local" value="{file}">
    </object>
"""

# Guides whose site alias differs from the source directory name the v21 CHM
# used. CHM topic paths keep the source name: help_urls.h depends on them.
CHM_DIR_FOR_ALIAS = {
    "net-interface-guide": "dotnet-interface-guide",
    "net-framework-interface-guide": "dotnet-framework-interface-guide",
}

IMAGE_EXTENSIONS = {".png", ".gif", ".jpg", ".bmp", ".jpeg"}

# A title's classifier ("Property", "Event 33", "Example 1"), which the site's
# title normalisation (transforms.normalise_titles) moves out of the h1 into a
# paragraph of its own directly below. The v21 CHM's titles and index entries
# carried it ("APLVersion Property"), so it is put back for those.
CLASSIFIER_RE = re.compile(r"(?:Property|Object|Method|Event|Example)(?:\s+\d+[a-z]?)?")


def chm_dir(path: str) -> str:
    """A docs-relative POSIX path with an aliased guide directory renamed to
    its CHM (v21) name."""
    head, sep, rest = path.partition("/")
    return CHM_DIR_FOR_ALIAS.get(head, head) + sep + rest if sep else path


def chm_path(page: str) -> str:
    """The CHM topic path for a docs-relative Markdown page."""
    return re.sub(r"\.md$", ".htm", chm_dir(page))


@dataclass
class Node:
    name: str = ""
    value: str | List[str] = None
    depth: int = 0
    parent: "Node" = None
    children: List["Node"] = field(default_factory=list)
    html_name: str = field(default=None, init=False)

    def __post_init__(self):
        if isinstance(self.value, str) and self.value.endswith(".md"):
            self.html_name = self.value.replace(".md", ".htm")

    def isdir(self):
        return len(self.children) > 0


def _process_nav_item(item: dict | str, parent: Node, project="project") -> None:
    """
    Process a single nav item -- either a dict, which has either a string
    value (leaf node), or a list value -- of other nav items, or a string
    referencing a file, from which the name needs to be picked up from the
    H1 later.
    """
    if isinstance(item, dict):
        ((key, value),) = item.items()
        # Navigation titles are already cleaned in parse_mkdocs_yml
    else:  # No name in the yml -- name should be picked up from the H1 in the file
        key = ""
        value = item  # item is the filename -- swap to .htm to parse
        if item.endswith(".md"):
            html_file = os.path.join(project, item.replace(".md", ".htm"))
            # Check if the HTML file exists (it might have been excluded)
            if os.path.exists(html_file):
                with open(html_file, "r", encoding="utf-8") as f:
                    data = f.read()
                if h1 := extract_h1(data):
                    key = h1
            else:
                # File was excluded, skip this nav item
                return

            # Special case for welcome.md - override the H1 title
            if item == "welcome.md":
                key = "Welcome"

    node = Node(key, value, parent.depth + 1, parent)
    parent.children.append(node)

    if isinstance(value, list):
        for sub_item in value:
            _process_nav_item(sub_item, node, project)


def _traverse(node: Node, toc: IO[str]) -> None:
    # Just clean up quotes and backticks (HTML tags and quad already removed during parsing)
    title = node.name.replace("`", "").replace('"', "")

    # Determine a linked page for directory nodes, if available
    linked_file = None
    if node.children:
        # Prefer an explicit index page within this section
        for child in node.children:
            if not child.isdir() and child.html_name:
                base = os.path.basename(child.html_name).lower()
                if base in ("index.htm", "index.html"):
                    linked_file = child.html_name
                    break
        # Fall back to a child named "Introduction" if present
        if linked_file is None:
            for child in node.children:
                if (
                    child.name == "Introduction"
                    and not child.isdir()
                    and child.html_name
                ):
                    linked_file = child.html_name
                    break

    # Write the entry with or without a Local parameter
    if linked_file:
        toc.write(FILE_ENTRY.format(name=title, file=linked_file))
    else:
        toc.write(DIR_ENTRY.format(name=title))

    if node.children:  # Only write <ul> if there are children
        toc.write("<ul>\n")
        for child in node.children:
            if child.isdir():
                _traverse(child, toc)
            else:
                # Just clean up quotes and backticks
                child_name = child.name.replace("`", "").replace('"', "")
                toc.write(
                    FILE_ENTRY.format(
                        name=child_name,
                        file=child.html_name,
                    )
                )
        toc.write("</ul>\n")


def rename_aliased_dirs(navdata):
    """Map every page path in the nav to its CHM directory (chm_dir)."""
    if isinstance(navdata, dict):
        return {key: rename_aliased_dirs(value) for key, value in navdata.items()}
    if isinstance(navdata, list):
        return [rename_aliased_dirs(item) for item in navdata]
    if isinstance(navdata, str):
        return chm_dir(navdata)
    return navdata


def clean_nav_titles(navdata):
    """
    Clean navigation titles by removing HTML tags and the quad symbol.
    This ensures CHM compatibility by removing UTF-8 characters that don't display properly.
    """
    if isinstance(navdata, dict):
        cleaned = {}
        for key, value in navdata.items():
            # Clean the key if it's a navigation title
            if isinstance(value, (str, list, dict)) and not key.startswith("!"):
                # Remove HTML tags and quad symbol from navigation titles
                cleaned_key = re.sub(r"<[^>]+>", "", key).replace("⎕", "")
            else:
                cleaned_key = key
            # Recursively clean the value
            cleaned[cleaned_key] = clean_nav_titles(value)
        return cleaned
    elif isinstance(navdata, list):
        return [clean_nav_titles(item) for item in navdata]
    else:
        return navdata


def load_nav(site: Site, exclude: List[str]) -> dict:
    """
    The nav from zensical.toml as {"nav": [...]}, the shape generate_toc
    consumes: top-level sections whose title is in `exclude` dropped, titles
    cleaned for the CHM, and page paths in their CHM directories.
    """
    nav = [
        item
        for item in site.config.get("project", {}).get("nav", [])
        if not (isinstance(item, dict) and next(iter(item)) in exclude)
    ]
    return {"nav": clean_nav_titles(rename_aliased_dirs(nav))}


def generate_toc(yml_data: dict, project="project"):
    """
    Given the parsed and expanded mkdocs.yml -- specifically its "nav"
    section -- write out the corresponding CHM TOC XML with top-level items
    directly under the root <ul>.
    """
    root = Node()
    nav = yml_data.get("nav", [])
    for item in nav:
        _process_nav_item(item, root, project)

    # Post-process to add language reference disambiguation pages
    add_langref_disambiguation_pages(root, project)

    toc = open(os.path.join(project, "_table_of_contents.hhc"), "w", encoding="utf-8")
    toc.write(HEADER)
    toc.write("<ul>\n")

    # Write top-level children directly, skipping the empty root
    for child in root.children:
        if child.isdir():
            _traverse(child, toc)
        else:
            # Just clean up quotes and backticks (HTML tags and quad already removed during parsing)
            child_name = child.name.replace("`", "").replace('"', "")
            toc.write(
                FILE_ENTRY.format(
                    name=child_name,
                    file=child.html_name,
                )
            )

    toc.write("</ul>\n")
    toc.write("</body></html>\n")
    toc.close()
    return None


def _nav_paths(navdata) -> List[str]:
    if isinstance(navdata, str):
        return [navdata]
    if isinstance(navdata, dict):
        navdata = list(navdata.values())
    return [path for item in navdata for path in _nav_paths(item)]


def chm_pages(site: Site, nav: dict, verbose: bool = True) -> List[Tuple[str, str]]:
    """
    The Markdown pages that go into the CHM, as (docs-relative source path,
    CHM topic path) pairs: every page under a guide directory the nav
    reaches (listed in the nav or not -- symbol pages, for one, are reached
    only by link), plus the root-level pages the nav lists. -print.md pages
    are left out.
    """
    nav_paths = [p for p in _nav_paths(nav["nav"]) if p.endswith(".md")]
    dirs = {p.split("/", 1)[0] for p in nav_paths if "/" in p}
    root_pages = {p for p in nav_paths if "/" not in p}
    pages = []
    excluded_print_files = []
    for page in site.pages():
        top = chm_dir(page).split("/", 1)[0]
        if not (top in dirs and "/" in page) and page not in root_pages:
            continue
        if page.endswith("-print.md"):
            excluded_print_files.append(page)
            continue
        pages.append((page, chm_path(page)))

    if excluded_print_files and verbose:
        print(
            f"\nExcluded {len(excluded_print_files)} -print.md files from all processing:"
        )
        for f in sorted(excluded_print_files):
            print(f"  - {posixpath.basename(f)}")

    return pages


def chm_images(site: Site, pages: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    The image files under the guide directories of `pages`, as (absolute
    source path, CHM path) pairs.
    """
    dirs = sorted({src.split("/", 1)[0] for src, _ in pages if "/" in src})
    images = []
    for d in dirs:
        for path in sorted((site.docs / d).rglob("*")):
            if path.suffix in IMAGE_EXTENSIONS and path.is_file():
                rel = path.relative_to(site.docs).as_posix()
                images.append((str(path), chm_dir(rel)))
    return images


def expand_macros(data: str, macros: dict) -> str:
    """
    Substitute {{ name }} variables as the site build does (dyalog_vars): the
    scalars of [project.extra], plus the CHM-only build_date and git_info.
    Unknown names are left as written.
    """
    return dyalog_vars.substitute(data, macros)


def purge_css(css: str, html_content: str) -> str:
    """
    Simple CSS purger that removes unused selectors.
    Uses cssutils for parsing CSS and BeautifulSoup for HTML.
    Preserves selectors for important HTML structure elements.
    """
    # Parse the HTML to find all class names and IDs
    soup = BeautifulSoup(html_content, "html.parser")

    # Find all class names in use
    classes_in_use = set()
    for tag in soup.find_all(class_=True):
        classes_in_use.update(tag.get("class"))

    # Find all IDs in use
    ids_in_use = {tag.get("id") for tag in soup.find_all(id=True)}

    # Find all tag names in use
    tags_in_use = {tag.name for tag in soup.find_all()}

    # Add essential HTML structure elements that might not appear in the parsed HTML
    # but are still needed for proper styling
    essential_tags = {
        "html",
        "head",
        "body",
        "main",
        "article",
        "section",
        "header",
        "footer",
    }
    tags_in_use.update(essential_tags)

    # Parse the CSS
    stylesheet = cssutils.parseString(css)

    # Track which rules to keep
    rules_to_keep = []

    # Process each rule
    for rule in stylesheet:
        if rule.type == rule.STYLE_RULE:
            selectors = rule.selectorText.split(",")
            matched_selectors = []

            for selector in selectors:
                selector = selector.strip()
                should_keep = False

                # Always keep essential structure selectors and their variants
                for tag in essential_tags:
                    if re.search(r"\b" + re.escape(tag) + r"\b", selector):
                        should_keep = True
                        break

                if not should_keep:
                    # Simple tag selectors like "div" or "p"
                    if selector in tags_in_use:
                        should_keep = True
                    # Class selectors like ".my-class"
                    elif selector.startswith(".") and selector[1:] in classes_in_use:
                        should_keep = True
                    # ID selectors like "#my-id"
                    elif selector.startswith("#") and selector[1:] in ids_in_use:
                        should_keep = True
                    # Universal selector
                    elif selector == "*":
                        should_keep = True
                    # Complex selectors - more conservative, keep these to avoid breaking things
                    elif any(c in selector for c in (":", ">", "+", "~", "[", "]")):
                        should_keep = True
                    # Selectors with tags, classes, or IDs that might be used
                    else:
                        # Check for tag names
                        for tag in tags_in_use:
                            if re.search(r"\b" + re.escape(tag) + r"\b", selector):
                                should_keep = True
                                break

                        # Check for class names
                        if not should_keep:
                            for cls in classes_in_use:
                                if re.search(r"\." + re.escape(cls) + r"\b", selector):
                                    should_keep = True
                                    break

                        # Check for IDs
                        if not should_keep:
                            for id_val in ids_in_use:
                                if re.search(
                                    r"#" + re.escape(id_val) + r"\b", selector
                                ):
                                    should_keep = True
                                    break

                if should_keep:
                    matched_selectors.append(selector)

            # If any selectors matched, keep this rule with only the matched selectors
            if matched_selectors:
                rule.selectorText = ", ".join(matched_selectors)
                rules_to_keep.append(rule)

        # Keep other types of rules (like @media, @keyframes, etc.)
        elif rule.type != rule.COMMENT:
            rules_to_keep.append(rule)

    # Create a new stylesheet with only the retained rules
    new_stylesheet = cssutils.css.CSSStyleSheet()
    for rule in rules_to_keep:
        new_stylesheet.add(rule)

    # Serialise the new stylesheet
    return new_stylesheet.cssText.decode("utf-8")


def parse_frontmatter(content: str) -> Tuple[dict, str]:
    """
    Parse YAML frontmatter from markdown content.
    Returns (frontmatter_dict, content_without_frontmatter)
    """
    frontmatter_pattern = r"^\s*---\n(.*?)\n---\n+"
    match = re.match(frontmatter_pattern, content, flags=re.DOTALL)

    if match:
        try:
            frontmatter = yaml.safe_load(match.group(1))
            if not isinstance(frontmatter, dict):
                frontmatter = {}
        except yaml.YAMLError:
            frontmatter = {}
        content_without = re.sub(
            frontmatter_pattern, "", content, count=1, flags=re.DOTALL
        )
        return frontmatter, content_without
    return {}, content


def markdown_extensions(site: Site) -> Tuple[List[str], dict]:
    """
    The site's markdown_extensions from zensical.toml, as (names, configs) for
    Python-Markdown, adjusted for the CHM: Pygments highlighting is forced on
    (a CHM has no client-side highlighter), and dyalog_vars is dropped, as
    expand_macros substitutes the variables (including the CHM-only
    build_date and git_info).
    """
    names: List[str] = []
    configs: dict = {}
    for entry in site.config.get("project", {}).get("markdown_extensions", []):
        if isinstance(entry, str):
            name, config = entry, None
        else:
            ((name, config),) = entry.items()
        if name == "dyalog_vars":
            continue
        names.append(name)
        if config:
            configs[name] = dict(config)
    if "pymdownx.highlight" not in names:
        names.append("pymdownx.highlight")
    configs.setdefault("pymdownx.highlight", {})["use_pygments"] = True
    return names, configs


def render_markdown(md: str, site: Site) -> str:
    """Render one page's Markdown with the CHM's extension set."""
    extensions, configs = markdown_extensions(site)
    return markdown.markdown(md, extensions=extensions, extension_configs=configs)


def mark_os_admonitions(html: str) -> str:
    """
    Add class "os" to info admonitions titled "Dyalog on ...": the site maps
    the old OS-specific admonition types onto info, and the CHM stylesheet
    gives them back their own look.
    """
    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for div in soup.find_all("div", class_="admonition"):
        title = div.find("p", class_="admonition-title", recursive=False)
        if "info" in div["class"] and title and title.get_text().startswith("Dyalog on "):
            div["class"].append("os")
            changed = True
    return str(soup) if changed else html


def mark_example_headings(html: str) -> str:
    """
    Add class "example" to h2-h5 headings reading exactly "Example" or
    "Examples". In v21 these were <hN class="example"> in the source, which
    the site turned into plain headings; the CHM stylesheet's h2.example rule
    sets them in the smaller v21 size again.
    """
    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for heading in soup.find_all(["h2", "h3", "h4", "h5"]):
        if heading.get_text().strip() in ("Example", "Examples"):
            heading["class"] = heading.get("class", []) + ["example"]
            changed = True
    return str(soup) if changed else html


def mark_title_syntax(html: str) -> str:
    """
    Restore the pre-July CHM title layout: the name on the left, the calling
    syntax flush right. A title `<h1><span class="title-name">N</span>
    <code>S</code></h1>` gets class "syntax" on the h1, "name" on the span and
    "command" on the code, so the stylesheet's old .name/.command floats apply.
    Both parts float (rather than the code alone) because the help viewer's
    old IE engine drops a right float that follows inline text onto the next
    line. Classes, floats and overflow are all the viewer reliably supports.
    """
    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for h1 in soup.find_all("h1"):
        name = h1.find("span", class_="title-name", recursive=False)
        code = name.find_next_sibling("code") if name else None
        if code is None:
            continue
        h1["class"] = h1.get("class", []) + ["syntax"]
        name["class"] = name["class"] + ["name"]
        code["class"] = code.get("class", []) + ["command"]
        changed = True
    return str(soup) if changed else html


def convert_to_html(
    pages: List[Tuple[str, str]],
    css: str,
    macros: dict,
    project: str,
    site: Site,
    version: str = None,
) -> Tuple[List[str], List[str]]:
    """
    Convert each Markdown page, given as (absolute source path, CHM topic
    path) pairs, to HTML with the site's Markdown extensions (see
    markdown_extensions). We expand the {{ variable }} templates. Currently, we add
    all the CSS in the header - this is required as the HTML engine in the Windows CHM
    viewer does not understand <link ...>.

    As a mitigation, take steps to only add the actually used CSS bits.

    Returns: (converted_files, excluded_files)
    """
    converted: List[str] = []
    excluded: List[str] = []

    head_template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
"""

    for file, newname in pages:
        realpath_newname = str(os.path.join(project, newname))
        os.makedirs(os.path.dirname(realpath_newname), exist_ok=True)

        with open(file, "r", encoding="utf-8") as f:
            md = f.read()

        # Parse frontmatter and check for search exclusion
        frontmatter, md = parse_frontmatter(md)
        if frontmatter.get("search", {}).get("exclude", False):
            excluded.append(file)

        # Variables are defined in [project.extra] in zensical.toml. In the Markdown
        # source, they are templates of the type
        #
        #    {{ variable-name }}
        md = expand_macros(md, macros)

        body = render_markdown(md, site)

        body = body.replace("``", "")  # Empty code blocks aren't rendered correctly

        body = fix_links_html(body, version=version)
        body = remove_footnote_backlinks(body)
        body = make_footnote_urls_clickable(body)
        body = fix_key_notation_link(body, newname)
        body = fix_external_links(body)
        body = fix_apl_root_namespace_highlighting(body)
        body = mark_os_admonitions(body)
        body = mark_example_headings(body)
        body = mark_title_syntax(body)

        # Extract the H1 content for use in the title tag, setting for_title=True
        # to only extract the name part (excluding command span)
        title = extract_h1(body, for_title=True)

        # Use a default title if no H1 is found
        if not title:
            # Use the filename without extension as a fallback title
            title = (
                os.path.splitext(os.path.basename(file))[0].replace("_", " ").title()
            )

            # For special files like welcome.md, use a more appropriate title
            if newname == "welcome.htm":
                title = "Welcome to Dyalog APL"

        # Format the head with the title
        head = head_template.format(title=title)

        # Optimise CSS specifically for this page: only use selectors referring to
        # ids, classes and tags on the actual page.
        optimised_css = purge_css(css, body)

        # Minimise the CSS
        optimised_css = css_compress(optimised_css)

        # Construct and minimise the HTML
        final_html = (
            f"{head}<style>{optimised_css}</style></head><body>{body}</body></html>"
        )
        final_html = html_minify(
            final_html,
            remove_comments=True,
            remove_empty_space=True,
            remove_all_empty_space=False,
            reduce_boolean_attributes=True,
        )

        with open(realpath_newname, "w", encoding="utf-8") as f:
            f.write(final_html)

        converted.append(str(newname))
    return converted, excluded


def copy_images(images: List[Tuple[str, str]], project="project") -> List[str]:
    copied: List[str] = []
    for file, newname in images:
        realpath_newname = os.path.join(project, newname)
        os.makedirs(os.path.dirname(realpath_newname), exist_ok=True)
        shutil.copy(file, realpath_newname)
        copied.append(newname)
    return copied


def static_assets(
    src_dir="assets", project="project"
) -> Tuple[List[str], str, List[str]]:
    """
    Copy the items in the assets dir. There is no supported way to
    bundle css via a <link> tag in a CHM file, so we concatenate
    the css files we can find and return this as a string for
    injection into each page.
    """
    assets = []
    css_files = []  # Keep track of CSS files
    css = ""

    if not os.path.exists(src_dir):
        return assets, css, css_files

    os.makedirs(os.path.join(project, "assets"), exist_ok=True)

    for root, _, files in os.walk(src_dir):
        for file in files:
            src_path = os.path.join(root, file)
            rel_path = os.path.relpath(src_path, src_dir)
            dst_path = os.path.join(project, "assets", rel_path)

            # Ensure destination directory exists
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)

            if file.endswith(".css"):
                with open(src_path, "r", encoding="utf-8") as f:
                    data = f.read()
                css = css + data
                css_files.append(src_path)  # Add to list of CSS files
            else:
                shutil.copy(src_path, dst_path)
                assets.append(posixpath.join("assets", Path(rel_path).as_posix()))

    return assets, css, css_files


def extract_h1(data: str, for_title: bool = False) -> str:
    """
    Return the name from the first h1, cleaned for use as a title or index entry.

    Current headers render as `<h1><span>Name</span> <code>command</code>
    <a>...</a></h1>`: the command comes from backticks and the trailing link from
    the {{key}} macro. Both carry UTF-8 the Windows CHM index and title bar cannot
    display, so the name is the h1 text with <code> and <a> elements removed.
    Headers without those (e.g. Object Reference "Name Type") keep their full text.

    A normalised title, `<h1><span class="title-name">Name</span> <code>command</code>`,
    and legacy <span class="name">/<span class="command"> markup are honoured, where
    for_title selects the name alone versus "name command".
    """
    soup = BeautifulSoup(data, "html.parser")
    h1 = soup.find("h1")
    if not h1:
        return ""

    if name_span := h1.find("span", class_="title-name"):
        name = re.sub(r"\s+", " ", name_span.get_text()).strip()
        code = h1.find("code")
        if not for_title and code:
            name = f"{name} {code.get_text(' ').strip()}"
        return name.replace('"', "").replace("`", "")

    if name_span := h1.find("span", class_="name"):
        name = name_span.get_text().strip()
        command_span = h1.find("span", class_="command")
        if for_title or not command_span:
            return name.replace('"', "").replace("`", "")
        return f"{name} {command_span.get_text()}".strip().replace('"', "").replace("`", "")

    for tag in h1.find_all(["code", "a"]):
        tag.decompose()
    name = re.sub(r"\s+", " ", h1.get_text()).strip()
    following = h1.find_next_sibling()
    if following is not None and following.name == "p":
        classifier = following.get_text().strip()
        if CLASSIFIER_RE.fullmatch(classifier):
            name = f"{name} {classifier}"
    return name.replace('"', "").replace("`", "")


def extract_headers(filename: str) -> List[str]:
    """
    Return a page's title (name) for the CHM keyword index, cleaned to plain text.

    Only the page title is indexed; sub-headings ("Introduction", "Examples", ...)
    are not distinguishing keywords. The title comes from a literal HTML <h1> or,
    for markdown pages, the first `# ` heading. The {{key}} macro, the backtick
    command and HTML tags are stripped so the entry is the plain-text name and
    carries no UTF-8 the Windows CHM index display cannot cope with.
    """
    with open(filename, "r", encoding="utf-8") as f:
        data = f.read()

    # Legacy pages with a literal HTML <h1>.
    if h1 := extract_h1(data, for_title=True):
        return [h1]

    _, body = parse_frontmatter(data)
    body = re.sub(r"^```.*?^```", "", body, flags=re.DOTALL | re.MULTILINE)
    match = re.search(r"^#\s+(.+?)\s*$", body, flags=re.MULTILINE)
    if not match:
        return []

    title = re.sub(r"\{\{.*?\}\}", "", match.group(1))  # drop macros e.g. {{key}}
    title = re.sub(r"`[^`]*`", "", title)  # drop a backtick command
    soup = BeautifulSoup(title, "html.parser")
    for tag in soup.find_all(["code", "a"]):  # drop a literal <code> command / key link
        tag.decompose()
    title = re.sub(r"\s+", " ", soup.get_text()).strip().strip('"').strip("`").strip()
    following = re.match(r"\s*\n\s*([^\n]*?)\s*$", body[match.end():], flags=re.MULTILINE)
    if title and following and CLASSIFIER_RE.fullmatch(following.group(1)):
        title = f"{title} {following.group(1)}"
    return [title] if title else []


def generate_index_data(files: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    Note: we're generating the index data from the source Markdown, given as
    (source path, CHM topic path) pairs, so that entries refer to the
    converted HTML files.
    """
    entries = []
    for filename, newname in files:
        for heading in extract_headers(filename):
            entries.append((heading, newname))

    # Sort the index alphabetically by entry (not filename!)
    entries.sort(key=lambda w: w[0])

    return entries


def write_index_data(entries: List[Tuple[str, str]], filename: str) -> None:
    """
    Write out the CHM index file.
    """
    with open(filename, "w", encoding="utf-8") as idxfh:
        idxfh.write(HEADER)
        idxfh.write("<ul>\n")
        for x in entries:
            idxfh.write(FILE_ENTRY.format(name=x[0], file=x[1]))
        idxfh.write("</ul>\n")
        idxfh.write("</body></html>\n")


def generate_hfp(
    project: str,
    chmfile: str,
    files: List[str],
    images: List[str],
    assets: List[str],
    title: str,
    codepage: int = 65001,
) -> None:
    """
    Generates a project .hfp file for the chmcmd chm compiler, listing all .htm-files
    in the directory tree, plus the _index.hhk and _table_of_contents.hhc files.
    """
    start_page = None
    base, chmext = os.path.splitext(chmfile)
    hfp = base + ".hfp"
    outfile = os.path.join(project, hfp)

    impl = getDOMImplementation()

    doc = impl.createDocument(None, "CONFIG", None)

    # Create the <CONFIG> base element
    cfg = doc.documentElement

    # Files section
    filegroup = doc.createElement("Files")
    cfg.appendChild(filegroup)

    count = doc.createElement("Count")
    count.setAttribute("Value", str(len(files) + len(images) + len(assets)))
    filegroup.appendChild(count)

    fcount = 0

    for file in itertools.chain(files, images, assets):
        f = doc.createElement("FileName" + str(fcount))
        f.setAttribute("Value", file)
        filegroup.appendChild(f)
        if start_page is None:
            _, ext = os.path.splitext(file)
            if ext == ".htm":
                start_page = file
        fcount = fcount + 1

    tcf = doc.createElement("IndexFile")
    tcf.setAttribute("Value", "_index.hhk")
    filegroup.appendChild(tcf)

    tcf = doc.createElement("TOCFile")
    tcf.setAttribute("Value", "_table_of_contents.hhc")
    filegroup.appendChild(tcf)

    settings = doc.createElement("Settings")
    cfg.appendChild(settings)

    searchable = doc.createElement("MakeSearchable")
    searchable.setAttribute("Value", "True")
    settings.appendChild(searchable)

    if start_page is None:
        start_page = "welcome.htm"
    dflt = doc.createElement("DefaultPage")
    dflt.setAttribute("Value", start_page)
    settings.appendChild(dflt)

    te = doc.createElement("Title")
    te.setAttribute("Value", title)
    settings.appendChild(te)

    ofn = doc.createElement("OutputFileName")
    ofn.setAttribute("Value", chmfile)
    settings.appendChild(ofn)

    fnt = doc.createElement("DefaultFont")
    fnt.setAttribute("Value", "")
    settings.appendChild(fnt)

    # Add CodePage setting
    cp = doc.createElement("CodePage")
    cp.setAttribute("Value", str(codepage))
    settings.appendChild(cp)

    # Hard-coded Language ID for UK English (0x0809)
    lang = doc.createElement("Language")
    lang.setAttribute("Value", "0x0809")
    settings.appendChild(lang)

    # Add additional CHM-specific settings that might help with search
    binary_index = doc.createElement("BinaryIndex")
    binary_index.setAttribute("Value", "True")
    settings.appendChild(binary_index)

    full_text_search = doc.createElement("FullTextSearch")
    full_text_search.setAttribute("Value", "True")
    settings.appendChild(full_text_search)

    with open(outfile, "w", encoding="utf-8") as f:
        f.write(doc.toprettyxml(indent="  "))


def fix_links_html(html: str, version: str = None) -> str:
    """
    Applies the link transformations to an HTML document:
    1. Links with targets ending in ".md" are changed to ".htm".
    2. Links without extensions and leading "../" are lifted one relative level, and suffixed with ".htm".
    3. Off-site links starting with "http" remain unchanged.
    4. Links containing "/files/" are converted to external URLs pointing to docs.dyalog.com.

    Parameters:
        html (str): The HTML content as a string.
        version (str): The version string for external file links (e.g., "20.0")

    Returns:
        str: The modified HTML content.
    """

    def transform_link(href: str) -> str:
        """
        Transforms an individual href link according to the rules.
        """
        if href.startswith("http"):  # Off-site link: leave unchanged
            return href

        if href.startswith("#"):  # Pure anchor link: leave unchanged
            return href

        # Check if this is a /files/ link that should be converted to external URL
        # Look for patterns like ../files/ or ../../files/ etc.
        if "/files/" in href:
            # Extract the part after /files/
            files_index = href.index("/files/") + len("/files/")
            file_path = href[files_index:]
            # Use version if provided, otherwise default to 20.0
            ver = version if version else "20.0"
            return f"https://docs.dyalog.com/{ver}/files/{file_path}"

        # Separate anchor from the path (if present)
        anchor = ""
        if "#" in href:
            href, anchor = href.split("#", 1)
            anchor = "#" + anchor

        # Remove trailing slash if present
        if href.endswith("/"):
            href = href[:-1]

        # Links into a guide whose site alias differs from its CHM directory
        href = "/".join(CHM_DIR_FOR_ALIAS.get(seg, seg) for seg in href.split("/"))

        path, name = posixpath.split(href)
        base, ext = posixpath.splitext(name)

        if ext == ".md":  # Link to another sub-site: change ".md" to ".htm"
            return posixpath.join(path, f"{base}.htm") + anchor

        if (
            ext == ""
        ):  # Link within the same side -- lift one relative level, and add .htm
            target = re.sub(r"^\.\.\/", "", href, count=1)
            return f"{target}.htm" + anchor

        # Something else; leave unchanged (but restore anchor)
        return href + anchor

    # Parse the HTML
    soup = BeautifulSoup(html, "html.parser")

    # Find and transform all <a> tags with href attributes
    for a_tag in soup.find_all("a", href=True):
        original_href = a_tag["href"]
        a_tag["href"] = transform_link(original_href)

    # Return the modified HTML as a string
    return str(soup)


def remove_footnote_backlinks(html: str) -> str:
    """
    Remove linking aspects from footnotes:
    1. Convert footnote reference links to plain superscript text
    2. Remove backlinks from footnote text

    Parameters:
        html (str): The HTML content as a string.

    Returns:
        str: The modified HTML content.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find all footnote reference links and replace with plain superscript text
    for a_tag in soup.find_all("a", class_="footnote-ref"):
        # Get the footnote number/text
        footnote_text = a_tag.get_text()
        # Create a superscript element
        sup_tag = soup.new_tag("sup")
        sup_tag.string = footnote_text
        # Replace the link with the superscript
        a_tag.replace_with(sup_tag)

    # Find all footnote backlinks and remove them
    for a_tag in soup.find_all("a", class_="footnote-backref"):
        # Simply remove the backlink
        a_tag.decompose()

    return str(soup)


def make_footnote_urls_clickable(html: str) -> str:
    """
    Convert bare URLs in footnote content into clickable links.
    This is necessary because Python Markdown doesn't automatically convert
    bare URLs to links in footnotes, making them unclickable in CHM files.

    Parameters:
        html (str): The HTML content as a string.

    Returns:
        str: The modified HTML content with clickable URLs in footnotes.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the footnote div
    footnote_div = soup.find("div", class_="footnote")
    if not footnote_div or not isinstance(footnote_div, Tag):
        return str(soup)

    # Pattern to match URLs (http, https, ftp)
    url_pattern = re.compile(
        r'(https?://[^\s<>"{}|\\^`\[\]]+|ftp://[^\s<>"{}|\\^`\[\]]+)', re.IGNORECASE
    )

    # Find all <p> tags within footnotes that might contain URLs
    p_tags = footnote_div.find_all("p")
    if not p_tags:
        return str(soup)

    for p_tag in p_tags:
        # We need to work with a copy of contents since we're modifying it
        if not isinstance(p_tag, Tag):
            continue

        contents_to_process = list(p_tag.contents)

        for content in contents_to_process:
            # Only process NavigableString (text) nodes, not tags
            if isinstance(content, NavigableString) and not isinstance(
                content, type(soup)
            ):
                text = str(content)
                # Check if this text contains a URL
                if url_pattern.search(text):
                    # Split the text by URLs and create new elements
                    new_contents = []
                    last_end = 0

                    for match in url_pattern.finditer(text):
                        url = match.group(0)
                        start, end = match.span()

                        # Add text before the URL
                        if start > last_end:
                            new_contents.append(text[last_end:start])

                        # Create a link for the URL
                        a_tag = soup.new_tag("a", href=url, target="_blank")
                        a_tag.string = url
                        new_contents.append(a_tag)

                        last_end = end

                    # Add any remaining text after the last URL
                    if last_end < len(text):
                        new_contents.append(text[last_end:])

                    # Replace the original text node with the new contents
                    content.replace_with(*new_contents)

    return str(soup)


def fix_key_notation_link(html: str, page: str) -> str:
    """
    Point the key-notation marker at the in-CHM page and make it superscript.

    The {{key}} macro renders as an anchor whose text is the U+1F6C8 glyph,
    linking to the key-to-notation page on the website. In the CHM it must be a
    relative link to the bundled page so it navigates in-place, and the marker
    reads as a superscript. `page` is the anchor's project-relative .htm path,
    used to resolve the link. Runs before fix_external_links so the now-internal
    link is not given target="_blank".
    """
    soup = BeautifulSoup(html, "html.parser")
    relative = posixpath.relpath(
        "language-reference-guide/key-to-notation.htm", os.path.dirname(page)
    )
    changed = False
    for anchor in soup.find_all("a"):
        if anchor.get_text() == "\U0001f6c8":
            anchor["href"] = relative
            if anchor.has_attr("target"):
                del anchor["target"]
            anchor.wrap(soup.new_tag("sup"))
            changed = True
    return str(soup) if changed else html


def fix_external_links(html: str) -> str:
    """
    Fix external links for CHM compatibility.
    The Windows CHM viewer can open external links with target="_blank".
    This function adds the target attribute to all external HTTP/HTTPS links.

    Parameters:
        html (str): The HTML content as a string.

    Returns:
        str: The modified HTML content.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find all external links
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href")
        if href and href.startswith(("http://", "https://")):
            # Add target="_blank" to open in external browser
            a_tag["target"] = "_blank"

    return str(soup)


def fix_apl_root_namespace_highlighting(html: str) -> str:
    """
    Fix incorrect syntax highlighting of '#' in APL code blocks.

    The Pygments APL lexer treats '#' as a comment character (for GNU APL
    compatibility).

    This function finds comment spans within highlight blocks that start with '#'
    and converts them to regular variable spans, preserving any actual APL
    comments that start with '⍝'.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find all highlight divs (code blocks with syntax highlighting)
    for highlight_div in soup.find_all("div", class_="highlight"):
        # Find comment spans (c1 = Comment.Single, c = Comment)
        for comment_span in highlight_div.find_all("span", class_=["c1", "c"]):
            text = comment_span.get_text()

            # Check if this "comment" starts with '#' (root namespace, not a real comment)
            # Real APL comments start with '⍝'
            if text.startswith("#"):
                # Change the class from comment to variable (nv = Name.Variable)
                comment_span["class"] = ["nv"]

    return str(soup)


def find_image_references_in_markdown(md_files: List[str]) -> set:
    """
    Find all image references in Markdown files.
    Returns set of image filenames.
    """
    image_refs = set()

    # Regular expression for Markdown image syntax: ![alt](path/image.png)
    md_img_pattern = r"!\[.*?\]\((.*?)\)"

    # Regular expression for HTML image tags in Markdown: <img src="path/image.png">
    html_img_pattern = r'<img[^>]*src=[\'"]([^\'"]+)[\'"][^>]*>'

    for file in md_files:
        try:
            with open(file, "r", encoding="utf-8") as f:
                content = f.read()

                # Find Markdown-style image references
                for match in re.findall(md_img_pattern, content):
                    if not match.startswith(("http:", "https:", "data:")):
                        image_refs.add(os.path.basename(match))

                # Find HTML-style image references
                for match in re.findall(html_img_pattern, content):
                    if not match.startswith(("http:", "https:", "data:")):
                        image_refs.add(os.path.basename(match))
        except Exception as e:
            print(f"Warning: Could not check images in {file}: {e}")

    return image_refs


def find_image_references_in_css(css_files: List[str]) -> set:
    """
    Find all image references in CSS files.
    Returns set of image filenames.
    """
    image_refs = set()

    # Regular expression for CSS url() syntax: url('/path/image.png')
    css_img_pattern = r'url\([\'"]?([^\'"()]+)[\'"]?\)'

    for file in css_files:
        try:
            with open(file, "r", encoding="utf-8") as f:
                content = f.read()

                # Find CSS url() references
                for match in re.findall(css_img_pattern, content):
                    if not match.startswith(("http:", "https:", "data:")):
                        # Extract just the filename from the path
                        image_refs.add(os.path.basename(match))
        except Exception as e:
            print(f"Warning: Could not check images in CSS file {file}: {e}")

    return image_refs


def filter_unused_images(
    md_files: List[str], css_files: List[str], image_files: List[str]
) -> List[str]:
    """
    Filter out images that are not referenced in any Markdown or CSS files.
    Returns filtered list of image files to include.
    """
    # Find all image references from Markdown and CSS
    md_referenced_images = find_image_references_in_markdown(md_files)
    css_referenced_images = find_image_references_in_css(css_files)

    referenced_images = md_referenced_images.union(css_referenced_images)

    # Filter image_files to only include referenced images
    used_images = []
    unused_images = []

    for img in image_files:
        if os.path.basename(img) in referenced_images:
            used_images.append(img)
        else:
            unused_images.append(img)

    # Report findings
    if unused_images:
        print("\nExcluding unused images:")
        for img in sorted(unused_images):
            print(f"  {os.path.basename(img)}")
        print(
            f"\nTotal: {len(unused_images)} unused images removed, keeping {len(used_images)} used images"
        )
    else:
        print("\nAll images are referenced in the documentation or CSS.")

    if css_referenced_images:
        print("\nImages referenced in CSS:")
        for img in sorted(css_referenced_images):
            print(f"  {img}")

    return used_images


def add_langref_disambiguation_pages(root: Node, project: str) -> None:
    """
    Add missing symbol files to the TOC as a post-processing step.
    This function finds all converted HTML files in the symbols directory
    and adds them to the "Symbols" node in the TOC if they aren't already there.

    Parameters:
        root (Node): The root node of the TOC tree
        project (str): The project directory
    """
    print("Post-processing TOC to add Language Reference disambiguation pages...")

    # First find the "Dyalog APL Language" node which might be under "Core Reference" section group
    lang_ref_node = None

    # Check direct children first
    for child in root.children:
        if child.name == "Dyalog APL Language":
            lang_ref_node = child
            break

    # If not found, check under section groups
    if not lang_ref_node:
        for child in root.children:
            if child.name == "Core Reference" and child.children:
                for subchild in child.children:
                    if subchild.name == "Dyalog APL Language":
                        lang_ref_node = subchild
                        break
                if lang_ref_node:
                    break

    if not lang_ref_node:
        print("  Warning: Dyalog APL Language node not found in TOC")
        return

    # Then find the "Symbols" node under "Dyalog APL Language"
    symbols_node = None
    for child in lang_ref_node.children:
        if child.name == "Symbols":
            symbols_node = child
            break

    if not symbols_node:
        print("  Warning: Symbols node not found under Language Reference in TOC")
        return

    print(
        f"  Found Symbols node in TOC with {len(symbols_node.children)} existing entries"
    )

    # Get the symbol html files from the project directory
    symbols_dir = os.path.join(project, "language-reference-guide", "symbols")
    if not os.path.isdir(symbols_dir):
        print(f"  Warning: Symbols directory not found at {symbols_dir}")
        return

    # Get all HTML files in the symbols directory
    html_files = [f for f in os.listdir(symbols_dir) if f.endswith(".htm")]
    print(f"  Found {len(html_files)} HTML files in symbols directory")

    # Find which ones are already in the TOC
    existing_files = set()
    for child in symbols_node.children:
        if child.html_name:
            existing_files.add(os.path.basename(child.html_name))

    print(f"  Found {len(existing_files)} files already in TOC")

    # For each HTML file not in the TOC, extract its title and add it
    added_count = 0
    for html_file in html_files:
        # Skip if already in TOC
        if html_file in existing_files:
            continue

        # Read the HTML file to extract its title
        html_path = os.path.join(symbols_dir, html_file)
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            # Extract the title from the HTML
            soup = BeautifulSoup(html_content, "html.parser")
            title = ""

            # Try to find title in the head first
            if title_tag := soup.find("title"):
                title = title_tag.string.strip()

            # If no title or title is too generic, try to find the name span in h1
            if not title or title == "Dyalog APL":
                if h1 := soup.find("h1", class_="heading"):
                    if name_span := h1.find("span", class_="name"):
                        title = name_span.string.strip()

            # If still no title, use filename
            if not title:
                title = os.path.splitext(html_file)[0].replace("-", " ").title()

            # Create a new node and add it to the Symbols node
            node = Node(
                title, f"symbols/{html_file}", symbols_node.depth + 1, symbols_node
            )
            node.html_name = f"language-reference-guide/symbols/{html_file}"
            symbols_node.children.append(node)
            added_count += 1
            print(f"    Added: {title} -> {node.html_name}")

        except Exception as e:
            print(f"  Warning: Error processing {html_file}: {e}")

    if added_count > 0:
        print(f"  Added {added_count} additional symbol files to TOC")
    else:
        print("  No additional symbol files needed to be added")


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate a CHM bundle from the Zensical project"
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="Directory holding zensical.toml (default: docsite.find_project())",
    )
    parser.add_argument(
        "--project-dir", type=str, required=True, help="Name of output directory"
    )
    parser.add_argument(
        "--assets-dir",
        type=str,
        default=str(HERE / "assets"),
        help="Name of assets directory",
    )
    parser.add_argument(
        "--welcome",
        type=str,
        default=str(HERE / "welcome.md"),
        help="Path to welcome.md file",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(HERE / "config.json"),
        help="JSON config file for additional options",
    )
    parser.add_argument("--git-info", type=str, help="Git branch and commit info")
    parser.add_argument("--build-date", type=str, help="Build date")
    parser.add_argument(
        "--codepage",
        type=int,
        default=65001,
        help="CodePage to use (default: 65001 for UTF-8)",
    )
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Write the CHM project only; do not run chmcmd",
    )

    args = parser.parse_args(argv)

    project = args.project if args.project is not None else find_project()
    if not (project / "zensical.toml").is_file():
        sys.exit(f"--> no zensical.toml in {project}")

    if not os.path.isfile(args.welcome):
        sys.exit(f"--> welcome file {args.welcome} not found.")

    os.makedirs(args.project_dir, exist_ok=True)

    # Parse config for excludes
    excludes = []
    if args.config:
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                config = json.load(f)
                excludes = config.get("exclude", [])
        except (json.JSONDecodeError, FileNotFoundError) as e:
            sys.exit(f"--> Error reading config file: {e}")

    site = Site.load(project)
    nav_data = load_nav(site, exclude=excludes)

    extra = dict(site.config.get("project", {}).get("extra", {}))
    version = extra.get("version_majmin")
    if not version:
        sys.exit("--> zensical.toml has no Dyalog version set (extra.version_majmin)")

    # The pages to convert, as (absolute source path, CHM topic path), welcome first
    doc_pages = chm_pages(site, nav_data)
    pages = [(os.path.abspath(args.welcome), "welcome.htm")] + [
        (str(site.docs / src), out) for src, out in doc_pages
    ]
    md_files = [src for src, _ in pages]

    # Modify nav to include welcome page at the start
    nav_data["nav"].insert(0, "welcome.md")

    # Copy images and other static assets into the project
    assets, css, css_files = static_assets(args.assets_dir, args.project_dir)

    # Filter out unused images, considering both MD and CSS references
    images = dict(chm_images(site, doc_pages))
    used = filter_unused_images(md_files, css_files, list(images))
    copied_images = copy_images(
        [(src, images[src]) for src in used], project=args.project_dir
    )

    # Add git info and build date to the variables
    macros = extra
    if args.git_info:
        macros["git_info"] = args.git_info
    if args.build_date:
        macros["build_date"] = args.build_date

    # Convert to HTML
    html_files, excluded_files = convert_to_html(
        pages,
        css,
        macros=macros,
        project=args.project_dir,
        site=site,
        version=version,
    )

    # Remove excluded files from the pages for indexing
    pages = [p for p in pages if p[0] not in excluded_files]

    if excluded_files:
        print(
            f"\nExcluded {len(excluded_files)} files from search index due to 'search: exclude: true':"
        )
        for f in excluded_files:
            print(f"  - {os.path.basename(f)}")

    # Generate the CHM ToC
    generate_toc(nav_data, project=args.project_dir)

    # Generate the index
    idx = generate_index_data(pages)
    write_index_data(idx, f"{args.project_dir}/_index.hhk")

    print(f"Converted {len(html_files)} Markdown files to HTML.")

    # Generate the CHM project config file
    chm_name = "dyalog.chm"
    generate_hfp(
        args.project_dir,
        chm_name,
        html_files,
        copied_images,
        assets,
        title=f"Dyalog version {version}",
        codepage=args.codepage,
    )

    if args.no_compile:
        return

    # Run the compiler
    output = Popen(["chmcmd", "dyalog.hfp"], cwd=args.project_dir)
    if output.wait() != 0:
        sys.exit("--> chmcmd failed")


if __name__ == "__main__":
    main()
