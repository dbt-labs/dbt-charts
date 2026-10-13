# dbt-charts/tests/core/test_no_process_state_reads.py
#
# Self-contained guard test. AST helpers are private functions inlined at the
# top of the module, matching dbt-charts/tests/test_vocabulary.py:35-73. The
# allow-list constant below is the suppression mechanism — adding to it
# requires editing this file in the same PR as the new module-global.

from __future__ import annotations

import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import NamedTuple

import dbt_charts

CORE = Path(dbt_charts.__file__).resolve().parent / "core"

_CACHE_NAME_RE = re.compile(r"^_.*(cache|registry|sources|context).*", re.IGNORECASE)

# Legitimate import-time process-globals that match the name heuristic but are
# NOT FR-003 targets (none are keyed by resolved cwd / project_root / env). Adding
# to this set requires editing this file in the same PR as the new global, so a
# reviewer is naturally pulled in.
#
# The broader FR-003 clause "or that survive across requests in a long-lived
# server" is arguably violated by the memoization caches below; design.md
# interpreted FR-003 narrowly (project-state-keyed only) and the dependency
# chain targets reflect that. Re-opening the broader scope is a separate
# initiative-level decision.
_ALLOW_MODULE_GLOBALS: set[tuple[str, str]] = {
    ("compile/config.py", "_compiled_theme_cache"),  # keyed by theme name
    (
        "compile/models/factories.py",
        "_PATCH_REGISTRY",
    ),  # import-time class registry; load-bearing
    (
        "compile/resolve/style/board.py",
        "_RESOLVED_STYLE_CACHE",
    ),  # keyed by Style content hash
    ("compile/resolve/style/palette.py", "_spine_cache"),  # keyed by palette name
}


class _Violation(NamedTuple):
    path: Path
    line: int
    name: str


def _scan_module_globals(core_root: Path) -> list[_Violation]:
    out: list[_Violation] = []
    for path in sorted(core_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        rel = path.relative_to(core_root).as_posix()
        for stmt in tree.body:
            targets: list[ast.expr]
            value: ast.AST | None
            if isinstance(stmt, ast.Assign):
                targets, value = list(stmt.targets), stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets, value = [stmt.target], stmt.value
            else:
                continue
            if not isinstance(
                value,
                (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp),
            ):
                continue
            for tgt in targets:
                if isinstance(tgt, ast.Name) and _CACHE_NAME_RE.match(tgt.id):
                    if (rel, tgt.id) in _ALLOW_MODULE_GLOBALS:
                        continue
                    out.append(_Violation(path, stmt.lineno, tgt.id))
    return out


def test_core_ruff_config_bans_process_state_reads(tmp_path: Path) -> None:
    cfg = tomllib.loads((CORE / "ruff.toml").read_text(encoding="utf-8"))
    banned = cfg["lint"]["flake8-tidy-imports"]["banned-api"]
    # pathlib.Path.cwd is no longer its own entry — the blanket pathlib.Path ban
    # (added for the filesystem-access-enforcement task) subsumes it; the snippet
    # loop below confirms a pathlib.Path call-site (pathlib.Path.cwd()) trips TID251.
    assert set(banned) >= {"os.getcwd", "pathlib.Path", "os.environ", "os.getenv"}
    assert "TID251" in cfg["lint"].get("extend-select", []), (
        "core/ruff.toml must extend-select TID251"
    )

    banned_api_snippets: list[tuple[str, str]] = [
        ("os.environ", "import os\nos.environ.get('X')\n"),
        ("os.getenv", "import os\nos.getenv('X')\n"),
        ("os.getcwd", "import os\nos.getcwd()\n"),
        ("pathlib.Path", "import pathlib\npathlib.Path.cwd()\n"),
    ]
    for api_name, snippet in banned_api_snippets:
        leak = tmp_path / f"leak_{api_name.replace('.', '_')}.py"
        leak.write_text(snippet)
        proc = subprocess.run(
            [
                "uv",
                "run",
                "ruff",
                "check",
                "--config",
                str(CORE / "ruff.toml"),
                "--output-format",
                "json",
                str(leak),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        # Split asserts so a uv/lock-file failure (or a broken `extend` path,
        # which exits rc=2 with empty stdout and the real cause on stderr)
        # surfaces stderr instead of being masked by an AND-chain or a bare
        # JSONDecodeError from parsing empty stdout.
        assert proc.returncode != 0, (
            f"ruff did not flag {api_name!r}: stderr={proc.stderr!r}"
        )
        assert proc.stdout.strip(), (
            f"ruff produced no JSON output: stderr={proc.stderr!r}"
        )
        # JSON `code` is stable across preview's text-rendering changes (preview
        # prints rule names like "banned-api" instead of "TID251" in plain text).
        codes = {d["code"] for d in json.loads(proc.stdout)}
        assert "TID251" in codes, (
            f"ruff flagged {api_name!r} but not TID251: codes={codes!r} stderr={proc.stderr!r}"
        )


def test_no_module_global_caches_in_core() -> None:
    violations = _scan_module_globals(CORE)
    assert violations == [], (
        "Module-global mutable caches in dbt_charts/core:\n"
        + "\n".join(f"  {v.path}:{v.line}: {v.name}" for v in violations)
    )
