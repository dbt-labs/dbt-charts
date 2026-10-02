"""Catch a dbt column rename before `dbt run`, from the manifest's model SQL.

`--warehouse` validation reads the *warehouse*, so it is a lagging detector:
edit `models/orders.sql` to rename a column and every board referencing the
old name stays green until the model is rebuilt. This module derives each
model's output columns statically from the SQL recorded in
`target/manifest.json` (written by a bare `dbt parse` — no build, no
credentials) and checks board queries against that, so the rename fails
validation at edit time.

Both halves ride the same seam as the `dct impact` index
(:func:`~dbt_charts.core.column_refs.parse_sql_statements`): dbt-call
substitution with provenance, all-branches skeletonization, and honest
failure. Static inference is not total, and the contract mirrors
`warehouse_check`'s ``unchecked``: a model whose output columns cannot be
derived — a macro or templated suffix in projection position, any projection
without an authored name (an unaliased aggregate, cast, subscript, or
literal), jinja branches that disagree about the projection list, a snapshot
(dbt injects meta columns at build time), a seed (its columns live in the
CSV, not the manifest), SQL that does not parse — is reported as *unresolved*
via ``WARN-DBT-MODEL-COLUMNS-UNRESOLVED``, never silently skipped and never
guessed at. Errors are raised only against a model whose columns resolved
completely, and only for tables that reached the query through a ``ref()``
call — a bare table name that merely collides with a model's name is not
evidence the query reads that model.

A trailing ``select * from final`` — the shape the dbt style guide
recommends models end with — is not itself unresolvable: when the ``*`` is
the sole projection and its FROM names a CTE from the model's own ``WITH``
clause, the star resolves through that CTE's own output columns, recursively
through a chain of same-statement CTEs. Everything that qualifies is an
allowlist, not a denylist, so a construct this module doesn't yet know about
fails closed rather than being silently passed through: the SELECT's
populated args must all be in ``_STAR_PASSTHROUGH_SAFE_ARGS`` (so no join,
lateral, MATCH_RECOGNIZE, ...), the star itself must carry no
``EXCEPT``/``EXCLUDE``/``REPLACE``/``RENAME`` modifier, the FROM source must
be a bare (unqualified, unpivoted) table with no column-renaming alias, and
the CTE it names must have no explicit column list and no nested ``WITH`` of
its own (which could locally shadow an outer CTE's name) anywhere on the way
to its underlying SELECT. A ``*`` reaching anything else — a bare table,
another dbt model already substituted to a bare name, or a cyclical CTE
reference — still reports unresolved rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlglot.expressions as exp
from sqlglot.dialects.dialect import Dialect as SqlglotDialect

from dbt_charts.core.column_refs import (
    extract_base_column_refs,
    parse_sql_statements,
)
from dbt_charts.core.compile.models.query.normalized import is_sql_query
from dbt_charts.core.compile.sql_guard import (
    SKELETON_PLACEHOLDER_PREFIX,
    sqlglot_dialect,
)
from dbt_charts.core.dbt_manifest import LoadedManifest, load_manifest
from dbt_charts.core.dbt_ref_check import query_target_paths
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_DBT_MODEL_COLUMN_MISSING,
    WARN_DBT_MODEL_COLUMNS_UNRESOLVED,
    WARN_DBT_QUERY_COLUMNS_INDETERMINATE,
)
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja

if TYPE_CHECKING:
    from dbt_charts.core.compile.compiler import CompileResult
    from dbt_charts.core.project import Project

_PLACEHOLDER_PREFIX = SKELETON_PLACEHOLDER_PREFIX

# Leading jinja-only headers — `{{ config(materialized='table') }}`, jinja
# comments, `--` line comments — skeletonize to bare placeholder identifiers
# (or noise) ahead of the SELECT. They never contribute output columns, so
# they are stripped before the shared seam sees the SQL.
# One content atom of a `{{ }}` header: any non-brace, a lone brace (config
# args carry dict literals — meta={'owner': …}), or a one-level nested jinja
# call (post_hook="{{ grant_select(this) }}"). A `}}` never matches as content,
# so the outer match terminates at the header's own closing braces.
_HEADER_ATOM = r"[^{}]|\{(?!\{)|\}(?!\})"
_LEADING_JINJA_HEADER_RE = re.compile(
    r"^(?:\s+|--[^\n]*|\{#.*?#\}"
    rf"|\{{\{{(?:{_HEADER_ATOM}|\{{\{{(?:{_HEADER_ATOM})*\}}\}})*\}}\}})+",
    re.DOTALL,
)

# Per-process memo mirroring dbt_manifest's: derivation walks every node with
# a parse each, and every validated board would otherwise redo it.
_MEMO_MAXSIZE = 16
_memo: dict[tuple[str, str], dict[str, ModelColumns]] = {}


@dataclass(frozen=True)
class ModelColumns:
    """One model's statically derived output columns, or why there are none."""

    columns: frozenset[str] = frozenset()
    unresolved: str | None = None


def resolve_model_output_columns(loaded: LoadedManifest) -> dict[str, ModelColumns]:
    """Model name (lowercased) → output columns, derived from each node's SQL.

    Reads ``compiled_code`` when the manifest carries it and falls back to
    ``raw_code`` (all a bare ``dbt parse`` writes). Snapshots are always
    unresolved — dbt injects ``dbt_valid_from``-style meta columns at build
    time. Seeds are always unresolved — their columns live in the CSV, and a
    manifest ``columns`` entry holds only what someone *documented*, which a
    complete claim cannot be built on.
    """
    key = (loaded.relpath, loaded.version)
    cached = _memo.get(key)
    if cached is not None:
        return cached

    # adapter_type is written by the user's dbt project — unconstrained. An
    # unmapped name (vertica, glue) must degrade to the generic-parse path,
    # never reach sqlglot's dialect lookup, which raises a bare ValueError.
    adapter_type = loaded.raw.get(  # type-state: silent_fallback — absent adapter_type is a legal manifest state; a None dialect takes the shared seam's generic-parse path
        "metadata", {}
    ).get("adapter_type")
    dialect = sqlglot_dialect(adapter_type) if isinstance(adapter_type, str) else None
    if dialect not in SqlglotDialect.classes:
        dialect = None
    out: dict[str, ModelColumns] = {}
    for node in loaded.raw.get(
        "nodes", {}
    ).values():  # type-state: silent_fallback — raw-dict tolerance is dbt_manifest's documented contract; no nodes means nothing to derive
        resource_type = node.get("resource_type")
        if resource_type not in ("model", "seed", "snapshot"):
            continue
        name = node.get("name")
        if not name:
            continue
        name_l = name.lower()
        if name_l in out:
            # A cross-package (or case-variant) name collision: either node's
            # columns would be a guess about which relation the ref meant.
            out[name_l] = ModelColumns(unresolved="two dbt nodes share this model name")
            continue
        if resource_type == "snapshot":
            out[name_l] = ModelColumns(
                unresolved="dbt injects snapshot meta columns at build time"
            )
            continue
        if resource_type == "seed":
            out[name_l] = ModelColumns(
                unresolved="seed columns come from its CSV, which the manifest "
                "does not carry"
            )
            continue
        sql = (
            node.get("compiled_code")
            or node.get("raw_code")
            or ""  # type-state: silent_fallback — bare `dbt parse` writes null compiled_code; an empty string resolves to the explicit no-SELECT unresolved state, not a pass
        )
        out[name_l] = _output_columns(sql, dialect=dialect)

    while len(_memo) >= _MEMO_MAXSIZE:
        _memo.pop(next(iter(_memo)))
    _memo[key] = out
    return out


def _output_columns(sql: str, dialect: str | None) -> ModelColumns:
    sql = _LEADING_JINJA_HEADER_RE.sub("", sql)
    parsed = parse_sql_statements(sql, dialect)
    if isinstance(parsed, str):
        return ModelColumns(unresolved=parsed)
    statements, via_dbt = parsed

    projection_sets: list[frozenset[str]] = []
    for statement in statements:
        result = _statement_columns(statement, via_dbt)
        if isinstance(result, str):
            return ModelColumns(unresolved=result)
        projection_sets.append(result)

    distinct = set(projection_sets)
    if not distinct:
        return ModelColumns(unresolved="the model contains no SELECT statement")
    if len(distinct) > 1:
        # The all-branches skeleton splits a statement-level {% if %} into one
        # statement per arm; arms that disagree mean the projection list is
        # decided at render time.
        return ModelColumns(
            unresolved="jinja branches disagree about the model's projection list"
        )
    return ModelColumns(columns=distinct.pop())


def _unwrap_select(expr: exp.Expr) -> exp.Select | None:
    """Peel set-operation arms (columns come from the first arm) and a bare
    subquery down to the ``SELECT`` whose projection list decides the
    statement's output columns, or ``None`` if it never bottoms out at one.
    """
    while isinstance(expr, (exp.Union, exp.Intersect, exp.Except)):
        expr = expr.this
    if isinstance(expr, exp.Subquery):
        expr = expr.this
    return expr if isinstance(expr, exp.Select) else None


def _carries_own_with(expr: exp.Expr) -> bool:
    """True if `expr` — or any set-operation/subquery layer ``_unwrap_select``
    would peel through on the way to its underlying ``SELECT`` — carries its
    own ``WITH`` clause (e.g. a set-operation arm defining a CTE, not just
    the arm's ``SELECT`` itself). Walks the identical peel path so a nested
    ``WITH`` can never hide behind a layer the unwrap steps over.
    """
    if isinstance(expr, exp.Query) and expr.args.get("with_"):
        return True
    while isinstance(expr, (exp.Union, exp.Intersect, exp.Except)):
        expr = expr.this
        if isinstance(expr, exp.Query) and expr.args.get("with_"):
            return True
    if isinstance(expr, exp.Subquery):
        expr = expr.this
        if isinstance(expr, exp.Query) and expr.args.get("with_"):
            return True
    return False


def _statement_columns(
    statement: exp.Expr, via_dbt: frozenset[str]
) -> frozenset[str] | str:
    """One statement's output column names, or why they cannot be known.

    ``via_dbt`` names the bare table names a ``ref()``/``source()``
    substitution produced anywhere in this SQL — passed through so a trailing
    star never resolves against a same-named CTE that a substitution could
    equally have meant (see ``_resolve_star_source``).
    """
    select = _unwrap_select(statement)
    if select is None:
        return "the model is not a plain SELECT statement"

    ctes = (
        {cte.alias.lower(): cte for cte in statement.ctes if cte.alias}
        if isinstance(statement, exp.Query)
        else {}
    )
    return _select_columns(select, ctes, frozenset(), via_dbt)


# SELECT-level args that never change what a plain `*` projects — an
# allowlist, not the reverse: an arg sqlglot adds later (its own
# MATCH_RECOGNIZE support once looked like this — `match` populates a
# measures clause whose columns aren't in any FROM source) is unsafe by
# default rather than silently passed through. Excluded on purpose: `kind`
# (BigQuery `AS STRUCT`/`AS VALUE` wraps the row shape), `into`, `match`
# (MATCH_RECOGNIZE), `laterals`, `joins`, `connect` (hierarchical query),
# `pivots`, `operation_modifiers`, `options` — none of those describe a bare
# passthrough of one source's columns.
_STAR_PASSTHROUGH_SAFE_ARGS = frozenset(
    {
        "with_",
        "expressions",
        "hint",
        "distinct",
        "from_",
        "prewhere",
        "where",
        "group",
        "having",
        "qualify",
        "windows",
        "distribute",
        "sort",
        "cluster",
        "order",
        "limit",
        "offset",
        "locks",
        "sample",
        "settings",
        "format",
    }
)


def _trailing_star_source(select: exp.Select) -> str | None:
    """The sole FROM source's table name, if this SELECT's only projection is
    a plain `*` (bare, or qualified by that same source) drawn plainly from
    it — the shape the dbt style guide's ``select * from final`` idiom
    takes. ``None`` for every other shape: a `*` mixed with other
    projections, a SELECT arg outside ``_STAR_PASSTHROUGH_SAFE_ARGS`` (a
    join, a lateral, MATCH_RECOGNIZE, ...), one modified by
    ``EXCEPT``/``EXCLUDE``/``REPLACE``/``RENAME``, or one whose source is
    schema-qualified, pivoted, or aliased with its own column list — all of
    which can change the projected column set in a way this static read does
    not attempt to replay. These stay unresolved rather than guessed at.
    """
    if len(select.expressions) != 1:
        return None
    populated = {k for k, v in select.args.items() if v}
    if populated - _STAR_PASSTHROUGH_SAFE_ARGS:
        return None
    projection = select.expressions[0]
    if isinstance(projection, exp.Star):
        star, qualifier = projection, None
    elif isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
        star, qualifier = projection.this, projection.table
    else:
        return None
    if star.args.get("except_") or star.args.get("replace") or star.args.get("rename"):
        return None
    from_ = select.args.get("from_")
    if from_ is None or not isinstance(from_.this, exp.Table):
        return None
    source = from_.this
    if source.args.get("db") or source.args.get("catalog") or source.args.get("pivots"):
        # A schema-qualified name is a real table, never a same-statement CTE
        # reference (CTE names are bare); a PIVOT reshapes the star's columns.
        return None
    alias = source.args.get("alias")
    if isinstance(alias, exp.TableAlias) and alias.columns:
        # `FROM final AS f(x, y)` renames the output — not replayed here.
        return None
    if qualifier and qualifier.lower() not in {
        n.lower() for n in (source.alias, source.name) if n
    }:
        return None
    return source.name


def _select_columns(
    select: exp.Select,
    ctes: dict[str, exp.CTE],
    seen: frozenset[str],
    via_dbt: frozenset[str],
) -> frozenset[str] | str:
    star_source = _trailing_star_source(select)
    if star_source is not None:
        return _resolve_star_source(star_source, ctes, seen, via_dbt)

    columns: set[str] = set()
    for projection in select.expressions:
        if isinstance(projection, exp.Star) or (
            isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
        ):
            return "a `*` projection hides the model's column list"
        # Only an authored name enters the claim: an alias, or a column
        # reference (optionally parenthesized). Everything else — count(*),
        # COLUMNS(), a subscript or JSON extract, a bare literal, an
        # unaliased cast (named after the inner column on Postgres, `f0_` on
        # BigQuery, the expression text on DuckDB/Snowflake) — has a name
        # only the engine decides; sqlglot's output_name/alias_or_name
        # render literal text for several of those, a fabricated name inside
        # a complete claim.
        if isinstance(projection, exp.Alias):
            name = projection.output_name
        else:
            node = projection
            while isinstance(node, exp.Paren):
                node = node.this
            if not isinstance(node, exp.Column) or isinstance(node.this, exp.Star):
                return "a projection carries no output name this static read can know"
            name = node.name
        if not name:
            return "a projection carries no output name this static read can know"
        if _PLACEHOLDER_PREFIX in name.lower():
            # A macro or templated fragment in projection position — the
            # engine decides an output name this static read cannot know.
            return (
                "a projection's output name is decided by a macro or "
                "templated expression"
            )
        columns.add(name.lower())
    if not columns:
        return "the model projects no columns"
    return frozenset(columns)


def _resolve_star_source(
    name: str,
    ctes: dict[str, exp.CTE],
    seen: frozenset[str],
    via_dbt: frozenset[str],
) -> frozenset[str] | str:
    """The output columns of a same-statement CTE a trailing `*` reads from.

    Only a CTE defined in the model's own ``WITH`` clause is resolved this
    way — a bare table (or another dbt model reached via ``ref()``, already
    substituted to a bare name before this module ever sees the SQL) is not
    something a single model's static read can know the columns of, so it
    stays unresolved rather than guessed at.
    """
    name_l = name.lower()
    cte = ctes.get(name_l)
    if cte is None:
        return "a `*` projection hides the model's column list"
    if name_l in via_dbt:
        # This bare name also arrived via a ref()/source() substitution
        # elsewhere in the statement — a common dbt convention names a
        # staging CTE after the very model it imports. Substitution erases
        # which occurrence is which, so a same-named CTE is never trusted
        # once this name is ambiguous with a dbt call.
        return (
            "a `*` projection's FROM name matches both a same-statement CTE "
            "and a dbt ref()/source() call"
        )
    if name_l in seen:
        # A literal self-reference with no ref()/source() call involved at
        # all (`WITH stg AS (SELECT * FROM stg) SELECT * FROM stg`) — the
        # via_dbt check above only catches the case where the collision came
        # from a dbt call. Resolving this would recurse into the same CTE
        # forever.
        return "the model's `*` projection passes through a cyclical CTE reference"
    alias = cte.args.get("alias")
    if isinstance(alias, exp.TableAlias) and alias.columns:
        # `WITH final (x, y) AS (...)` renames the CTE's own output — not
        # replayed here.
        return "a `*` projection resolves to a CTE with an explicit column list"
    if _carries_own_with(cte.this):
        # A CTE with its own nested WITH — whether directly on its SELECT or
        # on a set-operation/subquery wrapper peeled on the way to one —
        # could shadow an outer CTE's name; scoping this static read does
        # not attempt to replay.
        return "a `*` projection resolves to a CTE with its own nested WITH clause"
    inner = _unwrap_select(cte.this)
    if inner is None:
        return "a `*` projection resolves to a CTE that is not a plain SELECT statement"
    return _select_columns(inner, ctes, seen | {name_l}, via_dbt)


def check_model_columns(compile_result: CompileResult, project: Project) -> None:
    """Check board column references against statically derived model columns.

    Mutates ``compile_result.errors`` / ``.warnings``, like
    ``check_manifest_refs``. No-ops without a manifest — nothing to claim. A
    manifest that exists but cannot be read is a real fault at a real
    location and is reported here (``check_manifest_refs`` only loads the
    manifest when a query carries a ref()/source() call, so it cannot be
    relied on to have said it).

    Claims are scoped to ref() provenance: only a table that reached the
    query through ``{{ ref(...) }}`` is checked against the model registry —
    a bare name that happens to match a model may be a same-named relation on
    a different source entirely.
    """
    if compile_result.board is None:
        # A board that failed compile has no query registry to analyze; its
        # own compile errors are the report.
        return
    dbt_queries = {
        name
        for name, query in compile_result.query_registry.items()
        if is_sql_query(query)
        and (
            has_dbt_jinja(query.sql)
            or (query.setup_sql is not None and has_dbt_jinja(query.setup_sql))
        )
    }
    target_paths = query_target_paths(compile_result, project, dbt_queries)
    manifests: dict[str | None, LoadedManifest] = {}
    # The default manifest is read even when no query calls ref(): an unreadable
    # one is a fault the author should hear about.
    for target_path in dict.fromkeys([None, *target_paths.values()]):
        reads_it = target_path is not None or None in target_paths.values()
        try:
            loaded = load_manifest(project, target_path, optional=not reads_it)
        except ExecutionError as exc:
            diagnostic = exc.to_diagnostic()
            if diagnostic not in compile_result.errors:
                compile_result.errors.append(diagnostic)
            continue
        if loaded is not None:
            manifests[target_path] = loaded
    if not manifests or not dbt_queries:
        return

    column_refs = extract_base_column_refs(compile_result)
    models_by_path: dict[str | None, dict[str, ModelColumns]] = {}
    warned: set[tuple[str, str]] = set()

    for query_name, refs in column_refs.items():
        if refs.indeterminate is not None:
            if query_name in dbt_queries:
                # A dbt-backed query this check cannot vouch for — the gap
                # stays visible, never a silent green. Plain-SQL queries are
                # the SQL lint tiers' business.
                compile_result.warnings.append(
                    Diagnostic.from_code(
                        WARN_DBT_QUERY_COLUMNS_INDETERMINATE,
                        message=WARN_DBT_QUERY_COLUMNS_INDETERMINATE.message_template.format(
                            query_name=query_name, reason=refs.indeterminate
                        ),
                        fix=WARN_DBT_QUERY_COLUMNS_INDETERMINATE.fix_template,
                        query=query_name,
                    )
                )
            continue
        for table, column in sorted(refs.columns):
            table_l = table.lower()
            if table_l not in refs.via_dbt:
                continue
            target_path = target_paths.get(query_name)
            if target_path not in models_by_path:
                loaded = manifests.get(target_path)
                models_by_path[target_path] = (
                    resolve_model_output_columns(loaded) if loaded else {}
                )
            model = models_by_path[target_path].get(table_l)
            if model is None:
                continue
            if model.unresolved is not None:
                if (query_name, table_l) not in warned:
                    warned.add((query_name, table_l))
                    compile_result.warnings.append(
                        Diagnostic.from_code(
                            WARN_DBT_MODEL_COLUMNS_UNRESOLVED,
                            message=WARN_DBT_MODEL_COLUMNS_UNRESOLVED.message_template.format(
                                query_name=query_name,
                                model=table,
                                reason=model.unresolved,
                            ),
                            fix=WARN_DBT_MODEL_COLUMNS_UNRESOLVED.fix_template,
                            query=query_name,
                        )
                    )
                continue
            if column.lower() not in model.columns:
                compile_result.errors.append(
                    DbtChartsError.from_code(
                        ERR_DBT_MODEL_COLUMN_MISSING,
                        query_name=query_name,
                        column_name=column,
                        model=table,
                        available=sorted(model.columns),
                    ).to_diagnostic()
                )
