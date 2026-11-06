"""`dct migrate`'s text rewrite, byte for byte.

The fixtures are every `migrate_yaml_text` call the migration tests made against
the real declarations when the Python engine still planned the rewrite: the
source text, the target, and what it wrote (or the error class it raised).
They pin the bytes the writer produces from the Rust engine's edits, and are
the differential a Rust writer must reproduce when `yaml_patch` ports.
"""

from __future__ import annotations

import json
import warnings

import pytest

from dbt_charts.core.compile.migrations import migrate_yaml_text

from ..._paths import DBT_CHARTS_DIR

FIXTURES = json.loads(
    (DBT_CHARTS_DIR / "tests" / "fixtures" / "migrate_text_fixtures.json").read_text(
        encoding="utf-8"
    )
)

assert FIXTURES, "the recorded fixtures are the gate; an empty file must not pass"


@pytest.mark.parametrize("row", FIXTURES, ids=[f"row{i}" for i in range(len(FIXTURES))])
def test_the_writer_reproduces_the_recorded_bytes(row: dict[str, str | None]) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if "err" in row:
            with pytest.raises(Exception) as caught:  # noqa: PT011 — the recorded class is asserted below
                migrate_yaml_text(row["text"], stop_target=row["stop"])
            assert type(caught.value).__name__ == row["err"]
        else:
            assert migrate_yaml_text(row["text"], stop_target=row["stop"]) == row["ok"]
