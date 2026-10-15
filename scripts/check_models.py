#!/usr/bin/env python3
"""AST-based model-convention checker for ``dbt_charts.core.compile.models``.

Five rules enforced (CI fails on any violation):

  Rule 1 — No bare-dict model_config.
    model_config = {"extra": "forbid"} → BANNED.
    model_config = ConfigDict(extra="forbid") → OK.

  Rule 2 — Direct BaseModel subclasses must declare model_config = ConfigDict(extra="forbid").
    Only fires on classes whose *direct* bases include the bare name "BaseModel".
    Sub-subclasses that inherit from a project-internal model are exempt
    (they inherit the config through the chain).

  Rule 3 — No new "Compiled"-prefixed class names.
    ADR-001: the compiled stage owns the bare name (Board, Chart, Style …).
    A class named CompiledFoo is a regression.

  Rule 4 — No "_types"-suffixed module filenames.
    Convention: {authored.py, theme.py, normalized.py, resolved.py} per subdirectory.

  Rule 5 — Every T | None = None field in a compiled model must have
    a justification comment on the same line or the immediately preceding line.
    "Justification" = any inline '#' comment.  Examples that pass:
        angle: float | None = None  # None = VL chooses angle
        # Only populated on axis_y; None elsewhere skips the VL property.
        categorical_orient: str | None = None
    A bare field with no comment is flagged — it may be a theme-populated
    field mistakenly left Optional instead of required.

Usage:
    python scripts/check_models.py                    # check whole models tree
    python scripts/check_models.py path/to/file.py    # check a single file

Returns exit code 0 when clean, 1 when violations found.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from collections.abc import Iterator

import dbt_charts

_pkg_file = dbt_charts.__file__
assert _pkg_file is not None

# Root of the models tree to audit
MODELS_ROOT = pathlib.Path(_pkg_file).resolve().parent / "core" / "compile" / "models"

# Rule 5 is scoped to theme-stage files/directories only (not authored/resolved).
# A path matches if it equals a file entry OR is inside a directory entry.
_RULE5_TARGETS: tuple[pathlib.Path, ...] = (
    MODELS_ROOT / "style" / "theme",  # package — matches all style/theme/*.py
    MODELS_ROOT / "config.py",
    MODELS_ROOT / "chart" / "normalized",  # package — matches all chart/normalized/*.py
)
# Fail loudly if any target has disappeared (e.g. after a future rename/move).
for _t in _RULE5_TARGETS:
    assert _t.exists(), f"check_models Rule 5 target missing: {_t}"


# ---------------------------------------------------------------------------
# Violation dataclass
# ---------------------------------------------------------------------------


class Violation:
    __slots__ = ("path", "line", "rule", "message")

    def __init__(self, path: pathlib.Path, line: int, rule: int, message: str) -> None:
        self.path = path
        self.line = line
        self.rule = rule
        self.message = message

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [Rule {self.rule}] {self.message}"


# ---------------------------------------------------------------------------
# Per-rule checkers
# ---------------------------------------------------------------------------


def _check_rule1(tree: ast.Module, path: pathlib.Path) -> Iterator[Violation]:
    """Rule 1: model_config must use ConfigDict(…), not a bare dict literal."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            targets = stmt.targets
            if not any(
                isinstance(t, ast.Name) and t.id == "model_config" for t in targets
            ):
                continue
            if isinstance(stmt.value, ast.Dict):
                yield Violation(
                    path,
                    stmt.lineno,
                    1,
                    f"class {node.name}: model_config = {{...}} — "
                    "use ConfigDict(extra='forbid') instead of a bare dict",
                )


def _check_rule2(tree: ast.Module, path: pathlib.Path) -> Iterator[Violation]:
    """Rule 2: direct BaseModel subclasses must declare model_config."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Only check classes that directly subclass bare "BaseModel"
        base_names = [ast.unparse(b) for b in node.bases]
        if "BaseModel" not in base_names:
            continue
        # Skip enums (don't need model_config)
        if any("Enum" in b for b in base_names):
            continue
        # Skip dataclasses (decorated with @dataclass)
        if any(
            (isinstance(d, ast.Name) and d.id == "dataclass")
            or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
            for d in node.decorator_list
        ):
            continue
        has_mc = any(
            isinstance(stmt, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
            )
            for stmt in node.body
        )
        if not has_mc:
            yield Violation(
                path,
                node.lineno,
                2,
                f"class {node.name}(BaseModel) is missing "
                "model_config = ConfigDict(extra='forbid')",
            )


def _check_rule3(tree: ast.Module, path: pathlib.Path) -> Iterator[Violation]:
    """Rule 3: no Compiled-prefixed class names in compile/models/."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.startswith("Compiled"):
            yield Violation(
                path,
                node.lineno,
                3,
                f"class {node.name}: 'Compiled' prefix is banned — "
                "use the bare name (Board, Chart, Style …) per ADR-001",
            )


def _check_rule4(path: pathlib.Path) -> Iterator[Violation]:
    """Rule 4: no _types-suffixed module filenames."""
    if path.stem.endswith("_types"):
        yield Violation(
            path,
            1,
            4,
            f"module '{path.name}': '_types' suffix is banned — "
            "use authored.py / theme.py / normalized.py / resolved.py",
        )


def _check_rule5(
    tree: ast.Module, path: pathlib.Path, source_lines: list[str]
) -> Iterator[Violation]:
    """Rule 5: T | None = None fields in compiled models need a justification comment."""
    resolved = path.resolve()
    if not any(
        resolved == t.resolve() if t.is_file() else resolved.is_relative_to(t.resolve())
        for t in _RULE5_TARGETS
    ):
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign):
                continue
            ann = ast.unparse(stmt.annotation)
            if "| None" not in ann:
                continue
            if stmt.value is None:
                continue
            if ast.unparse(stmt.value) != "None":
                continue
            line_idx = stmt.lineno - 1  # 0-based
            end_idx = getattr(stmt, "end_lineno", stmt.lineno) - 1
            prev_line = source_lines[line_idx - 1].strip() if line_idx > 0 else ""
            # Accept a comment on the preceding line OR anywhere within the statement.
            stmt_lines = source_lines[line_idx : end_idx + 1]
            if any("#" in ln for ln in stmt_lines) or prev_line.startswith("#"):
                continue
            target = (
                ast.unparse(stmt.target) if isinstance(stmt.target, ast.Name) else "?"
            )
            yield Violation(
                path,
                stmt.lineno,
                5,
                f"class {node.name}.{target}: {ann} = None — "
                "add an inline '# None = ...' comment explaining why this field "
                "is Optional rather than required",
            )


# ---------------------------------------------------------------------------
# File-level runner
# ---------------------------------------------------------------------------


def check_file(path: pathlib.Path) -> list[Violation]:
    """Run all applicable rules against a single file."""
    violations: list[Violation] = []
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    try:
        tree = ast.parse(source, str(path))
    except SyntaxError as exc:
        # Not our problem to report syntax errors; let ruff/pyright handle them.
        print(f"WARNING: could not parse {path}: {exc}", file=sys.stderr)
        return violations

    violations.extend(_check_rule1(tree, path))
    violations.extend(_check_rule2(tree, path))
    violations.extend(_check_rule3(tree, path))
    violations.extend(_check_rule4(path))
    violations.extend(_check_rule5(tree, path, source_lines))
    return violations


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    paths = [pathlib.Path(a) for a in args] if args else list(MODELS_ROOT.rglob("*.py"))

    all_violations: list[Violation] = []
    for path in sorted(paths):
        if not path.is_file() or path.suffix != ".py":
            continue
        all_violations.extend(check_file(path))

    for v in sorted(all_violations, key=lambda x: (str(x.path), x.line)):
        print(v)

    if all_violations:
        print(
            f"\n{len(all_violations)} model-convention violation(s). "
            "See scripts/check_models.py for rule descriptions.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
