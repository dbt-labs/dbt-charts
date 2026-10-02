"""Guard: no hardcoded __file__ parent-hop chains, and no path escapes above
DBT_CHARTS_DIR, in dbt-charts/tests/.

Every test file that needs a project-root or fixture directory must import
`DBT_CHARTS_PKG_DIR` or `DBT_CHARTS_DIR` from `._paths` (or a relative
equivalent for subdirectories).  Hardcoded chains like
`Path(__file__).resolve().parents[3]` or `.parent.parent.parent` are
fragile: `Path.parent` never raises on a wrong count, so a file that
moves to a deeper directory silently resolves to the wrong location.

Four patterns this test bans, repo-wide under dbt-charts/tests/:
  - `.resolve().parent.parent` or a bare `__file__).parent.parent` (two or
    more `.parent` hops off `__file__`, with or without `.resolve()` --
    a bare chain is exactly as fragile and slipped past a resolve()-only
    pattern). Scoped to `__file__` specifically, not any `.parent.parent`
    walk -- a fixture-path variable's own `.parent.parent` (e.g. a board
    file's containing project dir) isn't the __file__-based project-root
    rediscovery this guard exists to catch.
  - `.parents[N]`             (bracket-style index access, any N 0–9)
  - `DBT_CHARTS_DIR.parent` / `DBT_CHARTS_PKG_DIR.parent` — INV1: nothing
    under dbt-charts/ may read a path outside dbt-charts/, and going up
    from either anchor always lands outside it.
  - the identifier `PROJECT_ROOT` — the monorepo-root escape hatch this
    invariant removes; there is nothing legitimate above DBT_CHARTS_DIR
    for a test to reach.

The sole exemptions: `dbt-charts/tests/_paths.py` itself (the canonical
resolver; consumers import from it instead of re-deriving) and
`dbt-charts/tests/visual/` (the sole named INV1 carve-out — it discovers
docs-fence cases from apps/docs/ and reads examples/; see visual/discovery.py).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ._paths import DBT_CHARTS_DIR

_TESTS_DIR = DBT_CHARTS_DIR / "tests"
_PATHS_PY = _TESTS_DIR / "_paths.py"
_VISUAL_DIR = _TESTS_DIR / "visual"
_SELF = Path(__file__).resolve()

# Match any of the four banned forms: resolve().parent.parent / __file__).parent.parent
# — two or more .parent hops off __file__, resolve() or not
#   .parents[N]                    — bracket index, N in 0-9
#   DBT_CHARTS_DIR.parent / DBT_CHARTS_PKG_DIR.parent — escapes the INV1 boundary
#   PROJECT_ROOT                   — the removed monorepo-root escape hatch
_RAW_WALK_RE = re.compile(
    r"resolve\(\)\.parent\.parent"
    r"|__file__\)(?:\.parent){2,}"
    r"|\.parents\[[0-9]\]"
    r"|DBT_CHARTS_(?:PKG_)?DIR\.parent\b"
    r"|\bPROJECT_ROOT\b"
)


def _all_violations() -> list[tuple[str, int]]:
    violations: list[tuple[str, int]] = []
    for root, _dirs, files in os.walk(_TESTS_DIR):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            if path in (_PATHS_PY, _SELF) or _VISUAL_DIR in path.parents:
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if _RAW_WALK_RE.search(line):
                    violations.append((str(path.relative_to(DBT_CHARTS_DIR)), lineno))
    return violations


def test_tests_dir_exists() -> None:
    """Scan root must exist — otherwise the walk is a silent no-op."""
    assert _TESTS_DIR.is_dir(), f"tests dir missing: {_TESTS_DIR}"


def test_no_raw_parent_walks_in_tests() -> None:
    """No test file may use hardcoded parent-hop chains or escape DBT_CHARTS_DIR.

    Import DBT_CHARTS_PKG_DIR / DBT_CHARTS_DIR from ._paths (or the
    equivalent relative import) instead.  See module docstring.
    """
    violations = _all_violations()
    assert not violations, (
        "Hardcoded __file__ parent-hop chains or DBT_CHARTS_DIR escapes found "
        "in dbt-charts/tests/ — import from ._paths (or its relative "
        "equivalent) instead, and never read a path outside dbt-charts/ "
        "(tests/visual/ is the sole named exception):\n"
        + "\n".join(f"  {path}:{lineno}" for path, lineno in violations)
    )
