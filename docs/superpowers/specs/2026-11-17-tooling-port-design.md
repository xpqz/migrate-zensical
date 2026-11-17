# Porting the documentation tooling to Zensical — design

Date: 2026-11-17. Status: draft for review.

## Intent

The Dyalog documentation (github.com/Dyalog/documentation) is moving from an
MkDocs monorepo to a single flattened Zensical project. Content conversion is
done (`tools/convert.py`, `tools/transforms.py`; `zensical/` in this repo).
This design covers the *tooling* around the content: authoring, checking,
CHM output, CI and the Jenkins deployment tie-in.

The why: the monorepo's tooling grew to compensate for MkDocs (slow builds,
monorepo-plugin link warnings, Docker to avoid local Python setups). Zensical
builds the whole site in seconds and checks internal links itself, so most of
that tooling should go. What remains must run natively — no Docker — on
macOS, Windows and Linux.

This repository (xpqz/migrate-zensical) is a proof of concept that others
will review. It eventually replaces `main` of Dyalog/documentation as v22.

## Goals

1. One native setup on macOS, Windows and Linux: install uv, then one
   command to preview and one to check.
2. Retire every upstream tool whose job Zensical (CLI or Zensical Studio)
   now does.
3. Port the tools Zensical does not replace, simplified for one flat tree
   and one nav.
4. Produce the Windows CHM from this repo (phase 1 requirement).
5. CI: a pull-request gate, a publish workflow, a CHM release workflow,
   compatible with the existing Jenkins deployment.
6. A scripted, repeatable, history-preserving cutover onto
   Dyalog/documentation `main`, run only locally until approved.

## Supported releases

After cutover three releases are supported: v22 (`main`, Zensical) and the
v21.0 and v20.0 branches (MkDocs). Older releases age out. Fixes that apply
to several releases are cherry-picked by hand; there is no supported tool
for converting old-markup changes to the new form, so `convert.py` and
`transforms.py` are deleted after cutover. Open upstream topic branches are
merged before the freeze or re-applied by hand afterwards.

## Non-goals

- Porting v20.0 or v21.0. They stay on MkDocs on their release branches.
- PDF output (`pdf/` is dropped).
- Native Zensical versioning (not shipped; we keep the `mike` fork).
- Changing the Jenkins server configuration or SVN docbin process.
- Restyling with Dyalog/documentation-theme (deferred; see README).

## Anti-goals (failure even if it "works")

- Any write to Dyalog/documentation (push, PR, issue, release, workflow
  dispatch, branch) without the owner's explicit approval of that action.
- Breaking deployability of v20.0/v21.0: their publish and CHM workflows
  must keep working after cutover.
- Docker, or platform-specific setup instructions beyond "install uv".
- Re-creating a home-grown check that Zensical already does.

## Constraints

- **Old branches copy Jenkins files from `main`.** Upstream
  `mkdocs-publish.yml` (on every release branch) runs
  `git checkout origin/main -- tools/jenkins/Jenkinsfile tools/jenkins/service.yml tools/jenkins/.rsync-exclude tools/jenkins/get_svn_docbin`
  and commits them to `gh-pages`. After cutover `main` must keep those four
  files at those paths, and the Jenkinsfile must keep deploying MkDocs-built
  versions.
- **Old branches dispatch workflows registered on `main`.** GitHub only
  offers manual dispatch for workflow files present on the default branch;
  the selected branch's copy then runs. v20.0/v21.0 releases are run from
  `mkdocs-publish.yml`, `mkdocs-chm.yml` and `full-release.yml` (v20.0
  also `mkdocs-pdf.yml`) per `tools/jenkins/RELEASE.md`. `main` must keep
  files with those names.
- **Shared `gh-pages`.** v20/v21 (mike 2.2, MkDocs) and v22 (squidfunk/mike
  fork, Zensical) write sibling version directories and one root
  `versions.json`. Both must be able to deploy without clobbering the
  other's directories.
- **Jenkins reads releases by tag prefix** (`v20.0.*`, `v21.0.*`;
  `getVersionedReleaseAssets`) for the CHM and `buildinfo.json`. v22
  releases must follow the same naming (`v22.0.*`).
- **Zensical facts (0.1.1, verified 2026-11-17):** commands are `build`,
  `serve`, `new`; `build --strict` fails on warnings; built-in validation
  covers internal links and anchors only — not nav entries pointing at
  missing pages, orphan pages, missing images, or external links. No
  PDF/CHM output. Versioning needs the mike fork. Python ≥ 3.11 wheels for
  Windows, Linux, macOS; uv's symlink link-mode is unsupported. Zensical
  Studio (VS Code/Cursor/Zed extension `zensical.zensical-studio`) gives
  free live preview and link/anchor diagnostics; it has no CI mode.
- The caption extension (`dyalog_caption`) must be importable by the
  Zensical build.
- Content after cutover is the transformed form (plain Markdown titles with
  `<span class="title-name">`, real example headings, standard admonition
  types). Ported tools must parse that form, not the MkDocs house markup.

## Approaches considered (cutover)

1. **Commit on top of upstream `main` — chosen.** At content freeze cut
   `v21.0` from upstream `main`; then land a pure-rename commit
   (`<guide>/docs/x.md` → `docs/<alias>/x.md`, 100% similarity so Git keeps
   per-page history) followed by a commit that applies the transforms and
   swaps the tooling. This repo is the workshop until then; the cutover is a
   script that replays both commits on a fresh local clone.
2. Force-push this repo's history over `main` — simpler, but page history on
   `main` restarts at cutover. Rejected.
3. Merge the unrelated histories — keeps both, but a confusing merge and
   unreliable blame across the move. Rejected.

## Design

### House style as a submodule (owner's requirement, 2026-11-17)

The Zensical house style must be reusable by other Dyalog documentation
projects and products, so it lives in its own repository and is consumed
as a git submodule — alongside, not replacing, the MkDocs style
(Dyalog/documentation-theme), which v20/v21 and other MkDocs projects keep
using. Two style repos, one per generator.

- New repo **xpqz/documentation-theme-zensical** (PoC home; transferred to
  the Dyalog org at cutover, with the submodule URL updated then),
  extracted from `zensical/docs/documentation-assetsz/` with its history
  (`git subtree split` or `git filter-repo --subdirectory-filter`).
  Contents: `css/dyalog.css`, `fonts/`, `images/`, plus README (how to
  consume it from a Zensical project, the matching `zensical.toml` theme
  settings to copy) and LICENCE.
- Mounted here as submodule `docs/documentation-theme-zensical/`
  (`zensical/docs/…` in the PoC); every `documentation-assetsz` reference
  is renamed.
- Project-specific pieces stay in the project: `zensical.toml` theme
  settings (Zensical cannot import config), `overrides/` (a template dir
  under `docs/` would be published), the gitissue icons used only by the
  front page.
- The submodule's README/LICENCE must not be published as pages; the
  checks treat the submodule directory as assets.
- CI checks out submodules; publish strips the submodule's `.git` pointer
  before `mike` (mike's fast-import rejects `.git` paths).

### Repository layout (target, at cutover)

```
zensical.toml            # was zensical/zensical.toml
docs/                    # was zensical/docs/ (content + documentation-theme-zensical/ submodule)
overrides/               # theme overrides (outdated-version banner)
pyproject.toml, uv.lock  # one native environment
tools/
  check.py               # the one checking command (below)
  dyalog_caption.py      # Markdown extension
  maintain/              # ported content-maintenance scripts
  chm/                   # CHM build
  buildgui/              # GUI object-model tools (APL + Python)
  language-bar/          # unchanged
  jenkins/               # unchanged paths (constraint above)
  cutover/               # rename + transform replay script (removed after cutover)
.github/workflows/       # check.yml, publish.yml, chm.yml, full-release.yml
```

In the PoC the content stays under `zensical/`; the cutover script performs
the move to the root layout so that the workshop and the target differ only
by that prefix. `convert.py`/`transforms.py` move under `tools/cutover/`
and are deleted after cutover.

Repo-internal planning material (`docs/plans`, `docs/prs`, `docs/reviews`,
`docs/superpowers`) must not collide with the content `docs/` at the root;
it moves to `design/` before the first cutover rehearsal.

### Native environment

- Single root `pyproject.toml` (uv project, Python ≥ 3.11) with Zensical,
  pymdown-extensions, markdown-tables-extended, the caption extension (as
  a local package), the mike fork (dev/deploy group), and the tooling
  dependencies (PyYAML, BeautifulSoup, pytest). `uv.lock` committed.
- Author commands, identical on all platforms:
  - `uv run zensical serve` — live preview.
  - `uv run python tools/check.py` — everything CI checks.
- Windows: documented in the README with PowerShell equivalents where they
  differ; set `UV_LINK_MODE=copy` if needed (uv symlink mode unsupported).
- Editor: recommend Zensical Studio (free tier) in the README and in
  `.vscode/extensions.json`.

### What happens to each upstream tool

| Upstream | Decision | Replacement / note |
|---|---|---|
| `tools/docker-compose.yml`, `nginx/`, `utils/Dockerfile`, `.env.template` | Retire | `uv run zensical serve` |
| `utils/render.py` (live single-file renderer) | Retire | `zensical serve` is fast enough; Studio preview |
| `utils/dangling_links.py`, `findlinks`-based link checks, `bare_links.py` | Retire | `zensical build --strict` (internal links + anchors) |
| `utils/check_links.py` (spider deployed site) | Retire | covered by strict build; external links out of scope |
| `utils/check_yml_files.py` (nav → file exists) | Port into `check.py` | Zensical does not validate nav entries |
| `utils/find_ghost_pages.py`, `find_orphans.py` | Port into `check.py` | one "page not in nav" check; CHM-only disambiguation pages allow-listed |
| `utils/validate_images.py`, `remove-unused-images.sh` | Port into `check.py` (missing = error, unused = report) | `--prune-images` flag replaces the shell script |
| `utils/check_admonitions.py/.sh` | Port into `check.py` | rules updated for post-transform admonition types |
| `utils/check_search_exclusions.py` | Port into `check.py` | |
| `utils/order_sysfns.py`, `order_syscmds.py`, `order_ibeams.py` | Port to `tools/maintain/order_nav.py` | one script, three sections, edits `zensical.toml` nav |
| `utils/add_synonyms.py`, `ibeams.py`, `exclude_from_search.py` | Port to `tools/maintain/` | parse the transformed h1 |
| `utils/sysfns.py`, `sysfntables.py` | Port to `tools/maintain/` | |
| `utils/find_footnotes.py`, `find_admonitions.py`, `findlinks.py` | Retire | ad-hoc reports; `grep`/Studio search |
| `utils/doc_utils.py` (+ tests) | Replace | small `tools/site.py`: load nav from `zensical.toml`, iterate pages, parse links/images |
| `utils/checkchm.py` | Port with CHM | |
| `one_shot_transform.py` | Retire | run-once, already applied |
| `buildgui/` (APL + `objref_audit.py`, `regen_properties.py`) | Port | path changes to `docs/object-reference/` |
| `language-bar/` | Keep unchanged | independent of the site build |
| `chm/mkdocs2chm.py` | Adapt | see CHM below |
| `pdf/` | Drop | |
| `img-src/` | Keep unchanged | image sources |
| `tools/jenkins/*` | Keep at same paths | update docs; Jenkinsfile logic unchanged |
| `.github/ISSUE_TEMPLATE/*` | Keep | |
| `.github/dependabot.yml` (gitsubmodule) | Replace | `uv` ecosystem + GitHub Actions ecosystem |

### `tools/check.py`

One command, run locally and in CI, non-zero exit on any error:

1. `zensical build --strict --clean` (links and anchors; config warnings).
2. Nav entries resolve to existing pages.
3. Every content page is in the nav, except an explicit allow-list file.
4. Every image reference resolves; unused images reported (warning).
5. Admonition types and titles match the guidelines.
6. Search-exclusion / synonym-div conflicts.

`known-issues.txt` remains the baseline for step 1 and only shrinks.
Each check is a function over the `tools/site.py` page model, with pytest
tests on small fixture trees.

### CHM

Owner's bar (2026-11-17): the CHM should look and behave as today's to the
extent possible. It is a stop-gap for the initial cutover — it will be
replaced by a different format before v22 ships — so effort beyond "looks
like the v21 CHM" is not wanted.

`mkdocs2chm.py` is adapted, not rewritten: replace the `mkdocs.yml` +
`!include` reader with a reader for `zensical.toml`'s nav (already merged and
alias-prefixed), read `markdown_extensions` from `zensical.toml` (using
`dyalog_caption` instead of `caption`), read pages from the flat `docs/`
tree, and update `extract_h1` for the transformed title form. Its own
Markdown rendering pipeline stays, and it keeps its own extension settings
for code (Pygments highlighting on), overriding `zensical.toml`'s
`use_pygments = false`, since a CHM has no client-side highlighter.
`chm/assets/main.css` gets rules for the transformed markup so pages look
as before: the orange title banner from `h1:has(> span.title-name)` with
the syntax right-aligned, example headings, `(default)` table cells in
place of shading, the sliced toolbar PNGs, and the standard admonition
types (`tip`, `note`, `info`) in place of `hint`, `legacy` and the OS
types. `checkchm.py` must pass, and a side-by-side check against the v21
CHM on Windows is part of acceptance. It still needs `chmcmd` (Free Pascal),
which runs on Linux in CI; locally on any platform where `fpc` is installed
(document `brew install fpc`, `apt install fpc`, Windows installer).

### CI (GitHub Actions)

- **check.yml** — on pull requests and pushes to `main`: `uv sync`,
  `uv run python tools/check.py`. Linux only; plus a scheduled
  Windows/macOS matrix run of `zensical build` to catch platform
  regressions.
- **publish.yml** — existing workflow, kept: manual dispatch, version from
  `extra.version_majmin`, `mike deploy` via the fork, `$BUILD_DATE` etc.
  stamping. Added: copy the four Jenkins files onto `gh-pages` (as
  upstream does), deploy only the selected version directory and never
  delete others.
- **chm.yml** — build the CHM and attach it to a GitHub release tagged
  `v22.0.<n>` (Jenkins derives `buildinfo.json` itself via `gh release
  view`).
- **full-release.yml** — calls chm then publish, as upstream. Same file
  name as upstream, so it also serves old-branch dispatch.
- **Old-branch dispatch stubs** (builder's call, 2026-11-17):
  `mkdocs-publish.yml`, `mkdocs-chm.yml`, `mkdocs-pdf.yml` stay on `main`
  as stubs that only register the workflow for dispatch; run on `main`
  they fail with "run this from a vM.0 release branch". The cutover script
  asserts all three exist.
- `test-jenkins-simulation.yml` — kept (exercises the Jenkinsfile logic).

In the PoC these run against xpqz/migrate-zensical (staging on its GitHub
Pages). They are written so they need no change to run on
Dyalog/documentation after cutover (no hard-coded repo names; use
`github.repository`).

### Jenkins

No server-side change at cutover: Jenkins keeps watching
Dyalog/documentation `gh-pages` and reading its releases. v22 joins by
adding `22.0` to `PRODUCTION_VERSIONS` when it goes live — the same
mechanism v21 used. In the PoC we verify layout compatibility by building
a gh-pages tree containing a v21 MkDocs version and a v22 Zensical version
and running `test-jenkins-simulation.yml` against it.

### Cutover script (`tools/cutover/`)

Given a fresh local clone of Dyalog/documentation and a freeze commit:

1. Create local branch `v21.0` at the freeze commit, following
   `tools/jenkins/RELEASE.md` procedure E (including its pinning commit).
2. On a local branch off `main`: commit A — `git mv` every page and image
   to the root layout (`docs/<alias>/…`), no content edits; verify with
   `git diff -M100% --stat` that every change is a pure rename.
3. Commit B — apply `transforms.py`, write `zensical.toml`, add the new
   tooling, delete MkDocs-only files (sub-project `mkdocs.yml`s, `chm/`
   moved, `pdf/`, Docker files), keep `tools/jenkins/*`.
4. Run `tools/check.py` on the result and stop. Pushing is a manual step
   that requires explicit approval.

Rehearsed repeatedly in the PoC; the output branch can be pushed to
xpqz/migrate-zensical for review.

### Author documentation

- README: install uv, `uv sync`, `uv run zensical serve`,
  `uv run python tools/check.py`, Zensical Studio. Per-OS notes.
- CONTRIBUTE.md ported from upstream with the Docker sections removed and
  the title/example/admonition authoring conventions updated to the
  transformed form. Flag to the owners that
  Dyalog/documentation-guidelines describes the old markup.

## Settled during review (builder's calls)

- Upgrade Zensical from the pinned 0.0.62 to 0.1.1 as the first step.
- Retire the `documentation-assets` (MkDocs) submodule *in this repo*; the
  MkDocs style stays in Dyalog/documentation-theme for v20/v21 and other
  MkDocs projects.
- Workflows and `check.py` take the content root as a setting, so they run
  with the PoC's `zensical/` prefix and at the root after cutover.

## Left to the builder

- Internal structure of `tools/site.py` and the check functions.
- Exact allow-list format for orphan pages and image exceptions.
- Whether `order_nav.py` edits `zensical.toml` textually or via a TOML
  round-trip (comments in the template must survive).
- The Windows/macOS scheduled matrix frequency.
- Sequencing of the work into PRs on xpqz/migrate-zensical.
