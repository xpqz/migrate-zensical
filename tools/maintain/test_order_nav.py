import order_nav

TOML = '''\
# top comment
[project]
nav = [
    { "Intro" = "intro.md" },
    # keep this comment
    { "System Functions (A-Z)" = [
    { "System Functions (A-Z)" = "sf/index.md" },
    { "Character Input Output" = "sf/cio.md" },
    { "<code>⎕UCS</code>: Unicode Convert" = "sf/ucs.md" },
    { "<code>⎕a</code>: lower" = "sf/a.md" },
    { "<code>⎕AI</code>: Account Information" = "sf/ai.md" },
    { "<code>⎕A</code>: Uppercase Alphabet" = "sf/a2.md" },
] },
    { "System Commands (A-Z)" = [
    { "System Commands (A-Z)" = "sc/index.md" },
    { "SAVE: Save" = "sc/save.md" },
    { "CLEAR: Clear" = "sc/clear.md" },
] },
    { I-Beam = [
    { I-Beam = "ib/index.md" },
    { "43: Monadic" = "ib/43.md" },
    { "8: Inverted" = "ib/8.md" },
    { "13: Log" = "ib/13.md" },
] },
    { Tail = "tail.md" },
]
# bottom comment
'''


def titles(text, section):
    out, inside = [], False
    for line in text.split("\n"):
        if line.startswith(f'    {{ "{section}" = [') or line.startswith(f"    {{ {section} = ["):
            inside = True
            continue
        if inside:
            if line.startswith("]"):
                break
            out.append(line)
    return out


def test_reorder_sorts_sysfns_ignoring_quad_and_case():
    new = order_nav.reorder(TOML, "System Functions (A-Z)", order_nav.KEYS["sysfns"])
    codes = [l.split("<code>")[1].split("</code>")[0] for l in titles(new, "System Functions (A-Z)") if "<code>" in l]
    assert codes == ["⎕a", "⎕A", "⎕AI", "⎕UCS"]  # case-folded; ties keep file order


def test_reorder_keeps_index_first():
    new = order_nav.reorder(TOML, "System Functions (A-Z)", order_nav.KEYS["sysfns"])
    lines = titles(new, "System Functions (A-Z)")
    assert 'sf/index.md' in lines[0]
    assert 'sf/cio.md' in lines[1]  # unkeyed entries stay pinned after the index
    new = order_nav.reorder(TOML, "System Commands (A-Z)", order_nav.KEYS["syscmds"])
    lines = titles(new, "System Commands (A-Z)")
    assert 'sc/index.md' in lines[0]
    assert [l.split('"')[1] for l in lines[1:]] == ["CLEAR: Clear", "SAVE: Save"]


def test_reorder_numeric_ibeams():
    new = order_nav.reorder(TOML, "I-Beam", order_nav.KEYS["ibeams"])
    lines = titles(new, "I-Beam")
    assert "ib/index.md" in lines[0]
    assert [l.split('"')[1] for l in lines[1:]] == ["8: Inverted", "13: Log", "43: Monadic"]


def test_reorder_leaves_rest_of_file_byte_identical():
    new = order_nav.reorder(TOML, "I-Beam", order_nav.KEYS["ibeams"])
    old_lines, new_lines = TOML.split("\n"), new.split("\n")
    assert len(old_lines) == len(new_lines)
    changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    start = old_lines.index("    { I-Beam = [")
    assert changed and all(start < i < start + 5 for i in changed)
    assert "# keep this comment" in new and new.startswith("# top comment")
    assert new.endswith("# bottom comment\n")


def test_reorder_is_idempotent():
    once = order_nav.reorder(TOML, "I-Beam", order_nav.KEYS["ibeams"])
    assert order_nav.reorder(once, "I-Beam", order_nav.KEYS["ibeams"]) == once
