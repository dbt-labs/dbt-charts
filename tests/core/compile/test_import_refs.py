from __future__ import annotations

import pytest

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.import_refs import parse_import_refs


def test_self_referential_anchor_is_a_compilation_error() -> None:
    with pytest.raises(CompilationError):
        parse_import_refs("a: &x [*x]\n", "charts/a.yml")
