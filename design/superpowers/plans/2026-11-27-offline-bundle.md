# Offline Documentation Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CI-built, network-free offline copy of the Zensical site in one `.tar.gz`, installable and updatable from a Dyalog session and from a shell, and opened by F1 in RIDE.

**Architecture:** A generated offline config enables Zensical's `offline` plugin and removes every remote load; a Python tool builds and packages the site; a Playwright test proves no network request from `file://`. CI attaches the bundle to the versioned `v<X.Y>.<n>` release beside the CHM, so Jenkins carries it into SVN `docbin`, the installers and `docs.dyalog.com/<X.Y>/files/`. Two installers (APL user command, POSIX sh) share one archive layout. The tauride fork maps `file:` help URLs onto the bundle.

**Tech Stack:** Zensical 0.1.3, Python 3.14 with pytest, Playwright (Chromium), GitHub Actions, Dyalog APL 21.0 user commands with HttpCommand, POSIX sh with curl and tar, tauride (JavaScript, `node --test`).

**Spec:** `design/superpowers/specs/2026-11-27-offline-bundle-design.md`

## Global Constraints

- No output format other than Zensical's `offline` plugin output; no HTML rewriting after the build.
- Archive name `dyalog-docs-<X.Y>.tar.gz`, one top-level directory `dyalog-docs-<X.Y>/`; checksum file `dyalog-docs-<X.Y>.tar.gz.sha256` in `sha256sum` format.
- Released exactly as `dyalog.chm` is: assets of the versioned `v<X.Y>.<n>` release made by `chm.yml`.
- Phase 1 (Tasks 0 to 3) must work end to end before Phase 2 starts.
- Install path `<root>/<X.Y>/`, roots as in the spec's Install location table.
- Zero requests to non-`file:` URLs when pages load and search runs.
- Writing style per the repository: UK English, no em- or en-dashes, no emojis, comments state invariants.
- `X.Y` comes from `zensical.toml` `extra.version_majmin` via `tools/release_version.py`.

## Review Focus

- Search from `file://` returns results (the shim is what makes the worker load); covered in Task 2.
- Install path containing spaces (macOS `Application Support`, Windows user names), both on disk and as a percent-encoded URL; covered in Tasks 3, 4 and 5.
- A failed update (truncated download, bad checksum) leaves the previous bundle usable; covered in Tasks 4 and 5.
- A remote resource on any one of about 2,000 pages, not only the sampled ones; covered by the static scan in Task 2.
- Windows `file:///C:/...` URLs through the tauride opener; covered in Task 3's manual check on Windows.

---

## Phase 1: basics

### Task 0: Confirm the interpreter's help URLs

Done. Windows IDE: physical F1, 21.0.54393. RIDE on macOS: F1 through tauride with `DYALOGHELPDIR` unset and set to a `file:` prefix both opened the bundle's pages. The spec's interface section records the results.

### Task 1: Offline config generator and vendored shim

**Files:**
- Create: `tools/offline/__init__.py` (empty), `tools/offline/config.py`, `tools/offline/test_config.py`
- Create: `zensical/docs/assets/javascripts/iframe-worker-shim.js` (from npm `iframe-worker@1.0.0` `shim/index.js`, licence header kept)
- Modify: `.gitignore` (add `/zensical/offline.toml`, `/zensical/site-offline/`)

Tests import `from offline.config import ...`; `tools` is already on the pytest `pythonpath`.

**Interfaces:**
- Produces: `offline_config(base: dict, *, build_date: str, git_info: str, year: str) -> dict`; `write_offline_config(project: Path, *, build_date: str, git_info: str, year: str) -> Path` (returns `project / "offline.toml"`).

- [ ] **Step 1: Write the failing tests** in `tools/offline/test_config.py`:

```python
def test_adds_offline_plugin_once():
    cfg = offline_config({"project": {"plugins": ["search"]}}, build_date="d", git_info="g", year="y")
    assert cfg["project"]["plugins"] == ["search", "offline"]

def test_vendored_shim_replaces_unpkg():
    cfg = offline_config(BASE, build_date="d", git_info="g", year="y")
    assert cfg["project"]["extra"]["polyfills"] == [
        {"path": "assets/javascripts/iframe-worker-shim.js", "type": "text/javascript", "async": False}]

def test_remote_keys_removed():
    cfg = offline_config(BASE, build_date="d", git_info="g", year="y")
    for key in ("repo_url", "edit_uri", "site_url"):
        assert key not in cfg["project"]
    assert cfg["project"]["site_dir"] == "site-offline"

def test_copyright_placeholders_stamped():
    cfg = offline_config(BASE, build_date="2026-11-27", git_info="main:abc", year="2026")
    c = cfg["project"]["copyright"]
    assert "2026-11-27" in c and "main:abc" in c and "$" not in c

def test_base_not_mutated(): ...

def test_real_project_config_round_trips(tmp_path):
    # write_offline_config on a copy of zensical/zensical.toml produces TOML that tomllib reads,
    # with the nav identical to the source
```

`BASE` is a minimal dict with `repo_url`, `edit_uri`, `site_url`, `copyright` containing `$BUILD_DATE $GIT_INFO $CURRENT_YEAR`, and `plugins = ["search"]`.

- [ ] **Step 2: Run** `uv run pytest tools/offline/test_config.py -v`. Expected: FAIL, `ModuleNotFoundError`.
- [ ] **Step 3: Implement** with `tomllib` to read and `tomli_w` to write; deep-copy `base`. Vendor the shim file.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `Add offline build config generator and vendored iframe-worker shim`.

### Task 2: Build, package and prove network-free

**Files:**
- Create: `tools/offline/build_bundle.py`, `tools/offline/test_build_bundle.py`, `tools/offline/test_offline_site.py`
- Modify: `pyproject.toml` (new dependency group `offline = ["playwright==<current>"]`, added to `default-groups`)

**Interfaces:**
- Consumes: `write_offline_config` (Task 1).
- Produces: `package(site: Path, version: str, out: Path, meta: dict) -> tuple[Path, Path]` (archive, sha256 file); CLI `uv run python tools/offline/build_bundle.py --out DIR --build-date D --git-info G` which builds with `zensical build -f offline.toml`, removes `offline.toml`, writes `bundle.json` into the site, then packages. Exit non-zero on build failure. `OFFLINE_SITE` env var names a built site directory for `test_offline_site.py`.

- [ ] **Step 1: Write the failing packaging tests** on a fake site in `tmp_path`:
  - `test_archive_has_single_top_dir`: every member starts with `dyalog-docs-21.0/`.
  - `test_bundle_json_fields`: `bundle.json` in the archive has exactly `version`, `build_date`, `git`.
  - `test_sha256_file_format`: content is `<64 hex>  dyalog-docs-21.0.tar.gz\n` and matches `hashlib.sha256` of the archive.
- [ ] **Step 2: Write the failing site tests** in `test_offline_site.py`, skipped unless `OFFLINE_SITE` is set:
  - `test_no_network_requests`: Chromium via Playwright, `page.route("**/*")` aborts and records every non-`file:` URL; load `index.html`, `language-reference-guide/primitive-functions/union.html`, `language-reference-guide/symbols/rho.html`, `object-reference/objects/htmlrenderer.html`; assert the record is empty.
  - `test_search_works_offline`: on `index.html`, type `union` into the search box; assert at least one result link whose href ends `union.html`, and the record is still empty.
  - `test_no_remote_resources_anywhere`: parse every HTML file with BeautifulSoup; no `script[src]`, `link[href]` (any `rel` except `canonical`, which must be absent too), `img[src]`, `iframe[src]`, `source[src]`, `video[src]`, `audio[src]` has a scheme other than none or `file`, nor starts with `//` or `/`. Text inside code listings is not markup and is not checked.
  - `test_relative_links_resolve`: for every `a[href]` and resource attribute with no scheme, the target file exists (fragment stripped). `404.html` is absent from the bundle.
  - `test_no_same_version_absolute_links`: no `href` starts with `https://docs.dyalog.com/<X.Y>/`. Marked `xfail(strict=True)`; removed if Task 6 goes ahead and lands, deleted if it does not.
- [ ] **Step 3: Run** both test files. Expected: packaging FAIL (no module); site tests SKIP.
- [ ] **Step 4: Implement** `build_bundle.py`. Omit `404.html`. `tarfile` with `gz`, members sorted, `mtime` from the build date so reruns are byte-identical.
- [ ] **Step 5: Build and run**: `uv run playwright install chromium`, build into `out/offline`, extract, `OFFLINE_SITE=... uv run pytest tools/offline -v`. Expected: all PASS except the strict xfail. If `test_no_network_requests` fails, fix it in `config.py` (Task 1), never by post-processing HTML.
- [ ] **Step 6: Commit** `Build and package the offline bundle; test it loads nothing remote`.

### Task 3: tauride F1 opens the bundle

Repository: `~/work/tauride`. Read its `CLAUDE.md` first. Branch `offline-help`; PR with `gh pr create --repo xpqz/tauride --base main`.

**Files:**
- Modify: `src/hlp.js` (help base from `DYALOGHELPDIR`; `D.helpTarget`)
- Modify: `src/km.js` (`openURI` applies `D.helpTarget` before `encodeURI`)
- Test: `tests-node/help-offline.js`

**Interfaces:**
- Produces: `D.helpTarget(url: string) -> string`: for `file:` URLs, `…/<path>/` becomes `…/<path>.html`, `…/<path>/#f` becomes `…/<path>.html#f`, the bare prefix (`…/21.0` or `…/21.0/`) becomes `…/21.0/index.html`; every other URL is returned unchanged. `D.InitHelp` uses `DYALOGHELPDIR` from the shim's `process.env` as `helpUri` when it starts with `file://`, for 20.0 and later; otherwise `https://docs.dyalog.com/<v>/` as now. With `DYALOGHELPDIR` unset, `D.helpTarget` maps `https://docs.dyalog.com/<X.Y>/<path>/` onto the first installed bundle in the spec's lookup order (per-user, then machine-wide), via `fs.existsSync` on each candidate's `index.html`. On Windows the machine-wide candidate is `<install dir>\help\docs`, set by `D.help.setInterpreter(exe)` when `cn.js` spawns an interpreter. Browser-only Ride has no environment or file system and stays online.

- [ ] **Step 1: Failing unit tests** (`node --test`):
  - `file:///Users/a b/docs/21.0/language-reference-guide/symbols/rho/` → `.../symbols/rho.html`
  - `file:///C:/Users/x/AppData/Local/Dyalog/docs/21.0/object-reference/properties/height/` → `.../height.html`
  - `file:///r/21.0` and `file:///r/21.0/` → `file:///r/21.0/index.html`
  - `https://docs.dyalog.com/21.0/language-reference-guide/symbols/rho/` unchanged
  - with `DYALOGHELPDIR=file:///r/21.0`, `D.hlp.WELCOME` is `file:///r/21.0/` and `D.helpTarget(D.hlp.WELCOME)` is `file:///r/21.0/index.html`
  - with `DYALOGHELPDIR` unset and only the machine-wide bundle present (stubbed existence check), `https://docs.dyalog.com/21.0/language-reference-guide/symbols/rho/` maps to the machine-wide `rho.html`; with neither present it is unchanged
  - a root containing `Jürgen Smith` reaches the opener percent-encoded as UTF-8 (`J%C3%BCrgen%20Smith`)
  - `openURI` end to end, with `D.openExternal` stubbed: `file:///Users/a/Library/Application Support/Dyalog/docs/21.0/language-reference-guide/symbols/rho/` reaches the opener as `file:///Users/a/Library/Application%20Support/Dyalog/docs/21.0/language-reference-guide/symbols/rho.html`
- [ ] **Step 2: Run** `npm run test:unit`. Expected: new tests FAIL.
- [ ] **Step 3: Implement**. Help-menu items open through the same `D.helpTarget`.
- [ ] **Step 4: Run** `npm run test:unit`. Expected: PASS.
- [ ] **Step 5: Manual check, macOS**: with the bundle from Task 2 extracted to `<root>/21.0`, `DYALOGHELPDIR=file://<root>/21.0 RIDE_SPAWN=/usr/local/bin/dyalog npm start`; F1 on `⍴`, `⎕DT`, `:If`, `)clear`, `'f'⎕WC'Form'` opens the local `.html` page in the default browser, with Wi-Fi off. This also confirms that the 21.0 interpreter returns `file:` URLs for a `file://` `DYALOGHELPDIR`.
- [ ] **Step 6: Manual check, Windows**: the same on the Windows tauride session, `file:///C:/...` root.
- [ ] **Step 7: Commit, push** `git push origin offline-help`, open the PR.
## Phase 2: distribution as the CHM is distributed

### Task 4: Shell installer

**Files:**
- Create: `tools/offline/dyalog-docs-update.sh` (POSIX sh, `set -eu`), `tools/offline/test_update_sh.py`

**Interfaces:**
- Produces: `dyalog-docs-update.sh [--version X.Y] [--root DIR] [--system] [--base-url URL] [--from ARCHIVE]`. Defaults: `--root` the spec's per-user root for the platform, `--system` the machine-wide root; `--base-url https://docs.dyalog.com/<X.Y>/files`. `--version` is required unless `--from` is given (then read from the archive's top directory). Exit codes: 0 installed or already current, 1 usage, 2 download, 3 checksum mismatch, 4 extraction. Prints the `DYALOGHELPDIR=file://...` value on success.

- [ ] **Step 1: Failing tests**, each running the script with `subprocess` against an archive from Task 2's `package` in `tmp_path`, and `--base-url file://<tmp>` (curl reads `file:` URLs, so no network):
  - `test_fresh_install`: `<root>/21.0/index.html` exists; `bundle.json` has `sha256`.
  - `test_already_current_is_noop`: second run exits 0, prints `up to date`, directory mtime unchanged.
  - `test_checksum_mismatch_keeps_old`: tamper the archive; exit 3; previous install intact; no stray temp directories under root.
  - `test_root_with_spaces`: root `tmp/Application Support/Dyalog/docs` works.
  - `test_from_local_archive`: `--from` with the `.sha256` beside it, no `--base-url`.
- [ ] **Step 2: Run**. Expected: FAIL.
- [ ] **Step 3: Implement**: download `.sha256`, compare with installed `bundle.json` `sha256`, download archive, verify with `sha256sum -c` or `shasum -a 256 -c`, extract into `<root>/.<X.Y>.new`, write `sha256` into its `bundle.json`, rename old to `.<X.Y>.old`, new to `<X.Y>`, delete old. Trap removes temp directories on failure.
- [ ] **Step 4: Run**. Expected: PASS.
- [ ] **Step 5: Commit** `Add shell installer for the offline bundle`.

### Task 5: User command `]Docs`

Done. Style and helpers follow `]Get` (`get.dyalog`).

**Files:**
- Create: `tools/offline/Docs.dyalog` (group Experimental), `tools/offline/test_docs_ucmd.apls`, `tools/offline/test_docs_ucmd.py`

**Interfaces:**
- `]Docs [status|update] [-version=X.Y] [-root=DIR] [-system] [-from=ARCHIVE] [-baseurl=URL]`; `status` is the default. Version defaults to the running interpreter's `X.Y`. On Windows `-system` installs into `<install dir>\help\docs` (`2 ⎕NQ'.' 'GetEnvironment' 'DYALOG'`) and needs an elevated session. Download with HttpCommand from `-baseurl` (default `https://docs.dyalog.com/%V/files`); checksum with `shasum -a 256`, `sha256sum` or `certutil -hashfile … SHA256`; extraction with `tar -xzf` (Windows' built-in `tar.exe`). `-from` requires the `.sha256` beside the archive.
- API for scripts and tests: `Docs.Install opts` and `Docs.Status opts`, each returning message lines; `opts` fields as the modifiers.

- [x] `test_docs_ucmd.py` builds fixture archives with `package` and runs `test_docs_ucmd.apls` under `dyalogscript` (12 checks: fresh install, recorded sha256, LF-only `bundle.json`, printed `DYALOGHELPDIR`, up-to-date no-op, tampered archive refused with the previous install intact and no temporary directories left, root with spaces, missing `.sha256` refused, status).
- [x] Manual, macOS session: `]Docs`, `]Docs -??`, `]Docs update -baseurl=http://127.0.0.1:8765` (download, then up to date), and a 404 reported as `HTTP 404 fetching …`.
- [x] Windows session (21.0.54393, through the API rather than the UCMD framework): install to `%LOCALAPPDATA%\Dyalog\docs\21.0`, up to date, tampered archive refused with no leftovers, root with spaces, status, help; `bundle.json` gets CRLF. F1 in tauride (main) opened the per-user copy.

### Task 6: Same-version absolute links (documentation repository), pending decision

Only if the owner decides these links should be relative (spec, Open decisions). Repository: `~/work/dyalog-docs/documentation`, a branch and PR there. The links are in five `release-notes` pages and one `language-reference-guide` page; check whether each version pin is deliberate before rewriting it.

**Files:**
- Modify: `tools/utils/bare_links.py` (flag `https://docs.dyalog.com/<current X.Y>/` links; the current version from `mkdocs.yml` `extra.version_majmin`)
- Modify: the six Markdown files with such links, each rewritten to the source-relative `.md` form required by the repository's `CLAUDE.md`
- Test: `tools/utils/test_bare_links.py` (create if absent)

- [ ] **Step 1: Failing test**: `bare_links` reports a page containing `[x](https://docs.dyalog.com/21.0/language-reference-guide/system-functions/dt/)` and does not report the same link with `20.0`.
- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement** the check; run `python tools/utils/bare_links.py` and fix every reported link. Links to a page that does not exist in this version stay absolute and are listed in the PR body.
- [ ] **Step 4: Verify**: `python tools/utils/bare_links.py` exits 0; the `ghost` skill's link report shows no new broken links.
- [ ] **Step 5: Commit, PR**; after the migration re-run picks it up, remove the `xfail` in Task 2's test.

### Task 7: Release with the CHM

**Files:**
- Modify: `.github/workflows/chm.yml` (build the bundle beside the CHM; run the Task 2 site tests on it; attach the archive, its `.sha256` and `tools/offline/Docs.dyalog` to the same versioned release as `dyalog.chm`; the "no asset-less releases" guard covers the bundle too)
- Modify: `.github/workflows/check.yml` (on pull requests: build the bundle and run the `tools/offline` tests, including the site and shell installer tests)
- Modify: `CONTRIBUTE.md` (building and testing the bundle locally; both installers)

- [ ] **Step 1:** Add to `chm.yml`: `build_bundle.py --out out/offline --git-info "$GIT_INFO" --build-date "$BUILD_DATE"`, `uv run playwright install chromium`, the site tests on the extracted bundle, then `out/offline/*` and `tools/offline/Docs.dyalog` in the release's asset list.
- [ ] **Step 2: Verify** with `workflow_dispatch` on a branch: the draft `v21.0.<n>` release lists `dyalog.chm`, the PDFs, `dyalog-docs-21.0.tar.gz`, `dyalog-docs-21.0.tar.gz.sha256` and `Docs.dyalog`; `dyalog-docs-update.sh --from` installs the downloaded archive.
- [ ] **Step 3: Commit** `Release the offline bundle with the CHM`.

### Task 8: Jenkins, SVN and installer handoff

Outside this repository; written up for the owners, not built here.

- [x] **Step 1:** Write `design/offline-bundle-handoff.md`: `gitdocs2svn` (JenkinsBuild) copies the three new assets into `docbin`; each version's `docbin` `filelist.txt` lists them so "Update online files" publishes them to `https://docs.dyalog.com/<X.Y>/files/`; the interpreter installers unpack the bundle where they place `dyalog.chm` and set `DYALOGHELPDIR`; the interpreter's two changes from the spec. Note that the gh-pages `Jenkinsfile` is what Jenkins runs and must be synced first.
- [ ] **Step 2:** Once Jenkins has run, `dyalog-docs-update.sh --version 21.0` with the default base URL installs from `docs.dyalog.com`.

