"""Shipped source and tests must not cite internal decision labels.

Field docstrings feed the published JSON schema, so an ``ADR-NNN`` there
reaches users who cannot open the decision record; tests are contributor-visible
and must not lean on identifiers only the authors can resolve. ``spec §N`` is
the same leak under another name. Frozen versioned schema snapshots under
``data/schemas`` are exempt: they record what shipped. This file is exempt: it
spells the patterns.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._paths import DBT_CHARTS_DIR, DBT_CHARTS_PKG_DIR

_DECISION_ID = re.compile(r"\bADR-\d+|\bspec\s*§|§\d", re.IGNORECASE)
_FROZEN_SNAPSHOTS = DBT_CHARTS_PKG_DIR / "data" / "schemas"
_THIS_FILE = Path(__file__).resolve()
_SCANNED_ROOTS = (DBT_CHARTS_PKG_DIR, DBT_CHARTS_DIR / "tests")


def test_source_and_tests_have_no_internal_decision_identifiers() -> None:
    hits = [
        f"{path.relative_to(DBT_CHARTS_DIR)}:{lineno}: {line.strip()}"
        for root in _SCANNED_ROOTS
        for path in sorted(root.rglob("*"))
        if path.suffix in {".py", ".yaml", ".yml", ".md"}
        and path.resolve() != _THIS_FILE
        and _FROZEN_SNAPSHOTS not in path.parents
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _DECISION_ID.search(line)
    ]
    assert not hits, "Internal decision identifiers:\n" + "\n".join(hits)
