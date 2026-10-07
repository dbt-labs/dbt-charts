"""Board ``text:`` reads query results through ``queries.<q>``, end to end."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render.board_variables import board_data_provider
from dbt_charts.core.render.boards import resolve_display_text_from_provider
from dbt_charts.core.render.render_result import RenderResult
from dbt_charts.core.render.sizing import get_markdown_text_height

_QUERIES = """
queries:
  summary:
    columns: [net_revenue, vs_plan, churn]
    values:
      - [4210000.5, 0.042, null]
  top_regions:
    columns: [region, revenue]
    values:
      - [EMEA, 1800000]
      - [AMER, 1500000]
      - [APAC, 910000]
  empty_q:
    columns: [x]
    values: []
  items_q:
    columns: [items, values]
    values:
      - [widgets, 5]
  values:
    columns: [v]
    values:
      - [7]
  _x:
    columns: [v]
    values:
      - [8]
  _results:
    columns: [_cells]
    values:
      - [9]
"""


def _executor(yaml: str, local_project: Callable[..., Project]) -> Executor:
    result = compile(yaml)
    assert result.success, result.errors
    assert result.board is not None
    return Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )


def _render(yaml: str, local_project: Callable[..., Project]) -> RenderResult:
    executor = _executor(yaml, local_project)
    return render(executor.board, executor, format="svg")


def _text(yaml: str, local_project: Callable[..., Project]) -> str:
    result = _render(yaml, local_project)
    assert result.board_error is None, result.board_error
    assert isinstance(result.output, str)
    plain = html.unescape(re.sub(r"<[^>]+>", " ", result.output))
    return re.sub(r"\s+", " ", plain)


def _board_text(text_block: str) -> str:
    indented = "\n".join(f"  {line}" for line in text_block.strip().splitlines())
    return f"text: |\n{indented}\n{_QUERIES}"


def _error_code(text_block: str, local_project: Callable[..., Project]) -> str:
    result = _render(_board_text(text_block), local_project)
    assert result.board_error is not None
    return result.board_error.code


def test_root_board_scalar_is_formatted_with_the_injected_format(
    local_project: Callable[..., Project],
) -> None:
    text = _text(
        _board_text(
            "Net **{{ queries.summary.one.net_revenue | format('currency') }}**."
        ),
        local_project,
    )
    assert "$4.21mn" in text


def test_full_example_renders_every_form(
    local_project: Callable[..., Project],
) -> None:
    text = _text(
        _board_text(
            """
{% set s = queries.summary.one %}
Net {{ s.net_revenue | format('currency') }},
{{ 'ahead of' if s.vs_plan >= 0 else 'behind' }} plan by
{{ s.vs_plan | abs | format('percent') }}.

Churn: {{ 'not available' if s.churn is none else s.churn | format('percent') }}.

{% for r in queries.top_regions.rows %}
- **{{ r.region }}**: {{ r.revenue | format('currency') }}
{% endfor %}

Leading: {{ queries.top_regions.rows | map(attribute='region') | join(', ') }}.
{{ queries.top_regions.rows | length }} regions; {{ queries.top_regions.rows[0].region }} leads.
"""
        ),
        local_project,
    )
    assert "Net $4.21mn, ahead of plan by 4.2%" in text
    assert "Churn: not available." in text
    assert "EMEA : $1.8mn" in text or "EMEA: $1.8mn" in text
    assert "Leading: EMEA, AMER, APAC." in text
    assert "3 regions; EMEA leads." in text


def test_unformatted_value_prints_python_str(
    local_project: Callable[..., Project],
) -> None:
    text = _text(_board_text("{{ queries.summary.one.net_revenue }}"), local_project)
    assert "4210000.5" in text


def test_columns_accessor_and_bracket_access(
    local_project: Callable[..., Project],
) -> None:
    text = _text(
        _board_text(
            "{{ queries.top_regions.columns | join('+') }} "
            "{{ queries['top_regions'].rows[1]['region'] }}"
        ),
        local_project,
    )
    assert "region+revenue AMER" in text


def test_column_named_items_renders_its_cell(
    local_project: Callable[..., Project],
) -> None:
    text = _text(
        _board_text("{{ queries.items_q.one.items }}-{{ queries.items_q.one.values }}"),
        local_project,
    )
    assert "widgets-5" in text


def test_query_named_like_a_dict_method_renders_its_rows(
    local_project: Callable[..., Project],
) -> None:
    text = _text(_board_text("A{{ queries.values.one.v }}B"), local_project)
    assert "A7B" in text


def test_underscore_query_name_points_to_subscript(
    local_project: Callable[..., Project],
) -> None:
    assert "A8B" in _text(_board_text("A{{ queries['_x'].one.v }}B"), local_project)
    result = _render(_board_text("A{{ queries._x.one.v }}B"), local_project)
    assert result.board_error is not None
    assert result.board_error.code == "ERR-TEXT-QUERY-ACCESS"
    assert "queries['_x']" in result.board_error.message


@pytest.mark.parametrize(
    "template",
    [
        "A{{ queries._results.one['_cells'] }}B",
        "A{{ queries['_results'].one._cells }}B",
        "A{{ queries['_results']._rows }}B",
    ],
)
def test_underscore_names_matching_internal_slots_raise(
    template: str, local_project: Callable[..., Project]
) -> None:
    result = _render(_board_text(template), local_project)
    assert result.board_error is not None
    assert result.board_error.code == "ERR-TEXT-QUERY-ACCESS"


def test_nested_board_text_reads_its_own_queries(
    local_project: Callable[..., Project],
) -> None:
    text = _text(
        _QUERIES
        + """
rows:
  - title: nested
    text: 'Leader {{ queries.top_regions.rows[0].region }}'
    cols: []
""",
        local_project,
    )
    assert "Leader EMEA" in text


@pytest.mark.parametrize(
    ("text_block", "code"),
    [
        ("{{ queries.top_regions.one.region }}", "ERR-TEXT-QUERY-ROW-COUNT"),
        ("{{ queries.empty_q.one.x }}", "ERR-TEXT-QUERY-ROW-COUNT"),
        ("{{ queries.summary.one.net_revnue }}", "ERR-TEXT-QUERY-UNKNOWN-COLUMN"),
        ("{{ queries.empty_q.rows[0].x }}", "ERR-TEXT-QUERY-INDEX"),
        ("{{ queries.empty_q.rows[-1].x }}", "ERR-TEXT-QUERY-INDEX"),
        ("{{ queries.top_regions.rows[3].region }}", "ERR-TEXT-QUERY-INDEX"),
        ("{{ queries.top_regions.columns[5] }}", "ERR-TEXT-QUERY-INDEX"),
        ("{{ (queries.empty_q.rows | first).x }}", "ERR-TEXT-EMPTY-SEQUENCE"),
        ("{{ (queries.empty_q.rows | last).x }}", "ERR-TEXT-EMPTY-SEQUENCE"),
        ("{{ (queries.empty_q.rows | list | first).x }}", "ERR-TEXT-EMPTY-SEQUENCE"),
        ("{{ queries.empty_q.rows | max(attribute='x') }}", "ERR-TEXT-EMPTY-SEQUENCE"),
        ("{{ queries.empty_q.rows | min(attribute='x') }}", "ERR-TEXT-EMPTY-SEQUENCE"),
        ("{{ queries.empty_q.rows | random }}", "ERR-TEXT-EMPTY-SEQUENCE"),
        ("{{ queries.summary.net_revenue }}", "ERR-TEXT-QUERY-ACCESS"),
        ("{{ queries.summary }}", "ERR-TEXT-QUERY-ACCESS"),
        ("{{ 'x' if queries.summary.one.churn > 0.05 }}", "ERR-TEXT-NULL-COMPARISON"),
        ("{{ '%.1f' | format(queries.summary.one.vs_plan) }}", "ERR-TEXT-FORMAT-USAGE"),
        ("{{ queries.summary.one.vs_plan | format }}", "ERR-TEXT-FORMAT-USAGE"),
        ("{{ 1 | format('percent', 'x') }}", "ERR-TEXT-FORMAT-USAGE"),
        ("{{ 'Hello %s' | format('Bob') }}", "ERR-TEXT-FORMAT-USAGE"),
        ("{{ '%s rows' | format('12') }}", "ERR-TEXT-FORMAT-USAGE"),
        ("{{ '%s items' | format('5') }}", "ERR-TEXT-FORMAT-USAGE"),
        ("{{ 'Hi %s' | format('d') }}", "ERR-TEXT-FORMAT-USAGE"),
        ("{{ 1234.5 | format('bogus') }}", "ERR-TEXT-FORMAT-USAGE"),
        ("{{ queries.top_regions.rows }}", "ERR-TEXT-QUERY-ACCESS"),
        ("{{ queries.summary.one._fivetran_synced }}", "ERR-TEXT-QUERY-UNKNOWN-COLUMN"),
        ("{{ queries.summary.one }}", "ERR-TEXT-QUERY-ACCESS"),
    ],
)
def test_render_errors_carry_their_own_code(
    text_block: str, code: str, local_project: Callable[..., Project]
) -> None:
    assert _error_code(text_block, local_project) == code


def test_format_kind_mismatch_keeps_the_existing_error(
    local_project: Callable[..., Project],
) -> None:
    assert (
        _error_code(
            "{{ queries.summary.one.net_revenue | format('%H:%M') }}", local_project
        )
        == "ERR-TABLE-FORMAT-KIND-MISMATCH"
    )


def test_unscoped_provider_raises(local_project: Callable[..., Project]) -> None:
    executor = _executor(
        _board_text("{{ queries.summary.one.net_revenue }}"), local_project
    )
    with pytest.raises(RuntimeError, match="no data provider is scoped"):
        resolve_display_text_from_provider(
            "{{ queries.summary.one.net_revenue }}",
            {},
            resolved_style=executor.board.resolved_style,
        )


def test_fenced_code_is_left_alone(local_project: Callable[..., Project]) -> None:
    executor = _executor(_board_text("x"), local_project)
    with board_data_provider(executor):
        out = resolve_display_text_from_provider(
            "```\n{{ queries[name] }}\n```\n{{ queries.summary.one.vs_plan }}",
            {},
            resolved_style=executor.board.resolved_style,
        )
    assert out == "```\n{{ queries[name] }}\n```\n0.042"


def test_measured_height_equals_painted_height_for_a_rows_list(
    local_project: Callable[..., Project],
) -> None:
    from dbt_charts.core.render.boards import _render_text_svg
    from dbt_charts.core.render.prose import plan_board_prose

    text = (
        "{% for r in queries.top_regions.rows %}\n"
        "- {{ r.region }}: {{ r.revenue | format('currency') }}\n"
        "{% endfor %}"
    )
    executor = _executor(_board_text(text), local_project)
    board = executor.board
    executor.execute_query("top_regions", {})
    style = board.resolved_style
    with board_data_provider(executor):
        plan_board_prose(board, {}, 800.0)
        measured = get_markdown_text_height(
            board.text,
            400.0,
            {},
            text_style=style.text,
            resolved_style=style,
            base_dir=None,
            plan=board.prose_plan,
        )
        _, painted = _render_text_svg(
            board.text,
            {},
            400.0,
            resolved_style=style,
            text_style=style.text,
            base_dir=None,
            error_collector=None,
            plan=board.prose_plan,
            painted_canvas=None,
        )
    assert measured == painted
    assert measured > 0


@pytest.mark.parametrize(
    ("text_block", "code"),
    [
        ("{{ queries.top_regions.one.region }}", "ERR-TEXT-QUERY-ROW-COUNT"),
        ("{{ queries.summary.one.net_revnue }}", "ERR-TEXT-QUERY-UNKNOWN-COLUMN"),
        ("{{ queries.summary.net_revenue }}", "ERR-TEXT-QUERY-ACCESS"),
        ("{{ 'x' if queries.summary.one.churn > 0.05 }}", "ERR-TEXT-NULL-COMPARISON"),
    ],
)
def test_codes_survive_the_lenient_sizing_and_prose_sites(
    text_block: str, code: str, local_project: Callable[..., Project]
) -> None:
    from dbt_charts.core.diagnostics.base import DbtChartsError
    from dbt_charts.core.render.prose import plan_board_prose

    executor = _executor(_board_text(text_block), local_project)
    board = executor.board
    for name in ("summary", "top_regions"):
        executor.execute_query(name, {})
    style = board.resolved_style
    with board_data_provider(executor):
        with pytest.raises(DbtChartsError) as sizing:
            get_markdown_text_height(
                board.text,
                400.0,
                {},
                text_style=style.text,
                resolved_style=style,
                base_dir=None,
                plan=None,
            )
        with pytest.raises(DbtChartsError) as prose:
            plan_board_prose(board, {}, 800.0)
    assert sizing.value.code is not None and sizing.value.code.code == code
    assert prose.value.code is not None and prose.value.code.code == code


def test_nested_board_error_carries_its_code(
    local_project: Callable[..., Project],
) -> None:
    result = _render(
        _QUERIES
        + """
rows:
  - title: nested
    text: '{{ queries.top_regions.one.region }}'
    cols: []
""",
        local_project,
    )
    assert result.board_error is not None
    assert result.board_error.code == "ERR-TEXT-QUERY-ROW-COUNT"


_ALIASES: dict[str, str | FormatConfig] = {
    "mine": "currency",
    "eur": FormatConfig(prefix="EUR ", suffix=" net"),
    "analytic_alias": FormatConfig(spec="currency", notation="analytic"),
}


@pytest.mark.parametrize(
    ("value", "spec", "expected"),
    [
        (4210000.5, "currency", "$4.21mn"),
        (910000, "number", "910k"),
        (4210000.5, "$.3~s", "$4.21mn"),
        (0.042, "percent", "4.2%"),
        (1234.5, ",.0f", "1,235"),
        (1234.5, "mine", "$1.23k"),
        (4210000.5, "analytic_alias", "$4.21 M"),
        (Decimal("1234.5"), ",.0f", "1,235"),
        (None, "percent", "—"),
        ("2026-01-05", "%b %Y", "Jan 2026"),
    ],
)
def test_format_accepts_presets_aliases_d3_and_strftime(
    value: object, spec: str, expected: str
) -> None:
    from dbt_charts.core.compile.template.display_text import _text_format_filter

    assert _text_format_filter(_ALIASES)(value, spec) == expected


def test_affix_only_alias_keeps_its_affixes_with_narrative_notation() -> None:
    from dbt_charts.core.compile.template.display_text import _text_format_filter

    out = _text_format_filter(_ALIASES)(1234.5, "eur")
    assert out == "EUR 1.23k net"


def test_format_error_names_the_problem() -> None:
    from dbt_charts.core.compile.template.display_text import _text_format_filter
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    fmt = _text_format_filter(None)
    for args, detail in [
        ((1,), "no spec"),
        ((1, "bogus"), "'bogus'"),
        ((1, "percent", 2), "1 extra"),
    ]:
        with pytest.raises(ChartDataError) as exc:
            fmt(*args)
        assert detail in str(exc.value)


@pytest.mark.parametrize(
    ("value", "type_name"),
    [
        (time(12, 30), "time"),
        (True, "bool"),
        ([1], "list"),
        ({"a": 1}, "dict"),
        (timedelta(hours=1), "timedelta"),
    ],
)
def test_format_rejects_values_that_are_not_numbers_or_dates(
    value: object, type_name: str
) -> None:
    from dbt_charts.core.compile.template.display_text import _text_format_filter
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    with pytest.raises(ChartDataError) as exc:
        _text_format_filter(None)(value, "%H:%M")
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TEXT-FORMAT-USAGE"
    assert f"value of type '{type_name}' is not a number or date" in str(exc.value)


_FLAG_QUERIES = (
    _QUERIES + "  flags:\n    columns: [has_top]\n    values:\n      - [false]\n"
)
_RAISING = "text: 'Value {{ queries.empty_q.one.x }}'"


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(
            "variables:\n  show:\n    input: checkbox\n    default: false\n"
            "rows:\n  - text: shown\n"
            f'  - visible: "{{{{ show }}}}"\n    {_RAISING}\n',
            id="hidden-section",
        ),
        pytest.param(
            "variables:\n  show:\n    input: checkbox\n    default: false\n"
            "rows:\n  - text: shown\n  - cols:\n"
            f'      - visible: "{{{{ show }}}}"\n        {_RAISING}\n'
            "      - text: fine\n",
            id="hidden-cols-section",
        ),
        pytest.param(
            "rows:\n  - text: shown\n  - tabs:\n      items:\n"
            "        - title: First\n          text: fine\n"
            f"        - title: Second\n          {_RAISING}\n",
            id="inactive-tab",
        ),
        pytest.param(
            "rows:\n  - text: shown\n  - visible:\n      query: flags\n"
            f"      column: has_top\n    {_RAISING}\n",
            id="query-gated-section",
        ),
    ],
)
def test_text_that_is_not_drawn_never_fails_the_board(
    layout: str, local_project: Callable[..., Project]
) -> None:
    result = _render(_FLAG_QUERIES + layout, local_project)
    assert result.board_error is None, result.board_error
