"""CHART.SUPPORT_TABLE — attached mini-table primitive."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
)

from dbt_charts.core.compile.models.markers import DisplayText, Format
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.schema_names import FormatAlias

ChartSupportTableAggregateOp = Literal[
    "sum", "avg", "min", "max", "median", "count", "count_distinct"
]


def _coerce_bare_string(
    data: object,  # type-state: object_annotation — validator input: pydantic hands this any raw YAML scalar/mapping pre-coercion; narrowed by isinstance below
) -> object:  # type-state: object_annotation — same boundary as the parameter above
    """Coerce a bare column-name string to {"source": "..."} — the "revenue" ==
    {source: revenue} shorthand.

    Declared as a `BeforeValidator` on `ChartSupportTableEntry`'s "source" arm
    (below), not as a `model_validator` on `ChartSupportTableSource` itself —
    a model-level validator is invisible to JSON Schema; the annotation-level
    `json_schema_input_type` is what schema/introspection.py reads back. No
    non-test code constructs `ChartSupportTableSource` directly from a raw
    string outside that arm (the tests that used to do so now go through
    `TypeAdapter(ChartSupportTableEntry)`), so a single attachment is enough.
    """
    return {"source": data} if isinstance(data, str) else data


class ChartSupportTableSource(BaseModel):
    """A support_table row that reads a column's raw per-x value."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        description="Query column the row reads from (per-x raw value)."
    )
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="D3 format string or format config object. Optional; inherits the chart measure format when omitted and source matches chart.y.",
    )
    label: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description="Left-stub row label. Optional.",
    )


class ChartSupportTableAggregate(BaseModel):
    """A support_table row that reads an aggregate of a column grouped by x."""

    model_config = ConfigDict(extra="forbid")

    aggregate: ChartSupportTableAggregateOp = Field(
        description=(
            "Aggregate operation applied per x-group. One of: "
            "sum, avg, min, max, median, count, count_distinct. "
            "Exact names only: no aliases (spec G4)."
        ),
    )
    source: str = Field(
        description=(
            "Query column being aggregated. Always required alongside "
            "`aggregate:` (spec G2)."
        ),
    )
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="D3 format string or format config object for the aggregated value. Optional.",
    )
    label: Annotated[str | None, DisplayText()] = Field(
        default=None, description="Column header label override for this row."
    )


class ChartSupportTablePerSeries(BaseModel):
    """A support_table entry that expands into one row per color: series.

    When ``by_measure=True`` the entry expands into a single row reading the
    named y-field directly, without a color: channel.  This is the expansion
    mode for multi-y charts (``y: [revenue, margin]``) where each measure is
    its own y-axis series rather than a color-encoded pivot.
    """

    model_config = ConfigDict(extra="forbid")

    per_series: str = Field(
        description="Query column the row reads from (per-x, per-series value)."
    )
    by_measure: bool = Field(
        default=False,
        description=(
            "When True, expand one row reading the named measure field directly "
            "(no color: groupby). Required for multi-y charts where each measure "
            "is its own y-field rather than a color-encoded series."
        ),
    )
    label: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description=(
            "Row label for a by_measure=True row; when None and by_measure=True, "
            "the per_series column name is used. Also the header of a horizontal "
            "grouped bar's single per_series column. Not allowed on a "
            "non-by_measure strip row or stacked-bar column."
        ),
    )
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="D3 format string or format config object. Optional.",
    )


def _discriminate_entry(entry: Any) -> str | None:
    """Tag-selector for the ChartSupportTableEntry tagged union.

    A bare string routes to the "source" tag, same as `{source: ...}` — the
    "source" arm's own `BeforeValidator(_coerce_bare_string)` wraps it before
    field validation runs.
    """
    if isinstance(entry, ChartSupportTableAggregate):
        return "aggregate"
    if isinstance(entry, ChartSupportTablePerSeries):
        return "per_series"
    if isinstance(entry, ChartSupportTableSource):
        return "source"
    if isinstance(entry, str):
        return "source"
    if isinstance(entry, dict):
        if "aggregate" in entry:
            return "aggregate"
        if "per_series" in entry:
            return "per_series"
        return "source"
    return None


ChartSupportTableEntry = Annotated[
    Annotated[
        ChartSupportTableSource,
        BeforeValidator(
            _coerce_bare_string, json_schema_input_type=str | ChartSupportTableSource
        ),
        Tag("source"),
    ]
    | Annotated[ChartSupportTableAggregate, Tag("aggregate")]
    | Annotated[ChartSupportTablePerSeries, Tag("per_series")],
    Discriminator(_discriminate_entry),
]


def _accept_bare_list(
    data: object,  # type-state: object_annotation — validator input: pydantic hands this any raw YAML scalar/mapping pre-coercion; narrowed by isinstance below
) -> object:  # type-state: object_annotation — same boundary as the parameter above
    """Coerce a bare entry list to {"entries": [...]} — the array shorthand for
    a whole `support_table:` block.

    Declared as a `BeforeValidator` on `ChartSupportTableOrList` (below), not
    as a `model_validator` on `ChartSupportTable` itself — same reasoning as
    `_coerce_bare_string` above. `ChartSupportTableOrList` is the type every
    `support_table:` field uses (authored and normalized alike), and the tests
    that used to call `ChartSupportTable.model_validate([...])` directly now go
    through `TypeAdapter(ChartSupportTableOrList)`.
    """
    if isinstance(data, list):
        return {"entries": data}
    return data


class ChartSupportTable(BaseModel):
    """Container for a chart's support_table block."""

    model_config = ConfigDict(extra="forbid")

    entries: list[ChartSupportTableEntry] = Field(
        min_length=1,
        description=(
            "List of support-table entries (source, aggregate, or per-series rows). "
            "A source entry may be written as a bare column name (`revenue` in "
            "place of `{source: revenue}`), the same scalar-listable spelling "
            "`y:` uses."
        ),
    )


# The `support_table:` field's annotation (chart/authored/_base.py): accepts
# either a full {entries: [...]} block or the bare-list shorthand.
ChartSupportTableOrList = Annotated[
    ChartSupportTable,
    BeforeValidator(
        _accept_bare_list,
        json_schema_input_type=Annotated[
            list[ChartSupportTableEntry], Field(min_length=1)
        ]
        | ChartSupportTable,
    ),
]


CHART_SUPPORT_TABLE_SUPPORTED_TYPES: frozenset[str] = frozenset({"bar", "line", "area"})


def validate_support_table_shape(entries: list[ChartSupportTableEntry]) -> None:
    """Data-free validation of a support_table entry list (spec §3.2)."""
    seen: set[tuple[str, str | None, bool]] = set()
    for entry in entries:
        if isinstance(entry, ChartSupportTablePerSeries):
            key: tuple[str, str | None, bool] = (entry.per_series, None, True)
            if key in seen:
                raise ValueError(
                    f"chart.support_table has duplicate per_series entries for "
                    f"{{per_series: {entry.per_series!r}}}. "
                    "Remove the duplicate."
                )
            seen.add(key)
            continue
        agg = entry.aggregate if isinstance(entry, ChartSupportTableAggregate) else None
        source = entry.source
        normal_key: tuple[str, str | None, bool] = (source, agg, False)
        if normal_key in seen:
            label = (
                f"{{aggregate: {agg}, source: {source}}}"
                if agg is not None
                else f"{{source: {source}}}"
            )
            raise ValueError(
                f"chart.support_table has duplicate entries for {label}. "
                "Remove the duplicate or differentiate by aggregate operation."
            )
        seen.add(normal_key)
