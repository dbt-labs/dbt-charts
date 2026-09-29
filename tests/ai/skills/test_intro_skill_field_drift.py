"""Drift guard for the field table and the format names in the `intro` skill.

Both are copies of what the board schema and the predefined-format registry
say, and nothing else keeps them in step. A renamed field or format fails
here instead of going stale in the first page an agent reads.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.agent_api.skills import get_skill
from dbt_charts.core.compile.schema.introspection import (
    UNION_ALIAS_VARIANT_NAMES,
    AuthorableSchema,
    SchemaField,
    introspect,
)
from dbt_charts.core.text.predefined_formats import (
    PredefinedNumberFormat,
    PredefinedTimeFormat,
)

SKILL = "intro"


@pytest.fixture(scope="module")
def body() -> str:
    return get_skill(SKILL, surface="cli").body


@pytest.fixture(scope="module")
def schema() -> AuthorableSchema:
    return introspect()


def _fields_section(body: str) -> str:
    start = body.index("### Fields for a typical board")
    end = body.index("### Formats")
    return body[start:end]


def _formats_section(body: str) -> str:
    start = body.index("### Formats")
    end = body.index("### Styling a board")
    return body[start:end]


def _field_paths(body: str) -> list[str]:
    """Every field path in the first column; a row may name several."""
    paths: list[str] = []
    for line in _fields_section(body).splitlines():
        if line.startswith("| `"):
            paths += re.findall(r"`([\w.*]+)`", line.split("|")[1])
    return paths


def _format_names(body: str) -> set[str]:
    return set(re.findall(r"`([a-z_]+)`", _formats_section(body)))


def _resolve(schema: AuthorableSchema, path: str) -> list[SchemaField] | None:
    """Resolve a dotted, `*`-wildcarded authored path against the schema.

    At each `*`, descend into every nested model the previous field(s)
    named — a chart-family union has many variant models (BarChart,
    KpiChart, ...) and a field like `value` may live on only one of them, so
    every part is searched across all models currently in play, not just
    the first match.
    """
    current_models = [schema.root]
    last_matches: list[SchemaField] | None = None
    for part in path.split("."):
        if part == "*":
            if last_matches is None:
                return None
            nested = [n for f in last_matches for n in f.nested_models]
            current_models = list(
                dict.fromkeys(
                    x for n in nested for x in UNION_ALIAS_VARIANT_NAMES.get(n, [n])
                )
            )
            continue
        matches = []
        for model_name in dict.fromkeys(current_models):
            model = schema.models.get(model_name)
            if model is None:
                continue
            for field in model.fields:
                if field.name == part:
                    matches.append(field)
                    break
        if not matches:
            return None
        last_matches = matches
        nested = [n for f in matches for n in f.nested_models]
        current_models = list(
            dict.fromkeys(
                x for n in nested for x in UNION_ALIAS_VARIANT_NAMES.get(n, [n])
            )
        )
    return last_matches


def test_every_field_path_in_the_table_resolves_in_the_schema(
    body: str, schema: AuthorableSchema
) -> None:
    paths = _field_paths(body)
    assert paths, "no field paths found in the Fields table"
    missing = [p for p in paths if _resolve(schema, p) is None]
    assert missing == [], f"intro names field paths not in the schema: {missing}"


def test_every_table_row_names_a_field(body: str) -> None:
    rows = [x for x in _fields_section(body).splitlines() if x.startswith("| `")]
    unread = [x for x in rows if not re.findall(r"`([\w.*]+)`", x.split("|")[1])]
    assert rows and unread == []


def test_format_names_are_the_predefined_formats(body: str) -> None:
    registry = {m.value for m in (*PredefinedNumberFormat, *PredefinedTimeFormat)}
    named = _format_names(body)
    assert named == registry, (
        f"only in intro: {named - registry}, only in registry: {registry - named}"
    )
