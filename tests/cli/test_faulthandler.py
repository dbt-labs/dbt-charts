"""A native crash in a loaded driver leaves a traceback instead of a silent exit."""

from __future__ import annotations

import subprocess
import sys


def test_the_cli_entry_point_enables_faulthandler() -> None:
    """A fresh interpreter, because pytest enables faulthandler for itself."""
    program = (
        "import faulthandler\n"
        "assert not faulthandler.is_enabled()\n"
        "import dbt_charts.cli.main\n"
        "assert faulthandler.is_enabled()\n"
    )
    subprocess.run([sys.executable, "-I", "-c", program], check=True)
