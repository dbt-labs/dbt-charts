"""Vocabulary guard: reject stale 'catalog' terminology in agent-facing surfaces.

Scans the agent_api, CLI, inspect, AI (Python + SKILL.md files), and AGENTS.md
surfaces under the dbt_charts package, plus the user-facing docs paths where MCP
tool names and schema-cache terminology appear. Legitimate uses are on the
allow-list; everything else fails so the half-finished-rename antipattern can't
drift back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ._paths import DBT_CHARTS_DIR, DBT_CHARTS_PKG_DIR

_REPO_ROOT = DBT_CHARTS_DIR

_DBT_CHARTS_ROOT = DBT_CHARTS_PKG_DIR

# Patterns that are allowed — external names we cannot rename.
_ALLOW_PATTERNS = [
    re.compile(r"pg_catalog"),  # PostgreSQL system schema
    re.compile(r"CatalogException"),  # DuckDB exception class
    re.compile(r"catalog\.json"),  # dbt artifact filename
    re.compile(r"external_catalog"),  # BigQuery API field
    re.compile(r"ERR-CATALOG"),  # error-registry code
    re.compile(r"catalog=exp\."),  # sqlglot AST node kwarg (3-part table name)
    re.compile(r'text\("catalog"\)'),  # sqlglot AST attribute read (3-part name)
    re.compile(r"Unity Catalog"),  # Databricks product name
    re.compile(r"get\(\"catalog\"\)"),  # Databricks connection field
    re.compile(r"skill catalog"),  # dct skills registry phrasing in MCP docs
    re.compile(r"Pattern Catalog"),  # layout-pattern registry heading in skills
]


def _catalog_violations(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, line) pairs where 'catalog' appears outside allow-list."""
    pattern = re.compile(r"catalog", re.IGNORECASE)
    violations: list[tuple[int, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if not pattern.search(line):
            continue
        if any(allow.search(line) for allow in _ALLOW_PATTERNS):
            continue
        violations.append((lineno, line.rstrip()))
    return violations


def _collect_paths() -> list[Path]:
    """Collect scanned paths: agent_api/, cli/, core/inspect/, ai/, plus docs."""
    paths: list[Path] = []
    for sub in ("agent_api", "cli", "core/inspect"):
        paths.extend((_DBT_CHARTS_ROOT / sub).rglob("*.py"))
    for p in (_DBT_CHARTS_ROOT / "ai").rglob("*.py"):
        paths.append(p)
    for p in (_DBT_CHARTS_ROOT / "ai").rglob("SKILL.md"):
        paths.append(p)
    agents_md = _DBT_CHARTS_ROOT / "AGENTS.md"
    if agents_md.exists():
        paths.append(agents_md)
    # User-facing docs where MCP tool names and schema-cache terminology appear.
    _DOCS_ROOT = _REPO_ROOT / "docs" / "docs"
    for doc in (
        _DOCS_ROOT / "inspector" / "context-stack.md",
        _DOCS_ROOT / "inspector" / "running-and-using.md",
        _DOCS_ROOT / "guides" / "installation.md",
        _DOCS_ROOT / "contributing" / "architecture.md",
        _DOCS_ROOT / "blog" / "posts" / "how-we-prevent-fanout.md",
        _DOCS_ROOT / "cli" / "mcp.md",
    ):
        if doc.exists():
            paths.append(doc)
    return sorted(paths)


@pytest.mark.parametrize(
    "path", _collect_paths(), ids=lambda p: str(p.relative_to(_REPO_ROOT))
)
def test_no_catalog_vocabulary(path: Path) -> None:
    violations = _catalog_violations(path)
    if violations:
        lines = "\n".join(f"  line {n}: {text}" for n, text in violations)
        pytest.fail(
            f"{path.relative_to(_REPO_ROOT)} contains stale 'catalog' vocabulary:\n{lines}"
        )
