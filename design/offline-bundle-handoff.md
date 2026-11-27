# Offline documentation bundle: changes outside this repository

The offline bundle replaces `dyalog.chm` behind F1. It is the documentation site built by Zensical's `offline` plugin, one `<path>.html` per topic, opening from disk with no network access, including search. Design: `design/superpowers/specs/2026-11-27-offline-bundle-design.md`.

This repository builds the bundle and releases it beside the CHM. RIDE (tested in the tauride fork) and the `]Docs` user command already work with it. This note lists what the owners of the interpreter, the installers and the Jenkins pipeline need to change.

## The bundle

- `dyalog-docs-<X.Y>.tar.gz`, about 28 MB, holding one directory `dyalog-docs-<X.Y>/` with `index.html`, the topic pages and `bundle.json` (`version`, `build_date`, `git`; installers add `sha256`).
- `dyalog-docs-<X.Y>.tar.gz.sha256`, in `sha256sum` format.
- `Docs.dyalog`, the `]Docs` user command (install, update, status).

Topic paths are those in `help_urls.h`. Of the 980 topics it uses (commented-out and `#if 0` entries excluded), 968 have a page, including the disambiguation pages such as `language-reference-guide/symbols/rho`, which no table of contents lists. The other 12 are the 12 already missing from the v21 CHM (`tools/chm/known-missing-help-urls.txt`).

## Interpreter (help.c)

Current behaviour, observed in 21.0 (Windows IDE, physical F1; RIDE on macOS):

- `DYALOGHELPDIR` (environment, else the registry's `DyalogHelpDir`, which the installer sets to `<install dir>\help`) selects the source.
- A value starting with `http://`, `https://` or `file://` is a URL prefix: a topic becomes `<prefix>/<path>/`, and the welcome topic `<prefix>//`. The Windows IDE writes `%TEMP%\dyalog_help_<guid>.html`, a `<meta http-equiv=Refresh>` page pointing there, opens it with the shell's `open` verb and never deletes it.
- Any other value is the directory holding `dyalog.chm`, shown in-process with the HtmlHelp API.
- With a `file://` prefix the topic URL ends in `/`, which a browser shows as a directory listing or an error, never `<path>.html`. F1 therefore cannot open the bundle today.

Requested changes:

1. For a `file://` prefix, build `<prefix>/<path>.html`, and `<prefix>/index.html` for the welcome topic.
2. When `DYALOGHELPDIR` is not a URL (the installed default), look for, in order:
   1. `%LOCALAPPDATA%\Dyalog\docs\<X.Y>\index.html` (per-user copy, written by `]Docs update`; macOS `~/Library/Application Support/Dyalog/docs/<X.Y>`, Linux `${XDG_DATA_HOME:-~/.local/share}/dyalog/docs/<X.Y>`)
   2. `<DyalogHelpDir>\docs\index.html` (machine-wide copy from the installer; macOS `/Library/Application Support/Dyalog/docs/<X.Y>`, Linux `/usr/local/share/dyalog/docs/<X.Y>`)
   3. `<DyalogHelpDir>\dyalog.chm`, as now
   4. `https://docs.dyalog.com/<X.Y>/`

   With this order nobody needs to set `DYALOGHELPDIR`, and an update from a session takes effect at once.
3. Percent-encode file URLs as UTF-8. The machine-wide path contains spaces (`Program Files`, `Dyalog APL-64 21.0 Unicode`), and user names may contain spaces or non-ASCII characters.
4. Open a local page directly with the shell's `open` verb; the `%TEMP%` redirect file is unnecessary for it. Delete redirect files that are still written for URL prefixes, or reuse one.
5. Route the Help menu's fixed topics (welcome, language elements, licences overview, release notes, GUI overview) through the same lookup. Decide whether "Documentation Centre" (`<install dir>\Help\index.htm`) stays or points at the bundle's `index.html`.
6. Over the RIDE protocol, answer `GetHelpInformation` with the same URL the IDE would open. RIDE maps `<prefix>/<path>/` and online URLs onto installed copies itself, so this is not a blocker for RIDE.

What users will notice: help opens in the default browser, not an in-process window; search is the site's own; whichever program Windows has registered for `.html` opens the page (on one test PC, VS Code's registration made Windows ask which application to use).

## Installers

- Unpack `dyalog-docs-<X.Y>.tar.gz` into the machine-wide location: Windows `<install dir>\help\docs` (beside `dyalog.chm`, no `<X.Y>` level, since the install directory is per version), macOS `/Library/Application Support/Dyalog/docs/<X.Y>`, Linux `/usr/local/share/dyalog/docs/<X.Y>`.
- Keep `dyalog.chm` during the transition; the lookup order prefers the bundle.
- Ship `Docs.dyalog` with the other user commands.
- Tools the user command relies on are present on every supported platform with no extra installs: `tar` (Windows' `tar.exe` is bsdtar) and `certutil -hashfile … SHA256`, `shasum -a 256` or `sha256sum`.

## Jenkins and SVN

`chm.yml` attaches the three files above to the versioned GitHub release `v<X.Y>.<n>`, beside `dyalog.chm` and the PDFs.

- `getVersionedReleaseAssets` already downloads every asset of that release; nothing to change.
- `gitdocs2svn` (JenkinsBuild repository) must copy the three new files into each version's `docbin`.
- Each version's `docbin` `filelist.txt` must list them, so "Update online files" publishes them to `https://docs.dyalog.com/<X.Y>/files/`, the default download location of `]Docs update`.
- Jenkins runs the `Jenkinsfile` on the documentation repository's `gh-pages` branch, not the copy in `tools/jenkins/`; sync it there before relying on any pipeline change.
