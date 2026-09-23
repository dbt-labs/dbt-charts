"""Compilation configuration module.

Stage: COMPILE
Purpose: Provide default values and configuration for compilation.

Entry Points:
    - get_config() -> Config
    - load_config(project) -> Config
    - reset_config() -> None

Configuration is loaded from the defaults stack and accessed via dot notation:
    config = get_config()
    config.execution.max_workers
    config.server.port

Runtime defaults are assembled from:
    - core/defaults/default_config.yml (engine knobs)
    - core/defaults/palettes/<family>/*.yml (color palettes)

YAML is the single source of truth — no Python dataclass defaults.

Presentation (style, board width) belongs in the Board cascade via
charts/meta.yml — NOT in dbt_charts.yml. Any stray ``style:`` key in
``dbt_charts.yml`` raises a pydantic ValidationError (``Extra inputs are not
permitted``).

Project-wide theme defaults live in ``charts/meta.yml: extends: <theme>``,
not in ``dbt_charts.yml``. Unknown top-level keys in ``dbt_charts.yml`` raise a
pydantic ValidationError (``Extra inputs are not permitted``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path  # noqa: TID251 — reads package-shipped default/theme YAML
from typing import TYPE_CHECKING, Any, NamedTuple, TypeGuard, get_args

import yaml
from importlib_resources import files
from pydantic import ValidationError

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.merge import deep_merge_dict
from dbt_charts.core.compile.models.config import (
    ChartRenderingConfig,
    Config,
    ConfigNode,
    ExecutionConfig,
    InspectorConfig,
    ProjectCacheConfig,
    RenderingConfig,
    ServerConfig,
    VegaRuntimeConfig,
    as_plain_mapping,
    is_mapping_like,
)
from dbt_charts.core.compile.models.primitives import HtmlPolicy
from dbt_charts.core.compile.models.schema_names import ThemeName
from dbt_charts.core.compile.models.source import (
    parse_source_config,
    reject_credential_literals,
)
from dbt_charts.core.compile.sources.dbt_jinja import render_dbt_jinja_in_dict
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_CONFIG_INVALID
from dbt_charts.core.diagnostics.suppression import validate_suppression_codes
from dbt_charts.core.utils import UniqueKeyLoader

if TYPE_CHECKING:
    from importlib_resources.abc import Traversable

    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.project import Project

# ============================================================================
# GLOBAL CONFIGURATION
# ============================================================================

_config: Config | None = None
# The shipped cascade root, memoized: `_load_defaults()` reads every palette
# file, and shipped_cache_root() is called per query normalization.
# Deliberately NOT cleared by reset_config(), unlike every other memo in this
# module: those cache project-derived state, which reset_config() exists to
# drop, whereas this is read from the packaged defaults stack and cannot change
# within a process. Clearing it would only re-read the same files.
_shipped_cache_root: ProjectCacheConfig | None = None
_defaults_dir = files("dbt_charts.core").joinpath("defaults")
_default_config_path = _defaults_dir.joinpath("default_config.yml")
_built_in_unified_theme_dir = _defaults_dir.joinpath("themes")
_palettes_dir = _defaults_dir.joinpath("palettes")

# Per-module default YAML files colocated with their consumers.
# Each file contributes one or more top-level config namespaces.
# Add a path here when a new module ships its own defaults.
_core_dir = files("dbt_charts.core")
_MODULE_DEFAULT_PATHS = [
    _core_dir / "inspect" / "defaults.yml",
    _core_dir / "render" / "geo_defaults.yml",
    _core_dir / "render" / "terminal_defaults.yml",
]

# Theme cache (avoid repeated resolution of inheritance)
_compiled_theme_cache: dict[str, Any] = {}  # str -> Style

# Shipped built-in default theme name. Immutable — never patched at runtime.
# Project-wide overrides live in charts/meta.yml: extends: <name>.
# The VS Code inspector flips the theme per-session via DCT_DEFAULT_THEME env var,
# read at call time by get_default_theme_name() without any global mutation.
SHIPPED_DEFAULT_THEME_NAME: str = "clarity"


def _load_yaml_data(path: Traversable) -> Any:
    """Load YAML as plain Python containers from a Traversable."""
    result = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    return {} if result is None else result


def _as_mapping(value: Any, context: str) -> dict[str, Any]:
    """Normalize mapping-like content into a plain dict."""
    if is_mapping_like(value):
        return as_plain_mapping(value)
    raise TypeError(f"{context} must decode to a mapping")


def _load_dbt_charts_yml(project: Project) -> tuple[str, dict[str, Any]]:
    # tach-ignore(pre-existing compile->project coupling — accepted debt)
    from dbt_charts.core.project import PROJECT_CONFIG_NAME

    doc = project.config_document()
    if doc is None:
        return PROJECT_CONFIG_NAME, {}
    name, parsed = doc
    return name, _as_mapping({} if parsed is None else parsed, name)


def _dbt_charts_yml_mapping_section(
    project: Project, key: str
) -> tuple[str, dict[str, Any] | None]:
    filename, file_data = _load_dbt_charts_yml(project)
    value = file_data.get(key)
    if value is None:
        return filename, None
    return filename, _as_mapping(value, f"{filename} {key} section")


def _dbt_charts_yml_string_value(project: Project, key: str) -> str | None:
    filename, file_data = _load_dbt_charts_yml(project)
    if key not in file_data:
        return None
    value = file_data[key]
    if not isinstance(value, str):
        raise TypeError(
            f"{filename}: {key} must be a string, got {type(value).__name__}: {value!r}"
        )
    return value


def _load_defaults() -> dict[str, Any]:
    """Assemble the built-in defaults stack (no project YAML)."""
    data = _load_yaml_data(_default_config_path)
    for module_path in _MODULE_DEFAULT_PATHS:
        data = deep_merge_dict(data, _load_yaml_data(module_path))
    data = deep_merge_dict(data, _load_palettes())
    return data


def get_config() -> Config:
    """Get the current config.

    Loads the defaults stack on first call. In a project context, call
    load_config(project) first to merge project-level dbt_charts.yml.

    Returns:
        Config with typed configuration values
    """
    global _config

    if _config is None:
        _config = Config.model_validate(_load_defaults())

    return _config


def load_config(project: Project) -> Config:
    """Load and validate project config from dbt_charts.yml.

    Merges the defaults stack with the project's dbt_charts.yml. Any unknown
    top-level key in dbt_charts.yml (e.g. ``style:``, ``board:``, ``theme:``)
    raises a pydantic ValidationError; presentation and theme defaults belong
    in charts/meta.yml.

    Args:
        project: Project whose dbt_charts.yml to read.

    Returns:
        Config validated from the merged defaults + project YAML.

    Raises:
        pydantic.ValidationError: Unknown key in dbt_charts.yml, or invalid value.
    """
    global _config, _compiled_theme_cache
    from dbt_charts.core.compile.resolve.style.board import clear_resolve_style_cache

    data = _load_defaults()
    _, project_data = _load_dbt_charts_yml(project)
    if project_data:
        data = deep_merge_dict(data, project_data)

    config = Config.model_validate(data)
    _config = config
    _compiled_theme_cache = {}
    clear_resolve_style_cache()
    return config


def reset_config() -> None:
    """Reset configuration to reload from YAML."""
    global _config, _compiled_theme_cache
    from dbt_charts.core.compile.resolve.style.board import clear_resolve_style_cache

    _config = None
    _compiled_theme_cache = {}
    clear_resolve_style_cache()


def get_chart_rendering() -> ChartRenderingConfig:
    """Narrow getter — engine constants for chart layout and formatting."""
    return get_config().chart_rendering


def get_inspector_config() -> InspectorConfig:
    """Narrow getter — inspector / directory-tree config."""
    return get_config().inspector


def get_terminal_config() -> ConfigNode:
    """Narrow getter — terminal rendering defaults."""
    return get_config().terminal


def get_rendering_config() -> RenderingConfig:
    """Narrow getter — export/output rendering config."""
    return get_config().rendering


def get_vega_config() -> VegaRuntimeConfig:
    """Narrow getter — Vega-Lite renderer runtime config."""
    return get_config().vega


def get_execution_config() -> ExecutionConfig:
    """Narrow getter — query execution config (e.g. max_workers)."""
    return get_config().execution


@dataclass(frozen=True)
class ExportConfig:
    """Export settings slice of the project config."""

    public_url: str


def get_export_config(project: Project | None = None) -> ExportConfig:
    """Narrow getter — export settings (public_url for origin-absolute links).

    When *project* is provided, reads ``public_url`` from the project's
    ``dbt_charts.yml`` if present; otherwise falls back to the global default
    (empty string, meaning root-relative links).
    """
    if project is not None:
        project_value = _dbt_charts_yml_string_value(project, "public_url")
        if project_value is not None:
            return ExportConfig(public_url=project_value.rstrip("/"))
    return ExportConfig(public_url=get_config().public_url.rstrip("/"))


def resolve_max_workers(explicit: int | None) -> int:
    """Resolve the query-parallelism width: explicit arg → DCT_MAX_WORKERS → config.

    Single source of truth for both the render-time worker pool and the
    per-source connection pool, so the two never disagree on width.
    """
    if explicit is not None:
        return explicit
    env_val = os.getenv("DCT_MAX_WORKERS")  # noqa: TID251 — DCT_MAX_WORKERS knob
    if env_val is not None:
        return int(env_val)
    return get_execution_config().max_workers


# Derived from HtmlPolicy's Literal declaration — declaration order is the
# canonical ordering (most to least restrictive). Never hand-state this tuple.
_HTML_POLICY_ORDER: tuple[str, ...] = get_args(HtmlPolicy)


def _is_html_policy(val: str) -> TypeGuard[HtmlPolicy]:
    return val in _HTML_POLICY_ORDER


class HtmlPolicyCeiling(NamedTuple):
    """Resolved html_policy ceiling with its attribution source.

    ``source`` is the human-readable name for the ``{ceiling_source}`` slot in
    WARN-HTML-POLICY-CAPPED — e.g. ``"DCT_HTML_POLICY_CEILING env var"`` or
    ``"project config"``.  Keeping attribution inside the resolver means
    compiler.py never independently reads the env var.
    """

    value: HtmlPolicy
    source: str


def resolve_html_policy_ceiling() -> HtmlPolicyCeiling:
    """Resolve the html_policy ceiling: DCT_HTML_POLICY_CEILING env var → project config.

    The ceiling is the maximum html_policy tier any board in this deployment may
    use.  Cloud hard-pins DCT_HTML_POLICY_CEILING=safe-subset; local development
    has no env var and the default_config.yml ships trusted-raw (no ceiling).

    Returns:
        HtmlPolicyCeiling with the resolved tier and its attribution source.

    Raises:
        ValueError: If DCT_HTML_POLICY_CEILING is set to an unrecognized value.
    """
    _env_name = "DCT_HTML_POLICY_CEILING"
    env_val = os.getenv(_env_name)  # noqa: TID251 — DCT_HTML_POLICY_CEILING knob
    if env_val is None:
        return HtmlPolicyCeiling(get_config().html_policy_ceiling, "project config")
    if _is_html_policy(env_val):
        return HtmlPolicyCeiling(env_val, f"{_env_name} env var")
    raise ValueError(
        f"DCT_HTML_POLICY_CEILING={env_val!r} is not a valid html_policy_ceiling tier; "
        f"expected one of {_HTML_POLICY_ORDER}"
    )


def cap_html_policy(policy: HtmlPolicy, ceiling: HtmlPolicy) -> HtmlPolicy:
    """Return the lesser of policy and ceiling using the policy tier ordering.

    Args:
        policy: The board's requested html_policy tier.
        ceiling: The deployment/project ceiling tier.

    Returns:
        The capped policy — whichever of policy or ceiling is more restrictive.
    """
    capped = _HTML_POLICY_ORDER[
        min(_HTML_POLICY_ORDER.index(policy), _HTML_POLICY_ORDER.index(ceiling))
    ]
    if not _is_html_policy(capped):
        raise AssertionError(f"cap_html_policy: {capped!r} is not a valid HtmlPolicy")
    return capped


def resolve_max_query_duration_seconds(source_config: Mapping[str, Any] | None) -> int:
    """Resolve the per-query duration cap: per-source override → global config default.

    ``source_config`` is the raw dumped source dict (typed field or ADR-009
    passthrough key alike — both surface the same way in the dict), so this
    works uniformly whether the warehouse has a typed
    ``max_query_duration_seconds`` field or not.

    Raises:
        ValueError: If the per-source override is not a positive integer.
    """
    if source_config is not None:
        raw = source_config.get("max_query_duration_seconds")
        if raw is not None:
            try:
                val = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"max_query_duration_seconds must be a positive integer, got {raw!r}"
                ) from exc
            if val <= 0:
                raise ValueError(f"max_query_duration_seconds must be > 0, got {val!r}")
            return val
    return get_execution_config().max_query_duration_seconds


def _resolve_positive_int_env_ceiling(env_name: str) -> int | None:
    """Resolve a positive-integer deployment ceiling env var, if set.

    ``None`` means no ceiling (local development default) — callers skip the
    ``min()`` clamp entirely in that case rather than treating it as unbounded.
    A non-positive or non-integer value would silently zero out (or worse,
    negate) every render's row/byte budget, so it is rejected here rather
    than handed to ``min()`` as a legitimate ceiling.

    Raises:
        ValueError: If the env var is set but not a positive integer.
    """
    env_val = os.getenv(env_name)  # noqa: TID251 — deployment ceiling knob
    if env_val is None:
        return None
    try:
        val = int(env_val)
    except ValueError as exc:
        raise ValueError(f"{env_name}={env_val!r} is not a valid integer") from exc
    if val <= 0:
        raise ValueError(f"{env_name}={env_val!r} must be a positive integer")
    return val


def resolve_max_rows_ceiling() -> int | None:
    """Resolve the DCT_MAX_ROWS_CEILING deployment ceiling, if set.

    Raises:
        ValueError: If DCT_MAX_ROWS_CEILING is set to a non-positive or
            non-integer value.
    """
    return _resolve_positive_int_env_ceiling("DCT_MAX_ROWS_CEILING")


def resolve_max_result_bytes_ceiling() -> int | None:
    """Resolve the DCT_MAX_RESULT_BYTES_CEILING deployment ceiling, if set.

    Raises:
        ValueError: If DCT_MAX_RESULT_BYTES_CEILING is set to a non-positive
            or non-integer value.
    """
    return _resolve_positive_int_env_ceiling("DCT_MAX_RESULT_BYTES_CEILING")


def resolve_max_rows() -> int:
    """Resolve the effective max_rows: project config, clamped to the
    deployment ceiling (DCT_MAX_ROWS_CEILING) if one is set.

    A project's own dbt_charts.yml can never raise the value above the
    deployment ceiling — only the ceiling or a tighter config value wins.
    """
    ceiling = resolve_max_rows_ceiling()
    config_value = get_execution_config().max_rows
    return config_value if ceiling is None else min(config_value, ceiling)


def resolve_max_result_bytes() -> int:
    """Resolve the effective max_result_bytes: project config, clamped to the
    deployment ceiling (DCT_MAX_RESULT_BYTES_CEILING) if one is set.

    A project's own dbt_charts.yml can never raise the value above the
    deployment ceiling — only the ceiling or a tighter config value wins.
    """
    ceiling = resolve_max_result_bytes_ceiling()
    config_value = get_execution_config().max_result_bytes
    return config_value if ceiling is None else min(config_value, ceiling)


def resolve_max_template_output_bytes_ceiling() -> int | None:
    """Resolve the DCT_MAX_TEMPLATE_OUTPUT_BYTES_CEILING deployment ceiling,
    if set.

    Raises:
        ValueError: If DCT_MAX_TEMPLATE_OUTPUT_BYTES_CEILING is set to a
            non-positive or non-integer value.
    """
    return _resolve_positive_int_env_ceiling("DCT_MAX_TEMPLATE_OUTPUT_BYTES_CEILING")


def resolve_max_template_output_bytes() -> int:
    """Resolve the effective max_template_output_bytes: project config,
    clamped to the deployment ceiling (DCT_MAX_TEMPLATE_OUTPUT_BYTES_CEILING)
    if one is set.

    A project's own dbt_charts.yml can never raise the value above the
    deployment ceiling — only the ceiling or a tighter config value wins.
    """
    ceiling = resolve_max_template_output_bytes_ceiling()
    config_value = get_execution_config().max_template_output_bytes
    return config_value if ceiling is None else min(config_value, ceiling)


def resolve_file_source_max_bytes_ceiling() -> int | None:
    """Resolve the DCT_FILE_SOURCE_MAX_BYTES_CEILING deployment ceiling, if set.

    Raises:
        ValueError: If DCT_FILE_SOURCE_MAX_BYTES_CEILING is set to a
            non-positive or non-integer value.
    """
    return _resolve_positive_int_env_ceiling("DCT_FILE_SOURCE_MAX_BYTES_CEILING")


def resolve_file_source_max_tables_ceiling() -> int | None:
    """Resolve the DCT_FILE_SOURCE_MAX_TABLES_CEILING deployment ceiling, if set.

    Raises:
        ValueError: If DCT_FILE_SOURCE_MAX_TABLES_CEILING is set to a
            non-positive or non-integer value.
    """
    return _resolve_positive_int_env_ceiling("DCT_FILE_SOURCE_MAX_TABLES_CEILING")


def resolve_file_source_max_bytes() -> int:
    """Resolve the effective file_source_max_bytes: project config, clamped to
    the deployment ceiling (DCT_FILE_SOURCE_MAX_BYTES_CEILING) if one is set.

    A project's own dbt_charts.yml can never raise the value above the
    deployment ceiling — only the ceiling or a tighter config value wins.
    """
    ceiling = resolve_file_source_max_bytes_ceiling()
    config_value = get_execution_config().file_source_max_bytes
    return config_value if ceiling is None else min(config_value, ceiling)


def resolve_file_source_max_tables() -> int:
    """Resolve the effective file_source_max_tables: project config, clamped to
    the deployment ceiling (DCT_FILE_SOURCE_MAX_TABLES_CEILING) if one is set.

    A project's own dbt_charts.yml can never raise the value above the
    deployment ceiling — only the ceiling or a tighter config value wins.
    """
    ceiling = resolve_file_source_max_tables_ceiling()
    config_value = get_execution_config().file_source_max_tables
    return config_value if ceiling is None else min(config_value, ceiling)


@dataclass(frozen=True)
class CacheBoot:
    """Resolved boot-time decision: whether/where to open the result cache.

    ``path`` is meaningful only when ``enabled`` — ``None`` there means the
    zero-config in-memory cache (ephemeral, discarded on process exit).
    """

    enabled: bool
    path: Path | None


def resolve_cache_boot(
    project: FilesystemProject,
    *,
    no_cache: bool = False,
    cache_path: Path | None = None,
) -> CacheBoot:
    """Resolve the boot-time result-cache decision: flag > env > project config.

    ``no_cache`` is the only kill switch: the project's ``cache: false`` sets
    the *cascade* default to off, and a source/board/query below it can still
    opt back in (``cache: 1h``), so the store is provisioned either way — the
    per-query policy, resolved at compile time, decides what actually gets
    written. ``cache_path`` (``--cache``, whose ``DCT_CACHE_PATH`` env backing
    is handled upstream by the CLI's ``envvar=`` binding) wins over config.
    Otherwise the project's ``cache:`` block picks the location: a configured
    ``path`` opens that file (created if absent, resolved relative to the
    project root); no path opens the zero-config in-memory default.

    Reads only the ``cache:`` section of ``dbt_charts.yml`` (like
    ``get_project_server_config``'s narrow ``server:`` read) rather than the
    whole file through ``load_config`` — dbt_charts.yml also serves as a
    lightweight project marker in tests/examples, so unrelated top-level keys
    must not make cache resolution (and therefore server boot) fail.
    """
    if no_cache:
        return CacheBoot(enabled=False, path=None)
    if cache_path is not None:
        return CacheBoot(enabled=True, path=cache_path)

    project_cache = get_project_cache_root(project)
    if not project_cache.path:
        return CacheBoot(enabled=True, path=None)
    candidate = Path(project_cache.path)
    resolved_path = candidate if candidate.is_absolute() else project.root / candidate
    return CacheBoot(enabled=True, path=resolved_path)


def get_project_cache_root(project: Project) -> ProjectCacheConfig:
    """Read the project's ``cache:`` block — the root of the cascade.

    Narrow read, like ``get_project_server_config``'s ``server:`` one: the whole
    file through ``load_config`` would make an unrelated top-level key fail
    every compile, and dbt_charts.yml doubles as a lightweight project marker.

    This is the *only* way a project's root reaches the cascade. It is threaded
    down to ``normalize_query`` (via ``Project.cache``) rather than installed in
    the process global that ``load_config`` writes: ``_config`` is per-process
    and one Cloud worker serves many orgs, so a global root leaks one project's
    policy into another's compile.
    """
    data = _load_defaults()
    _, file_data = _load_dbt_charts_yml(project)
    if "cache" in file_data:
        data = deep_merge_dict(data, {"cache": file_data["cache"]})
    return Config.model_validate(data).cache


def shipped_cache_root() -> ProjectCacheConfig:
    """The cascade root for a compile with no project attached.

    A board compiled from text (tests, the playground) has no dbt_charts.yml to
    read, and the shipped ``cache:`` in ``defaults/default_config.yml`` is the
    documented answer for that case. Read from the defaults stack rather than
    ``get_config()`` so it cannot pick up a ``load_config`` mutation — that
    global is exactly what ``get_project_cache_root`` exists to bypass.
    """
    global _shipped_cache_root

    if _shipped_cache_root is None:
        _shipped_cache_root = Config.model_validate(_load_defaults()).cache
    return _shipped_cache_root


def get_project_server_config(project: Project) -> ServerConfig:
    """Read project-level ``server:`` config from dbt_charts.yml/yaml.

    Only the ``server:`` section is validated here. dbt_charts.yml is also used
    as a lightweight project marker in tests and examples, so unrelated top-level
    keys must not make serve startup fail.
    """
    base = get_config().server.to_plain_dict(exclude_none=False)
    _, server_section = _dbt_charts_yml_mapping_section(project, "server")
    if server_section is None:
        return ServerConfig.model_validate(base)
    return ServerConfig.model_validate(deep_merge_dict(base, server_section))


def get_project_markdown_metadata_table(project: Project) -> bool:
    """Return whether the project has the markdown_metadata_table flag enabled."""
    return get_project_server_config(project).markdown_metadata_table


def _load_palettes() -> dict[str, Any]:
    """Load palette YAMLs from defaults/palettes/ into the config tree.

    Returns a dict with:
      - ``palettes``: categorical palettes keyed by name (vivid-10, hero-6, …)
        from ``defaults/palettes/categorical/*.yml``.
      - ``dbt_grays`` / ``dbt_creams``: flat hex mappings from
        ``defaults/palettes/scaffold/dbt-grays.yml`` and ``dbt-creams.yml``.

    Sequential, diverging, and tone palettes are accessed via
    ``dbt_charts.core.compile.resolve.style.palette`` — not the config tree.

    Shipped YAMLs are expected to be well-formed. Any malformed file raises —
    no silent skip.
    """
    overlay: dict[str, Any] = {}
    cat_dir = _palettes_dir.joinpath("categorical")
    if cat_dir.is_dir():
        palettes: dict[str, list[str]] = {}
        for path in sorted(
            (p for p in cat_dir.iterdir() if p.name.endswith(".yml")),
            key=lambda p: p.name,
        ):
            data = _load_yaml_data(path)
            name = data.get("name") if isinstance(data, Mapping) else None
            # Categorical files use colors: (renamed from stops:).
            colors = data.get("colors") if isinstance(data, Mapping) else None
            if not isinstance(name, str) or not isinstance(colors, list):
                raise ValueError(
                    f"{path}: categorical palette YAML must have string 'name' "
                    f"and list 'colors' keys"
                )
            palettes[name] = list(colors)
        if palettes:
            overlay["palettes"] = palettes

    for legacy_attr, palette_name in (
        ("dbt_grays", "dbt-grays"),
        ("dbt_creams", "dbt-creams"),
    ):
        # Use _load_spine (which resolves extends: inheritance) so that child
        # palettes like dbt-creams inherit the full alias graph from their parent.
        from dbt_charts.core.compile.resolve.style.palette import (
            _load_spine,
            resolve_alias_chain,
        )

        spine = _load_spine(palette_name)
        if spine.colors is None or spine.aliases is None:
            raise ValueError(
                f"scaffold palette '{palette_name}' must have 'colors:' list and "
                f"'aliases:' mapping (unified palette shape)"
            )
        # Flatten: resolve every alias to a terminal hex for legacy consumers.
        flat: dict[str, str] = {
            alias_key: resolve_alias_chain(
                alias_key, dict(spine.aliases), colors=list(spine.colors)
            )
            for alias_key in spine.aliases
        }
        if not flat:
            raise ValueError(
                f"scaffold palette '{palette_name}' has no aliases — check palette YAML shape"
            )
        overlay[legacy_attr] = flat

    return overlay


@cache
def list_built_in_themes() -> list[str]:
    """Return sorted stem names of all built-in unified themes."""
    names = [
        entry.name[: -len(".yaml")]
        for entry in _built_in_unified_theme_dir.iterdir()
        if entry.name.endswith(".yaml")
    ]
    return sorted(names)


def user_facing_theme_names() -> list[str]:
    """Built-in theme names offered to authors as `theme:` completions.

    The single canonical theme listing — the playground `/api/themes`
    endpoint, LSP hints, and the Cloud design panel's theme picker all read
    this instead of each filtering list_built_in_themes() on their own. (The
    VS Code/engine-shipped JSON Schema's `theme`/`extends` enum arms arrive
    separately, via the SchemaSugar marker on AuthoredBoardInput in
    authored.py — not through this function.) Backed by the generated
    ThemeName Literal (private `_base` and diagnostic-only themes already
    excluded at codegen time), not a second hand-maintained filter.
    """
    return sorted(get_args(ThemeName))


def get_theme_style(theme_name: str | None = None) -> Any:  # -> Style
    """Load a built-in theme as a fully-resolved Style via the resolution engine.

    Folds the theme's extends chain via resolve_built_in_theme, sharing the
    same merge_patches core as board compilation so theme YAML is processed
    identically to board extends fragments.

    Token resolution (palette tokens → hex, self-tokens → concrete values)
    is applied once at load time so the returned Style is ready for use as
    the base argument to resolve_style().

    Args:
        theme_name: Built-in theme stem (e.g. "clarity", "paper") or None
            to use the configured default theme.

    Returns:
        Style with all required fields populated and tokens resolved.

    Raises:
        ValueError:       Theme resolved but produced no style data.
        CompilationError: Unknown theme name, cycle, or I/O / YAML parse error.
    """
    from dbt_charts.core.compile.models.style.theme import Style
    from dbt_charts.core.compile.resolve.style.tokens import (
        _resolve_color_tokens,
        _resolve_self_tokens,
        expand_palette_refs,
    )

    name = theme_name or get_default_theme_name()

    if name in _compiled_theme_cache:
        return _compiled_theme_cache[name]

    from dbt_charts.core.compile.merge import resolve_built_in_theme

    patch = resolve_built_in_theme(name)
    # resolve_built_in_theme always returns a BoardPatch; BaseModel return type
    # avoids a circular import.
    style_obj = patch.style  # type: ignore[attr-defined]
    if style_obj is None:
        available = list_built_in_themes()
        raise ValueError(
            f"Theme '{name}' produced no style data. Available themes: {available}"
        )
    style_data = style_obj.model_dump(exclude_unset=True)
    compiled = Style.model_validate(style_data)
    # Resolve theme-self tokens before color tokens so a substituted palette
    # token is itself resolved by the second pass.
    compiled = _resolve_self_tokens(compiled)
    compiled = _resolve_color_tokens(compiled)
    # Roles are resolvable only now that `style.palettes` is final; expanding
    # here keeps every downstream consumer seeing plain stop lists.
    compiled = expand_palette_refs(compiled, path="style")
    _compiled_theme_cache[name] = compiled
    return compiled


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def get_default_theme_name() -> str:
    """Return the engine-level default chart theme name.

    The floor used wherever no theme is otherwise resolved — sizing computations
    and the ``theme_name or get_default_theme_name()`` fallback in the normalizer.
    Reads ``DCT_DEFAULT_THEME`` at call time (a process-level override the VS Code
    inspector sets per session — no module mutation), falling back to
    ``SHIPPED_DEFAULT_THEME_NAME`` when unset.

    This is distinct from per-board theming: ``extends:``/``theme:`` on a board (or
    ``charts/meta.yml``) resolves through the cascade and wins over this default
    via ``theme_name or get_default_theme_name()``.
    """
    env_value = os.getenv("DCT_DEFAULT_THEME")  # noqa: TID251 — DCT_DEFAULT_THEME knob
    return env_value if env_value else SHIPPED_DEFAULT_THEME_NAME


def get_palette(name: str = "default") -> list[str]:
    """Get a named color palette."""
    if name == "default":
        _categorical = get_theme_style().charts.color.categorical
        assert _categorical is not None and _categorical.palette is not None, (
            "charts.color.categorical.palette must be populated by the theme"
        )
        return list(_categorical.palette)
    config = get_config()
    palettes = config.palettes
    if name not in palettes:
        raise KeyError(f"Unknown palette '{name}'")
    palette_val = palettes[name]
    if not isinstance(palette_val, list):
        raise TypeError(f"Palette '{name}' must be a list")
    return list(palette_val)


# ============================================================================
# PROJECT SOURCES
# ============================================================================


def load_project_sources(project: FilesystemProject) -> ProjectSourcesConfig:
    """Read dbt_charts.yml from disk and return parsed source config.

    Reads disk every call — no cache. dbt_charts.yml edits are visible to the
    next call. Relative ``path:`` (duckdb) and ``file:`` (csv / parquet /
    json) values resolve against ``project.root`` so callers can pass the
    returned config straight into ``compile()`` without further work.

    The project's own dbt_charts.yml is the only input. The process-global
    ``_config`` is deliberately not consulted: the shipped defaults stack
    declares no ``sources:``, so ``get_config().sources`` is non-empty only
    when ``load_config`` has installed *some* project's registry into the
    global — for the same project that is redundant with the read below, and
    for any other project it is one project's warehouses appearing in
    another's registry. Any process holding more than one project hits that:
    a pytest session spanning several packages' suites did, silently. Same
    reasoning as ``get_project_cache_root``.

    ``sources.default`` is not supported at the project level; set
    ``source: <name>`` on the board or folder ``meta.yml`` instead.
    """
    # dbt_charts.yml sources — default key is rejected
    filename, sources_section = _dbt_charts_yml_mapping_section(project, "sources")
    if sources_section is None:
        sources_section = {}
    elif "default" in sources_section:
        raise TypeError(
            f"{filename}: sources.default is no longer supported. "
            "Set the default source at the board or folder meta.yml level: `source: <name>`."
        )
    all_sources = {k: v for k, v in sources_section.items() if isinstance(v, dict)}
    for name, entry in all_sources.items():
        # Reject raw secret literals in the git-committed registry, on the
        # RAW authored value — before env_var rendering could turn a
        # reference into a literal (dbt defers `password`, renders the rest).
        reject_credential_literals(name, entry)

    normalized = _normalize_project_sources(all_sources)
    _validate_source_registry(normalized, filename)
    sources = _absolutize_source_paths(normalized, project)
    return ProjectSourcesConfig(sources=sources)


def _validate_source_registry(
    sources: dict[str, dict[str, Any]], filename: str
) -> None:
    """Parse every registry entry through ``SourceConfig`` so a malformed entry
    errors at load (validate-and-error-fast) with the source name and file — not
    lazily when a resolver first touches it."""
    for name, config in sources.items():
        try:
            parse_source_config(config)
        except (ValueError, ValidationError) as e:
            raise CompilationError.from_code(
                ERR_SOURCE_CONFIG_INVALID,
                source_name=name,
                filename=filename,
                detail=str(e),
            ) from e


def _absolutize_source_paths(
    sources: dict[str, dict[str, Any]],
    project: FilesystemProject,
) -> dict[str, dict[str, Any]]:
    """Absolutize a relative duckdb database path against ``project.root``."""
    resolved: dict[str, dict[str, Any]] = {}
    for name, config in sources.items():
        cfg = dict(config)
        source_type = cfg.get("type")
        if source_type == "duckdb":
            path_value = cfg.get("path")
            if (
                isinstance(path_value, str)
                and path_value != ":memory:"
                and not Path(path_value).is_absolute()
            ):
                cfg["path"] = str(project.data_path(path_value))
        resolved[name] = cfg
    return resolved


# ============================================================================
# PROJECT WARNINGS IGNORE
# ============================================================================


def get_project_warnings_ignore(project: Project) -> frozenset[str]:
    """Return warning codes to suppress project-wide. Fresh read per call."""
    filename, warnings_map = _dbt_charts_yml_mapping_section(project, "warnings")
    if warnings_map is None:
        return frozenset()
    raw_ignore = warnings_map.get("ignore")
    if raw_ignore is None:
        return frozenset()
    if not isinstance(raw_ignore, list):
        raise TypeError(f"{filename}: warnings.ignore must be a list of strings")
    for entry in raw_ignore:
        if not isinstance(entry, str):
            raise TypeError(
                f"{filename}: warnings.ignore entries must be "
                f"strings, got {type(entry).__name__}: {entry!r}"
            )
    validate_suppression_codes(raw_ignore, source=f"{filename}: warnings.ignore")
    return frozenset(raw_ignore)


class ProjectSourcesConfig:
    """Project-level sources configuration.

    A pure name→definition map. The project-level ``sources.default`` key
    has been removed; per-board defaults come from ``source:`` on the board or
    folder ``meta.yml``.
    """

    def __init__(
        self,
        sources: dict[str, dict[str, Any]] | None = None,
    ):
        self.sources = sources or {}


def _normalize_project_sources(
    sources: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {name: render_dbt_jinja_in_dict(config) for name, config in sources.items()}
