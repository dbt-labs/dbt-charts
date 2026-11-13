"""Warn where a source cannot honor the board's `regional.time_zone`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.query.normalized import is_sql_query
from dbt_charts.core.compile.models.source import (
    authored_time_zone,
    parse_source_config,
)
from dbt_charts.core.diagnostics.codes_execute import (
    WARN_SESSION_TIME_ZONE_UNSUPPORTED,
    WARN_SOURCE_TIME_ZONE_OVERRIDDEN,
)
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.dialects import DIALECTS
from dbt_charts.core.text.time_zones import UTC

if TYPE_CHECKING:
    from dbt_charts.core.compile.compiler import CompileResult
    from dbt_charts.core.project import Project


def check_session_time_zones(compile_result: CompileResult, project: Project) -> None:
    """Warn once per named source whose queries cannot run in the board's zone.

    Either the warehouse has no session time zone (and the board's is not UTC),
    or the source authors a zone of its own that differs from the board's, which
    wins. A `dbt_profile` source names its warehouse only in profiles.yml, so it
    is not inspected here.

    Mutates ``compile_result.warnings``.
    """
    board = compile_result.board
    if board is None:
        return
    zone = board.regional.time_zone
    sources = project.declared_sources.sources
    names = dict.fromkeys(
        query.source
        for query in compile_result.query_registry.values()
        if is_sql_query(query) and query.source is not None
    )
    for name in names:
        authored = board.sources.get(name) or sources.get(name)
        if authored is None:
            continue
        try:
            config = parse_source_config(dict(authored))
        except ValueError:
            # A malformed source is reported where it is used.
            continue
        if config.type not in DIALECTS:
            continue
        dialect = DIALECTS[config.type]
        if (
            zone != UTC
            and not dialect.time_zone_per_request
            and dialect.session_time_zone_sql(zone) is None
        ):
            compile_result.warnings.append(
                Diagnostic.from_code(
                    WARN_SESSION_TIME_ZONE_UNSUPPORTED,
                    message=WARN_SESSION_TIME_ZONE_UNSUPPORTED.message_template.format(
                        source=name, adapter_type=config.type, time_zone=zone
                    ),
                    fix=WARN_SESSION_TIME_ZONE_UNSUPPORTED.fix_template,
                )
            )
        source_zone = authored_time_zone(config)
        if source_zone is not None and source_zone != zone:
            compile_result.warnings.append(
                Diagnostic.from_code(
                    WARN_SOURCE_TIME_ZONE_OVERRIDDEN,
                    message=WARN_SOURCE_TIME_ZONE_OVERRIDDEN.message_template.format(
                        source=name, source_zone=source_zone, time_zone=zone
                    ),
                    fix=WARN_SOURCE_TIME_ZONE_OVERRIDDEN.fix_template,
                )
            )
