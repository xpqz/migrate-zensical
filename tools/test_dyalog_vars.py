"""Behavioural surface for the variables extension.

dyalog_vars replaces the mkdocs-macros plugin, which the corpus used only to
substitute scalar values from [project.extra] (version numbers) into pages.
"""

import logging
import os

import markdown

import dyalog_vars

TOML = """\
[project]
site_name = "Test"

[project.extra]
version_majmin = "21.0"
version_condensed = 210
conga_version = 3.6
generator = false

[project.extra.version]
provider = "mike"
"""


def test_substitutes_known_variable():
    assert dyalog_vars.substitute("v{{ version_majmin }}", {"version_majmin": "21.0"}) == "v21.0"


def test_spacing_variants():
    variables = {"x": "1"}
    assert dyalog_vars.substitute("{{x}} {{ x }} {{  x  }}", variables) == "1 1 1"


def test_unknown_left_untouched():
    text = "a {{ nope }} b"
    assert dyalog_vars.substitute(text, {"x": "1"}) == text


def test_unknown_is_logged_once(caplog):
    with caplog.at_level(logging.WARNING, logger="dyalog_vars"):
        dyalog_vars.substitute("{{ once_only_name }} {{ once_only_name }}", {})
        dyalog_vars.substitute("{{ once_only_name }}", {})
    messages = [r.getMessage() for r in caplog.records if "once_only_name" in r.getMessage()]
    assert len(messages) == 1


def test_non_scalar_extra_ignored():
    variables = {"version": {"provider": "mike"}, "items": [1, 2]}
    text = "{{ version }} {{ items }}"
    assert dyalog_vars.substitute(text, variables) == text


def test_scalars_render_as_their_text():
    variables = {"n": 210, "f": 3.6, "b": False}
    assert dyalog_vars.substitute("{{ n }} {{ f }} {{ b }}", variables) == "210 3.6 False"


def test_inside_code_fence_substituted(tmp_path):
    config = tmp_path / "zensical.toml"
    config.write_text(TOML, encoding="utf-8")
    html = markdown.markdown(
        "```\ncd dyalog{{ version_condensed }}\n```\n",
        extensions=["pymdownx.superfences", dyalog_vars.makeExtension(config_file=str(config))],
    )
    assert "dyalog210" in html
    assert "{{" not in html


def test_markdown_end_to_end(tmp_path):
    config = tmp_path / "zensical.toml"
    config.write_text(TOML, encoding="utf-8")
    html = markdown.markdown(
        "Dyalog v{{ version_majmin }} and {{ unknown }}.",
        extensions=[dyalog_vars.makeExtension(config_file=str(config))],
    )
    assert html == "<p>Dyalog v21.0 and {{ unknown }}.</p>"


def test_reads_project_extra_from_toml(tmp_path):
    config = tmp_path / "zensical.toml"
    config.write_text(TOML, encoding="utf-8")
    assert dyalog_vars.load_variables(str(config)) == {
        "version_majmin": "21.0",
        "version_condensed": 210,
        "conga_version": 3.6,
        "generator": False,
    }


def test_config_file_defaults_to_the_working_directory(tmp_path, monkeypatch):
    (tmp_path / "zensical.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    html = markdown.markdown("v{{ version_majmin }}", extensions=[dyalog_vars.makeExtension()])
    assert html == "<p>v21.0</p>"


def test_reloads_when_the_config_changes(tmp_path):
    config = tmp_path / "zensical.toml"
    config.write_text(TOML, encoding="utf-8")
    assert dyalog_vars.load_variables(str(config))["version_majmin"] == "21.0"
    config.write_text(TOML.replace('"21.0"', '"22.0"'), encoding="utf-8")
    # Force a distinct mtime even on coarse-resolution filesystems.
    stat = config.stat()
    os.utime(config, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert dyalog_vars.load_variables(str(config))["version_majmin"] == "22.0"
