import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("sysfntables.py")


def test_symbols_on_stderr_are_utf8_whatever_the_console_encoding(tmp_path):
    sysfns = tmp_path / "sysfns.txt"
    sysfns.write_text("⎕AI ⎕UCS\n", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(sysfns)], capture_output=True, env=env
    )
    assert "⎕UCS".encode("utf-8") in result.stderr
    assert "⎕UCS".encode("utf-8") in result.stdout
