# Dyalog documentation

The source of the Dyalog user documentation published at [docs.dyalog.com](https://docs.dyalog.com):
every guide as one [Zensical](https://zensical.org) project. The pages are Markdown under `docs/`,
the site configuration and navigation are in `zensical.toml`, and the build, check and release
tooling is under `tools/` and `.github/`.

## Getting started

1. Install [uv](https://docs.astral.sh/uv/), version 0.5.4 or later (check with `uv --version`,
   upgrade with `uv self update`). It fetches the Python version and the pinned packages the
   site needs; there is nothing else to install.
2. Clone the repository with its submodule:

   ```
   git clone --recurse-submodules <repository-url>
   ```

   The house style (the site's stylesheet, fonts and logos) is a separate repository mounted as
   a submodule at `docs/documentation-theme-zensical`. In a clone made without
   `--recurse-submodules`, run `git submodule update --init`.
3. From the repository root, install everything:

   ```
   uv sync --all-groups
   ```

4. Preview the site at <http://localhost:8000>; it rebuilds when you save a page:

   ```
   uv run zensical serve
   ```

5. Before you push, run the checks. They build the site, compare its warnings with
   `known-issues.txt`, and check images, admonitions and the navigation:

   ```
   uv run python tools/check.py
   ```

[Zensical Studio](https://marketplace.visualstudio.com/items?itemName=zensical.zensical-studio),
an extension for VS Code, Cursor and Zed, gives a live preview and link diagnostics while you
write.

## Contributing

The contribution process (issues, branches, pull requests and review), the per-platform setup
notes and the authoring conventions are in [CONTRIBUTE.md](CONTRIBUTE.md).
