"""AuthoredBoard compilation module.

Stage: COMPILE
Purpose: Transform YAML board definitions into Board objects
ready for execution and rendering.

Entry Points:
    - compile(yaml_content: str) -> CompileResult
    - compile_file(board: BoardFile) -> CompileResult

Outputs:
    - Board: Fully normalized board with all references resolved
    - CompileResult: Result container with board, errors, and warnings

Dependencies:
    - yaml (PyYAML)
    - pydantic

This module is pure - it does NOT import from execute/ or render/.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbt_charts.core.compile.compiler import (
        CompileResult as CompileResult,
        compile as compile,
        compile_authored_board as compile_authored_board,
        compile_file as compile_file,
    )
    from dbt_charts.core.compile.config import (
        ProjectSourcesConfig as ProjectSourcesConfig,
        get_config as get_config,
        get_project_warnings_ignore as get_project_warnings_ignore,
        load_config as load_config,
        load_project_sources as load_project_sources,
    )
    from dbt_charts.core.compile.errors import (
        CompilationError as CompilationError,
        JinjaError as JinjaError,
        ParseError as ParseError,
        ReferenceError as ReferenceError,
        ValidationError as ValidationError,
    )
    from dbt_charts.core.compile.models.board.authored import (
        AuthoredBoard as AuthoredBoard,
        LayoutType as LayoutType,
    )
    from dbt_charts.core.compile.models.board.normalized import (
        Board as Board,
        Layout as Layout,
        LayoutItem as LayoutItem,
        VariableValues as VariableValues,
        is_board as is_board,
    )
    from dbt_charts.core.compile.models.chart.authored import (
        AreaChart as AreaChart,
        AuthoredChart as AuthoredChart,
        BarChart as BarChart,
        CalloutChart as CalloutChart,
        ChartType as ChartType,
        CustomChart as CustomChart,
        CustomChartStylePatch as CustomChartStylePatch,
        GeoshapeChart as GeoshapeChart,
        HeatmapChart as HeatmapChart,
        KpiChart as KpiChart,
        LineChart as LineChart,
        PieChart as PieChart,
        PointMapChart as PointMapChart,
        ScatterChart as ScatterChart,
        SparkBarChart as SparkBarChart,
        SparkConfig as SparkConfig,
        SparkTypeLiteral as SparkTypeLiteral,
        TableChart as TableChart,
        TableColumnConfig as TableColumnConfig,
    )
    from dbt_charts.core.compile.models.chart.normalized import (
        ChartDependencies as ChartDependencies,
    )
    from dbt_charts.core.compile.models.query.authored import (
        AuthoredCompactValuesQuery as AuthoredCompactValuesQuery,
        AuthoredQuery as AuthoredQuery,
        AuthoredSchemaQuery as AuthoredSchemaQuery,
        AuthoredSqlQuery as AuthoredSqlQuery,
        AuthoredValuesQuery as AuthoredValuesQuery,
    )
    from dbt_charts.core.compile.models.query.normalized import (
        VALID_QUERY_TYPES as VALID_QUERY_TYPES,
        AnyQuery as AnyQuery,
        SqlQuery as SqlQuery,
        is_sql_query as is_sql_query,
    )
    from dbt_charts.core.compile.models.source import (
        VALID_SOURCE_TYPES as VALID_SOURCE_TYPES,
        BigQuerySourceConfig as BigQuerySourceConfig,
        CsvSourceConfig as CsvSourceConfig,
        DatabricksSourceConfig as DatabricksSourceConfig,
        DbtProfileSourceConfig as DbtProfileSourceConfig,
        DuckDBSourceConfig as DuckDBSourceConfig,
        JsonSourceConfig as JsonSourceConfig,
        ParquetSourceConfig as ParquetSourceConfig,
        PostgresSourceConfig as PostgresSourceConfig,
        RedshiftSourceConfig as RedshiftSourceConfig,
        SnowflakeSourceConfig as SnowflakeSourceConfig,
        SourceConfig as SourceConfig,
        SQLiteSourceConfig as SQLiteSourceConfig,
        TrinoSourceConfig as TrinoSourceConfig,
        is_database_source as is_database_source,
        is_file_source as is_file_source,
        parse_source_config as parse_source_config,
    )
    from dbt_charts.core.compile.models.variable.authored import (
        Variable as Variable,
        VariableInputType as VariableInputType,
    )
    from dbt_charts.core.compile.normalize.chart_focus import (
        focus_on_chart as focus_on_chart,
    )
    from dbt_charts.core.compile.parse.meta import (
        MetaLintConfig as MetaLintConfig,
        find_meta_files as find_meta_files,
        load_meta_file as load_meta_file,
        resolve_meta_lint as resolve_meta_lint,
    )
    from dbt_charts.core.compile.sources.detection import (
        DB_INFO_MAP as DB_INFO_MAP,
        detect_database_type_from_registry as detect_database_type_from_registry,
        detect_dbt_connection_string as detect_dbt_connection_string,
        detect_dbt_database_type as detect_dbt_database_type,
        get_database_info as get_database_info,
        parse_dbt_profiles_yaml as parse_dbt_profiles_yaml,
    )
    from dbt_charts.core.compile.template.parameterized import (
        ParameterizedQuery as ParameterizedQuery,
        render_parameterized as render_parameterized,
        render_parameterized_with_queries as render_parameterized_with_queries,
    )
    from dbt_charts.core.compile.template.variables import (
        parse_variable_json_strings as parse_variable_json_strings,
    )

# Importing any submodule runs this file, so an eager re-export would build every
# board model for a caller that wanted one error class. As in ``agent_api``, no name
# here may equal a submodule's leaf name.
_LAZY_ATTRS: dict[str, str] = {
    "CompileResult": "dbt_charts.core.compile.compiler",
    "compile": "dbt_charts.core.compile.compiler",
    "compile_authored_board": "dbt_charts.core.compile.compiler",
    "compile_file": "dbt_charts.core.compile.compiler",
    "ProjectSourcesConfig": "dbt_charts.core.compile.config",
    "get_config": "dbt_charts.core.compile.config",
    "get_project_warnings_ignore": "dbt_charts.core.compile.config",
    "load_config": "dbt_charts.core.compile.config",
    "load_project_sources": "dbt_charts.core.compile.config",
    "CompilationError": "dbt_charts.core.compile.errors",
    "JinjaError": "dbt_charts.core.compile.errors",
    "ParseError": "dbt_charts.core.compile.errors",
    "ReferenceError": "dbt_charts.core.compile.errors",
    "ValidationError": "dbt_charts.core.compile.errors",
    "AuthoredBoard": "dbt_charts.core.compile.models.board.authored",
    "LayoutType": "dbt_charts.core.compile.models.board.authored",
    "Board": "dbt_charts.core.compile.models.board.normalized",
    "Layout": "dbt_charts.core.compile.models.board.normalized",
    "LayoutItem": "dbt_charts.core.compile.models.board.normalized",
    "VariableValues": "dbt_charts.core.compile.models.board.normalized",
    "is_board": "dbt_charts.core.compile.models.board.normalized",
    "AreaChart": "dbt_charts.core.compile.models.chart.authored",
    "AuthoredChart": "dbt_charts.core.compile.models.chart.authored",
    "BarChart": "dbt_charts.core.compile.models.chart.authored",
    "CalloutChart": "dbt_charts.core.compile.models.chart.authored",
    "ChartType": "dbt_charts.core.compile.models.chart.authored",
    "CustomChart": "dbt_charts.core.compile.models.chart.authored",
    "CustomChartStylePatch": "dbt_charts.core.compile.models.chart.authored",
    "GeoshapeChart": "dbt_charts.core.compile.models.chart.authored",
    "HeatmapChart": "dbt_charts.core.compile.models.chart.authored",
    "KpiChart": "dbt_charts.core.compile.models.chart.authored",
    "LineChart": "dbt_charts.core.compile.models.chart.authored",
    "PieChart": "dbt_charts.core.compile.models.chart.authored",
    "PointMapChart": "dbt_charts.core.compile.models.chart.authored",
    "ScatterChart": "dbt_charts.core.compile.models.chart.authored",
    "SparkBarChart": "dbt_charts.core.compile.models.chart.authored",
    "SparkConfig": "dbt_charts.core.compile.models.chart.authored",
    "SparkTypeLiteral": "dbt_charts.core.compile.models.chart.authored",
    "TableChart": "dbt_charts.core.compile.models.chart.authored",
    "TableColumnConfig": "dbt_charts.core.compile.models.chart.authored",
    "ChartDependencies": "dbt_charts.core.compile.models.chart.normalized",
    "AuthoredCompactValuesQuery": "dbt_charts.core.compile.models.query.authored",
    "AuthoredQuery": "dbt_charts.core.compile.models.query.authored",
    "AuthoredSchemaQuery": "dbt_charts.core.compile.models.query.authored",
    "AuthoredSqlQuery": "dbt_charts.core.compile.models.query.authored",
    "AuthoredValuesQuery": "dbt_charts.core.compile.models.query.authored",
    "AnyQuery": "dbt_charts.core.compile.models.query.normalized",
    "SqlQuery": "dbt_charts.core.compile.models.query.normalized",
    "VALID_QUERY_TYPES": "dbt_charts.core.compile.models.query.normalized",
    "is_sql_query": "dbt_charts.core.compile.models.query.normalized",
    "BigQuerySourceConfig": "dbt_charts.core.compile.models.source",
    "CsvSourceConfig": "dbt_charts.core.compile.models.source",
    "DatabricksSourceConfig": "dbt_charts.core.compile.models.source",
    "DbtProfileSourceConfig": "dbt_charts.core.compile.models.source",
    "DuckDBSourceConfig": "dbt_charts.core.compile.models.source",
    "JsonSourceConfig": "dbt_charts.core.compile.models.source",
    "ParquetSourceConfig": "dbt_charts.core.compile.models.source",
    "PostgresSourceConfig": "dbt_charts.core.compile.models.source",
    "RedshiftSourceConfig": "dbt_charts.core.compile.models.source",
    "SQLiteSourceConfig": "dbt_charts.core.compile.models.source",
    "SnowflakeSourceConfig": "dbt_charts.core.compile.models.source",
    "SourceConfig": "dbt_charts.core.compile.models.source",
    "TrinoSourceConfig": "dbt_charts.core.compile.models.source",
    "VALID_SOURCE_TYPES": "dbt_charts.core.compile.models.source",
    "is_database_source": "dbt_charts.core.compile.models.source",
    "is_file_source": "dbt_charts.core.compile.models.source",
    "parse_source_config": "dbt_charts.core.compile.models.source",
    "Variable": "dbt_charts.core.compile.models.variable.authored",
    "VariableInputType": "dbt_charts.core.compile.models.variable.authored",
    "focus_on_chart": "dbt_charts.core.compile.normalize.chart_focus",
    "MetaLintConfig": "dbt_charts.core.compile.parse.meta",
    "find_meta_files": "dbt_charts.core.compile.parse.meta",
    "load_meta_file": "dbt_charts.core.compile.parse.meta",
    "resolve_meta_lint": "dbt_charts.core.compile.parse.meta",
    "DB_INFO_MAP": "dbt_charts.core.compile.sources.detection",
    "detect_database_type_from_registry": "dbt_charts.core.compile.sources.detection",
    "detect_dbt_connection_string": "dbt_charts.core.compile.sources.detection",
    "detect_dbt_database_type": "dbt_charts.core.compile.sources.detection",
    "get_database_info": "dbt_charts.core.compile.sources.detection",
    "parse_dbt_profiles_yaml": "dbt_charts.core.compile.sources.detection",
    "ParameterizedQuery": "dbt_charts.core.compile.template.parameterized",
    "render_parameterized": "dbt_charts.core.compile.template.parameterized",
    "render_parameterized_with_queries": "dbt_charts.core.compile.template.parameterized",
    "parse_variable_json_strings": "dbt_charts.core.compile.template.variables",
}

__all__ = [
    # Main entry points
    "compile",
    "compile_authored_board",
    "compile_file",
    "CompileResult",
    "focus_on_chart",
    # Input types
    "AuthoredBoard",
    "AuthoredChart",
    "BarChart",
    "LineChart",
    "AreaChart",
    "ScatterChart",
    "HeatmapChart",
    "PieChart",
    "KpiChart",
    "TableChart",
    "PointMapChart",
    "GeoshapeChart",
    "CalloutChart",
    "SparkBarChart",
    "CustomChart",
    "CustomChartStylePatch",
    "AuthoredCompactValuesQuery",
    "AuthoredQuery",
    "AuthoredSchemaQuery",
    "AuthoredSqlQuery",
    "AuthoredValuesQuery",
    "Variable",
    # Compiled types
    "Board",
    "AnyQuery",
    "Layout",
    "LayoutItem",
    "VariableValues",
    "ChartDependencies",
    # Unified query types
    "SqlQuery",
    "VALID_QUERY_TYPES",
    # Source types
    "SourceConfig",
    "PostgresSourceConfig",
    "SnowflakeSourceConfig",
    "BigQuerySourceConfig",
    "RedshiftSourceConfig",
    "DatabricksSourceConfig",
    "TrinoSourceConfig",
    "DuckDBSourceConfig",
    "SQLiteSourceConfig",
    "CsvSourceConfig",
    "ParquetSourceConfig",
    "JsonSourceConfig",
    "DbtProfileSourceConfig",
    "VALID_SOURCE_TYPES",
    "parse_source_config",
    "is_database_source",
    "is_file_source",
    # Config
    "ProjectSourcesConfig",
    "get_config",
    "load_config",
    "load_project_sources",
    "get_project_warnings_ignore",
    # Meta configuration
    "MetaLintConfig",
    "find_meta_files",
    "load_meta_file",
    "resolve_meta_lint",
    # Type guards
    "is_board",
    "is_sql_query",
    # Enums
    "ChartType",
    "LayoutType",
    "VariableInputType",
    "SparkTypeLiteral",
    # Spark charts
    "SparkConfig",
    "TableColumnConfig",
    # Errors
    "CompilationError",
    "ParseError",
    "ValidationError",
    "ReferenceError",
    "JinjaError",
    # Parameterized queries
    "ParameterizedQuery",
    "render_parameterized",
    "render_parameterized_with_queries",
    # Variable utilities
    "parse_variable_json_strings",
    # Source detection utilities
    "DB_INFO_MAP",
    "detect_database_type_from_registry",
    "detect_dbt_connection_string",
    "detect_dbt_database_type",
    "get_database_info",
    "parse_dbt_profiles_yaml",
]


def __getattr__(name: str) -> Any:  # type-state: explicit_any — PEP 562 re-export
    module = _LAZY_ATTRS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
