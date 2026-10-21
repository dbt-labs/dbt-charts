"""Regression: every ```yaml fence the wheel ships to agents must pass dct validate.

Covers DBT_CHARTS_SYNTAX.md (inlined into the authoring prompts) and every packaged
ai/skills/*/SKILL.md (served by `dct skills`).

If this test fails, either the example is wrong (update the doc) or the
validator changed its contract (update the validator + the doc together).
Docs always follow the validator — never relax the validator to match a bad
example.
"""

from __future__ import annotations

import importlib.resources
import re
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.agent_api import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import raise_on_dashboard_failure
from dbt_charts.core.compile.config import load_project_sources
from dbt_charts.core.compile.models.board.authored import AuthoredBoard

# ---------------------------------------------------------------------------
# Source path (mirrors dbt_charts.ai.prompts.read_syntax_guide)
# ---------------------------------------------------------------------------

_SYNTAX_FILE = Path(
    str(importlib.resources.files("dbt_charts") / "DBT_CHARTS_SYNTAX.md")
)

# ---------------------------------------------------------------------------
# Top-level keys that Board (extra="forbid") accepts — derived from the model
# so a new board field cannot silently misclassify a fence as an inline chart
# snippet. `theme` is desugared before validation and so is not a model field.
# ---------------------------------------------------------------------------


_BOARD_KEYS = frozenset(AuthoredBoard.model_fields) | {"theme"}

# ---------------------------------------------------------------------------
# Fence extraction: only ```yaml fences (not ```yaml-schema, ```bash, etc.)
# ---------------------------------------------------------------------------

# Matches ```yaml on its own line, at any indentation, and closes on a fence at
# that same indentation. Does NOT match ```yaml-schema or ```yaml-anything (the
# \s*$ ensures the language tag ends immediately).
#
# The indentation is load-bearing, not cosmetic: a fence nested in a numbered
# list closes on an indented ```, and a column-0-only pattern runs straight
# past it into the prose below. That body then fails to parse, and the
# placeholder skip swallowed it — so an indented fence was silently uncovered.
_FENCE_RE = re.compile(
    r"^(?P<indent>[ \t]*)```yaml[ \t]*$\n(?P<body>.*?)\n?^(?P=indent)```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def _extract_fenced_blocks(md_text: str) -> list[tuple[int, str]]:
    """Return (start_line, body) for every ```yaml fence in md_text."""
    results: list[tuple[int, str]] = []
    for m in _FENCE_RE.finditer(md_text):
        start_line = md_text[: m.start()].count("\n") + 1
        results.append((start_line, textwrap.dedent(m.group("body"))))
    return results


# ---------------------------------------------------------------------------
# Scaffold: turn a fence fragment into a validatable Board dict
# ---------------------------------------------------------------------------


def _has_placeholder(obj: Any, depth: int = 0) -> bool:
    """Return True if obj contains a `...` placeholder, as a key or a value.

    `...` in value position is the same illustrative notation as `{...}` in key
    position — a fence saying "and the rest goes here", not a claim about
    shape. Checking only keys let one through to `validate`, where it failed as
    if the page were wrong about the schema.

    `safe_load` yields the plain string `"..."` in every position; a bare
    document-level `...` raises and never reaches here.
    """
    if depth > 10:
        return False
    if obj == "...":
        return True
    if isinstance(obj, dict):
        # Key position needs its own test — the recursion walks values only.
        if "..." in obj:
            return True
        return any(_has_placeholder(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_placeholder(v, depth + 1) for v in obj)
    return False


def _collect_layout_chart_refs(layout_val: Any, depth: int = 0) -> set[str]:
    """Collect string chart IDs referenced inside a layout structure."""
    if depth > 15:
        return set()
    refs: set[str] = set()
    if isinstance(layout_val, str):
        refs.add(layout_val)
    elif isinstance(layout_val, list):
        for item in layout_val:
            refs |= _collect_layout_chart_refs(item, depth + 1)
    elif isinstance(layout_val, dict):
        # grid item: {item: chart_id, width: N}
        item_ref = layout_val.get("item")
        if isinstance(item_ref, str):
            refs.add(item_ref)
        # TabLayout wrapper: {items: [...]}
        items = layout_val.get("items")
        if items is not None:
            refs |= _collect_layout_chart_refs(items, depth + 1)
        # Nested layout keys
        for key in ("rows", "cols", "grid", "tabs"):
            if key in layout_val:
                refs |= _collect_layout_chart_refs(layout_val[key], depth + 1)
    return refs


def _chart_style_board(style: Any) -> dict[str, Any]:
    """Mount a bare ``style:`` fragment on a chart instead of the board.

    ``style`` is a key on both, and the two models disagree on shared names —
    board ``color:`` is a string, a chart's is a ``{static: ...}`` mapping. A
    fence teaching either one is correct; only the mount tells them apart, and
    this corpus ships as raw markdown to agents, so it has no per-fence
    contract carrier to declare it with (``apps/docs`` fences do).

    So both mounts are attempted, and a fence is accepted if either validates.
    Both are real validators, and a fragment that is wrong under both still
    fails with both error sets — but a board-style fence that happens to be
    legal as a chart style would pass here. That is the price of a corpus with
    nowhere to write the declaration down.
    """
    return {
        "title": "_test",
        "source": "_test",
        "queries": {"_q": {"sql": "SELECT 1"}},
        "charts": {
            "_example": {
                "type": "bar",
                "query": "_q",
                "x": "_x",
                "y": "_y",
                "style": style,
            }
        },
        "rows": ["_example"],
    }


def _scaffold_to_board(fence_body: str) -> dict[str, Any] | None:
    """Parse fence_body and return a validatable Board dict, or None to skip.

    Skips blocks that:
    - are not valid YAML
    - are not a top-level mapping (e.g. a bare scalar or list)
    - contain '...' placeholder keys (illustrative notation like {type: bar, ...})

    For blocks that look like inline chart snippets (top-level keys not in
    _BOARD_KEYS), wraps them under a synthetic charts block.

    For Board-compatible fragments, merges into a minimal scaffold and injects
    synthetic queries for any chart that references a query not in the block.
    Injects synthetic charts for any chart ID referenced in the layout structure.
    """
    try:
        data = yaml.safe_load(fence_body)
    except yaml.YAMLError:
        return None  # not valid YAML; skip

    if not isinstance(data, dict):
        return None  # bare scalar or list; skip

    if _has_placeholder(data):
        return None  # illustrative placeholder syntax; skip

    _LAYOUT_KEYS = frozenset({"rows", "cols", "grid", "tabs"})

    board: dict[str, Any]
    unknown_keys = set(data) - _BOARD_KEYS
    if unknown_keys:
        # Inline chart snippet, e.g. {type: area, x: date, y: value}
        board = {
            "title": "_test",
            "source": "_test",
            "queries": {"_q": {"sql": "SELECT 1"}},
            "charts": {"_example": {"query": "_q", **data}},
            "rows": [],
        }
    else:
        # source: "_test" sets default for injected synthetic queries;
        # fences that have their own source: block override it via update() below.
        board = {"title": "_test", "source": "_test"}
        board.update(data)
        # Inject synthetic queries for any chart referencing an unknown query name.
        charts = board.get("charts")
        if isinstance(charts, dict):
            existing = set((board.get("queries") or {}).keys())
            missing: set[str] = set()
            for chart_def in charts.values():
                if isinstance(chart_def, dict):
                    q_ref = chart_def.get("query")
                    if isinstance(q_ref, str) and q_ref not in existing:
                        missing.add(q_ref)
            if missing:
                board.setdefault("queries", {})
                for q_name in missing:
                    board["queries"][q_name] = {"sql": "SELECT 1"}

        # Inject synthetic charts + queries for any chart ID referenced in the
        # layout that has no entry in board["charts"].  Layout-only examples in
        # docs/layout.md name chart IDs like "rev_chart" without defining them.
        existing_charts = set((board.get("charts") or {}).keys())
        layout_refs: set[str] = set()
        for lk in _LAYOUT_KEYS:
            if lk in board:
                layout_refs |= _collect_layout_chart_refs(board[lk])
        missing_charts = layout_refs - existing_charts
        if missing_charts:
            board.setdefault("charts", {})
            board.setdefault("queries", {})
            for chart_name in missing_charts:
                q_name = f"_q_{chart_name}"
                board["queries"][q_name] = {"sql": "SELECT 1"}
                board["charts"][chart_name] = {
                    "query": q_name,
                    "type": "bar",
                    "x": "_x",
                    "y": "_y",
                }

    # Board requires at least one layout key or a non-empty text field.
    if not board.get("text") and not (_LAYOUT_KEYS & set(board)):
        board["rows"] = []

    return board


# ---------------------------------------------------------------------------
# Parametrize: walk every .md file in the corpus at collection time
# ---------------------------------------------------------------------------


# `files()` returns a Traversable, a protocol with no `glob`/`stem` — see
# tests/conftest.py's NoGlobTraversable, which exists to catch exactly this
# Path-only sugar. tests/agent_api/test_skills.py resolves the same directory
# the same way.
_SKILLS_DIR = Path(str(importlib.resources.files("dbt_charts") / "ai" / "skills"))

# ```yaml-schema fences: annotated schema catalogs, not runnable examples — a
# `cache:` block lists four mutually-exclusive scalars under one key. No
# scaffold can validate one, so they are counted rather than pattern-excluded:
# an exclusion nothing counts is an exclusion nobody revisits. Ratchets down.
MAX_SCHEMA_FENCES = 5

# Fences skipped as illustrative rather than concrete (a `...` placeholder, or a
# body that is not a mapping). Counted so the skips cannot quietly grow into the
# coverage this file claims. Ratchets *down* only. Unparseable YAML is NOT in
# this budget — see the hard zero below.
MAX_PLACEHOLDER_FENCES = 2

# Floors on real coverage: without them, a change that stops discovering fences
# leaves every other check trivially satisfied.
MIN_SYNTAX_FENCES = 27
MIN_SKILL_FENCES = 4


def _label(path: Path) -> str:
    """22 of the 23 corpus files are named SKILL.md; the directory is the name."""
    return path.name if path == _SYNTAX_FILE else f"skills/{path.parent.name}"


def _corpus_files() -> list[Path]:
    """Every wheel-shipped markdown file whose YAML fences agents read as law."""
    return [_SYNTAX_FILE, *sorted(_SKILLS_DIR.glob("*/SKILL.md"), key=str)]


def _collect_corpus_blocks() -> list[tuple[str, int, str]]:
    """Return (label, start_line, fence_body) for every ```yaml block."""
    results: list[tuple[str, int, str]] = []
    for path in _corpus_files():
        label = _label(path)
        for start_line, body in _extract_fenced_blocks(
            path.read_text(encoding="utf-8")
        ):
            results.append((label, start_line, body))
    return results


_CORPUS_BLOCKS = _collect_corpus_blocks()
_SCHEMA_OPENER = r"^(?P<indent>[ \t]*)```yaml-schema[ \t]*$"
_SCHEMA_FENCE_RE = re.compile(_SCHEMA_OPENER, re.MULTILINE)


def test_schema_fence_count_does_not_rise() -> None:
    """```yaml-schema is the one uncompiled fence class. Keep it visible.

    Scans every corpus file, not just the syntax doc: a yaml-schema fence in a
    SKILL.md would otherwise be neither compiled nor counted — the exact
    pattern-exclusion this ratchet exists to retire, one directory over.
    """
    found = [
        line
        for path in _corpus_files()
        for line in _SCHEMA_FENCE_RE.findall(path.read_text(encoding="utf-8"))
    ]

    assert len(found) <= MAX_SCHEMA_FENCES


_SCHEMA_BODY_RE = re.compile(
    _SCHEMA_OPENER + r"\n(?P<body>.*?)\n?^(?P=indent)```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
_LAYOUT_LIST_KEYS = frozenset({"rows", "cols"})


def _layout_scalars(node: Any, depth: int = 0) -> list[Any]:
    """Non-string scalars sitting in a `rows:`/`cols:` list, at any depth."""
    if depth > 12:
        return []
    found: list[Any] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _LAYOUT_LIST_KEYS and isinstance(value, list):
                found += [
                    item for item in value if not isinstance(item, (str, dict, list))
                ]
            found += _layout_scalars(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            found += _layout_scalars(item, depth + 1)
    return found


def test_schema_fences_hold_the_layout_item_shape() -> None:
    """A yaml-schema fence is counted, never compiled — so the `cols:` half of
    the span fix lives somewhere no validator reaches.

    A full scaffold cannot run on these (they concatenate mutually-exclusive
    variants by design), but the one shape that went wrong is checkable without
    one: a `rows:`/`cols:` entry is a chart name or a nested layout, never a
    bare number. `cols: [big_chart, 2]` is exactly this violation.
    """
    offenders = []
    for path in _corpus_files():
        text = path.read_text(encoding="utf-8")
        for match in _SCHEMA_BODY_RE.finditer(text):
            line = text[: match.start()].count("\n") + 1
            try:
                parsed = yaml.safe_load(textwrap.dedent(match.group("body")))
            except yaml.YAMLError:
                continue
            for scalar in _layout_scalars(parsed):
                offenders.append(
                    f"{_label(path)}:{line}: {scalar!r} in a rows/cols list"
                )

    assert offenders == [], (
        "A rows:/cols: entry is a chart name or a nested layout — never a bare "
        "number. A number there does not validate:\n" + "\n".join(offenders)
    )


# Claims the shape guard cannot see: they lived in `#` comments and prose that
# `safe_load` discards. Each is pinned as it was actually spelled, never as the
# general term — "column span" is the correct name for a grid item's span
# (`width: 8  # alias for col_span`), so banning it outright would fail CI on a
# true sentence.
_RETIRED_CLAIMS = (
    "integer weight",
    "fractional column",
    "= column span",
)


def test_no_corpus_file_repeats_a_retired_layout_claim() -> None:
    """`cols:` never took a span number, and a bare `width:` is pixels rather
    than a share — both were stated in prose and comments, where the fence
    guards cannot reach.
    """
    offenders = [
        f"{_label(path)}:{number}: {line.strip()}"
        for path in _corpus_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for claim in _RETIRED_CLAIMS
        if claim in line.lower()
    ]

    assert offenders == [], "\n".join(offenders)


def test_corpus_fence_counts_do_not_fall() -> None:
    syntax = sum(1 for label, _, _ in _CORPUS_BLOCKS if label == _SYNTAX_FILE.name)
    skills = sum(1 for label, _, _ in _CORPUS_BLOCKS if label.startswith("skills/"))

    assert syntax >= MIN_SYNTAX_FENCES
    assert skills >= MIN_SKILL_FENCES


def test_placeholder_skip_count_does_not_rise() -> None:
    """A skip is invisible in a passing run; this is what makes it countable."""
    skipped = [
        f"{label}:{line}"
        for label, line, body in _CORPUS_BLOCKS
        if _scaffold_to_board(body) is None
    ]

    assert len(skipped) <= MAX_PLACEHOLDER_FENCES, skipped


def test_no_corpus_fence_is_unparseable_yaml() -> None:
    """Distinct from the placeholder budget: an illustrative `...` is a choice,
    YAML that does not parse is a typo in a file agents read as law. No budget.
    """
    broken = []
    for label, line, body in _CORPUS_BLOCKS:
        try:
            yaml.safe_load(body)
        except yaml.YAMLError as exc:
            broken.append(f"{label}:{line}: {exc}")

    assert broken == []


def test_docs_corpus_uses_runtime_jinja_variable_scope() -> None:
    """Docs should teach the same bare variable scope the SQL renderer provides."""
    offenders = []
    text = _SYNTAX_FILE.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if "{{ variables." in line:
            offenders.append(f"{_SYNTAX_FILE.name}:{line_no}: {line.strip()}")

    assert offenders == []


# ---------------------------------------------------------------------------
# Parametrized regression test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "start_line", "fence_body"),
    _CORPUS_BLOCKS,
    ids=[f"{fn}:{ln}" for fn, ln, _ in _CORPUS_BLOCKS],
)
def test_corpus_block_compiles(
    filename: str,
    start_line: int,
    fence_body: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every ```yaml block in the syntax guide must pass dct validate and
    restate no default: the corpus is what agents copy."""
    from dbt_charts.agent_api.validate import validate

    parsed = yaml.safe_load(fence_body)
    if isinstance(parsed, dict) and set(parsed) == {"sources"}:
        # A `sources:` registry is a dbt_charts.yml document, not a board:
        # load it the way the engine does. `env_var()` refs need a value to
        # render, as they would on the user's machine.
        for name in re.findall(r"env_var\('([A-Za-z_][A-Za-z0-9_]*)'\)", fence_body):
            monkeypatch.setenv(name, "_test")
        (tmp_path / "dbt_charts.yml").write_text(fence_body)
        load_project_sources(local_project(tmp_path))
        return

    board_dict = _scaffold_to_board(fence_body)
    if board_dict is None:
        pytest.skip(
            f"{filename}:{start_line} — not a concrete example (placeholder/non-dict YAML)"
        )

    candidates = [board_dict]
    parsed = yaml.safe_load(fence_body)
    if isinstance(parsed, dict) and set(parsed) == {"style"}:
        candidates.append(_chart_style_board(parsed["style"]))

    failures: list[str] = []
    for candidate in candidates:
        board_yaml = yaml.dump(candidate, allow_unicode=True)
        board_file = tmp_path / "board.yml"
        board_file.write_text(board_yaml)

        result = validate(board_file, project=local_project(tmp_path))
        if result.success:
            restated = [
                w.message
                for w in result.warnings
                if w.code.startswith("WARN-RESTATED-")
            ]
            assert not restated, (
                f"{filename}:{start_line} restates a default the engine already "
                f"supplies; agents copy the verbose spelling:\n" + "\n".join(restated)
            )
            return
        errors = "\n".join(f"  [{e.path or '?'}] {e.message}" for e in result.errors)
        failures.append(
            f"{errors}\n\nScaffolded board:\n{textwrap.indent(board_yaml, '  ')}"
        )

    joined = "\n\n--- next mount ---\n\n".join(failures)
    raise AssertionError(
        f"\n\n{filename}:{start_line} — dct validate failed under "
        f"{len(candidates)} mount(s):\n{joined}"
    )


def _custom_board_fences() -> list[tuple[str, int, str]]:
    """Corpus fences that are complete boards drawing a ``type: custom`` chart."""
    found = []
    for label, line, body in _CORPUS_BLOCKS:
        parsed = yaml.safe_load(body)
        if isinstance(parsed, dict) and {"queries", "charts", "rows"} <= set(parsed):
            if "type: custom" in body:
                found.append((label, line, body))
    return found


_CUSTOM_BOARD_FENCES = _custom_board_fences()


def test_custom_board_fences_are_discovered() -> None:
    assert _CUSTOM_BOARD_FENCES


@pytest.mark.parametrize(
    ("filename", "start_line", "fence_body"),
    _CUSTOM_BOARD_FENCES,
    ids=[f"{fn}:{ln}" for fn, ln, _ in _CUSTOM_BOARD_FENCES],
)
def test_custom_board_fence_renders(
    filename: str,
    start_line: int,
    fence_body: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """Validation cannot see a template that fails while drawing (a link
    channel the template never reads through ``href``), so render it."""
    board_file = tmp_path / "board.yml"
    board_file.write_text(fence_body)
    project = local_project(tmp_path)
    session = ProjectSession.from_project(project)
    try:
        rendered = session.render_board(
            board=project.path_for_fspath(board_file).read_board(), format="html"
        )
        raise_on_dashboard_failure(rendered)
        assert rendered.status == "ok", rendered.chart_errors
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Self-test: confirm the plumbing rejects a deliberately-broken fence
# ---------------------------------------------------------------------------


def test_self_test_detects_drift(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Regression guard: validate() must reject the known-bad KPI title drift.

    If this test fails (validate() returned success on a broken board), the test
    plumbing is broken — _scaffold_to_board or validate() is no longer exercising
    the validator that dct validate runs.
    """
    from dbt_charts.agent_api.validate import validate

    broken_board = yaml.dump(
        {
            "title": "_test",
            "queries": {"_q": {"sql": "SELECT 1"}},
            "charts": {
                "_c": {
                    "query": "_q",
                    "type": "kpi",
                    "title": "Broken",  # known drift: kpi rejects title:, requires label:
                }
            },
        }
    )
    board_file = tmp_path / "board.yml"
    board_file.write_text(broken_board)

    result = validate(board_file, project=local_project(tmp_path))
    assert not result.success, (
        "Self-test FAILED: validate() accepted a KPI board with title: instead of label:. "
        "Either the validator no longer rejects this (update the test and the docs), "
        "or the test scaffolding is broken."
    )
