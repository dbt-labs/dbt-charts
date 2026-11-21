"""Type stub for the PyO3 extension `dbt_charts._engine` (crate at libs/chart-svg/python)."""

from typing import Any, NotRequired, Protocol, TypedDict

class RenderDiagnostic(TypedDict):
    code: str
    params: dict[str, Any]

class CompileDiagnostic(TypedDict):
    """A compile finding or refusal: where it sits (`path`, `range` as
    `{file, start_line, end_line, columns?}`, `related` as `[{path?, range?, message}]`) in
    Python's wire shape; `message` and `fix` are rendered from `code` and `params`, and a
    `source_needle` rides in `params`."""

    code: str
    params: dict[str, Any]
    level: str
    related: list[dict[str, Any]]
    path: NotRequired[str]
    range: NotRequired[dict[str, Any]]
    hint: NotRequired[str]

class RenderDiagnosticError(ValueError):
    code: str
    params: dict[str, Any]

class Rows:
    """One query result parsed by the engine, shared by every call that draws from it."""

class ProjectFiles(Protocol):
    def read(self, path: str, /) -> str | None: ...
    def list(self, dir: str, /) -> list[str]: ...
    def exists(self, path: str, /) -> bool: ...

class CompileError(ValueError):
    """A refusal: its `diagnostics` are coded and located as a compile finding is."""

    diagnostics: list[CompileDiagnostic]
    # The non-fatal findings gathered before the refusal, such as a migration notice.
    warnings: list[CompileDiagnostic]

class CompiledBoard:
    """A board compiled in Rust. Opaque: no board model crosses to Python.

    `validate_findings` are the errors only `dct validate` reports; compile accepts the board."""

    def diagnostics(self) -> list[CompileDiagnostic]: ...
    def index_json(self) -> str: ...
    def host_facts_json(self) -> str: ...
    def validate_findings(self) -> list[CompileDiagnostic]: ...

class SqlAnalyzer(Protocol):
    """The host's SQL analysis: one call per compile, one outcome per request, in request order.
    It resolves each request's `source` to a dialect itself, so it holds the source registry."""

    def analyze_all(
        self, requests: list[dict[str, str | None]]
    ) -> list[dict[str, Any]]: ...

# A `cache:` layer as the host sends it: (enabled, ttl, prefetch), each None where the scope
# authors nothing. `cache_root` is the project's, `source_caches` each declared source's.
CacheLayer = tuple[bool | None, str | None, bool | None]

def compile(
    files: ProjectFiles,
    path: str | None,
    *,
    boards_root: str,
    today: str,
    default_theme: str,
    html_policy_ceiling: str | None,
    sources: dict[str, str],
    cache_root: CacheLayer,
    source_caches: dict[str, CacheLayer],
    implicit_source: str | None,
    zones: dict[str, tuple[str, str | None]],
    sql: SqlAnalyzer | None = None,
    named: bool = False,
    text: str | None = None,
    anchor: str | None = None,
) -> CompiledBoard: ...
def index(
    files: ProjectFiles,
    path: str,
    *,
    boards_root: str,
    today: str,
    sources: list[str],
    implicit_source: str | None,
) -> str: ...
def plan(
    board: CompiledBoard,
    given: str,
    builtins: str,
    now_epoch: int,
    all_queries: bool,
    active_tab_only: bool,
    strict: bool,
    only_charts: list[str] | None,
) -> str:
    """The QueryPlan JSON (`core/execute/query_plan.py`). `chart_queries` and `variable_queries` name
    the plan key of each shown chart's and variable's queries; they cover only what is planned."""
    ...

class RenderedBoard:
    svg: str
    charts: dict[str, str]
    diagnostics: list[dict[str, Any]]
    boxes: dict[str, dict[str, float]]
    width: float
    height: float

class CellDiagnostic(RenderDiagnostic):
    chart: str | None

class RenderedCell:
    svg: str
    height: float
    diagnostics: list[CellDiagnostic]

class ShapedFonts: ...

def load_shaped_fonts(fonts_dir: str) -> ShapedFonts: ...
def render_board(
    board: CompiledBoard,
    datasets: str,
    chart_errors: str,
    empty_filters: str,
    automatic_links: str,
    variables: str,
    presets: str,
    overridden: list[str],
    fonts: ShapedFonts,
    rendered_at: str,
    link_context: str | None,
) -> RenderedBoard: ...

class PreparedBoard:
    """One render's board and run, prepared once for every chart the host draws in a cell.
    The arguments are `render_board`'s, less `empty_filters` and `automatic_links`, which each draw carries, and `datasets`, the
    results `load_rows` parsed by plan key; `geo` is the geo sources' JSON, `{source: {descriptor, document}}`."""

    def __init__(
        self,
        board: CompiledBoard,
        datasets: dict[str, Rows],
        chart_errors: str,
        variables: str,
        presets: str,
        overridden: list[str],
        fonts: ShapedFonts,
        rendered_at: str,
        link_context: str | None,
        geo: str,
    ) -> None: ...
    def draw(
        self,
        placement: str,
        empty_filters: list[tuple[str, str]],
        automatic_link: str | None,
        width: float,
        height: float | None,
        inset: tuple[float, float, float, float],
        title_shift: tuple[float, float],
        images: dict[str, str],
    ) -> RenderedCell:
        """The chart placed as `placement` (`RenderedBoard.boxes`' keys) drawn in the host's
        cell: `width` and `height` (`None`: the chart's own) inside the padding `inset`
        (left, right, top, bottom) the slot was shrunk by, under the cols-row
        `title_shift` (`title_dy`, `body_dy`), as `render_native` takes them, naming
        `empty_filters` when it has nothing to show, and `automatic_link` the link production
        resolved for it (read only for a chart that authors none). `images` holds the `data:` URI of each
        project image the chart's text writes, by that reference: the engine reads no
        files. The chart draws at the larger of the cell's height and its natural one, and
        `RenderedCell.height` is that height.
        No other part of the board is laid out."""
        ...

def load_rows(rows_json: str) -> Rows: ...
def render_chart(
    rows: Rows, datasets: dict[str, Rows], layout_json: str
) -> tuple[str, list[RenderDiagnostic], float | None]: ...
def pie_layout(
    rows: Rows, datasets: dict[str, Rows], layout_json: str
) -> tuple[str, float, float, float | None]: ...
def measure_table_natural_width(rows: Rows, layout_json: str) -> float | None: ...
def table_title_geometry(payload_json: str) -> tuple[float, float]: ...
def font_loads() -> int: ...
def geometry_decodes() -> int: ...
def check_template(source: str) -> None: ...
def split_line_gaps(runs: list[list[float]], extent: float) -> list[list[float]]: ...
def fit_dash(dashes: list[float], length: float, closed: bool) -> list[float]: ...
def fit_dash_pattern(pattern: str, length: float, closed: bool) -> str: ...
def rect_perimeter(w: float, h: float, rx: float) -> float: ...
def languages() -> list[str]: ...
def language_for_time_locale(name: str | None) -> str: ...
def number_locales() -> list[str]: ...
def time_locales() -> list[str]: ...
def number_locale(tag: str) -> str: ...
def time_locale(tag: str) -> str: ...
def magnitude_words(tag: str) -> tuple[list[str], list[str]]: ...
def week_start(tag: str) -> int: ...
def message(
    lang: str,
    id: str,
    n: int | None = None,
    args: dict[str, str] | None = None,
) -> str: ...
def plural_expr(lang: str, x: str) -> str: ...
def message_forms(lang: str, id: str) -> dict[str, str]: ...
def apply_case(lang: str, text: str, case: str) -> str: ...
def migrate_mapping(
    document: Any, today: str, caller: str
) -> tuple[Any, list[str], str]: ...  # type-state: explicit_any — a raw board mapping
def plan_text_migration(
    document: Any, stop_target: str | None, caller: str
) -> tuple[
    Any, ...
]: ...  # type-state: explicit_any — a raw board mapping; ("current",) | ("planned", staged, edits, notices) | (error_kind, message)
def retired_keys(
    document: Any, caller: str
) -> list[
    tuple[list[str], str, list[str] | None, str | None]
]: ...  # type-state: explicit_any — a raw board mapping
def retired_theme_renames() -> list[tuple[str, str]]: ...
