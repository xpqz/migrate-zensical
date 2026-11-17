"""Python-Markdown extension substituting [project.extra] variables into pages.

The MkDocs site used the mkdocs-macros plugin, but the corpus only ever used
it for plain variable substitution: `{{ version_majmin }}`,
`{{ version_condensed }}` and `{{ conga_version_condensed }}`. From Zensical
0.0.68 a build with the macros plugin enabled is about 25 times slower on
macOS than one without, so this extension takes its place.

Every scalar (str, int, float, bool) top-level key of `[project.extra]` in
zensical.toml is available as `{{ name }}` (spacing inside the braces is
free). Nothing else of Jinja is: no filters, expressions, `{% %}` blocks or
nested keys. A `{{ name }}` with no matching variable is left as written and
logged once as a warning.

Substitution runs on the raw Markdown source, before fenced code is set
aside, so variables are replaced inside code blocks too, as macros did.
"""

import logging
import os
import re
import tomllib

from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor

log = logging.getLogger("dyalog_vars")

_VARIABLE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_SCALARS = (str, int, float, bool)

# Fenced code is stashed by pymdownx.superfences/fenced_code at priority 25;
# substitute before that so code blocks see the values too. Below 30 so the
# whitespace normalisation still runs first.
_PRIORITY = 28

# Resolved config path -> (mtime_ns, variables). One load per process unless
# the file changes.
_cache = {}
# Unknown names already warned about, so each is logged once per process.
_warned = set()


def load_variables(config_file):
    """Return the scalar top-level keys of [project.extra] in config_file."""
    path = os.path.realpath(config_file)
    mtime = os.stat(path).st_mtime_ns
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, "rb") as f:
        extra = tomllib.load(f).get("project", {}).get("extra", {})
    variables = {k: v for k, v in extra.items() if isinstance(v, _SCALARS)}
    _cache[path] = (mtime, variables)
    return variables


def substitute(text, variables):
    """Replace each {{ name }} in text with the scalar variables[name]."""

    def replace(match):
        name = match.group(1)
        value = variables.get(name)
        if not isinstance(value, _SCALARS):
            if name not in _warned:
                _warned.add(name)
                log.warning("dyalog_vars: no variable %r in [project.extra]; left as written", name)
            return match.group(0)
        return str(value)

    return _VARIABLE_RE.sub(replace, text)


class VariablesPreprocessor(Preprocessor):
    def __init__(self, md, config_file):
        super().__init__(md)
        self.config_file = config_file

    def run(self, lines):
        text = "\n".join(lines)
        if "{{" not in text:
            return lines
        return substitute(text, load_variables(self.config_file)).split("\n")


class VariablesExtension(Extension):
    def __init__(self, **kwargs):
        self.config = {
            "config_file": [
                "zensical.toml",
                "Config file whose [project.extra] supplies the variables, "
                "relative to the working directory",
            ],
        }
        super().__init__(**kwargs)

    def extendMarkdown(self, md):
        md.preprocessors.register(
            VariablesPreprocessor(md, self.getConfig("config_file")),
            "dyalog_vars",
            _PRIORITY,
        )


def makeExtension(**kwargs):
    return VariablesExtension(**kwargs)
