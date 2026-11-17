# Documentation Tooling Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the MkDocs monorepo's Docker-based tooling with a native, uv-based Zensical toolchain (preview, one check command, CHM, CI, Jenkins-compatible publishing) plus a rehearsable, history-preserving cutover script.

**Architecture:** One root uv project. A small page model (`tools/docsite.py`) reads the nav from `zensical.toml`; `tools/check.py` runs the strict Zensical build and five ported checks over it; maintenance scripts, the CHM converter and the GUI audit tools consume the same model. Workflows call the same commands. Everything takes the content root as a setting so it runs with the PoC's `zensical/` prefix and at the repo root after cutover.

**Tech Stack:** Python ≥ 3.11, uv, Zensical 0.1.1, pymdown-extensions, PyYAML, BeautifulSoup, pytest, GitHub Actions, squidfunk/mike fork, Free Pascal `chmcmd`.

**Spec:** `docs/superpowers/specs/2026-11-17-tooling-port-design.md` (moves to `design/specs/` in Task 1).

## Global Constraints

- Never write to github.com/Dyalog/documentation (push, PR, issue, release, workflow dispatch, branch). Read-only clones only. Cutover output is pushed to xpqz/migrate-zensical at most.
- House style: Zensical style in submodule `docs/documentation-theme-zensical` (repo xpqz/documentation-theme-zensical during the PoC); MkDocs style stays in Dyalog/documentation-theme, untouched. CI checkouts use `submodules: recursive`.
- No Docker. Setup instructions are "install uv" plus per-OS notes only.
- Python ≥ 3.11; Zensical pinned to `0.1.1`; mike pinned to `git+https://github.com/squidfunk/mike.git@2d4ad799442f4592db8ad53b179bfb33db8c69ac`.
- Do not re-implement anything Zensical does: internal link and anchor validation comes only from `zensical build --strict`.
- `known-issues.txt` is the build-warning baseline and only shrinks.
- `tools/jenkins/Jenkinsfile`, `tools/jenkins/service.yml`, `tools/jenkins/.rsync-exclude`, `tools/jenkins/get_svn_docbin` keep exactly these paths.
- Workflow files named `mkdocs-publish.yml`, `mkdocs-chm.yml`, `mkdocs-pdf.yml`, `full-release.yml` must exist on `main`.
- v22 GitHub releases are tagged `v22.0.<n>`.
- No hard-coded repository names in workflows; use `${{ github.repository }}`.
- Commit messages and PR text never mention Claude, AI, or carry Co-Authored-By trailers.
- Content root: `tools/docsite.py:find_project()` resolves it (env `DOCS_PROJECT`, else `zensical/` if `zensical/zensical.toml` exists, else repo root). Every tool and workflow goes through it.

## Review Focus

1. **CHM topic paths must not change.** The IDE's F1 help (`help_urls.h`) addresses CHM topics by the v21 paths, which use source directory names (`dotnet-interface-guide/…`), not the site aliases (`net-interface-guide/…`). Expect the CHM's file layout to match v21 exactly — test in Task 9.
2. **Windows paths.** Tools run on Windows; page keys must be POSIX (`/`) regardless of OS, and files read/written as UTF-8 explicitly (APL glyphs). Test in Task 2 (keys) and Task 3 (UTF-8 round trip).
3. **Editing `zensical.toml` must keep its comments and layout.** `order_nav.py` rewrites only the lines of one nav section; everything else byte-identical. Test in Task 6.
4. **Publishing one version must never remove another.** A v22 deploy leaves `20.0/`, `21.0/` and other root files untouched; `versions.json` keeps their entries. Test in Task 11.
5. **Fenced code is not prose.** Admonition-like or image-like text inside ``` fences must be ignored by every check. Test in Task 4 and Task 5.

---

### Task 1: Root uv project, Zensical 0.1.1, house style as a submodule, housekeeping

**Files:**
- Create: `pyproject.toml`, `uv.lock`, `.python-version` (`3.11`), `.vscode/extensions.json` (`["zensical.zensical-studio"]`)
- Delete: `tools/pyproject.toml`, `tools/requirements-build.txt`, `tools/requirements-deploy.txt`, `tools/requirements-dev.txt`, `zensical/docs/documentation-assets` (MkDocs submodule)
- Extract: `zensical/docs/documentation-assetsz/` → new public repo `xpqz/documentation-theme-zensical` (history preserved), re-added as submodule `zensical/docs/documentation-theme-zensical`; `.gitmodules` updated
- Move: `docs/` → `design/` (plans, prs, reviews, superpowers, `asset-switch-issues.md`, `deploy.md`); fix links in `README.md`
- Modify: `tools/convert.py` (`PRESERVED_DIRS` drops `documentation-assets`), `tools/check_flatten.py` (parser, if 0.1.1 output differs), `.github/workflows/publish.yml` (uv; drop submodule steps), `README.md`

**Interfaces:**
- Produces: `uv sync` installs everything; `uv run zensical …` and `uv run python tools/…` work from the repo root; `dyalog_caption` importable. Dependency groups: default (zensical, pymdown-extensions==12.0.1 or what 0.1.1 requires, markdown-tables-extended==0.1.3, pyyaml, beautifulsoup4, tomli-w), `dev` (pytest), `deploy` (mike fork).

- [ ] **Step 1:** Write `pyproject.toml`: `[project] name = "dyalog-docs-tooling"`, `requires-python = ">=3.11"`; `dyalog_caption` packaged via `[tool.setuptools] py-modules = ["dyalog_caption"]` with `package-dir = {"" = "tools"}`; `[dependency-groups]` as above; `[tool.pytest.ini_options] testpaths = ["tools"]`, `pythonpath = ["tools"]`.
- [ ] **Step 2:** `uv lock && uv sync --all-groups`. Expected: success.
- [ ] **Step 3:** `uv run pytest -q`. Expected: all existing tests pass.
- [ ] **Step 4:** `uv run python tools/check_flatten.py`. If the summary/issue format changed in 0.1.1, update `parse_issues` and its tests in `tools/test_check_flatten.py` with a captured 0.1.1 sample. Expected: `OK: build clean against the baseline.`
- [ ] **Step 5:** MkDocs submodule: `git rm zensical/docs/documentation-assets`; drop it from `PRESERVED_DIRS`.
- [ ] **Step 5a:** House style: in a scratch clone, `git subtree split --prefix=zensical/docs/documentation-assetsz -b theme`; add README (consuming it from a Zensical project, the `zensical.toml` theme block to copy, the Windows/macOS/Linux font notes) and LICENCE (copy the upstream theme's); create `xpqz/documentation-theme-zensical` (public) and push `theme` as `main`. Here: `git rm -r zensical/docs/documentation-assetsz`, `git submodule add https://github.com/xpqz/documentation-theme-zensical zensical/docs/documentation-theme-zensical`, rename every `documentation-assetsz` reference (`grep -rn documentation-assetsz` over `tools/`, `zensical/`, `.github/`; incl. `PRESERVED_DIRS`, `zensical.toml.template`, `tools/content/index.md`). Keep the submodule's README/LICENCE out of the built site (Zensical exclude setting) and verify `site/documentation-theme-zensical/README/` is absent after a build.
- [ ] **Step 5b:** `publish.yml`: keep `submodules: recursive` and the step that removes the submodule's `.git` pointer (path updated); switch its install step to `astral-sh/setup-uv` + `uv sync --all-groups`. Re-run Steps 3–4; `uv run python tools/convert.py --regenerate` leaves the submodule untouched (`git status` clean for it).
- [ ] **Step 6:** `git mv docs design`; update README links.
- [ ] **Step 7:** README "Local builds" section: install uv (macOS `brew install uv`; Linux `curl -LsSf https://astral.sh/uv/install.sh | sh`; Windows `winget install astral-sh.uv`), then `uv sync`, `cd zensical && uv run zensical serve`, `uv run python tools/check.py`; Windows note `set UV_LINK_MODE=copy` (PowerShell `$env:UV_LINK_MODE="copy"`) if uv is configured for symlinks; recommend Zensical Studio.
- [ ] **Step 8:** Commit: `Move to a root uv project on Zensical 0.1.1; make the house style a submodule`.

### Task 2: Page model (`tools/docsite.py`)

**Files:**
- Create: `tools/docsite.py`, `tools/test_docsite.py` (not `site.py`: it would shadow the stdlib `site` module)

**Interfaces:**
- Produces:
  - `find_project(repo_root: Path | None = None) -> Path` — directory holding `zensical.toml` (resolution rule in Global Constraints).
  - `@dataclass(frozen=True) class Site: project: Path; docs: Path; config: dict` and `Site.load(project: Path) -> Site`.
  - `Site.nav_pages() -> list[str]` — docs-relative POSIX page paths in nav order, duplicates kept once (first position).
  - `Site.nav_section(title_path: tuple[str, ...]) -> list` — the raw nav list under that title path (e.g. `("Core Reference", "Dyalog APL Language")`), raising `KeyError` if absent.
  - `Site.pages() -> list[str]` — all `*.md` under `docs/`, docs-relative POSIX, sorted, excluding `documentation-theme-zensical/`.
  - `Site.read(page: str) -> str` (UTF-8).
  - `prose_lines(text: str) -> Iterator[tuple[int, str]]` — 1-based line numbers of lines outside fenced code (``` or ~~~, 3+, optionally indented; the fence lines themselves excluded).

- [ ] **Step 1:** Tests in `tools/test_docsite.py` using a `tmp_path` project (a `zensical.toml` with a two-level nav incl. a section with an index page and a duplicate entry; three pages, one unlisted; a `documentation-theme-zensical/x.md`):
  - `test_nav_pages_in_order_without_duplicates` → `["index.md", "a/index.md", "a/b.md"]`
  - `test_pages_excludes_asset_dir` → unlisted page present, `documentation-theme-zensical/x.md` absent
  - `test_page_keys_are_posix` → no `\\` in any key
  - `test_nav_section_returns_raw_list` and `test_nav_section_missing_raises`
  - `test_prose_lines_skips_fences` → for `"a\n```apl\n!!! x\n```\nb"` yields `[(1,"a"),(5,"b")]`
  - `test_find_project_prefers_env_then_zensical_dir_then_root` (monkeypatch env)
- [ ] **Step 2:** `uv run pytest tools/test_docsite.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement with `tomllib`; nav entries are `str`, `{title: str}` or `{title: list}`.
- [ ] **Step 4:** Re-run → PASS. Smoke-test on the real project: `uv run python -c "import docsite as d; s=d.Site.load(d.find_project()); print(len(s.nav_pages()), len(s.pages()))"` prints two counts of roughly 3,000.
- [ ] **Step 5:** Commit: `Add the page model the checks and maintenance tools share`.

### Task 3: `tools/check.py` — build gate, nav targets, unlisted pages

**Files:**
- Create: `tools/check.py`, `tools/test_check.py`, `unlisted-pages.txt` (repo root, next to `known-issues.txt`)
- Modify: `tools/check_flatten.py` (expose `run_build(project: Path) -> tuple[int, str]` and `compare(found: set[str], baseline: set[str]) -> tuple[set, set]` if not already separable)

**Interfaces:**
- Consumes: `docsite.Site`, `docsite.find_project`.
- Produces:
  - `@dataclass(frozen=True) class Problem: path: str; line: int | None; message: str; severity: Literal["error", "warning"]`
  - `check_nav_targets(site: Site) -> list[Problem]` — error per nav entry whose file does not exist.
  - `check_unlisted(site: Site, allow: set[str]) -> list[Problem]` — error per page not in nav and not in `allow`; warning per `allow` entry that is now listed or no longer exists.
  - `check_build(site: Site, baseline_path: Path) -> list[Problem]` — wraps `check_flatten`; new issues are errors, resolved baseline entries warnings.
  - `CHECKS: list[Callable[[Site], list[Problem]]]` registry; `main(argv: list[str] | None = None) -> int` with flags `--skip-build`, `--prune-images` (Task 5), `--project PATH`. Prints `path:line: severity: message`, returns 1 if any error.

- [ ] **Step 1:** Tests: `test_nav_target_missing_is_error`, `test_unlisted_page_is_error`, `test_allow_listed_page_is_ok`, `test_stale_allow_entry_is_warning`, `test_main_exit_code_reflects_errors` (with `--skip-build`), `test_reads_utf8_titles` (page containing `⎕NA` round-trips through `Site.read` on a file written as UTF-8 bytes).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. `unlisted-pages.txt`: one docs-relative path per line, `#` comments.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** `uv run python tools/check.py --skip-build` against the real project; put every reported unlisted page into `unlisted-pages.txt` with a comment per group (e.g. `# CHM-only disambiguation pages`), and list the groups in the commit message for review. Re-run → exit 0.
- [ ] **Step 6:** `uv run python tools/check.py` (with build) → exit 0.
- [ ] **Step 7:** Commit: `Add tools/check.py: strict build, nav targets and unlisted pages`.

### Task 4: Content-rule checks — admonitions and search exclusions

**Files:**
- Modify: `tools/check.py`, `tools/test_check.py`

**Interfaces:**
- Produces: `check_admonitions(site) -> list[Problem]`, `check_search_exclusions(site) -> list[Problem]`, both registered in `CHECKS`.

Admonition rules for the transformed corpus (type case-insensitive, quoted title exact, trailing whitespace ignored, `!!!` and `???`/`???+` alike):

| type | allowed titles |
|---|---|
| info | `Information`, `Dyalog on Microsoft Windows`, `Dyalog on Linux`, `Dyalog on Unix`, `Dyalog on AIX`, `Dyalog on macOS` |
| warning | `Warning` |
| tip | `Hints and Recommendations` |
| note | `Legacy` |

Search exclusion rule: error if a page has front matter `search: {exclude: true}` **and** a hidden keyword div (`<div style="display: none;">`, any quote/semicolon/space variant).

- [ ] **Step 1:** Tests: `test_valid_admonitions_pass` (one per row), `test_unknown_type_is_error`, `test_wrong_title_is_error`, `test_missing_title_is_error`, `test_admonition_in_fence_is_ignored`, `test_trailing_whitespace_ok`, `test_search_exclusion_with_keywords_is_error`, `test_search_exclusion_alone_ok`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement using `docsite.prose_lines`; parse front matter with PyYAML. **Step 4:** Run → PASS.
- [ ] **Step 5:** `uv run python tools/check.py --skip-build` → exit 0 on the real corpus (fix nothing in content; if the corpus violates a rule, stop and report — the transforms or the table are wrong).
- [ ] **Step 6:** Commit: `Check admonitions and search exclusions`.

### Task 5: Image check with pruning

**Files:**
- Modify: `tools/check.py`, `tools/test_check.py`; Create: `unused-images.txt` only if Step 5 needs an allow-list.

**Interfaces:**
- Produces: `check_images(site, prune: bool = False) -> list[Problem]` — error per Markdown `![…](…)` or HTML `<img src>` in prose whose relative target does not exist; warning per image file (`png jpg jpeg gif svg webp`) under `docs/` (excluding `documentation-theme-zensical/`) not referenced by any page, by `zensical.toml`, or by the CSS under `documentation-theme-zensical/`; with `prune=True`, deletes those unreferenced files and reports them as warnings `removed`.

- [ ] **Step 1:** Tests: `test_missing_image_is_error`, `test_html_img_resolved`, `test_image_in_fence_ignored`, `test_external_image_ignored` (`http…`), `test_unused_image_is_warning`, `test_prune_removes_only_unused`, `test_image_referenced_from_css_is_used`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement (resolve relative to the page's directory; strip `#…` / `?…`; URL-decode). **Step 4:** PASS.
- [ ] **Step 5:** Run on the real corpus: missing images must be 0 (else report). Record the unused count in the commit message; do not prune in this task.
- [ ] **Step 6:** Commit: `Check image references; report and optionally prune unused images`.

### Task 6: Maintenance scripts

**Files:**
- Create: `tools/maintain/order_nav.py`, `tools/maintain/search_keywords.py`, `tools/maintain/sysfntables.py`, `tools/maintain/test_order_nav.py`, `tools/maintain/test_search_keywords.py`, `tools/maintain/README.md`
- Source to port (read-only): upstream `tools/utils/order_sysfns.py`, `order_syscmds.py`, `order_ibeams.py`, `add_synonyms.py`, `ibeams.py`, `sysfns.py`, `sysfntables.py`

**Interfaces:**
- Consumes: `docsite`.
- Produces:
  - `order_nav.py SECTION` where `SECTION ∈ {sysfns, syscmds, ibeams}`; `reorder(toml_text: str, section_title: str, key: Callable[[str], object]) -> str`. Section titles: `"System Functions (A-Z)"`, `"System Commands (A-Z)"`, `"I-Beam"`. The section's index entry stays first. Keys: sysfns/syscmds — the name in the nav title with `<code>`, `⎕`, `]` stripped, case-folded; ibeams — the integer before `:`.
  - `search_keywords.py PAGE...` — `add_keywords(md: str) -> str`: if the page has no `<!-- Hidden search keywords -->` block and its h1 is `# <span class="title-name">N</span> \`S\``, insert after the h1's syntax block the block `<!-- Hidden search keywords -->\n<div style="display: none;">\n  K\n</div>` where `K` is the primitive's glyph(s)/system name taken from `S` (port the extraction rules of `add_synonyms.py`/`sysfns.py`/`ibeams.py`). Idempotent.
  - `sysfntables.py` — port unchanged except input/output paths via `docsite`.

- [ ] **Step 1:** Tests: `test_reorder_sorts_sysfns_ignoring_quad_and_case`, `test_reorder_keeps_index_first`, `test_reorder_numeric_ibeams` (`8` before `13` before `43`), `test_reorder_leaves_rest_of_file_byte_identical` (assert text outside the section unchanged, including comments), `test_add_keywords_inserts_block`, `test_add_keywords_idempotent`, `test_add_keywords_skips_pages_without_syntax_title`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement `reorder` textually: locate the `{ "<title>" = [` line, sort the entry lines until its closing `] }`/`]`. **Step 4:** PASS.
- [ ] **Step 5:** Run `order_nav.py sysfns|syscmds|ibeams` on the real `zensical.toml`; `git diff --stat` should be empty or only reorderings (report any).
- [ ] **Step 6:** README in `tools/maintain/` with one line per script and its command. Commit: `Port the nav-ordering and search-keyword maintenance scripts`.

### Task 7: Carry over unchanged upstream assets; GUI object-model tools

**Files:**
- Copy from upstream (read-only source at `../documentation`): `language-bar/` → `tools/language-bar/`; `img-src/` → `img-src/`; `.github/ISSUE_TEMPLATE/` → same; `tools/jenkins/` → `tools/jenkins/` (all files); `tools/buildgui/` → `tools/buildgui/`
- Modify: `tools/buildgui/objref_audit.py`, `tools/buildgui/regen_properties.py`, `tools/buildgui/README.md` (paths → `docsite.find_project() / "docs" / "object-reference"`)
- Create: `tools/buildgui/test_paths.py`

**Interfaces:**
- Consumes: `docsite.find_project`.

- [ ] **Step 1:** Test `test_objref_root_resolves` — the audit's object-reference root exists and contains `objects/form.md`.
- [ ] **Step 2:** FAIL. **Step 3:** Copy files; change path constants only. **Step 4:** PASS.
- [ ] **Step 5:** Run `uv run python tools/buildgui/objref_audit.py` here and the upstream copy against upstream; diff the reports. Expected: identical findings (paths differ only by prefix). Report differences.
- [ ] **Step 6:** Commit: `Carry over the language bar, image sources, issue templates, Jenkins files and GUI audit tools`.

### Task 8: Author documentation

**Files:**
- Create: `CONTRIBUTE.md` (from upstream, Docker sections removed; preview/check commands from Task 1/3; authoring conventions for the transformed form: title `# <span class="title-name">Name</span> \`syntax\``, `## Example` headings, `(default)` table cells, the admonition table from Task 4, hidden search keywords via `tools/maintain/search_keywords.py`)
- Modify: `README.md` (status; link CONTRIBUTE.md; note that Dyalog/documentation-guidelines still describes the old markup)

- [ ] **Step 1:** Write; every command in the docs is run once on macOS and its output checked.
- [ ] **Step 2:** Commit: `Port CONTRIBUTE.md for the native Zensical workflow`.

### Task 9: CHM

**Files:**
- Copy: upstream `chm/` → `tools/chm/`; upstream `tools/utils/checkchm.py` → `tools/chm/checkchm.py`
- Modify: `tools/chm/mkdocs2chm.py` (rename → `tools/chm/build_chm.py`), `tools/chm/assets/main.css`
- Create: `tools/chm/test_build_chm.py`

**Interfaces:**
- Consumes: `docsite.Site`.
- Produces: `build_chm.py --project PATH --project-dir OUT [--assets-dir] [--welcome] [--config] [--git-info] [--build-date] [--no-compile]`.
  - `load_nav(site: Site, exclude: list[str]) -> dict` — replaces `parse_mkdocs_yml`/`resolve_includes`/`find_nav_files_and_dirs`; same shape `generate_toc` consumes today.
  - `CHM_DIR_FOR_ALIAS: dict[str, str] = {"net-interface-guide": "dotnet-interface-guide", "net-framework-interface-guide": "dotnet-framework-interface-guide"}` — topic paths in the CHM use the source directory names (Review Focus 1).
  - Extensions: read `markdown_extensions` from `site.config`, then force `pymdownx.highlight` to `use_pygments=True` and keep `dyalog_caption` in place of `caption`.
  - `extract_h1(data: str, for_title: bool = False) -> str` handles `# <span class="title-name">N</span> \`S\`` (title → `N`, full → `N S`), keeps legacy handling.
  - `--no-compile` skips `chmcmd`.

  - `config.json` `exclude` entries name nav sections by their current titles (e.g. `"Dyalog for Unix"` replaces the old `UNIX` spellings).
  - The converter adds class `os` to `info` admonitions whose title starts `Dyalog on ` so CSS can give them the old OS look.

- [ ] **Step 1:** Tests: `test_load_nav_matches_toc_shape` (fixture project), `test_net_guides_use_source_dir_names` (topic path for `net-interface-guide/x.md` is `dotnet-interface-guide/x.htm`, matching v21), `test_excluded_sections_absent`, `test_extract_h1_title_name_form`, `test_highlighting_forced_on`, `test_os_admonitions_get_os_class`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** CSS in `tools/chm/assets/main.css` so the transformed markup looks like v21: `h1:has(> span.title-name)` gets the old `.heading` banner with the code right-aligned; `.admonition.tip` the old `hint` look, `.admonition.note` the `legacy` look, `.admonition.info.os` the OS look; `(default)` cells and the toolbar PNGs need no rule. Keep the old rules (harmless).
- [ ] **Step 6:** Full run: `uv run python tools/chm/build_chm.py --project zensical --project-dir out/chm --no-compile`. Compare the list of generated `.htm` paths with the v21 CHM project built upstream (`python chm/mkdocs2chm.py … --no-compile` equivalent, or its `dyalog.hhc`); expected: identical except pages added/removed upstream since. Run `checkchm.py --help_urls ~/work/svn/documentation/help_urls.h --project-root out` (read-only; never commit or modify that SVN checkout) → pass.
- [ ] **Step 7:** With `fpc` installed (`brew install fpc`), build the `.chm`. Hand it to the owner for the Windows side-by-side comparison with the v21 CHM; record the outcome in `design/`.
- [ ] **Step 8:** Commit: `Build the CHM from zensical.toml and the flattened tree`.

### Task 10: CI check workflow

**Files:**
- Create: `.github/workflows/check.yml`, `.github/dependabot.yml` (ecosystems `uv` at `/` and `github-actions` at `/`, weekly)

- [ ] **Step 1:** `check.yml`: on `pull_request` and `push` to `main`: `ubuntu-latest`, `astral-sh/setup-uv`, `uv sync --all-groups`, `uv run pytest -q`, `uv run python tools/check.py`. Second job `platforms` on `schedule` (weekly) and `workflow_dispatch`: matrix `windows-latest`, `macos-latest`: `uv sync`, `uv run zensical build --clean` in the project dir.
- [ ] **Step 2:** Push a branch to xpqz/migrate-zensical, open a PR there; expected: `check` green. Dispatch `platforms`; expected: green on both.
- [ ] **Step 3:** Commit (before Step 2): `Run the checks on every pull request; build on Windows and macOS weekly`.

### Task 11: Publish workflow, Jenkins files on gh-pages, old-branch stubs

**Files:**
- Modify: `.github/workflows/publish.yml`
- Create: `.github/workflows/mkdocs-publish.yml`, `mkdocs-chm.yml`, `mkdocs-pdf.yml` (stubs), `.github/workflows/test-jenkins-simulation.yml` (from upstream, adjusted only where it assumes MkDocs paths)

**Interfaces:**
- Produces: `publish.yml` is `workflow_dispatch` + `workflow_call` with inputs `version_override`, `set_as_latest`, `site_url_override` (as now).

- [ ] **Step 1:** `publish.yml`: replace `python tools/check_flatten.py` with `uv run python tools/check.py`; after `mike deploy`, copy the four Jenkins files from the current commit onto `gh-pages` root (`Jenkinsfile`, `service.yml`, `.rsync-exclude`, `get_svn_docbin`) and push, as upstream's "Copy Jenkins files to gh-pages" step does. Project dir via `find_project`.
- [ ] **Step 2:** Stubs: each has `name:` identical to upstream's (`Publish MkDocs Documentation`, `Convert MkDocs to CHM`, upstream v20.0's name for PDF), `on: workflow_dispatch` with the same inputs as on the release branches, one job that runs only `if: github.ref == 'refs/heads/main'` and fails with `::error::Run this workflow from a vM.0 release branch`.
- [ ] **Step 3 (coexistence test):** On xpqz/migrate-zensical `gh-pages`: seed it with `20.0/` and `21.0/` directories plus `versions.json` copied read-only from upstream `gh-pages`; dispatch `publish.yml` for `22.0`. Expected: `20.0/` and `21.0/` byte-identical (`git diff --stat` on those paths empty), `versions.json` lists all three, Jenkins files present at root.
- [ ] **Step 4:** Dispatch `test-jenkins-simulation.yml` against that `gh-pages`; expected green with `PRODUCTION_VERSIONS="20.0 21.0 22.0"`.
- [ ] **Step 5:** Commit: `Publish alongside older versions and keep the Jenkins files on gh-pages`.

### Task 12: CHM and full-release workflows

**Files:**
- Create: `.github/workflows/chm.yml`, `.github/workflows/full-release.yml`

- [ ] **Step 1:** `chm.yml` (`workflow_dispatch` + `workflow_call`, input `generate_chm: boolean`): Ubuntu, `apt-get install -y fpc`, `uv sync`, run `build_chm.py`, create release `v22.0.<run_number>` (version from `extra.version_majmin`) with `dyalog.chm` attached, using `gh release create` and `GITHUB_TOKEN`.
- [ ] **Step 2:** `full-release.yml`: calls `chm.yml` then `publish.yml`, as upstream.
- [ ] **Step 3:** Dispatch on xpqz/migrate-zensical; expected: release `v22.0.N` with `dyalog.chm`; Jenkins' `getVersionedReleaseAssets` filter (prefix `v22.0.`) would select it — verify with the same `gh release list` query the Jenkinsfile uses.
- [ ] **Step 4:** Commit: `Build the CHM as a v22 release asset`.

### Task 13: Cutover script

**Files:**
- Move: `tools/convert.py`, `tools/transforms.py`, `tools/fixture_tree.py`, `tools/content/`, `tools/zensical.toml.template`, `tools/overrides/`, their tests → `tools/cutover/`
- Create: `tools/cutover/cutover.py`, `tools/cutover/test_cutover.py`

**Interfaces:**
- Consumes: `convert.mount_points`, `convert.transform_markdown`, `convert.write_zensical_toml`.
- Produces: `cutover.py --upstream PATH --freeze REF --tooling PATH [--branch NAME]` operating on a local clone only; functions `rename_plan(upstream: Path, mounts: dict[str, str]) -> list[tuple[str, str]]` (source → `docs/<alias>/…`, root `docs/` → `docs/`), `commit_renames(repo: Path, plan) -> str` (sha), `commit_transform(repo: Path, tooling: Path) -> str`, `assert_pure_renames(repo: Path, sha: str) -> None` (raises if `git diff -M100% --name-status sha^ sha` has any non-`R100` line).

Steps the script performs: create local branch `v21.0` at `REF`; create `--branch` (default `zensical-cutover`) from `REF`; commit A (renames only); `assert_pure_renames`; commit B (transforms, root `zensical.toml` and `overrides/`, `docs/documentation-theme-zensical` submodule, tooling copied from `--tooling` minus `tools/cutover/`, delete sub-project `mkdocs.yml`s, root `mkdocs.yml`, `chm/`, `pdf/`, `tools/docker-compose.yml`, `tools/nginx/`, `tools/utils/`, `tools/.env.template`, `documentation-assets` submodule; keep `tools/jenkins/*`); assert the four stub workflows exist; run `uv run python tools/check.py`; print the next manual step. **It never runs `git push`**, and before anything else it sets the push URL of every remote in `--upstream` to `no_push` (`git remote set-url --push <remote> no_push`), refusing to continue if that fails.

- [ ] **Step 1:** Tests on `fixture_tree.py` (git-initialised in `tmp_path`): `test_rename_plan_maps_net_guides_to_aliases`, `test_commit_a_is_pure_renames`, `test_assert_pure_renames_rejects_edits`, `test_jenkins_files_survive`, `test_stub_workflows_present`, `test_push_url_disabled`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Move existing convert/transform tests; all pass.
- [ ] **Step 5:** Rehearse on a fresh clone of upstream in the scratch area: `git log --follow` on a sample page (`docs/language-reference-guide/primitive-functions/reciprocal.md`) shows pre-cutover upstream history. `check.py` exit 0.
- [ ] **Step 6:** Push the rehearsal branch to xpqz/migrate-zensical as `cutover-rehearsal` (explicit remote URL; never upstream) for review.
- [ ] **Step 7:** Commit: `Add the rehearsable cutover script`.
