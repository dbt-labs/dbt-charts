"""Typed global config contract for compiled runtime defaults.

``Config`` is the authoritative required runtime config object.
Engine knobs only — no presentation fields (style, theme).

Dynamic sections remain open-ended but are backed by Pydantic
mapping-like nodes that still support mapping access.
"""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, KeysView, Mapping, ValuesView
from typing import Annotated, Any, ClassVar
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from dbt_charts.core.compile.models.cache import CachePatch, desugar_cache
from dbt_charts.core.compile.models.constraints import (
    ConstrainedModel,
    Constraint,
    Requires,
    ValueIn,
)
from dbt_charts.core.compile.models.primitives import HtmlPolicy

# The shape `published_to:` must take. Cloud mounts a project at
# `/<org>/<project>/`, and the trailing slash is what makes the recorded URL
# a page rather than a 404. `cloud_client.published_to.EXPECTED_FORM` states
# the same rule for the writing side -- tach forbids that package importing
# this one, so a parity test holds the two together.
PUBLISHED_TO_FORM = "https://<host>/<org>/<project>/"


def _normalize_node_value(value: object) -> object:
    """Recursively normalize nested mapping values into ConfigNode objects."""
    if isinstance(value, BaseModel):
        return value
    if isinstance(value, Mapping):
        return ConfigNode.model_validate(dict(value))
    if isinstance(value, list):
        return [_normalize_node_value(item) for item in value]
    return value


class ConfigMappingBase(BaseModel, Mapping[str, Any]):
    """Pydantic model with mapping-like helpers for config consumers."""

    model_config = ConfigDict(extra="forbid")

    # Mapping access is a migration shim for dynamic config sections; cache the
    # assembled top-level view and invalidate when this model's own fields change.
    _mapping_cache: dict[str, Any] | None = PrivateAttr(default=None)

    def _mapping_data(self) -> dict[str, Any]:
        mapping_cache = object.__getattribute__(self, "__pydantic_private__").get(
            "_mapping_cache"
        )
        if mapping_cache is not None:
            return mapping_cache

        data: dict[str, Any] = {}
        for key, field in type(self).model_fields.items():
            value = object.__getattribute__(self, key)
            data[key] = value
            if field.alias and field.alias != key:
                data[field.alias] = value
        extra = object.__getattribute__(self, "__pydantic_extra__") or {}
        for key, value in extra.items():
            data[key] = value
        self._mapping_cache = data
        return data

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name != "_mapping_cache":
            self._mapping_cache = None

    def __delattr__(self, item: str) -> None:
        super().__delattr__(item)
        if item != "_mapping_cache":
            self._mapping_cache = None

    def __getitem__(self, key: str) -> Any:
        data = self._mapping_data()
        if key not in data:
            raise KeyError(key)
        return data[key]

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        return iter(self._mapping_data())

    def __len__(self) -> int:
        return len(self._mapping_data())

    def __bool__(self) -> bool:
        return bool(self._mapping_data())

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping_data().get(key, default)

    def items(self) -> ItemsView[str, Any]:
        return self._mapping_data().items()

    def keys(self) -> KeysView[str]:
        return self._mapping_data().keys()

    def values(self) -> ValuesView[Any]:
        return self._mapping_data().values()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        data = self._mapping_data()
        if name in data:
            return data[name]
        raise AttributeError(name)

    def to_plain_dict(self, *, exclude_none: bool = True) -> dict[str, Any]:
        """Dump config models to plain nested Python dicts."""
        return self.model_dump(mode="python", exclude_none=exclude_none, by_alias=True)


class ConfigNode(ConfigMappingBase):
    """Open-ended config section that still supports attribute and mapping access."""

    # Accepts arbitrary keys — the config tree is open-ended by design.
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, value: object) -> object:
        if isinstance(value, ConfigNode):
            return value
        if isinstance(value, Mapping):
            return {key: _normalize_node_value(item) for key, item in value.items()}
        return value


class InspectorConfig(ConfigNode):
    tree_max_depth: int


class RenderingConfig(ConfigNode):
    class PngRenderingConfig(ConfigNode):
        scale: float

    class ThumbnailConfig(ConfigNode):
        """Tuning knobs for ``render/thumbnail.py``'s abstracted board picture.

        Shares are fractions of the measured line box or table width; weights
        are how much of a source color survives blending into the page.
        """

        placeholder_ink: float = Field(gt=0, le=1)
        bar_color_weight: float = Field(gt=0, le=1)
        status_color_weight: float = Field(gt=0, le=1)
        chromatic_saturation: float = Field(gt=0, lt=1)
        text_height: float = Field(gt=0, le=1)
        large_text_height: float = Field(gt=0, le=1)
        large_text_size: float = Field(gt=0)
        min_text_height: float = Field(gt=0)
        bar_height: float = Field(gt=0, le=1)
        bar_max_corner_radius: float = Field(ge=0)
        min_bar_width: float = Field(gt=0)
        bar_gap: float = Field(ge=0)
        bar_room_share: float = Field(gt=0, le=1)
        block_share: float = Field(gt=0, le=1)
        min_block_width: float = Field(gt=0)
        variable_pill_height: float = Field(gt=0, le=1)
        glyph_dot_share: float = Field(gt=0, le=1)
        min_glyph_dot_radius: float = Field(gt=0)
        same_line_share: float = Field(gt=0, le=1)

    png: PngRenderingConfig
    thumbnail: ThumbnailConfig


class LakeComputeExecutionConfig(ConfigNode):
    default_base_url: str = Field(
        description="Lake compute endpoint used when a source sets no base_url.",
    )


class ExecutionConfig(ConfigNode):
    # DuckDB-backed executors serialize access via _DUCKDB_EXECUTE_LOCK.
    max_workers: int = Field(
        description="Maximum parallel query workers for a render. DuckDB "
        "serializes access regardless, so this only moves external warehouses.",
    )
    # Safety ceiling on how long a single query may run, enforced as a server-side
    # statement timeout on network warehouses (never a client-side abandon — see
    # execute/adapters/sql_adapter.py). DuckDB and SQLite are local file databases
    # with no server to enforce a timeout, so this has no effect on them.
    # Overridable per source via sources.<name>.max_query_duration_seconds.
    max_query_duration_seconds: int = Field(
        gt=0, description="Maximum seconds a single query may run (must be > 0)."
    )
    # Hard cap on files matched per glob pattern in file sources. Error is raised
    # before any file is read so a runaway glob fails fast. Override in dbt_charts.yml.
    max_glob_file_count: int = Field(
        gt=0, description="Maximum files a single glob may match (must be > 0)."
    )
    # Hard cap on table entries in a single files: map. A deployment ceiling
    # (DCT_FILE_SOURCE_MAX_TABLES_CEILING) can only lower it, never raise it.
    file_source_max_tables: int = Field(
        gt=0,
        description="Max tables in a files: map (must be > 0).",
    )
    # Safety ceiling on a file-source table's uncompressed bytes: the file's
    # own bytes for CSV/JSON, and for Parquet the uncompressed total its
    # footer records, so one cap means one thing across formats. Checked while
    # reading, before the cache backend is written, so a runaway relation
    # fails fast. A deployment ceiling (DCT_FILE_SOURCE_MAX_BYTES_CEILING) can
    # only lower it, never raise it — so a message about this limit must not
    # tell the caller to raise it.
    file_source_max_bytes: int = Field(
        gt=0,
        description="Max uncompressed bytes per file-source table (must be > 0).",
    )
    # Safety ceiling on rows returned by a single query, enforced by bounding the
    # driver's own fetch (fetchmany()/execute(limit=...)) rather than rewriting
    # SQL. Exceeding it truncates the result and emits WARN_QUERY_RESULT_TRUNCATED
    # rather than failing the query. A Cloud deployment ceiling
    # (DCT_MAX_ROWS_CEILING) can only lower this, never raise it. Override in
    # dbt_charts.yml under execution.max_rows.
    max_rows: int = Field(
        gt=0, description="Maximum rows a single query may return (must be > 0)."
    )
    # Safety ceiling on the serialized byte size of a single query result,
    # checked incrementally during row accumulation so an oversized result is
    # never fully serialized to measure it. Exceeding it truncates the result
    # and emits WARN_QUERY_RESULT_TRUNCATED rather than failing the query. A
    # Cloud deployment ceiling (DCT_MAX_RESULT_BYTES_CEILING) can only lower
    # this, never raise it. Override in dbt_charts.yml under
    # execution.max_result_bytes.
    max_result_bytes: int = Field(
        gt=0,
        description="Maximum serialized byte size of a single query result "
        "(must be > 0).",
    )
    # Safety ceiling on the cumulative Jinja-emitted output of one board
    # render — summed across every templated field in that render (queries,
    # titles, markdown, …), not per field. Exceeding it is a hard error
    # (ERR-TEMPLATE-OUTPUT-TOO-LARGE), never a truncation — why, in
    # compile/template/output_budget.py's module docstring. A Cloud
    # deployment ceiling (DCT_MAX_TEMPLATE_OUTPUT_BYTES_CEILING) can only
    # lower this, never raise it. Override in dbt_charts.yml under
    # execution.max_template_output_bytes.
    max_template_output_bytes: int = Field(
        gt=0,
        description="Maximum cumulative bytes of Jinja-emitted template "
        "output for a single board render (must be > 0).",
    )
    # sqlglot uses different dialect names than dbt charts' public-facing dialect strings.
    dialect_aliases: dict[str, str] = Field(
        description="Maps a dbt charts dialect name to its sqlglot equivalent "
        "before parsing.",
    )
    lakecompute: LakeComputeExecutionConfig = Field(
        description="Lake compute connection defaults.",
    )


class ServerConfig(ConfigNode):
    model_config = ConfigDict(extra="forbid")

    debug: bool
    nav: bool
    port: int | None = None  # None = use deterministic project-path port
    # When True, non-board frontmatter keys in .md files are rendered as a
    # metadata table at the top of the page.  Off by default so AGENTS.md,
    # README, and other prose files don't suddenly acquire header tables.
    markdown_metadata_table: bool


class ProjectCacheConfig(CachePatch, ConstrainedModel):
    """Project cache root (``cache:`` in dbt_charts.yml): the cascade root every
    source/board/query inherits, plus the backend location.

    Deliberately the **same shape as every other scope** (``ttl`` — inherited
    from CachePatch), so ``cache: 4h`` means the same thing everywhere;
    ``path`` is the one project-only field, reachable via the block form
    (``cache: {ttl: 4h, path: .dct-cache.duckdb}``). There is no separate
    backend on/off switch — the store is provisioned lazily when any resolved
    query policy is enabled. The shipped root lives in
    ``defaults/default_config.yml``.

    ``cache: true`` is rejected here: the root has no scope above it to take a
    ttl from, so it would quietly mean forever.
    """

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        Requires(
            ("ttl",),
            "the project cache root has to say how long to keep results — "
            "write cache: <duration> (e.g. cache: 24h) or cache: forever. "
            "(cache: true means 'keep the ttl from the scope above', and "
            "the project root has no scope above it.)",
            when=(ValueIn("enabled", (True,)),),
        ),
    )

    # None = the per-user cache file, a documented outcome rather than
    # a fallback (see the field description). Defaulted, not required, so the
    # project scope accepts the same bare `cache: 4h` scalar as every other
    # scope — a required project-only key would make that spelling unauthorable.
    path: str | None = Field(
        default=None,
        description=(
            "Cache file location. None = the per-user cache file shared by "
            "every project, where entries older than a week are deleted; a "
            "string path selects that file instead (created if absent, never "
            "pruned). Both are documented outcomes, not a fallback."
        ),
    )


ProjectCacheInput = Annotated[ProjectCacheConfig, BeforeValidator(desugar_cache)]


class CloudConfig(ConfigMappingBase):
    """The ``cloud:`` block of dbt_charts.yml: rules dbt charts Cloud applies.

    Tighten-only: ``false`` turns the capability off for the project; ``None``
    (absent) and ``True`` both defer to Cloud's own settings, so the file can
    never switch a capability on.
    """

    allow_public: bool | None = None  # None = not stated
    honor_agent_instructions: bool | None = None  # None = not stated


class WarningsConfig(ConfigMappingBase):
    """The project's `warnings:` block: warning codes suppressed for every
    board in the project."""

    ignore: list[str] | None = None  # None = `ignore:` absent or left empty


class ExposureOwner(ConfigMappingBase):
    name: str | None = None  # None = email-only owner
    email: str | None = None  # None = name-only owner

    @model_validator(mode="after")
    def _name_or_email(self) -> ExposureOwner:
        # dbt rejects an exposure owner with neither.
        if self.name is None and self.email is None:
            raise ValueError("exposures.owner needs a name or email")
        return self


class ExposuresConfig(ConfigMappingBase):
    owner: ExposureOwner
    path: str = Field(
        default="models/dbt_charts/exposures.yml",
        description="Output file, relative to the dbt project root; must sit "
        "under one of dbt_project.yml's model-paths.",
    )


class Config(ConfigMappingBase):
    """Authoritative required runtime settings model.

    Engine knobs and definition registries only. Presentation (style, board
    layout) lives in the Board cascade via charts/meta.yml — not here.

    Closed top-level fields define the supported global settings surface; any
    stray key in dbt_charts.yml (e.g. ``style:``, ``board:``, ``theme:``) raises a
    pydantic ValidationError (``Extra inputs are not permitted``). The default
    theme is an engine-level fallback (``DCT_DEFAULT_THEME`` env var →
    ``SHIPPED_DEFAULT_THEME_NAME``), not a dbt_charts.yml key.
    """

    model_config = ConfigDict(extra="forbid")

    cache: ProjectCacheInput
    execution: ExecutionConfig
    geo_sources: ConfigNode
    # Deployment wins (DCT_HTML_POLICY_CEILING), then this project ceiling, then the board field.
    # Default (trusted-raw) is permissive — no ceiling — for local development.
    # Cloud pins DCT_HTML_POLICY_CEILING=safe-subset at the deployment level.
    html_policy_ceiling: HtmlPolicy
    inspector: InspectorConfig
    rendering: RenderingConfig
    server: ServerConfig
    terminal: ConfigNode
    # Nothing reads this: categorical palettes resolve through
    # dbt_charts.core.compile.resolve.style.palette's own name index, never
    # through the config tree. It must stay declared anyway --
    # Config is extra="forbid" and validates the whole file, so dropping the
    # field would fail load_config for a dbt_charts.yml that still sets it.
    # None = no explicit palettes: section (the shipped default carries none
    # either, now).
    palettes: ConfigNode | None = None
    strict: bool | None = None  # None = strict mode not configured; defaults to off
    # Nothing reads this: a project's registry is read per-project by
    # load_project_sources, never off the global. It must stay declared anyway —
    # Config is extra="forbid" and validates dbt_charts.yml, so dropping the
    # field would fail load_config (and `dct serve` startup) for every project
    # that declares sources:. None = no explicit sources section in config.
    sources: ConfigNode | None = None
    # Canonical public URL for dct render exports (e.g. "https://dashboards.example.com").
    # When set, rendered links are fully-qualified. Empty string means root-relative (default).
    public_url: str
    # Where this project is published in dbt charts Cloud, e.g.
    # "https://dbtcharts.com/acme-data/analytics/" -- written by
    # `dct cloud project connect`, read by context resolution
    # (dbt_charts.cloud_client.context) before it falls back to matching the
    # git remote. None = not connected, or not recorded yet.
    published_to: str | None = None
    # Read by Cloud from the work branch's dbt_charts.yml, never through Config.
    cloud: CloudConfig | None = None
    # Nothing reads this field: resolve_dbt_project_dir (core/project_roots.py)
    # reads dbt_charts.yml raw off disk before a Config exists (same reason
    # sources: above is unread here). It must stay declared anyway -- Config
    # is extra="forbid" and validates the whole file, so dropping the field
    # would fail load_config (and `dct serve` startup) for every project that
    # links an external dbt project. None = sibling default (no key set).
    dbt_project_dir: str | None = None
    # Nothing reads this field: Project.warnings_ignore reads the block raw
    # per project. It must stay declared anyway -- Config is extra="forbid"
    # and validates the whole file, so dropping the field would fail
    # load_config for every project that sets it. None = no block.
    warnings: WarningsConfig | None = None
    # Nothing reads this field: Project.locked_schema_version reads it raw per
    # project. It must stay declared anyway -- Config is extra="forbid" and
    # validates the whole file. None = no lock.
    locked_schema_version: str | None = None
    # None = exposure emission not configured; `dct exposures` refuses to run.
    exposures: ExposuresConfig | None = None

    @field_validator("published_to")
    @classmethod
    def _validate_published_to(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlsplit(value)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or len(segments) != 2
        ):
            raise ValueError(
                "published_to must be an absolute URL of the form "
                f"{PUBLISHED_TO_FORM}, got {value!r}"
            )
        return value


def as_plain_mapping(value: ConfigMappingBase | Mapping[str, Any]) -> dict[str, Any]:
    """Project a compiled config model or raw mapping into a plain dict."""
    if isinstance(value, ConfigMappingBase):
        return value.to_plain_dict(exclude_none=False)
    return dict(value)


def is_mapping_like(value: object) -> bool:
    """Return whether a value can be consumed through mapping helpers."""
    return isinstance(value, ConfigMappingBase | Mapping)
