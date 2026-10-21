"""An authored value equal to the one it resolves to without it is restated.

"Below" is either the style/board cascade (``merge.restated_leaves``) or an
inference run without the authored key. Comparison is exact and type-strict, so
only a key that provably changes nothing is reported.
"""

from __future__ import annotations

import json
import re
import typing
from collections.abc import Callable, Iterator, Sequence
from functools import partial, reduce
from typing import Any, TypeAlias

import sqlglot
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefinedType
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbt_charts.core.compile.format import declared_column_format, declared_format
from dbt_charts.core.compile.merge import (
    built_in_theme_style,
    merge_patches,
    restated_leaves,
    scope_patch,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import CalloutChart, Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.normalize.variable_input_refinement import (
    refine_input_type_from_data,
)
from dbt_charts.core.compile.normalize.variables import detect_variable_input_type
from dbt_charts.core.compile.resolve.style.board import resolve_cascaded_font
from dbt_charts.core.compile.template.variables import choice_member_type
from dbt_charts.core.diagnostics import (
    WARN_RESTATED_FORMAT,
    WARN_RESTATED_INFERRED,
    WARN_RESTATED_STYLE,
    Diagnostic,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.registry import WarningCode
from dbt_charts.core.text.case import inferred_display_name
from dbt_charts.core.text.format_d3 import language_scope

# Board fields that cascade from themes, meta.yml and extends fragments. The
# rest of a board (layout, queries, charts, prose) is content, not a setting.
_CASCADED_FIELDS = frozenset({"style", "source", "regional"})
_Tree: TypeAlias = Any  # type-state: explicit_any — dumped YAML and patch trees mix models, dicts and leaves
_Artifact: TypeAlias = (
    object  # type-state: object_annotation — only compared for equality
)


def restated_diagnostic(
    code: WarningCode,
    path: str,
    value: Any,  # type-state: explicit_any — any authored YAML leaf
    source: str,
    why: str,
    *,
    chart: str | None = None,
    field: str | None = None,
    fix: str | None = None,
) -> Diagnostic:
    """The WARN-RESTATED-* diagnostic: `source` is the message phrase, `why` the
    short reason the text output shows beside the key, `fix` replaces the
    delete-it advice where deleting alone would change the board."""
    shown = int(value) if isinstance(value, float) and value.is_integer() else value
    text = shown if isinstance(shown, str) else json.dumps(shown, default=str)
    return Diagnostic.from_code(
        code,
        path=path,
        chart=chart,
        field=field,
        message=code.message_template.format(path=path, value=text, source=source),
        fix=code.fix_template.format(path=path) if fix is None else fix,
        fields={"value": text, "source": source, "why": why},
    )


def _sets(patch: BaseModel, path: Sequence[str]) -> bool:
    """Whether *patch* authors the leaf at *path* (a model field or a dict key)."""
    node: Any = patch  # type-state: explicit_any — walks models and dicts alike
    for key in path:
        if isinstance(node, BaseModel):
            if key not in node.model_fields_set:
                return False
            node = getattr(node, key)
        elif isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return False
    return True


def cascade_restated(
    own: BaseModel,
    layers: Sequence[tuple[str, str, BaseModel]],
    *,
    path_prefix: Sequence[str] = (),
) -> list[Diagnostic]:
    """Warn for each cascaded leaf of *own* that the layers below already supply.

    *layers* run low to high as ``(source label, why, patch)``; the label names
    the layer in the message. *own* sits above all of them.
    """
    lower = reduce(merge_patches, (patch for _, _, patch in layers))
    narrow = type(own).model_construct(
        _fields_set=own.model_fields_set & _CASCADED_FIELDS,
        **{name: getattr(own, name) for name in type(own).model_fields},
    )
    warnings: list[Diagnostic] = []
    for path, value in restated_leaves(narrow, lower):
        # A value no layer sets is a model default, not an inherited one.
        source = next(
            (
                (label, why)
                for label, why, patch in reversed(layers)
                if _sets(patch, path)
            ),
            None,
        )
        if source is not None:
            warnings.append(
                restated_diagnostic(
                    WARN_RESTATED_STYLE,
                    ".".join((*path_prefix, *path)),
                    value,
                    *source,
                )
            )
    return warnings


def inferred_restated(
    path: str,
    authored: Any,  # type-state: explicit_any — any authored YAML leaf
    infer: Callable[[], Any],  # type-state: explicit_any — what the engine would pick
    source: str,
    why: str,
    *,
    chart: str | None = None,
    field: str | None = None,
) -> list[Diagnostic]:
    """Warn when *authored* equals what *infer* returns with the key absent."""
    inferred = infer()
    if type(authored) is not type(inferred) or authored != inferred:
        return []
    return [
        restated_diagnostic(
            WARN_RESTATED_INFERRED,
            path,
            authored,
            source,
            why,
            chart=chart,
            field=field,
        )
    ]


# Family style fields resolved at render by an explicit order (axis cascade) or
# a global -> family -> local stack: not one tier below the chart, so comparing
# the chart's value to the family tier alone could name a key that still matters.
_ORDERED_AT_RENDER = frozenset(
    {"axis", "axis_x", "axis_y", "axis_quantitative", "axis_band", "legend", "title"}
)
# Chart style slot -> the chart field naming the column that slot formats; the
# query's `formats:` entry for that column sits below the slot in the cascade.
# An x axis may be temporal, which only the data tells: the render-time pass
# judges it.
_FORMAT_SLOTS: dict[tuple[str, ...], str] = {
    ("value", "format"): "value",
    ("axis_y", "labels", "format"): "y",
}


def _without(model: BaseModel, names: frozenset[str]) -> BaseModel:
    """*model* with *names* treated as unauthored."""
    return type(model).model_construct(
        _fields_set=model.model_fields_set - names,
        **{name: getattr(model, name) for name in type(model).model_fields},
    )


def chart_restated_paths(chart: Chart, ctx: ChartStyleContext) -> frozenset[str]:
    """Paths the compile-time chart checks already report, for the render pass to skip."""
    return frozenset(w.path for w in _chart_restated(chart, ctx) if w.path)


def _chart_restated(chart: Chart, ctx: ChartStyleContext) -> list[Diagnostic]:
    """Chart-local style keys the board's family tier or the query already supply."""
    style = chart.style
    if style is None:
        return []
    from dbt_charts.core.compile.normalize.charts import (
        _AUTHORED_FAMILY_TO_MONOLITHIC_KEY,
    )

    where = {"chart": chart.id}
    base = f"charts.{chart.id}.style"
    family = getattr(ctx, _AUTHORED_FAMILY_TO_MONOLITHIC_KEY[chart.type])
    warnings = [
        restated_diagnostic(
            WARN_RESTATED_STYLE,
            f"{base}.{'.'.join(path)}",
            value,
            "the board's style for this chart type",
            "board style",
            field=".".join(path),
            **where,
        )
        for path, value in restated_leaves(
            _without(style, _ORDERED_AT_RENDER | _PROMOTED_TOP_LEVEL), family
        )
    ]
    dumped = style.model_dump(exclude_unset=True)
    authored_columns = dumped.get("columns")
    columns: dict[str, dict[str, _Tree]] = (
        {} if authored_columns is None else authored_columns
    )
    if chart.type == "table":
        header_font = resolve_cascaded_font(ctx.table.header.font)
        for name, column in columns.items():
            warnings += inferred_restated(
                f"{base}.columns.{name}.label",
                column.get("label"),
                partial(inferred_display_name, name, font=header_font),
                "the header derived from the column name",
                "derived header",
                field=f"columns.{name}.label",
                **where,
            )
    query = None if isinstance(chart, CalloutChart) else chart.query
    formats = query.formats if query is not None else None
    if formats is None:
        return warnings
    slots = dict(_FORMAT_SLOTS)
    slots |= {("columns", name, "format"): name for name in columns}
    for path, column in slots.items():
        authored = _dig(style.model_dump(exclude_unset=True), path)
        if not isinstance(authored, str):
            continue
        name = column if path[0] == "columns" else getattr(chart, column)
        declared = (
            declared_format(formats, name)
            if path[0] == "columns"
            else declared_column_format(
                formats, name, ctx.formats, time=False, text=path[0] == "value"
            )
        )
        if authored == declared:
            warnings.append(
                restated_diagnostic(
                    WARN_RESTATED_FORMAT,
                    f"{base}.{'.'.join(path)}",
                    authored,
                    f"the query's `formats:` entry for `{name}`",
                    "query format",
                    field=".".join(path),
                    **where,
                )
            )
    return warnings


def _dig(node: _Tree, path: Sequence[str]) -> _Tree:
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


# A nested board's background is pinned, never inherited, so restating its
# parent's changes the render.
_NESTED_PINNED = frozenset({"background"})


def tree_restated(board: Board) -> list[Diagnostic]:
    """Warn across the compiled tree: chart-local keys and nested-board styles.

    The root's own style is checked where its layers fold (``merged_patch``);
    here each nested board's authored style is compared with the in-force
    patch of its ancestors over its theme, and each placed chart with the
    style context of the board that holds it. A nested board with no authored
    coordinates (imported from another file) is judged in its own file.
    """
    warnings: list[Diagnostic] = []

    def walk(scope: Board, in_force: StylePatch | None, prefix: str | None) -> None:
        if prefix is not None:
            warnings.extend(_variable_labels_restated(scope, prefix))
        for item in scope.layout.items:
            if item.chart is not None:
                warnings.extend(_chart_restated(item.chart, scope.chart_style_context))
            elif item.board is not None:
                child = item.board
                own = child.authored_style
                if own is not None and item.source_path and child.theme is not None:
                    theme = built_in_theme_style(child.theme)
                    kept = scope_patch(in_force, None)
                    lower = (
                        kept
                        if theme is None
                        else theme
                        if kept is None
                        else merge_patches(theme, kept)
                    )
                    warnings.extend(
                        restated_diagnostic(
                            WARN_RESTATED_STYLE,
                            f"{item.source_path}.style.{'.'.join(path)}",
                            value,
                            "what this board inherits from its parent",
                            "inherited",
                        )
                        for path, value in restated_leaves(
                            _without(own, _NESTED_PINNED), lower
                        )
                    )
                walk(
                    child,
                    scope_patch(in_force, own),
                    f"{item.source_path}." if item.source_path else None,
                )

    walk(board, board.authored_style, "")
    return warnings


def _variable_labels_restated(scope: Board, prefix: str) -> list[Diagnostic]:
    """Authored variable ``label`` equal to the name's display text in the label font's case."""
    font = resolve_cascaded_font(scope.resolved_style.variables.label.font)
    warnings: list[Diagnostic] = []
    with language_scope(scope.regional.resolved_language):
        for name, var in scope.variables.items():
            if "label" in var.authored_fields and not var.defined_in_other_file:
                warnings += inferred_restated(
                    f"{prefix}variables.{name}.label",
                    var.label,
                    partial(inferred_display_name, name, font=font),
                    "the label inferred from the variable name",
                    "inferred",
                )
    return warnings


def _inferred_input(var: Variable) -> str | None:
    """The input type the variable would get with ``input`` omitted.

    ``None`` when it depends on query results (options query, column binding):
    those are refined after the data loads, so no compile-time answer exists.
    """
    trial = var.model_copy(update={"input": "auto", "input_auto_detected": False})
    inferred = detect_variable_input_type(trial)
    options = trial.options
    if inferred != "select":
        return inferred
    if trial.column or (options and options.query):
        return None
    if options is None or options.static is None:
        return inferred
    trial.input = inferred
    trial.input_auto_detected = True
    return refine_input_type_from_data(
        trial, [str(value) for value in options.static]
    ).input_type


def _data_type_restated(var: Variable, here: str) -> list[Diagnostic]:
    """``data_type`` that gives a choice input the member type it would infer anyway."""
    effective = _inferred_input(var) if var.input == "auto" else var.input
    if effective not in ("select", "radio", "multiselect"):
        return []
    bare = var.model_copy(update={"data_type": None})
    if choice_member_type(var) != choice_member_type(bare):
        return []
    return [
        restated_diagnostic(
            WARN_RESTATED_INFERRED,
            f"{here}.data_type",
            var.data_type,
            "what its static options infer",
            "inferred",
        )
    ]


def variable_restated(
    var: Variable,
    raw: dict[str, _Tree],
    here: str,
) -> list[Diagnostic]:
    """Restated ``input`` and ``data_type`` of one parsed variable.

    The label is compared where the control is drawn, with the label font's case.
    """
    warnings: list[Diagnostic] = []
    if raw.get("input") not in (None, "auto"):
        warnings += inferred_restated(
            f"{here}.input",
            var.input,
            lambda: _inferred_input(var),
            "the input its options infer",
            "inferred",
        )
    if "data_type" in raw and var.data_type is not None:
        warnings += _data_type_restated(var, here)
    return warnings


def scoped_charts(board: Board) -> Iterator[tuple[Chart, Board]]:
    """Every placed chart with the board that holds it."""
    for item in board.layout.items:
        if item.chart is not None:
            yield item.chart, board
        elif item.board is not None:
            yield from scoped_charts(item.board)


def _style_leaves(node: _Tree, path: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    """Paths of every authored leaf of a style patch (model fields and dict keys)."""
    if isinstance(node, BaseModel):
        children = {name: getattr(node, name) for name in node.model_fields_set}
    elif isinstance(node, dict):
        children = node
    else:
        yield path
        return
    for key, child in children.items():
        if child is not None:
            yield from _style_leaves(child, (*path, str(key)))


def _drop_leaf(node: _Tree, path: Sequence[str]) -> _Tree:
    """*node* with the leaf at *path* made unauthored."""
    head, *rest = path
    if isinstance(node, dict):
        return {
            key: _drop_leaf(value, rest) if rest and str(key) == head else value
            for key, value in node.items()
            if rest or str(key) != head
        }
    values = {name: getattr(node, name) for name in type(node).model_fields}
    fields = set(node.model_fields_set)
    if rest:
        values[head] = _drop_leaf(values[head], rest)
    else:
        field = type(node).model_fields[head]
        if field.is_required():
            raise ValueError(
                f"{type(node).__name__}.{head} has no default to fall back to"
            )
        values[head] = field.get_default(call_default_factory=True)
        fields.discard(head)
    return type(node).model_construct(_fields_set=fields, **values)


# Style keys the normalizer copies onto the chart before resolution: dropping
# them from `chart.style` alone leaves the promoted copy in place, so the
# resolve-and-compare cannot judge them.
_PROMOTED_TOP_LEVEL = frozenset({"stack", "aspect_ratio", "min_height", "max_height"})
_PROMOTED_AT_NORMALIZE = _PROMOTED_TOP_LEVEL | {
    "axis_y.labels.format",
    "color.gradient",
}
# `notes` is never drawn; `link` and `link_open` resolve against render-only
# context (automatic and foreign-key links) this comparison does not rebuild.
_CHART_NOT_JUDGED = frozenset(
    {
        "type",
        "id",
        "query",
        "style",
        "warnings_ignore",
        "authored_fields",
        "notes",
        "link",
        "link_open",
    }
)
_LAYOUT_FIELDS = frozenset(
    {"height", "width", "aspect_ratio", "min_height", "max_height"}
)
_RESOLVE_REFUSALS = (DbtChartsError, ValidationError, ValueError)


def _chart_leaves(chart: Chart) -> Iterator[tuple[str, ...]]:
    """Paths of every authored setting of *chart*: style leaves, then other fields."""
    if chart.style is not None:
        yield from _style_leaves(chart.style, ("style",))
    fields = type(chart).model_fields
    for name in sorted(chart.authored_fields - _CHART_NOT_JUDGED):
        if name in fields and not fields[name].is_required():
            value = getattr(chart, name)
            if value is not None:
                yield from _style_leaves(value, (name,))


def _equals_declared_default(
    field: FieldInfo,
    value: Any,  # type-state: explicit_any — any authored value
) -> bool:
    """Whether *value* is the field's declared default, spelled with its type.

    ``"auto"`` asks for inference and a single-member ``Literal`` is a union
    discriminator: neither is a value to judge.
    """
    default = field.default
    return (
        not isinstance(default, PydanticUndefinedType)
        and default is not None
        and default != "auto"
        and not _is_tag(field.annotation)
        and type(value) is type(default)
        and value == default
    )


def _is_declared_default(chart: Chart, path: Sequence[str]) -> bool:
    """Whether the leaf equals its model's declared default (the compile-time check's)."""
    try:
        parent = _authored_value(chart, path[:-1])
    except (AttributeError, KeyError):
        return False
    if not isinstance(parent, BaseModel):
        return False
    field = type(parent).model_fields.get(path[-1])
    return field is not None and _equals_declared_default(
        field, getattr(parent, path[-1])
    )


def _without_leaf(chart: Chart, path: Sequence[str]) -> Chart:
    """*chart* with the setting at *path* made unauthored."""
    name, *rest = path
    value = getattr(chart, name)
    if rest:
        return chart.model_copy(update={name: _drop_leaf(value, rest)})
    return chart.model_copy(
        update={
            name: type(chart).model_fields[name].get_default(call_default_factory=True)
        }
    )


def auto_restated(
    chart: Chart,
    resolve: Callable[[Chart], tuple[BaseModel, _Artifact]],
    skip: frozenset[str] = frozenset(),
) -> list[Diagnostic]:
    """Warn for each authored chart setting whose removal resolves and emits the same chart.

    ``skip`` holds paths an earlier check already reported.
    """
    try:
        resolved, artifact = resolve(chart)
    except _RESOLVE_REFUSALS:
        return []  # the render reports a chart that does not resolve
    warnings: list[Diagnostic] = []
    for path in _chart_leaves(chart):
        dotted = f"charts.{chart.id}.{'.'.join(path)}"
        if (
            dotted in skip
            or any(
                ".".join(path[1:]).startswith(promoted)
                for promoted in _PROMOTED_AT_NORMALIZE
            )
            or path[0] in _LAYOUT_FIELDS
            or _is_declared_default(chart, path)
        ):
            continue
        try:
            trimmed, trimmed_artifact = resolve(_without_leaf(chart, path))
        except Exception:  # noqa: BLE001 — a chart that fails to resolve or emit without the key (a data binding such as `x` trips emitter asserts) means the key matters
            continue
        # A non-style field is kept verbatim on the resolved chart, so its own
        # entry differs by construction; what it feeds must not. A `format`
        # leaf is echoed in the resolved style too: what it prints is judged
        # by the columns and the artifact.
        ignore = {path[0]} if path[0] != "style" or path[-1] == "format" else set()
        if (
            trimmed.model_dump(exclude=ignore) == resolved.model_dump(exclude=ignore)
            and trimmed_artifact == artifact
        ):
            warnings.append(
                restated_diagnostic(
                    WARN_RESTATED_FORMAT
                    if path[-1] == "format"
                    else WARN_RESTATED_INFERRED,
                    dotted,
                    _authored_value(chart, path),
                    "what the renderer picks for this chart's data",
                    "renderer picks it",
                    chart=chart.id,
                    field=".".join(path),
                )
            )
    return warnings


# Variable settings a control reflects on its own. Others (data_type, options,
# min/max/step) also feed validation and sizing that this comparison never sees.
_CONTROL_SETTINGS = frozenset({"input", "default", "placeholder"})


def auto_restated_variable(
    name: str,
    var: Variable,
    resolve: Callable[[Variable], _Artifact],
    infer_input: Callable[[Variable], None],
    prefix: str,
) -> list[Diagnostic]:
    """Warn for each authored control setting whose removal draws the same control.

    ``resolve`` settles the variable the way the render does (options loaded,
    widget refined by data); ``infer_input`` re-runs input detection on a copy
    whose ``input`` was reset to ``auto``.
    """
    baseline = resolve(var)
    warnings: list[Diagnostic] = []
    data_dependent = (var.options is not None and bool(var.options.query)) or bool(
        var.column or isinstance(var.query, str)
    )
    for field in sorted(var.authored_fields & _CONTROL_SETTINGS):
        if field == "input" and not data_dependent:
            continue  # the compile-time check settles it without data
        trial = var.model_copy(
            update={
                field: Variable.model_fields[field].get_default(
                    call_default_factory=True
                ),
                "input_auto_detected": False,
            }
        )
        if field == "input":
            infer_input(trial)
        if resolve(trial) == baseline:
            warnings.append(
                restated_diagnostic(
                    WARN_RESTATED_INFERRED,
                    f"{prefix}variables.{name}.{field}",
                    getattr(var, field),
                    "what its options and data settle",
                    "inferred",
                    field=field,
                )
            )
    return warnings


_FILTER_USE = re.compile(r"""\bfilter\(\s*(['"])(\w+)\1\s*,\s*(\w+)\s*[,)]""")
_JINJA_TAG = re.compile(r"\{%.*?%\}", re.DOTALL)
_JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)


def filtered_column(
    name: str, queries: Sequence[SqlQuery]
) -> tuple[SqlQuery, str, str] | None:
    """The ``(query, table, column)`` every ``filter('<column>', <name>, ...)`` in *queries* reads.

    ``None`` when the variable is never filtered on, the uses disagree, or a
    using query is not a plain single-table SELECT: no guessing.
    """
    found: dict[tuple[str, str], SqlQuery] = {}
    for query in queries:
        columns = {
            column for _q, column, var in _FILTER_USE.findall(query.sql) if var == name
        }
        if not columns:
            continue
        flat = _JINJA_EXPR.sub("1", _JINJA_TAG.sub("", query.sql))
        try:
            tables = list(sqlglot.parse_one(flat).find_all(exp.Table))
        except SqlglotError:
            return None
        if len(tables) != 1:
            return None
        table = tables[0].copy()
        table.set("alias", None)
        for column in columns:
            found.setdefault((table.sql(), column), query)
    if len(found) != 1:
        return None
    ((table_sql, column), query) = next(iter(found.items()))
    return query, table_sql, column


def static_options_restated(
    name: str,
    var: Variable,
    table: str,
    column: str,
    values: Sequence[Any],  # type-state: explicit_any — DB values
    prefix: str,
) -> list[Diagnostic]:
    """Warn when ``options.static`` lists, in order, every value of the column the
    variable filters; a curated order is a choice, not a restatement.

    *values* come ordered as ``column:`` options are (``ORDER BY`` the column).
    """
    static = var.options.static if var.options is not None else None
    if not static or list(static) != [v for v in values if v is not None]:
        return []
    return [
        restated_diagnostic(
            WARN_RESTATED_INFERRED,
            f"{prefix}variables.{name}.options.static",
            list(static),
            f"every value of `{table}.{column}`, as `column: {table}.{column}` lists them",
            "inferred",
            field="options.static",
            fix=f"Replace `options.static` with `column: {table}.{column}`.",
        )
    ]


def _authored_value(node: _Tree, path: Sequence[str]) -> _Tree:
    for key in path:
        node = node[key] if isinstance(node, dict) else getattr(node, key)
    return node


def _is_tag(annotation: Any) -> bool:  # type-state: explicit_any — a typing annotation
    """A single-member ``Literal``: a union discriminator, never omittable."""
    return (
        typing.get_origin(annotation) is typing.Literal
        and len(typing.get_args(annotation)) == 1
    )


def schema_default_restated(
    node: Any,  # type-state: explicit_any — any authored model, list or dict
    raw: Any,  # type-state: explicit_any — the YAML the node was parsed from
    path: tuple[str, ...] = (),
    html_policy: str = "none",
) -> list[Diagnostic]:
    """Warn for every authored field equal to its model's declared default.

    Walks the parsed authored tree beside its raw YAML and reports only keys the
    author literally wrote, so a field a shorthand or a migration filled in is
    never named. A default of ``"auto"`` is a request to infer, not a value, so
    it is compared with the inference instead (variables, above). An inline
    nested board inherits ``html_policy`` from the board above it, so that key
    is judged against *html_policy*, the enclosing board's, not the schema.
    """
    warnings: list[Diagnostic] = []
    if isinstance(node, BaseModel) and isinstance(raw, dict):
        inherited = html_policy
        if isinstance(node, AuthoredBoard) and "html_policy" in node.model_fields_set:
            html_policy = node.html_policy
        for name, field in type(node).model_fields.items():
            key = field.alias or name
            if name == "style" or name not in node.model_fields_set or key not in raw:
                continue
            value = getattr(node, name)
            here = (*path, key)
            restated = (
                value == inherited
                if name == "html_policy" and isinstance(node, AuthoredBoard)
                else _equals_declared_default(field, value)
            )
            if restated:
                warnings.append(
                    restated_diagnostic(
                        WARN_RESTATED_INFERRED,
                        ".".join(here),
                        value,
                        "the default",
                        "default",
                    )
                )
            else:
                warnings += schema_default_restated(value, raw[key], here, html_policy)
    elif isinstance(node, dict) and isinstance(raw, dict):
        for key, value in node.items():
            if key in raw:
                here = (*path, str(key))
                if isinstance(value, Variable) and isinstance(raw[key], dict):
                    warnings += variable_restated(value, raw[key], ".".join(here))
                warnings += schema_default_restated(value, raw[key], here, html_policy)
    elif isinstance(node, list) and isinstance(raw, list) and len(node) == len(raw):
        for index, (value, item) in enumerate(zip(node, raw, strict=True)):
            warnings += schema_default_restated(
                value, item, (*path, str(index)), html_policy
            )
    return warnings
