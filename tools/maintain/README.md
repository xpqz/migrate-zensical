# Maintenance scripts

Run from the repository root with `uv run python tools/maintain/<script>`; the content root comes from `docsite.find_project()`.

- `order_nav.py {sysfns|syscmds|ibeams}` — sort one reference section of `zensical.toml` in place, leaving the rest of the file untouched.
- `search_keywords.py [--dry-run] PAGE...` — append the hidden search-keyword block to pages that lack one (idempotent; pages are relative to the docs directory).
- `sysfntables.py [--row-wise] [--columns N] [--no-links] sysfns.txt` — generate the system-function tables from the output of `2031⌶7` / `2031⌶8`; Markdown goes to stdout.
