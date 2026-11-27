# Offline documentation bundle

## Purpose

F1 in the Dyalog IDEs opens help from `dyalog.chm` (Windows IDE) or from `https://docs.dyalog.com/<X.Y>/` (RIDE). CHM is obsolete. The offline bundle replaces it with a copy of the Zensical site that opens from disk and makes no network request.

## Requirements

- Built by Zensical's own `offline` plugin. No other output format, no HTML post-processing.
- Distributed as one compressed file per version: `dyalog-docs-<X.Y>.tar.gz`, beside `dyalog-docs-<X.Y>.tar.gz.sha256` (`sha256sum` format).
- Built and published by CI.
- Updatable from a Dyalog session (`]Docs.Update`) and from a shell (`dyalog-docs-update.sh`, usable in a Dockerfile). Both accept a local archive, for machines with no network.
- Opened from disk, no page makes a network request: no scripts, styles, fonts, images or API calls from another origin. Outbound links the reader clicks are allowed, except links to the same version of these docs, which must be relative.

## Bundle

The archive holds one top-level directory, `dyalog-docs-<X.Y>/`, containing the site (`index.html`, each page as `<path>.html`, `search.js`) and `bundle.json`:

```json
{"version": "21.0", "build_date": "2026-11-27", "git": "main:1a2b3c4", "sha256": "<of the archive>"}
```

`404.html` is left out: it uses root-absolute asset paths and nothing reaches it from disk.

The `sha256` field is filled in by the installer, not the build (an archive cannot contain its own hash). Installers compare it with the published `.sha256` to decide whether an update is needed.

The offline build differs from the published site only in config, generated from `zensical.toml` by `tools/offline/config.py`:

| Key | Offline value | Why |
|---|---|---|
| `plugins` | adds `"offline"` | file-based URLs and a search index that loads from `file://` |
| `extra.polyfills` | the vendored `iframe-worker` shim | the plugin otherwise loads it from `unpkg.com`; it skips that when a polyfill path contains `iframe-worker` |
| `repo_url`, `edit_uri` | removed | the theme fetches repository facts from `api.github.com` |
| `site_url` | removed | canonical links would point at the online site |
| `site_dir` | `site-offline` | must be inside the project |

## Install location

Two tiers.

| Platform | Machine-wide copy (interpreter installer) | Per-user copy (`]Docs.Update`, shell installer) |
|---|---|---|
| Windows | `<install dir>\help\docs\` | `%LOCALAPPDATA%\Dyalog\docs\<X.Y>\` |
| macOS | `/Library/Application Support/Dyalog/docs/<X.Y>/` | `~/Library/Application Support/Dyalog/docs/<X.Y>/` |
| Linux | `/usr/local/share/dyalog/docs/<X.Y>/` | `${XDG_DATA_HOME:-~/.local/share}/dyalog/docs/<X.Y>/` |

- On Windows the machine-wide copy sits beside `dyalog.chm` in `<install dir>\help` (for example `C:\Program Files\Dyalog\Dyalog APL-64 21.0 Unicode\help\docs`), the folder the installer already sets as the default `DyalogHelpDir`. The install directory is per version, so the copy has no `<X.Y>` level.
- The machine-wide copy is read-only to users and always present after an install; reinstalling or patching refreshes it.
- The per-user copy needs no administrator rights, so an update from a session always succeeds. It is not in a roaming profile or under Documents: roaming and OneDrive would sync about 2,800 files. Dyalog keeps nothing under `%LOCALAPPDATA%\Dyalog` today (RIDE uses `%LOCALAPPDATA%\com.dyalog.ride48`).
- Both installers take a root override (`--root`, `-root`) for Docker images and locked-down sites; `--system` and `-system` install the machine-wide copy (on Windows into the install directory of the given version, which needs elevation).
- An update extracts beside the old copy and swaps it in with renames, so a failed update leaves the old bundle intact.
- Paths may contain spaces and non-ASCII characters (`C:\Users\Jürgen Smith`); every `file:` URL built from them is percent-encoded UTF-8.

### Lookup order

When help is requested and `DYALOGHELPDIR` is not set:

1. the per-user copy's `index.html`, if present
2. the machine-wide copy's `index.html`, if present
3. `https://docs.dyalog.com/<X.Y>/`

A set `DYALOGHELPDIR` overrides all three. The interpreter knows its own install directory. RIDE knows it when it starts the interpreter itself (`RIDE_SPAWN` or the spawn dialog's executable); for a remote interpreter it uses the per-user copy or `DYALOGHELPDIR`. Until the interpreter implements this order, RIDE applies it itself and the installers print the `DYALOGHELPDIR` value to set.

## Publication

The bundle replaces `dyalog.chm` and travels the same route:

1. `chm.yml` (run by `full-release.yml`) builds the bundle beside the CHM and attaches the archive, its `.sha256` and `Docs.dyalog` (the user command) to the versioned release `v<X.Y>.<n>`.
2. Jenkins' `getVersionedReleaseAssets` downloads every asset of the newest `v<X.Y>.*` release; `gitdocs2svn` commits them into SVN `docbin`, from which the interpreter installers take the help.
3. Jenkins' "Update online files" stage publishes `docbin` files listed in `filelist.txt` to `https://docs.dyalog.com/<X.Y>/files/`.

The installers' default download location is therefore:

```
https://docs.dyalog.com/<X.Y>/files/dyalog-docs-<X.Y>.tar.gz
```

Steps 2 and 3 need changes outside this repository: `gitdocs2svn` (JenkinsBuild repository) must copy the three new assets, and `filelist.txt` in each version's `docbin` must list them. The interpreter installers unpack the bundle into the machine-wide location, on Windows `<install dir>\help\docs` beside `dyalog.chm`.

Both installers also install from a local archive alone (`--from`, `-from`), with no network.

## Open decisions

- Whether the links to `https://docs.dyalog.com/21.0/...` in five release-notes pages and one Language Reference page become relative upstream. Offline, they go to the network.

## Interface for the interpreter and IDEs

`DYALOGHELPDIR` already selects the help source in Dyalog 21.0. Observed with a physical F1 press in the Windows IDE (21.0.54393) and consistent with the format strings in the macOS binary:

- a value starting with `http://`, `https://` or `file://` is a URL prefix; a topic becomes `<prefix>/<path>/`
- any other value is the directory holding `dyalog.chm`; a topic becomes `<dir>/dyalog.chm::/<path>.htm`, shown in-process with the HtmlHelp API
- unset, the registry's `DyalogHelpDir` applies (the installer sets `<install dir>\help`)
- for a URL prefix the Windows IDE writes `%TEMP%\dyalog_help_<guid>.html`, a `<meta http-equiv=Refresh>` page pointing at the URL, and opens it with the shell's `open` verb; these files are not deleted
- the welcome topic has an empty path, giving `<prefix>//`

With `DYALOGHELPDIR=file:///C:/Temp/dd/21.0`, F1 on `⍴` opened `file:///C:/Temp/dd/21.0/language-reference-guide/symbols/rho/`, which does not resolve: a browser shows a directory listing or an error for a `file:` URL ending in `/`, never `index.html`, so neither the flat `.html` layout nor a `<path>/index.html` layout works without the interpreter change below.

Over the RIDE protocol the macOS 21.0 interpreter answers RIDE's own `GetHelpInformation` with the same URLs (observed through tauride: `file:` prefix and unset both resolved to the bundle's `rho.html` and `dt.html`). A minimal scripted client got no reply on either platform, so RIDE sends something during connection that the interpreter requires first.

Topic paths come from `help_urls.h` and match the bundle's paths. For the bundle, set:

```
DYALOGHELPDIR=file:///<root>/<X.Y>
```

The value is the unencoded path (`file:///Users/a/Library/Application Support/Dyalog/docs/21.0`), as the interpreter concatenates it with the topic. Whatever opens the URL percent-encodes it.

The trailing `/` form does not exist in the bundle: the topic `language-reference-guide/symbols/rho` is the file `language-reference-guide/symbols/rho.html`.

Requested of the interpreter team (spec only; not built here): see `design/offline-bundle-handoff.md`, which also covers the installers, Jenkins and SVN. In short: build `<prefix>/<path>.html` for a `file://` prefix; with the installed non-URL `DyalogHelpDir`, look for the per-user copy, then `<DyalogHelpDir>\docs`, then `dyalog.chm`, then online; percent-encode file URLs; open local pages directly; route the Help menu's fixed topics the same way.

RIDE (tested in the tauride fork) reads `DYALOGHELPDIR` from its own environment, which a spawned interpreter inherits, and rewrites `file:` help URLs ending in `/` to `.html` itself. With `DYALOGHELPDIR` unset, it applies the lookup order to the `https://docs.dyalog.com/<X.Y>/` URLs the interpreter returns. It therefore works against an unchanged 21.0 interpreter.
