import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import objref_audit  # noqa: E402


def test_objref_root_resolves():
    root = objref_audit.default_docs_dir() / "object-reference"
    assert root.is_dir()
    assert (root / "objects" / "form.md").is_file()
