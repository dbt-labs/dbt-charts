"""style.columns is styling-only; visibility is the explicit `visible: false`.

style.columns used to double as a show-list: any column not named in it was
removed from the render, so styling only the numeric columns (to add a
`format:`) silently deleted every text column. style.columns now styles the
columns it names and nothing else; `style.columns.<name>.visible: false` is
the sole way to hide a column, and a column consumed as a style input
(background / font.color / font.weight column refs) is hidden automatically.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from .._engine_render import render_table_svg

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())
_BOARD_RENDER_STYLE = resolve_style(get_theme_style())

_DATA = [
    {"metric": "MRR", "week": "W1", "status": "on_track", "amount": 250},
    {"metric": "Churn", "week": "W1", "status": "at_risk", "amount": 40},
]


def _render(style: dict, data: list[dict], **chart_kwargs) -> str:
    resolved = _resolve(style, data, **chart_kwargs)
    return render_table_svg(resolved, data, width=600, board_style=_BOARD_RENDER_STYLE)


def _resolve(style: dict, data: list[dict], **chart_kwargs):
    chart = TableChart(id="test", type="table", style=style, **chart_kwargs)
    return resolve(chart, data, chart_style_context=_BOARD_CTX)


class TestStylingSubsetRendersEveryColumn:
    """Styling only some columns must never hide the rest (the reported bug)."""

    def test_all_four_columns_render_and_styled_one_keeps_its_format(self) -> None:
        svg = _render(
            {"columns": {"amount": {"format": "$,.0f", "visible": True}}},
            _DATA,
        )
        assert "MRR" in svg
        assert "W1" in svg
        assert "on_track" in svg
        assert ">$<" in svg, "the styled column must keep its currency format"
        assert "250" in svg

    def test_unlisted_columns_still_receive_column_defaults(self) -> None:
        """A render-only fix would leave unlisted columns with no resolved
        config — no column_defaults fill, no align classification. The
        resolved mapping must cover every query column (mechanism B)."""
        resolved = _resolve(
            {
                "column_defaults": {"align": "right"},
                "columns": {"amount": {"format": "$,.0f", "visible": True}},
            },
            _DATA,
        )
        assert set(resolved.columns) == {"metric", "week", "status", "amount"}
        assert resolved.columns["metric"].align == "right"
        assert resolved.columns["amount"].align == "right"

    def test_no_style_columns_at_all_still_renders_every_column(self) -> None:
        svg = _render({}, _DATA)
        assert "MRR" in svg
        assert "W1" in svg
        assert "on_track" in svg
        assert "250" in svg

    def test_subset_with_no_visible_anywhere_still_renders_every_column(self) -> None:
        """No transitional check any more: a styled subset with no `visible:`
        authored anywhere renders every query column, same as any other
        subset."""
        svg = _render({"columns": {"amount": {"format": "$,.0f"}}}, _DATA)
        assert "MRR" in svg
        assert "W1" in svg
        assert "on_track" in svg
        assert ">$<" in svg, "the styled column must keep its currency format"
        assert "250" in svg


class TestVisibleFalseHidesColumn:
    """visible: false is the only way to hide a column."""

    def test_hides_exactly_the_named_column(self) -> None:
        svg = _render({"columns": {"week": {"visible": False}}}, _DATA)
        assert "MRR" in svg
        assert "on_track" in svg
        assert "250" in svg
        assert "W1" not in svg

    def test_hidden_column_stays_in_the_resolved_mapping(self) -> None:
        """Mechanism B: hiding happens at display time off a present entry —
        dropping the key at resolve would let transpose resurrect it."""
        resolved = _resolve({"columns": {"week": {"visible": False}}}, _DATA)
        assert resolved.columns is not None
        assert resolved.columns["week"].visible is False

    def test_visible_true_is_a_no_op(self) -> None:
        svg = _render({"columns": {"week": {"visible": True}}}, _DATA)
        assert "W1" in svg

    def test_visible_false_on_a_multi_measure_pivot_hides_the_measure(self) -> None:
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10, "cnt": 1},
            {"region": "East", "quarter": "Q2", "amt": 20, "cnt": 2},
        ]
        svg = _render(
            {"columns": {"amt": {"visible": False}, "cnt": {"align": "right"}}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt", "cnt"],
        )
        assert "East" in svg
        assert ">1<" in svg and ">2<" in svg, "every cnt leaf cell must render"
        assert ">10<" not in svg and ">20<" not in svg, "amt leafs must be hidden"
        # Group headers must survive intact: hiding a measure narrows each
        # quarter's span, it must not shift later group labels off their
        # columns. (The kept measure's sub-label text form is pinned by the
        # separate raw-separator-label task, not here.)
        assert "Q1" in svg and "Q2" in svg, "both group headers must remain"

    def test_visible_false_on_a_single_measure_pivot_measure_key_hides_all(
        self,
    ) -> None:
        """Measure-keyed authoring means the same thing on both pivot shapes:
        on a single-measure pivot the measure entry fans out to every bare-value
        leaf, so `visible: false` collapses the cross-tab body to the row
        dimensions — explicitly, at the author's request."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        svg = _render(
            {"columns": {"amt": {"visible": False}}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
        )
        assert "East" in svg
        assert ">10<" not in svg and ">20<" not in svg

    def test_measure_keyed_visible_on_multi_dim_single_measure_pivot(self) -> None:
        """Multi-dim single-measure leafs carry no measure suffix — the
        single measure's entry still fans onto them, so measure-keyed
        `visible: false` means the same thing on every pivot shape."""
        data = [
            {"region": "East", "quarter": "Q1", "seg": "A", "amt": 10},
            {"region": "East", "quarter": "Q1", "seg": "B", "amt": 11},
            {"region": "East", "quarter": "Q2", "seg": "A", "amt": 20},
            {"region": "East", "quarter": "Q2", "seg": "B", "amt": 21},
        ]
        svg = _render(
            {"columns": {"amt": {"visible": False}}},
            data,
            rows=["region"],
            columns=["quarter", "seg"],
            values=["amt"],
        )
        assert "East" in svg
        assert ">10<" not in svg and ">21<" not in svg

    def test_measure_keyed_visible_with_inferred_values(self) -> None:
        """`values:` omitted — the measure is inferred, and the fan-out must
        read the *resolved* measure set, not the authored one."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        svg = _render(
            {"columns": {"amt": {"visible": False}}},
            data,
            rows=["region"],
            columns=["quarter"],
        )
        assert "East" in svg
        assert ">10<" not in svg and ">20<" not in svg

    def test_visible_true_on_a_single_measure_pivot_renders(self) -> None:
        """`visible: true` is the inert default and must never turn a valid
        board into a render error — on any pivot shape."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        svg = _render(
            {"columns": {"amt": {"align": "right", "visible": True}}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
        )
        assert "East" in svg
        assert ">10<" in svg and ">20<" in svg

    def test_visible_false_under_transpose_does_not_resurrect_as_a_row(self) -> None:
        """transpose rebuilds its source columns from the resolved mapping AND
        the raw row — a hidden column must not reappear as a (label, value)
        row either way."""
        data = [{"name": "Alice", "amount": 250, "internal_id": "acct_1"}]
        svg = _render(
            {"transpose": True, "columns": {"internal_id": {"visible": False}}},
            data,
        )
        assert "Alice" in svg
        assert "acct_1" not in svg


class TestVisibleFalseColumnStillFeedsLinks:
    """link: templates resolve against the full result set, not the rendered
    columns — a hidden column's value must stay available."""

    def test_link_template_uses_hidden_column_value(self) -> None:
        data = [{"name": "Alice", "id": "acct_123"}]
        svg = _render(
            {
                "columns": {
                    "id": {"visible": False},
                    "name": {"link": "https://example.com/accounts/{{ id }}"},
                }
            },
            data,
        )
        assert "https://example.com/accounts/acct_123" in svg
        # id is hidden — acct_123 appears only inside the resolved href,
        # never as its own cell.
        assert svg.count("acct_123") == 1


class TestStyleInputColumnsAutoHide:
    """A column consumed as a style input (background / font.color /
    font.weight resolve column-ID-first) is hidden with no authored visible:."""

    _HELPER_DATA = [
        {"metric": "MRR", "weight_col": "bold"},
        {"metric": "Churn", "weight_col": "400"},
    ]

    def test_font_weight_helper_is_auto_hidden(self) -> None:
        svg = _render(
            {"columns": {"metric": {"font": {"weight": "weight_col"}}}},
            self._HELPER_DATA,
        )
        assert "MRR" in svg
        assert 'font-weight="bold"' in svg
        assert "Weight Col" not in svg, "the helper must not render a header"
        assert ">400<" not in svg, "the helper must not render as a column"

    def test_authored_visible_true_overrides_auto_hide(self) -> None:
        """Auto-hide is a default, not a verdict — an explicit `visible: true`
        on a style-input column shows it (as its literal values) while it
        keeps driving the other column's styling."""
        svg = _render(
            {
                "columns": {
                    "metric": {"font": {"weight": "weight_col"}},
                    "weight_col": {"visible": True},
                }
            },
            self._HELPER_DATA,
        )
        assert 'font-weight="bold"' in svg
        assert "Weight Col" in svg
        assert ">400<" in svg

    def test_explicit_entry_beats_auto_hide(self) -> None:
        """An explicit style.columns entry is a display signal: a labeled
        style-input column renders under its label instead of vanishing —
        under the old semantics its listing showed it, and auto-hiding an
        explicitly authored column would reintroduce the silent-loss bug."""
        svg = _render(
            {
                "columns": {
                    "metric": {"font": {"weight": "weight_col"}},
                    "weight_col": {"label": "Weight"},
                }
            },
            self._HELPER_DATA,
        )
        assert 'font-weight="bold"' in svg, "the style ref must keep driving"
        assert "Weight" in svg, "the labeled column must render"
        assert ">400<" in svg

    def test_font_color_helper_is_auto_hidden(self) -> None:
        data = [{"metric": "MRR", "ink": "#14532d"}]
        svg = _render({"columns": {"metric": {"font": {"color": "ink"}}}}, data)
        assert 'fill="#14532d"' in svg, "the color ref must apply"
        assert ">Ink<" not in svg, "the helper must not render a header"

    def test_pivot_consumed_style_input_renders_not_raises(self) -> None:
        """A style-input column the pivot reshape consumes is auto-hidden at
        resolve and absent from the post-pivot key space — the unaddressed-
        `visible:` guard must exempt it (the hide is derived, not authored),
        so the board renders instead of blaming the author."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        svg = _render(
            {"column_defaults": {"background": "quarter"}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
        )
        assert "East" in svg
        assert ">10<" in svg and ">20<" in svg

    def test_column_defaults_style_input_is_auto_hidden(self) -> None:
        """The derivation also reads style.column_defaults — a helper consumed
        by every column via defaults is paint, not display data."""
        data = [{"metric": "MRR", "row_bg": "#fee2e2"}]
        svg = _render({"column_defaults": {"background": "row_bg"}}, data)
        assert 'fill="#fee2e2"' in svg, "the background ref must apply"
        assert ">Row Bg<" not in svg, "the helper must not render a header"


class TestUnaddressedVisibleEntryRaises:
    """A `visible:` entry that addresses no rendered column, no row-role
    marker, and no expanded measure key is unreachable authoring (a typo, or
    the wrong key form for the pivot shape) — hiding it would silently do
    nothing, which is the failure `visible:` replaced."""

    def test_typoed_visible_key_raises(self) -> None:
        with pytest.raises(ChartDataError) as excinfo:
            _render({"columns": {"weekk": {"visible": False}}}, _DATA)
        message = str(excinfo.value)
        assert "weekk" in message
        assert "status" in message, "the error must name the real columns"

    def test_pivot_dimension_visible_true_renders(self) -> None:
        """`visible: true` is the inert default: on a key the pivot consumes
        it can never hide anything, so it must never raise."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        svg = _render(
            {"columns": {"quarter": {"visible": True}}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
        )
        assert "East" in svg
        assert ">10<" in svg and ">20<" in svg

    def test_pivot_dimension_visible_key_raises(self) -> None:
        """A pivot `columns:` field is consumed by the reshape — `visible:`
        on it can never act on a rendered column."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        with pytest.raises(ChartDataError) as excinfo:
            _render(
                {"columns": {"quarter": {"visible": False}}},
                data,
                rows=["region"],
                columns=["quarter"],
                values=["amt"],
            )
        assert "quarter" in str(excinfo.value)

    def test_measure_label_does_not_clobber_single_dim_leaf_headers(self) -> None:
        """A measure-keyed `label:` on a single-dim pivot must not stamp one
        name across every leaf column — leaf headers keep their pivoted-value
        identity."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        svg = _render(
            {"columns": {"amt": {"label": "Amount", "visible": True}}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
        )
        assert "Q1" in svg and "Q2" in svg, "leaf headers must keep their values"
        assert svg.count("Amount") == 0, "the measure label must not become headers"

    def test_fanned_leaf_keeps_shared_scale_anchor_formatting(self) -> None:
        """The fan must carry resolve's baked shared_scale onto each leaf —
        an ANCHOR-mode magnitude suffix appears once per column, exactly as
        the flat-table control formats the same data."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 1500000, "cnt": 1},
            {"region": "East", "quarter": "Q2", "amt": 2500000, "cnt": 2},
            {"region": "West", "quarter": "Q1", "amt": 3500000, "cnt": 3},
            {"region": "West", "quarter": "Q2", "amt": 4500000, "cnt": 4},
        ]
        svg = _render(
            {"columns": {"amt": {"format": "~s", "visible": True}}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt", "cnt"],
        )
        assert svg.count("M<") == 2, (
            "one anchored suffix per amt column — dropping shared_scale "
            "repeats it on every row"
        )

    def test_measure_width_does_not_fan_onto_leaves(self) -> None:
        """A measure-keyed width multiplied across N leaves would inflate the
        table — the sizing slot never fans (it was inert on the old
        semantics)."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        with_width = _render(
            {"columns": {"amt": {"width": "50%", "visible": True}}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
        )
        control = _render(
            {"columns": {"amt": {"visible": True}}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
        )
        assert with_width.split(">")[0] == control.split(">")[0], (
            "the svg root (width/viewBox) must not inflate"
        )

    def test_leaf_part_keyed_label_survives_on_multi_dim_pivot(self) -> None:
        """An entry the author keyed by a leaf's own display part keeps its
        authored label — only entries reached via the single-measure fallback
        are identity-stripped."""
        data = [
            {"region": "East", "quarter": "Q1", "seg": "A", "amt": 10},
            {"region": "East", "quarter": "Q1", "seg": "B", "amt": 11},
            {"region": "East", "quarter": "Q2", "seg": "A", "amt": 20},
            {"region": "East", "quarter": "Q2", "seg": "B", "amt": 21},
        ]
        svg = _render(
            {"columns": {"A": {"label": "Segment A", "visible": True}}},
            data,
            rows=["region"],
            columns=["quarter", "seg"],
            values=["amt"],
        )
        assert "Segment A" in svg, "the leaf-part-keyed label must survive"
        assert ">B<" in svg, "un-keyed leaves keep their value identity"

    def test_measure_label_does_not_stamp_multi_dim_leaf_headers(self) -> None:
        """A multi-dim single-measure leaf's header is a dimension value —
        an authored measure label must not overwrite every one of them."""
        data = [
            {"region": "East", "quarter": "Q1", "seg": "A", "amt": 10},
            {"region": "East", "quarter": "Q1", "seg": "B", "amt": 11},
            {"region": "East", "quarter": "Q2", "seg": "A", "amt": 20},
            {"region": "East", "quarter": "Q2", "seg": "B", "amt": 21},
        ]
        svg = _render(
            {"columns": {"amt": {"label": "Amount", "visible": True}}},
            data,
            rows=["region"],
            columns=["quarter", "seg"],
            values=["amt"],
        )
        assert ">A<" in svg and ">B<" in svg, "leaf headers keep their values"
        assert "Amount" not in svg, "the measure label must not become headers"

    def test_measure_header_link_does_not_attach_to_value_headers(self) -> None:
        """header_link carries the same identity rule as label: it belongs to
        measure-identity sub-labels only, never to pivoted-value headers."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        svg = _render(
            {
                "columns": {
                    "amt": {"header_link": "https://docs.example/amt", "visible": True}
                }
            },
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt"],
        )
        assert "https://docs.example/amt" not in svg

    def test_measure_header_link_survives_on_multi_measure_sub_labels(self) -> None:
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10, "cnt": 1},
            {"region": "East", "quarter": "Q2", "amt": 20, "cnt": 2},
        ]
        svg = _render(
            {
                "columns": {
                    "amt": {"header_link": "https://docs.example/amt", "visible": True}
                }
            },
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt", "cnt"],
        )
        assert svg.count("https://docs.example/amt") == 2, (
            "each amt sub-label carries the measure's header link"
        )

    def test_visible_false_measure_with_all_leafs_authored_fails_loud(self) -> None:
        """If every leaf carries its own entry, nothing fans — a measure-keyed
        `visible: false` would silently do nothing, so it must raise."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10},
            {"region": "East", "quarter": "Q2", "amt": 20},
        ]
        with pytest.raises(ChartDataError) as excinfo:
            _render(
                {
                    "columns": {
                        "amt": {"visible": False},
                        "Q1": {"align": "right"},
                        "Q2": {"align": "right"},
                    }
                },
                data,
                rows=["region"],
                columns=["quarter"],
                values=["amt"],
            )
        assert "amt" in str(excinfo.value)

    def test_bare_measure_styling_keeps_multi_dim_leaf_headers_clean(self) -> None:
        """A fanned measure config with no label must not surface the raw
        record-separator leaf key as a header."""
        data = [
            {"region": "East", "quarter": "Q1", "seg": "A", "amt": 10},
            {"region": "East", "quarter": "Q1", "seg": "B", "amt": 11},
            {"region": "East", "quarter": "Q2", "seg": "A", "amt": 20},
            {"region": "East", "quarter": "Q2", "seg": "B", "amt": 21},
        ]
        svg = _render(
            {"columns": {"amt": {"align": "right", "visible": True}}},
            data,
            rows=["region"],
            columns=["quarter", "seg"],
            values=["amt"],
        )
        assert "\x1e" not in svg, "the raw leaf separator must never render"
        assert ">A<" in svg and ">B<" in svg, "leaf headers keep their values"

    def test_error_shows_readable_leaf_keys_on_a_multi_measure_pivot(self) -> None:
        """Multi-measure leaf keys are record-separator joins no author can
        type — the addressable-columns hint must print the parts, never the
        raw control character."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10, "cnt": 1},
            {"region": "East", "quarter": "Q2", "amt": 20, "cnt": 2},
        ]
        with pytest.raises(ChartDataError) as excinfo:
            _render(
                {"columns": {"amtt": {"visible": False}}},
                data,
                rows=["region"],
                columns=["quarter"],
                values=["amt", "cnt"],
            )
        message = str(excinfo.value)
        assert "\x1e" not in message
        assert "Q1 / amt" in message

    def test_measure_keyed_visible_on_multi_measure_pivot_is_addressed(self) -> None:
        """Covered via leaf expansion — must not raise (and must hide)."""
        data = [
            {"region": "East", "quarter": "Q1", "amt": 10, "cnt": 1},
            {"region": "East", "quarter": "Q2", "amt": 20, "cnt": 2},
        ]
        svg = _render(
            {"columns": {"amt": {"visible": False}}},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["amt", "cnt"],
        )
        assert ">10<" not in svg and ">20<" not in svg

    def test_row_role_visible_false_is_accepted(self) -> None:
        """The migrator's fill emits `visible: false` for every unmapped SQL
        alias, including the `_df_row_role` marker on non-pivoted totals
        charts. The strip already satisfies the hide — accepting the entry,
        never raising, is load-bearing for every migrated totals board."""
        data = [
            {"region": "East", "revenue": 10, "_df_row_role": "value"},
            {"region": "Total", "revenue": 10, "_df_row_role": "total"},
        ]
        svg = _render(
            {
                "row": {"role": "_df_row_role"},
                "columns": {
                    "region": {},
                    "revenue": {"format": "$,.0f"},
                    "_df_row_role": {"visible": False},
                },
            },
            data,
        )
        assert "East" in svg
        assert "Row Role" not in svg

    def test_empty_data_skips_the_check(self) -> None:
        """No result shape to compare against — the empty-state render keeps
        its headers rather than guessing at typos."""
        resolved = _resolve({"columns": {"week": {"visible": False}}}, _DATA)
        svg = render_table_svg(resolved, [], width=600, board_style=_BOARD_RENDER_STYLE)
        assert "Metric" in svg, "empty-state headers must still render"
        assert "Week" not in svg, "the hidden column's header stays hidden"


class TestTransposeUnaddressedVisible:
    """The transpose path runs its own copy of the unreachable-authoring
    guard — a typoed `visible:` key must raise there too."""

    def test_typoed_visible_key_raises_under_transpose(self) -> None:
        data = [{"name": "Alice", "amount": 250}]
        resolved = _resolve({"columns": {"amountt": {"visible": False}}}, data)
        transposed_tc = resolved.style.table.model_copy(update={"transpose": True})
        resolved = resolved.model_copy(
            update={"style": resolved.style.model_copy(update={"table": transposed_tc})}
        )
        with pytest.raises(ChartDataError) as excinfo:
            render_table_svg(resolved, data, width=600, board_style=_BOARD_RENDER_STYLE)
        assert "amountt" in str(excinfo.value)


class TestStyleColumnsHasNoSayInColumnOrder:
    """Order is structural (pivot spec, else query order) — authored key
    order must not reorder the rendered table."""

    def test_reordering_the_authored_keys_changes_nothing(self) -> None:
        style_a = {
            "columns": {
                "amount": {"format": "$,.0f", "visible": True},
                "metric": {"label": "Metric"},
            }
        }
        style_b = {
            "columns": {
                "metric": {"label": "Metric"},
                "amount": {"format": "$,.0f", "visible": True},
            }
        }
        assert _render(style_a, _DATA) == _render(style_b, _DATA)
