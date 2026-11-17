"""Miniature monorepo builder mirroring the real source's shape.

Three sub-projects stand in for the fourteen, one of them (the .NET
guide) mounted under a slugified alias rather than its directory name. Every structure the tests
exercise is copied from the real corpus: quoted !include nav entries,
an extra: value that conflicts with the root (the monorepo build reads
only the root value, so root-wins preserves the rendered baseline),
duplicate pymdownx.highlight blocks, the arithmatex extension and
per-sub-project mathjax.js copies (neither is carried over), nested nav
sections, a binary asset, and pages carrying each house pattern the content
transforms rewrite: a raw-HTML title, a current-form styled title with the
{{key}} macro, an example heading, a bespoke admonition type, a shaded
default cell, and a sprite toolbar icon.

build_overlay_tree builds the matching content overlay: the toolbar icon
image the sprite page needs, and a front-page replacement with its
source-hash sidecar.

For the cutover script, build_upstream_repo turns the miniature monorepo into
a git repository with the rest of the upstream shape around it (the Jenkins
files at their fixed paths, the MkDocs-era workflows, chm/, pdf/, the Docker
tooling, the assets submodule, root language-bar/), and build_tooling_repo
builds the matching miniature of this repository: tracked tooling, the
dispatch stub workflows, the house-style gitlink and tools/cutover/ with the
overlay, template and theme override the cutover reads.
"""

import hashlib
import shutil
import subprocess
import textwrap
from pathlib import Path

SUB_NAMES = ("release-notes", "compiler-user-guide", "dotnet-interface-guide")

MATHJAX_JS = (
    "window.MathJax = {};\ndocument$.subscribe(() => MathJax.typesetPromise())\n"
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR fake image bytes"

INDEX_MD = "# Documentation\n\n!!! Hint \"Hints\"\n    New here?\n"
INDEX_OVERLAY_MD = "# Documentation\n\nReplaced front page.\n"

ROOT_MKDOCS = textwrap.dedent(
    """\
    site_name: Documentation
    repo_url: https://github.com/dyalog/documentation
    copyright: Copyright &copy; 1982-$CURRENT_YEAR Dyalog Limited
    theme:
      favicon: documentation-assets/images/favicon-32.png
      logo: documentation-assets/images/dyalog-logo_white.svg
      name: material
      features:
        - navigation.instant
        - navigation.footer
      font:
        text: Be Vietnam Pro
    extra_css:
      - documentation-assets/css/main.css
      - documentation-assets/css/extra.css
      - documentation-assets/css/admonition-ucmdhelp.css
    extra_javascript:
      - javascripts/mathjax.js
      - https://unpkg.com/mathjax@3/es5/tex-mml-chtml.js
    plugins:
      - privacy
      - search
      - macros
      - monorepo
      - caption:
          table:
            enable: true
            position: top
      - minify:
          minify_html: true
    extra:
      version_maj: 21
      version_majmin: "21.0"
      generator: false
      version:
        provider: mike
    markdown_extensions:
      - admonition
      - pymdownx.arithmatex:
          generic: true
      - pymdownx.highlight:
          use_pygments: false
          pygments_lang_class: true
      - attr_list
      - markdown_tables_extended
      - toc:
          title: On this page
    nav:
      - 'Release Notes':
          - 'v21.0 Release Notes': "!include ./release-notes/mkdocs.yml"
      - 'Code Tooling':
          - '.NET Interface': "!include ./dotnet-interface-guide/mkdocs.yml"
          - 'Compiler': "!include ./compiler-user-guide/mkdocs.yml"
      - 'About':
          - 'Conventions': conventions.md
    """
)

RELEASE_NOTES_MKDOCS = textwrap.dedent(
    """\
    site_name: Release Notes
    theme:
      name: material
      features:
        - navigation.instant
    extra_css:
      - documentation-assets/css/main.css
    extra_javascript:
      - javascripts/mathjax.js
      - https://unpkg.com/mathjax@3/es5/tex-mml-chtml.js
    plugins:
      - privacy
      - search
      - macros
      - site-urls
      - caption:
          table:
            enable: true
            position: top
    extra:
      version_maj: 21
      version_majmin: "21.0"
    markdown_extensions:
      - admonition
      - pymdownx.highlight:
          use_pygments: false
          pygments_lang_class: true
      - footnotes
    nav:
      - index.md
      - Announcements:
          - Announcements: announcements/index.md
          - Deprecated Functionality: announcements/deprecated-functionality.md
      - System Requirements: system-requirements.md
    """
)

COMPILER_MKDOCS = textwrap.dedent(
    """\
    site_name: Compiler User Guide
    theme:
      name: material
    extra_css:
      - documentation-assets/css/main.css
      - documentation-assets/css/extra.css
      - style/main.css
    plugins:
      - privacy
      - search
      - macros
      - site-urls
    extra:
      version_maj: 20
      version_majmin: "20.0"
    markdown_extensions:
      - admonition
      - pymdownx.highlight:
          use_pygments: false
          pygments_lang_class: true
    nav:
      - index.md
      - Basic Usage: basic-usage.md
    """
)

# The site_name mkdocs-monorepo-plugin slugifies to net-interface-guide: the
# one guide shape whose mount point differs from its directory name.
DOTNET_MKDOCS = textwrap.dedent(
    """\
    site_name: .NET Interface Guide
    theme:
      name: material
    plugins:
      - search
      - macros
    nav:
      - index.md
      - Installation: installation.md
    """
)

STYLED_TITLE_MD = (
    "# <span>Comma Separated Values</span> `{R}←{X} ⎕CSV Y`{{key}}\n\n"
    "This function imports CSV data.\n\n"
    '<h2 class="example">Examples</h2>\n```apl\n      ⎕CSV file\n```\n'
)

EXAMPLES_MD = textwrap.dedent(
    """\
    # Options

    !!! Hint "Hints and Recommendations"
        Use the defaults.

    |---|-------------|
    |0 { .shaded } |do not repair|
    |`1`|repair       |

    Default values are highlighted thus{ .shaded }  in the above tables.

    <h2 class="example">Example</h2>
    ```apl
          1 ⎕FCHK 'f'
    ```
    """
)

TOOLBAR_MD = textwrap.dedent(
    """\
    # Toolbar

    |Button|Name|
    |---|---|
    |<span class="toolbar-icon" style="background-position: -64px 0"></span>|Exec|
    """
)


def build_source_tree(root: Path) -> Path:
    """Materialise the miniature monorepo under root and return it."""
    src = root / "documentation"

    (src / "docs" / "javascripts").mkdir(parents=True)
    (src / "mkdocs.yml").write_text(ROOT_MKDOCS, encoding="utf-8")
    (src / "docs" / "index.md").write_text(INDEX_MD, encoding="utf-8")
    (src / "docs" / "conventions.md").write_text("# Conventions\n", encoding="utf-8")
    (src / "docs" / "javascripts" / "mathjax.js").write_text(MATHJAX_JS, encoding="utf-8")

    (src / "documentation-assets" / "css").mkdir(parents=True)
    (src / "documentation-assets" / "css" / "main.css").write_text("body {}\n", encoding="utf-8")

    rn = src / "release-notes"
    (rn / "docs" / "announcements").mkdir(parents=True)
    (rn / "docs" / "javascripts").mkdir(parents=True)
    (rn / "docs" / "images").mkdir(parents=True)
    (rn / "mkdocs.yml").write_text(RELEASE_NOTES_MKDOCS, encoding="utf-8")
    (rn / "docs" / "index.md").write_text("# Release Notes {{ version_majmin }}\n", encoding="utf-8")
    (rn / "docs" / "announcements" / "index.md").write_text("# Announcements\n", encoding="utf-8")
    (rn / "docs" / "announcements" / "deprecated-functionality.md").write_text(
        "# Deprecated\nSee [conventions](../../conventions.md).\n",
        encoding="utf-8",
    )
    (rn / "docs" / "system-requirements.md").write_text("# System Requirements\n", encoding="utf-8")
    (rn / "docs" / "javascripts" / "mathjax.js").write_text(MATHJAX_JS, encoding="utf-8")
    (rn / "docs" / "images" / "logo.png").write_bytes(PNG_BYTES)

    cu = src / "compiler-user-guide"
    (cu / "docs").mkdir(parents=True)
    (cu / "mkdocs.yml").write_text(COMPILER_MKDOCS, encoding="utf-8")
    (cu / "docs" / "index.md").write_text("# Compiler User Guide\n", encoding="utf-8")
    (cu / "docs" / "basic-usage.md").write_text("# Basic Usage\n", encoding="utf-8")
    # A page whose title is a raw-HTML heading (the pre-July object-reference
    # shape), to exercise the rewrite. Body left as plain text.
    (cu / "docs" / "raw-heading.md").write_text(
        '<h1 class="heading"><span class="name">Widget</span>'
        ' <span class="right">Object</span></h1>\n\nBody text.\n',
        encoding="utf-8",
    )
    (cu / "docs" / "styled-title.md").write_text(STYLED_TITLE_MD, encoding="utf-8")
    (cu / "docs" / "examples.md").write_text(EXAMPLES_MD, encoding="utf-8")
    (cu / "docs" / "toolbar.md").write_text(TOOLBAR_MD, encoding="utf-8")

    dn = src / "dotnet-interface-guide"
    (dn / "docs").mkdir(parents=True)
    (dn / "mkdocs.yml").write_text(DOTNET_MKDOCS, encoding="utf-8")
    (dn / "docs" / "index.md").write_text("# .NET Interface Guide\n", encoding="utf-8")
    (dn / "docs" / "installation.md").write_text(
        "# Installation\n\nSee [the compiler](../compiler-user-guide/index.md).\n",
        encoding="utf-8",
    )

    return src


def build_overlay_tree(root: Path, source: Path) -> Path:
    """Materialise a content overlay matching the fixture source: the toolbar
    icon image and a front-page replacement whose sidecar names the current
    source page's hash."""
    overlay = root / "content"
    (overlay / "windows-ui-guide" / "img").mkdir(parents=True)
    (overlay / "windows-ui-guide" / "img" / "tbt-exec.png").write_bytes(PNG_BYTES)
    (overlay / "index.md").write_text(INDEX_OVERLAY_MD, encoding="utf-8")
    (overlay / "index.md.source-sha256").write_text(
        hashlib.sha256((source / "docs" / "index.md").read_bytes()).hexdigest() + "\n",
        encoding="utf-8",
    )
    return overlay


def tree_digest(root: Path) -> dict:
    """Map of relative path to content sha256 for every file under root."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --- git repositories for the cutover script -----------------------------

# The four Jenkins files old release branches copy from main.
JENKINS_FILES = {
    "tools/jenkins/Jenkinsfile": "pipeline { agent any }\n",
    "tools/jenkins/service.yml": "service: docs\n",
    "tools/jenkins/.rsync-exclude": "22.0\n",
    "tools/jenkins/get_svn_docbin": "#!/bin/sh\nsvn export \"$1\"\n",
}

# The workflow names old branches dispatch; main must keep files so named.
STUB_WORKFLOWS = ("mkdocs-publish.yml", "mkdocs-chm.yml", "mkdocs-pdf.yml", "full-release.yml")

# Upstream files around the content, as on Dyalog/documentation main. The
# .gitignore is upstream's in the parts that matter: it ignores the files the
# Zensical project consists of.
UPSTREAM_FILES = {
    **JENKINS_FILES,
    ".gitattributes": "* text=auto eol=lf\n",
    ".gitignore": "*.toml\n*.lock\n.vscode/\n",
    ".github/workflows/mkdocs-publish.yml": "name: Publish MkDocs Documentation\n",
    ".github/workflows/mkdocs-chm.yml": "name: Build CHM\n",
    ".github/workflows/full-release.yml": "name: Full release\n",
    ".github/dependabot.yml": "version: 2\n",
    "LICENCE": "Licence text\n",
    "README.md": "# Upstream readme\n",
    "CONTRIBUTE.md": "# Upstream contributing guide\n",
    "chm/mkdocs2chm.py": "print('chm')\n",
    "pdf/mkdocs2pdf.py": "print('pdf')\n",
    "language-bar/README.md": "# Language bar\n",
    "language-bar/toc.json": "{}\n",
    "tools/docker-compose.yml": "services: {}\n",
    "tools/nginx/nginx.conf": "server {}\n",
    "tools/utils/doc_utils.py": "pass\n",
    "tools/.env.template": "DOCS_DIR=.\n",
    "tools/mkdocs/Dockerfile": "FROM python\n",
    "tools/one_shot_transform.py": "pass\n",
    "tools/requirements-docs.txt": "mkdocs\n",
    "tools/README.md": "# Docker tools\n",
    "tools/buildgui/regen_properties.py": "# upstream version\n",
}

# Any commit id serves as a gitlink target: nothing checks the submodule out.
ASSETS_GITLINK = "c1825129a637f886413cef8bb3e60383f02dff74"
THEME_GITLINK = "b5c2eef97ed26e3a376c0745ba0b63cfd129eea0"
THEME_URL = "https://github.com/xpqz/documentation-theme-zensical"
# The fetch URL of the fixture clone's origin; repo_url derives from it.
UPSTREAM_URL = "https://github.com/Dyalog/documentation.git"

# This repository's tracked files outside the content, in miniature.
TOOLING_FILES = {
    ".gitignore": "/zensical/site/\n/site/\n.venv/\n",
    ".python-version": "3.11\n",
    ".vscode/extensions.json": "{}\n",
    "pyproject.toml": "[project]\nname = \"tooling\"\n",
    "uv.lock": "version = 1\n",
    "README.md": "# Zensical readme\n",
    "CONTRIBUTE.md": "# Zensical contributing guide\n",
    "known-issues.txt": "# baseline\n",
    "unlisted-pages.txt": "# unlisted\n",
    "tools/check.py": "print('check')\n",
    "tools/chm/build_chm.py": "#!/usr/bin/env python3\n",  # executable
    "tools/buildgui/regen_properties.py": "# ported version\n",
    # Upstream-owned: carried by the tooling, identical to upstream's.
    **JENKINS_FILES,
    "tools/language-bar/README.md": "# Language bar\n",
    "tools/language-bar/toc.json": "{}\n",
    "tools/cutover/convert.py": "# conversion, removed at cutover\n",
    "design/notes.md": "# planning notes\n",
    "zensical/zensical.toml": "# the PoC project\n",
    "zensical/docs/index.md": "# PoC page\n",
    ".github/workflows/check.yml": "name: Check\n",
    ".github/workflows/publish.yml": "name: Publish\n",
    **{f".github/workflows/{name}": f"name: stub {name}\n" for name in STUB_WORKFLOWS},
}

GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


def git(repo: Path, *args: str) -> str:
    """Run git in repo and return its stdout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _write_files(root: Path, files: dict) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _init_repo(repo: Path) -> None:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", GIT_IDENTITY["GIT_AUTHOR_NAME"])
    git(repo, "config", "user.email", GIT_IDENTITY["GIT_AUTHOR_EMAIL"])
    git(repo, "config", "commit.gpgsign", "false")


def _add_gitlink(repo: Path, path: str, sha: str, url: str) -> None:
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},{path}")
    git(repo, "config", "-f", ".gitmodules", f"submodule.{path}.path", path)
    git(repo, "config", "-f", ".gitmodules", f"submodule.{path}.url", url)
    git(repo, "add", ".gitmodules")


def build_upstream_repo(root: Path) -> Path:
    """The miniature monorepo as a committed git repository with the upstream
    shape around its content, and a clone of it standing in for the fresh
    local clone the cutover runs on. Returns the clone; its origin is the
    original."""
    src = build_source_tree(root)
    _write_files(src, UPSTREAM_FILES)
    (src / "tools" / "jenkins" / "get_svn_docbin").chmod(0o755)
    # documentation-assets is a submodule upstream, not a directory.
    shutil.rmtree(src / "documentation-assets")
    _init_repo(src)
    git(src, "add", "-A")
    _add_gitlink(src, "documentation-assets", ASSETS_GITLINK,
                 "https://github.com/Dyalog/documentation-assets")
    git(src, "commit", "-q", "-m", "Upstream history before the cutover")
    page = src / "compiler-user-guide" / "docs" / "basic-usage.md"
    page.write_text("# Basic Usage\n\nA later edit.\n", encoding="utf-8")
    git(src, "commit", "-q", "-am", "Edit a page before the freeze")

    clone = root / "clone"
    subprocess.run(["git", "clone", "-q", str(src), str(clone)], check=True)
    git(clone, "config", "user.name", GIT_IDENTITY["GIT_AUTHOR_NAME"])
    git(clone, "config", "user.email", GIT_IDENTITY["GIT_AUTHOR_EMAIL"])
    git(clone, "config", "commit.gpgsign", "false")
    # The fetch URL a GitHub clone would have; nothing is fetched from it.
    git(clone, "remote", "set-url", "origin", UPSTREAM_URL)
    return clone


def build_tooling_repo(root: Path, overlay: Path, cutover_dir: Path) -> Path:
    """A miniature of this repository, committed: the files the cutover
    copies, the paths it must not copy, the house-style gitlink, and
    tools/cutover/ holding overlay (as content/) plus the zensical.toml
    template, theme override and root README taken from cutover_dir."""
    tooling = root / "tooling"
    _write_files(tooling, TOOLING_FILES)
    cut = tooling / "tools" / "cutover"
    shutil.copytree(overlay, cut / "content")
    shutil.copy2(cutover_dir / "zensical.toml.template", cut / "zensical.toml.template")
    shutil.copy2(cutover_dir / "README.root.md", cut / "README.root.md")
    (cut / "overrides").mkdir()
    shutil.copy2(cutover_dir / "overrides" / "main.html", cut / "overrides" / "main.html")
    _init_repo(tooling)
    git(tooling, "add", "-A")
    git(tooling, "update-index", "--chmod=+x",
        "tools/jenkins/get_svn_docbin", "tools/chm/build_chm.py")
    _add_gitlink(tooling, "zensical/docs/documentation-theme-zensical", THEME_GITLINK, THEME_URL)
    git(tooling, "commit", "-q", "-m", "Tooling")
    # An untracked file is not tooling: the cutover copies tracked files only.
    (tooling / "tools" / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    return tooling
