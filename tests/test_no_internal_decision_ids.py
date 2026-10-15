"""Shipped source must not cite internal ADR numbers.

Field docstrings feed the published JSON schema, so an ``ADR-NNN`` there
reaches users who cannot open the decision record. Frozen versioned schema
snapshots under ``data/schemas`` are exempt: they record what shipped.
"""

from __future__ import annotations

import re

from ._paths import DBT_CHARTS_PKG_DIR

_ADR_ID = re.compile(r"\bADR-\d+")
_FROZEN_SNAPSHOTS = DBT_CHARTS_PKG_DIR / "data" / "schemas"


def test_shipped_source_has_no_adr_identifiers() -> None:
    hits = [
        f"{path.relative_to(DBT_CHARTS_PKG_DIR)}:{lineno}: {line.strip()}"
        for path in sorted(DBT_CHARTS_PKG_DIR.rglob("*"))
        if path.suffix in {".py", ".yaml", ".yml", ".md"}
        and _FROZEN_SNAPSHOTS not in path.parents
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _ADR_ID.search(line)
    ]
    assert not hits, "Internal ADR identifiers in shipped source:\n" + "\n".join(hits)
