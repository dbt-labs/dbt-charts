from __future__ import annotations

import subprocess
import sys

import pytest

from dbt_charts.core.compile.sql_guard import require_sqlglot_dialect, source_dialect


def test_source_dialect_names_only_sqlglot_dialects() -> None:
    assert source_dialect("duckdb") == "duckdb"
    assert source_dialect("csv") is None
    assert source_dialect(None) is None


def test_require_sqlglot_dialect_rejects_an_unknown_name() -> None:
    assert require_sqlglot_dialect("postgres") == "postgres"
    with pytest.raises(ValueError, match="Unknown SQL dialect"):
        require_sqlglot_dialect("not_a_dialect")


def test_dialect_checks_do_not_import_every_sqlglot_dialect() -> None:
    """Asking whether one name is a dialect must not load all of them: that
    import is a few hundred milliseconds on a board's first compile."""
    program = (
        "import sys\n"
        "from dbt_charts.core.compile.sql_guard import (\n"
        "    require_sqlglot_dialect, source_dialect)\n"
        "source_dialect('duckdb')\n"
        "source_dialect('csv')\n"
        "require_sqlglot_dialect('postgres')\n"
        "assert 'sqlglot.dialects.snowflake' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-I", "-c", program], check=True)
