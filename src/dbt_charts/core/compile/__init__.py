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

from dbt_charts.core.compile.compiler import (
    CompileResult,
    compile,
    compile_authored_board,
    compile_file,
)
from dbt_charts.core.compile.config import (
    ProjectSourcesConfig,
    get_config,
    get_project_warnings_ignore,
    load_config,
    load_project_sources,
)
from dbt_charts.core.compile.errors import (
    CompilationError,
    JinjaError,
    ParseError,
    ReferenceError,
    ValidationError,
)
from dbt_charts.core.compile.models.board.authored import (
    AuthoredBoard,
    LayoutType,
)
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
    VariableValues,
    is_board,
)
from dbt_charts.core.compile.models.chart.authored import (
    AreaChart,
    AuthoredChart,
    BarChart,
    CalloutChart,
    ChartType,
    CustomChart,
    CustomChartStylePatch,
    GeoshapeChart,
    HeatmapChart,
    KpiChart,
    LineChart,
    PieChart,
    PointMapChart,
    ScatterChart,
    SparkBarChart,
    SparkConfig,
    SparkTypeLiteral,
    TableChart,
    TableColumnConfig,
)
from dbt_charts.core.compile.models.chart.normalized import ChartDependencies
from dbt_charts.core.compile.models.query.authored import (
    AuthoredCompactValuesQuery,
    AuthoredHttpQuery,
    AuthoredQuery,
    AuthoredSchemaQuery,
    AuthoredSqlQuery,
    AuthoredValuesQuery,
)
from dbt_charts.core.compile.models.query.normalized import (
    VALID_QUERY_TYPES,
    AnyQuery,
    HttpQuery,
    SqlQuery,
    is_http_query,
    is_sql_query,
)
from dbt_charts.core.compile.models.source import (
    VALID_SOURCE_TYPES,
    BigQuerySourceConfig,
    CsvSourceConfig,
    DatabricksSourceConfig,
    DbtProfileSourceConfig,
    DuckDBSourceConfig,
    HttpSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
    PostgresSourceConfig,
    RedshiftSourceConfig,
    SnowflakeSourceConfig,
    SourceConfig,
    SQLiteSourceConfig,
    TrinoSourceConfig,
    is_api_source,
    is_database_source,
    is_file_source,
    parse_source_config,
)
from dbt_charts.core.compile.models.variable.authored import (
    Variable,
    VariableInputType,
)
from dbt_charts.core.compile.normalize.chart_focus import focus_on_chart
from dbt_charts.core.compile.parse.meta import (
    MetaLintConfig,
    find_meta_files,
    load_meta_file,
    resolve_meta_lint,
)
from dbt_charts.core.compile.sources.detection import (
    DB_INFO_MAP,
    detect_database_type_from_registry,
    detect_dbt_connection_string,
    detect_dbt_database_type,
    get_database_info,
    parse_dbt_profiles_yaml,
)
from dbt_charts.core.compile.template.parameterized import (
    ParameterizedQuery,
    render_parameterized,
    render_parameterized_with_queries,
)
from dbt_charts.core.compile.template.variables import parse_variable_json_strings

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
    "AuthoredHttpQuery",
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
    "HttpQuery",
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
    "HttpSourceConfig",
    "DbtProfileSourceConfig",
    "VALID_SOURCE_TYPES",
    "parse_source_config",
    "is_database_source",
    "is_file_source",
    "is_api_source",
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
    "is_http_query",
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
