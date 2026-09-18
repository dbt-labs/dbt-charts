"""Regenerate syntax-highlighting artifacts from the schema IR.

Writes two committed files:
  dbt-charts/src/dbt_charts/data/highlighting/board.json
  apps/vscode-extension/syntaxes/dbt-charts.tmLanguage.json

Run via: just gen-highlight-artifacts
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# parents[1] is the dbt-charts package root in both monorepo (dbt-charts/scripts/)
# and standalone export (scripts/ after Copybara core.move("dbt-charts", "")).
_DBT_CHARTS_DIR = Path(__file__).resolve().parents[1]
_MONOREPO_ROOT = _DBT_CHARTS_DIR.parent

MANIFEST_PATH = (
    _DBT_CHARTS_DIR / "src" / "dbt_charts" / "data" / "highlighting" / "board.json"
)
# tmLanguage lives under apps/, which exists only in the monorepo.
# In a standalone Copybara export, _DBT_CHARTS_DIR IS the repo root.
_TM_GRAMMAR_PATH: Path | None = (
    _MONOREPO_ROOT
    / "apps"
    / "vscode-extension"
    / "syntaxes"
    / "dbt-charts.tmLanguage.json"
    if (_MONOREPO_ROOT / "dbt-charts").resolve() == _DBT_CHARTS_DIR.resolve()
    else None
)


def main() -> None:
    from dbt_charts.core.compile.schema.introspection import introspect
    from dbt_charts.core.compile.schema.renderers.highlight_manifest import (
        render_highlight_manifest,
    )

    schema = introspect()
    manifest = render_highlight_manifest(schema)

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n")
    print(f"✓ {MANIFEST_PATH.relative_to(_DBT_CHARTS_DIR)}")

    if _TM_GRAMMAR_PATH is not None:
        from dbt_charts.core.compile.schema.renderers.textmate_grammar import (
            render_textmate_grammar,
        )

        grammar = render_textmate_grammar(manifest)
        _TM_GRAMMAR_PATH.write_text(json.dumps(grammar, indent=2) + "\n")
        print(f"✓ {_TM_GRAMMAR_PATH.relative_to(_MONOREPO_ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
