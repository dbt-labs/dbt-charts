"""Drift guard: every authored Pydantic enum is mentioned in DBT_CHARTS_SYNTAX.md.

The canonical YAML syntax reference lives in ``dbt_charts/DBT_CHARTS_SYNTAX.md``
and is currently hand-curated. Until codegen takes over (see the
core-spec-reference initiative), this test fires whenever a
``AUTHORED_CHART_TYPE_TAGS`` or ``VariableInputType`` value is added without a matching
mention in the doc.
"""

from __future__ import annotations

import importlib.resources
import re
from typing import get_args, get_origin

import pytest

from dbt_charts.core.compile.models.chart.authored import AUTHORED_CHART_TYPE_TAGS
from dbt_charts.core.compile.models.query.authored import RestMethod
from dbt_charts.core.compile.models.variable.authored import VariableInputType
from dbt_charts.core.compile.parse.yaml_error_formatter import (
    _CHART_SHAPE_RECIPES,
    _UNSUPPORTED_CHART_SHAPES,
)

_SYNTAX_TEXT = (
    importlib.resources.files("dbt_charts") / "DBT_CHARTS_SYNTAX.md"
).read_text(encoding="utf-8")


@pytest.mark.parametrize("value", sorted(AUTHORED_CHART_TYPE_TAGS))
def test_chart_type_documented_in_syntax(value: str) -> None:
    """Every authorable chart type must appear verbatim in DBT_CHARTS_SYNTAX.md."""
    assert value in _SYNTAX_TEXT, (
        f"Authorable chart type {value!r} is not documented in dbt_charts/DBT_CHARTS_SYNTAX.md. "
        "Add a mention in the ## Charts section (or mark it internal explicitly)."
    )


def test_sources_registry_has_its_own_topic() -> None:
    """`dct docs sources` must exist: the `sources:` registry is the first thing
    a non-dbt project writes, and `## Queries` only names it in passing."""
    assert "\n## Sources\n" in _SYNTAX_TEXT
    section = _SYNTAX_TEXT.split("\n## Sources\n", 1)[1].split("\n## ", 1)[0]
    for source_type in ("duckdb", "postgres", "bigquery", "dbt_profile", "csv"):
        assert f"type: {source_type}" in section, f"no `type: {source_type}` example"


def test_format_object_form_is_documented_in_syntax() -> None:
    """A format slot accepts a `{spec, prefix, suffix, notation}` object, not just a
    string or a named alias.
    """
    section = _SYNTAX_TEXT.split("\n## Charts\n", 1)[1].split("\n## ", 1)[0]
    assert "prefix" in section and "suffix" in section and "notation" in section
    assert "style.formats" in section


@pytest.mark.parametrize("value", sorted(set(get_args(VariableInputType))))
def test_variable_input_type_documented_in_syntax(value: str) -> None:
    """Every VariableInputType literal must appear in DBT_CHARTS_SYNTAX.md."""
    assert value in _SYNTAX_TEXT, (
        f"VariableInputType {value!r} is not documented in dbt_charts/DBT_CHARTS_SYNTAX.md. "
        "Add it to the ## Variables section."
    )


@pytest.mark.parametrize("value", sorted(set(get_args(RestMethod))))
def test_rest_method_documented_in_syntax(value: str) -> None:
    """Every RestMethod literal must appear in DBT_CHARTS_SYNTAX.md."""
    assert value in _SYNTAX_TEXT, (
        f"RestMethod {value!r} is not documented in dbt_charts/DBT_CHARTS_SYNTAX.md. "
        "Add it to the ## Queries section (HTTP)."
    )


# ---------------------------------------------------------------------------
# Path-resolution + named-field guards
# ---------------------------------------------------------------------------
#
# The 2026-08-10 syntax-doc audit found stale dotted style paths and whole
# features with zero mentions. The guards below close both gaps: every dotted
# `style.*` path cited anywhere in the doc must resolve against the live model
# graph, and every named authored feature must be mentioned.

_STYLE_PATH_RE = re.compile(r"\bstyle\.[a-z0-9_]+(?:\.[a-z0-9_]+)*")


def _style_roots() -> list[type]:
    """Every model a doc-cited `style.` path may resolve against.

    The theme root Style (theme cascade paths like `style.charts.min_height`),
    the board-level StylePatch, and each chart family's own style patch type.
    """
    from pydantic import BaseModel

    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.models.chart.authored import AuthoredChart
    from dbt_charts.core.compile.models.style.theme import Style

    def models_in(annotation: object) -> list[type]:
        found = []
        stack = [annotation]
        while stack:
            ann = stack.pop()
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                found.append(ann)
            else:
                stack.extend(get_args(ann))
        return found

    roots: list[type] = [Style]
    roots += models_in(AuthoredBoard.model_fields["style"].annotation)
    for chart_cls in models_in(AuthoredChart):
        if "style" in chart_cls.model_fields:
            roots += models_in(chart_cls.model_fields["style"].annotation)
    return roots


def _walks(model: type, segments: list[str]) -> bool:
    """True if `segments` is a valid field path from `model`."""
    from pydantic import BaseModel

    if not segments:
        return True
    head, *rest = segments
    if head not in model.model_fields:
        return False
    if not rest:
        return True
    stack = [model.model_fields[head].annotation]
    while stack:
        ann = stack.pop()
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            if _walks(ann, rest):
                return True
        else:
            origin = get_origin(ann)
            args = get_args(ann)
            if origin is dict and args:
                # A dict consumes one arbitrary key segment.
                sub_stack = [args[1]]
                while sub_stack:
                    sub = sub_stack.pop()
                    if isinstance(sub, type) and issubclass(sub, BaseModel):
                        if _walks(sub, rest[1:]):
                            return True
                    else:
                        sub_stack.extend(get_args(sub))
            else:
                stack.extend(args)
    return False


def _cited_style_paths() -> list[str]:
    paths = set()
    for match in _STYLE_PATH_RE.findall(_SYNTAX_TEXT):
        if "." in match:
            paths.add(match)
    return sorted(paths)


@pytest.mark.parametrize("path", _cited_style_paths())
def test_cited_style_path_resolves_against_model_graph(path: str) -> None:
    """Every dotted style.* path cited in the doc must exist on some model.

    Guards the failure mode the 2026-08-10 audit found: stale paths (e.g. a
    `style.kpi.*` sub-block that never existed) surviving in prose, comments,
    and non-compiled fences where the example-compile test can't see them.
    """
    segments = path.split(".")[1:]  # drop the leading "style"
    roots = _style_roots()
    assert any(_walks(root, segments) for root in roots), (
        f"DBT_CHARTS_SYNTAX.md cites {path!r}, which does not resolve against "
        "the theme Style root, the board StylePatch, or any chart family's "
        "style patch. Fix the doc (or the model) — stale dotted paths teach "
        "agents fields that don't exist."
    )


# Interim explicit list (until `exposure: basic` tags exist to derive it):
# authored features the 2026-08-10 audit found entirely undocumented. `foreach`
# was retired from the authored surface in schema 0.4.0 and `allow_html` was
# renamed to `html_policy` in 0.5.0 — their successors are what the doc must
# carry.
_AUDIT_REQUIRED_MENTIONS = [
    "multiples",  # small-multiples partition (chart field)
    "cache",  # board/query cache policy overlay
    "extends",  # board inheritance chain (theme: is sugar for it)
    "html_policy",  # board HTML rendering policy (successor of allow_html)
    "auto_link",  # board-level table auto-linking
    "variant",  # kpi layout variant
    "basemap",  # point_map/bubble_map background layer
    "warnings_ignore",  # per-chart render-warning suppression
    "type: schema",  # dbt source schema query type (was mislabeled internal-only)
]


@pytest.mark.parametrize("mention", _AUDIT_REQUIRED_MENTIONS)
def test_audited_feature_documented_in_syntax(mention: str) -> None:
    """Every feature the audit named must be mentioned in DBT_CHARTS_SYNTAX.md."""
    assert mention in _SYNTAX_TEXT, (
        f"Authored feature {mention!r} is not documented in "
        "dbt_charts/DBT_CHARTS_SYNTAX.md. The 2026-08-10 audit flagged it as "
        "missing; document it in the matching H2 section (or mark the field "
        "internal in the models and drop it from this list)."
    )


_DIAG_CODE_RE = re.compile(r"\b(?:WARN|ERR)-[A-Z0-9]+(?:-[A-Z0-9]+)*")


def _cited_diagnostic_codes() -> list[str]:
    return sorted(set(_DIAG_CODE_RE.findall(_SYNTAX_TEXT)))


@pytest.mark.parametrize("code", _cited_diagnostic_codes())
def test_cited_diagnostic_code_is_registered(code: str) -> None:
    """Every WARN-*/ERR-* code cited in the doc must exist in the registry.

    An agent copying a doc example with an unregistered code ships a board
    that fails to compile (`validate_suppression_codes` hard-rejects unknown
    codes in `warnings_ignore`/`ignore`).
    """
    from dbt_charts.core.diagnostics import REGISTRY

    assert code in REGISTRY.codes(), (
        f"DBT_CHARTS_SYNTAX.md cites diagnostic code {code!r}, which is not in "
        "the registry. Use a registered code (`dct docs errors` / "
        "`dct docs warnings`) — agents copy doc examples verbatim."
    )


def _predefined_number_formats() -> list[tuple[str, str | None]]:
    from dbt_charts.core.text.predefined_formats import (
        PREDEFINED_SPECS,
        PredefinedNumberFormat,
    )

    return [(m.value, PREDEFINED_SPECS.get(m.value)) for m in PredefinedNumberFormat]


@pytest.mark.parametrize(("alias", "spec"), _predefined_number_formats())
def test_number_format_alias_documented_with_its_spec(
    alias: str, spec: str | None
) -> None:
    """Every predefined number-format alias (and its engine-owned d3 spec)
    must appear in the doc's alias catalog — markdown tables are invisible to
    the corpus compile test, so this is their drift guard."""
    assert f"`{alias}`" in _SYNTAX_TEXT, (
        f"Number-format alias {alias!r} is missing from the alias catalog in "
        "dbt_charts/DBT_CHARTS_SYNTAX.md."
    )
    if spec is not None:
        assert f"`{spec}`" in _SYNTAX_TEXT, (
            f"Alias {alias!r}'s engine-owned spec {spec!r} is not in the doc — "
            "the catalog's spec column has drifted from PREDEFINED_SPECS."
        )


# Probe inputs mirroring the catalog's "Renders like" examples. The engine's
# default (analytic-register) rendering for each must appear verbatim in that
# alias's table row — the column is machine-checkable, so check it.
_ALIAS_RENDER_PROBES: dict[str, float | int] = {
    "integer": 12345.6,
    "number": 12400,
    "number_full": 12345.678,
    "currency": 12400,
    "currency_whole": 12345.6,
    "currency_full": 12345.678,
    "percent": 0.42,
    "percent_whole": 0.42,
    "percent_delta": 0.018,
    "percent_number": 148.23,
    "percent_number_delta": 1.8,
    "percentage_points_delta": 1.8,
    "delta": 1234,
    "year": 2026,
}


@pytest.mark.parametrize(("alias", "probe"), sorted(_ALIAS_RENDER_PROBES.items()))
def test_number_format_alias_example_matches_engine(
    alias: str, probe: float | int
) -> None:
    """The catalog row's rendered example must be the engine's actual output
    for the stated input (default register). Guards the failure mode where a
    hand-written example teaches a rendering the engine never produces."""
    from dbt_charts.core.render.format_utils import format_value

    rendered = format_value(probe, alias)
    rows = [
        line for line in _SYNTAX_TEXT.splitlines() if line.startswith(f"| `{alias}` |")
    ]
    assert rows, f"no alias-catalog row found for {alias!r}"
    assert f"`{rendered}`" in rows[0], (
        f"Alias {alias!r}: the engine renders {probe!r} as {rendered!r} on the "
        f"default path, but the doc row does not show it: {rows[0]!r}"
    )


@pytest.mark.parametrize("noun", sorted(_CHART_SHAPE_RECIPES))
def test_chart_shape_recipe_documented_in_syntax(noun: str) -> None:
    """Every composed shape the error formatter names is also in the docs.

    The error map is reactive — it fires only once an author has already guessed
    a `type:` that does not exist. `DBT_CHARTS_SYNTAX.md` is what `dct docs`
    serves, so it is the copy an agent reads *before* authoring. A recipe in one
    and not the other means the shape is discoverable only by failing first.
    """
    assert noun.replace("_", " ") in _SYNTAX_TEXT or noun in _SYNTAX_TEXT, (
        f"Chart shape {noun!r} has a recipe in _CHART_SHAPE_RECIPES but is not "
        "named in dbt_charts/DBT_CHARTS_SYNTAX.md. Add it to the "
        "'Named shapes that have no `type:`' table in the ## Charts section."
    )
    # The recipe's TEXT, not just its noun: editing a recipe value would
    # otherwise leave the doc stale with CI green, and the doc is the copy
    # `dct docs charts` serves — the one an agent actually authors from.
    assert _CHART_SHAPE_RECIPES[noun] in _SYNTAX_TEXT, (
        f"The recipe for {noun!r} differs between _CHART_SHAPE_RECIPES and "
        "dbt_charts/DBT_CHARTS_SYNTAX.md. Regenerate the table."
    )


@pytest.mark.parametrize("noun", sorted(_UNSUPPORTED_CHART_SHAPES))
def test_unsupported_chart_shape_documented_in_syntax(noun: str) -> None:
    """Naming what cannot be drawn is the half a recipe cannot cover."""
    assert noun.replace("_", " ") in _SYNTAX_TEXT or noun in _SYNTAX_TEXT, (
        f"Chart shape {noun!r} is listed unsupported but is not named in "
        "dbt_charts/DBT_CHARTS_SYNTAX.md."
    )
