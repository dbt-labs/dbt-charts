"""Dashboard pack proposal and scaffold-apply API.

Thin agent-API wrapper:
- ``propose_pack``: reads schema via the registry + resolver, calls the pure
  planner, and writes the resulting artifact to
  ``target/dbt-charts/proposals/<slug>/proposal.yml``.
- ``apply_proposal``: takes an approved ``PackProposal`` and writes a sparse,
  validated ``charts/`` folder tree.

All business logic lives in ``dbt_charts.core.pack``.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict

from dbt_charts.core.execute.adapters import AdapterRegistry, build_adapter_registry
from dbt_charts.core.inspect.resolver import LayeredSchemaResolver
from dbt_charts.core.pack.models import PackProposal, ProposedDashboard
from dbt_charts.core.pack.planner import SourceEntry, plan_pack
from dbt_charts.core.pack.proposal_store import dump_proposal
from dbt_charts.core.project import CHARTS_SUBDIR, is_board_relpath

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject


class ScaffoldResult(BaseModel):
    """Result of a scaffold apply operation.

    Mirrors :class:`~dbt_charts.agent_api.init.InitResult` — ``created_files``
    and ``skipped_files`` are relative to ``project_dir``; ``errors`` are
    human-readable strings collected from the validate gate.
    """

    model_config = ConfigDict(frozen=True)

    created_files: list[Path] = []
    skipped_files: list[Path] = []
    errors: list[str] = []


def _slugify(text: str) -> str:
    """Convert a string to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "proposal"


# Schema name segments that identify dbt-internal or warehouse-internal schemas
# (staging views, compiled models, intermediate layers) that should not become
# connector-first folders in the proposal. These are never user-facing connectors.
_DBT_INTERNAL_SCHEMA_SEGMENTS: frozenset[str] = frozenset(
    {
        "stg",
        "staging",
        "serving",
        "intermediate",
        "int",
        "mart",
        "mrt",
        "raw",
        "source",
    }
)


def _is_connector_schema(schema_name: str) -> bool:
    """Return True when a schema name looks like a connector/source schema.

    Rejects schemas whose name (or any ``_``-separated segment) is a known
    dbt internal layer name (staging, serving, intermediate, raw, source).
    Also rejects schemas named ``main`` or ``information_schema`` — those are
    database-level namespaces, not connectors.

    Examples rejected:  main, main_staging, main_stg_stripe, main_zendesk_source,
                        stg_product_db, main_serving, information_schema
    Examples accepted:  zendesk, salesforce, hubspot, stripe, workday, product_db
    """
    lower = schema_name.lower()
    if lower in {"main", "information_schema", "pg_catalog"}:
        return False
    segments = lower.split("_")
    return not any(seg in _DBT_INTERNAL_SCHEMA_SEGMENTS for seg in segments)


def _collect_source_entries(
    registry: AdapterRegistry,
    source_name: str,
) -> dict[str, SourceEntry]:
    """Return one SourceEntry per connector-like schema for a source.

    Uses the LayeredSchemaResolver to enumerate all schemas, then tables per
    schema. Each connector-like schema becomes its own entry keyed by schema
    name so the planner can create one connector-first folder per schema (e.g.
    ``zendesk``, ``salesforce``) rather than collapsing everything into one entry.

    dbt-internal schemas (staging, serving, intermediate, raw) are skipped —
    they are transformation layers, not connectors.

    Returns an empty dict when the source is unreachable or has no tables.
    Schemas with no reachable tables are silently skipped — the caller surfaces
    this as an uncertainty note when the whole source returns nothing.
    """
    resolver = LayeredSchemaResolver(
        adapter_registry=registry,
        project=registry.project,
    )
    try:
        schema_resp = resolver.list_schemas(source_name)
    except Exception:  # noqa: BLE001 — unreachable source is non-fatal at proposal time
        return {}

    raw_schemas = schema_resp.get("sources", {}).get(source_name, {}).get("schemas", {})
    if not raw_schemas:
        return {}

    entries: dict[str, SourceEntry] = {}
    for schema_name in raw_schemas:
        if not _is_connector_schema(schema_name):
            continue
        try:
            table_resp = resolver.list_tables(source_name, schema_name)
        except Exception:  # noqa: BLE001 — same as above
            continue
        raw_tables = (
            table_resp.get("sources", {})
            .get(source_name, {})
            .get("schemas", {})
            .get(schema_name, {})
            .get("tables", {})
        )
        tables = sorted(raw_tables.keys())
        if tables:
            entries[schema_name] = SourceEntry(
                schema=schema_name,
                tables=tables,
                source_name=source_name,  # carry the configured source identifier
            )
    return entries


def propose_pack(
    project: FilesystemProject,
    mode: str | None = None,
) -> tuple[PackProposal, Path]:
    """Read schema via the resolver and emit a deterministic PackProposal.

    The proposal is written to
    ``<project.root>/target/dbt-charts/proposals/<slug>/proposal.yml``.

    Args:
        project: dbt-charts project (root must contain ``dbt_charts.yml``).
        mode: Organization mode override (``"connector-first"``,
            ``"domain-first"``, or ``"hybrid"``). When ``None``, the planner
            chooses automatically.

    Returns:
        A ``(proposal, out_path)`` tuple — the generated
        :class:`~dbt_charts.core.pack.models.PackProposal` and the absolute path
        of the written YAML file.

    Raises:
        FileNotFoundError: When ``project.root`` does not exist.
        ValueError: When no SQL sources are configured in the project, or when
            all configured sources are unreachable.
    """
    project_dir = project.root
    if not project_dir.exists():
        raise FileNotFoundError(f"Project directory not found: {project_dir}")

    registry = build_adapter_registry(project)
    sql_sources = registry.list_sql_sources()

    if not sql_sources:
        raise ValueError(
            f"propose_pack requires at least one configured data source; "
            f"no sources found in {project_dir}"
        )

    # Gather schema + table names per source via the resolver.
    # Multi-schema sources (e.g. a single DuckDB with zendesk + salesforce schemas)
    # expand into one entry per schema so the planner can produce a connector-first
    # folder per schema.  The original source name is preserved in detected_sources.
    # Sources with no reachable schemas are reported as None for uncertainty notes.
    sources: dict[str, SourceEntry | None] = {}
    original_source_names: list[str] = []
    collision_notes: list[str] = []
    for src in sql_sources:
        source_name = src["name"]
        original_source_names.append(source_name)
        schema_entries = _collect_source_entries(registry, source_name)
        if not schema_entries:
            # Unreachable source — keep as None so plan_pack emits the note.
            sources[source_name] = None
        elif len(schema_entries) == 1:
            # Single schema: keep original source name so proposals don't
            # silently rename "fivetran_zendesk" → "zendesk".
            if source_name in sources:
                collision_notes.append(
                    f"Source '{source_name}' conflicts with an existing entry; "
                    f"only the first occurrence is included in the proposal."
                )
            else:
                sources[source_name] = next(iter(schema_entries.values()))
        else:
            # Multiple schemas: expand into per-schema entries named by schema.
            # The original source name is no longer added as a key — it would
            # create a None entry that gets reported as unreachable.
            for schema_name, entry in schema_entries.items():
                if schema_name in sources:
                    # Two configured sources expose the same schema name.
                    # Keep the first and record a note — don't silently clobber.
                    collision_notes.append(
                        f"Schema '{schema_name}' appears in multiple sources; "
                        f"only the first occurrence is included in the proposal."
                    )
                else:
                    sources[schema_name] = entry

    proposal = plan_pack(sources=sources, mode=mode)

    # When a source expanded into per-schema keys (e.g. "db" → "zendesk", "salesforce"),
    # the original source name disappears from detected_sources. Append any original
    # source names that were expanded away so the proposal is traceable to the
    # configured source.
    expanded_originals = [n for n in original_source_names if n not in sources]
    # Dedup collision notes (same schema may collide from multiple sources).
    deduped_collision_notes = list(dict.fromkeys(collision_notes))
    extra_notes = [
        *deduped_collision_notes,
        *(
            [
                f"Source '{n}' expanded into per-schema entries; "
                "the original source name is retained in detected_sources."
                for n in expanded_originals
            ]
        ),
    ]
    if expanded_originals or extra_notes:
        merged_sources = sorted(
            set(proposal.detected_sources) | set(expanded_originals)
        )
        proposal = proposal.model_copy(
            update={
                "detected_sources": merged_sources,
                "uncertainty_notes": list(proposal.uncertainty_notes) + extra_notes,
            }
        )

    # Write to target/dbt-charts/proposals/<slug>/proposal.yml
    slug = _slugify("-".join(proposal.detected_sources))
    out_path = (
        project_dir / "target" / "dbt-charts" / "proposals" / slug / "proposal.yml"
    )
    dump_proposal(proposal, out_path)

    return proposal, out_path


def _build_board_dict(dashboard: ProposedDashboard) -> dict[str, Any]:
    """Build a minimal valid board dict from a ProposedDashboard.

    Sparse + valid + no TODO placeholders. Emits title + notes + text only.
    Queries are intentionally omitted — the user fills those in after review.
    A ``text:`` field is required because the compiler rejects boards with no
    layout type and no text content.

    When the dashboard has a ``canonical_data_url``, an ``aliases:`` entry
    is included so the data URL redirects to this authored board. The alias
    lives on the board file — no central mapping needed.
    """
    board: dict[str, Any] = {
        "title": dashboard.title,
        "notes": dashboard.purpose,
        "text": (
            f"Dashboard for {dashboard.primary_entity}. Add queries and charts here."
        ),
    }
    if dashboard.canonical_data_url:
        board["aliases"] = [dashboard.canonical_data_url]
    return board


def _build_index_dict(
    folder_path: str, dashboards: list[ProposedDashboard]
) -> dict[str, Any]:
    """Build the index.yml landing board for a folder.

    Generates a simple text-only landing that lists the dashboards in the folder.
    No queries or charts — those belong to the individual boards.

    When a dashboard has a ``canonical_data_url``, the index links to that
    data URL instead of the raw file path. Data URLs always resolve: the
    generic system view serves them when no authored board claims them, and
    redirects to the authored board when one does.
    """
    folder_name = folder_path.split("/")[-1]
    lines: list[str] = []
    for d in dashboards:
        link_target = d.canonical_data_url if d.canonical_data_url else f"{d.name}.yml"
        lines.append(f"- [{d.title}]({link_target})")
    dash_list = "\n".join(lines)
    return {
        "title": folder_name.replace("-", " ").title(),
        "notes": f"Dashboard index for the {folder_name} pack folder.",
        "text": f"## Dashboards\n\n{dash_list}\n",
    }


def _write_board(path: Path, board_dict: dict[str, Any]) -> None:
    """Write a board dict to a YAML file in block style."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            board_dict, default_flow_style=False, sort_keys=False, allow_unicode=True
        ),
        encoding="utf-8",
    )


def _require_bare_filename(value: str, label: str) -> None:
    """Reject absolute paths, separators, and empty path components."""
    path = Path(value)
    if path.is_absolute() or path.name != value or value in {"", ".", ".."}:
        raise ValueError(f"{label} must be a bare filename: {value!r}")


def _charts_target(
    project: FilesystemProject, *parts: str, label: str
) -> tuple[Path, Path]:
    """Resolve the canonical ``charts/...`` scaffold target *parts* to where the
    project keeps it, and verify it stays inside the boards folder."""
    relpath = posixpath.normpath("/".join(parts))
    target = project.data_path(relpath)
    if not is_board_relpath(relpath) or not target.is_relative_to(
        project.charts_dir.resolve()
    ):
        raise ValueError(f"{label} must stay inside charts/: {relpath}")
    return target, target.relative_to(project.root)


def _check_duplicate_data_urls(proposal: PackProposal) -> None:
    """Raise if two dashboards in the proposal claim the same canonical data URL.

    Two boards cannot share an alias URL; catching this before any filesystem
    write prevents a broken alias index at serve time.
    """
    seen: dict[str, str] = {}  # url → dashboard name
    for folder in proposal.folders:
        for dash in folder.dashboards:
            if dash.canonical_data_url is None:
                continue
            url = dash.canonical_data_url
            if url in seen:
                raise ValueError(
                    f"duplicate canonical data URL {url!r}: claimed by both "
                    f"'{seen[url]}' and '{dash.name}'. Each canonical data URL "
                    "must be unique across the proposal."
                )
            seen[url] = dash.name


def _validate_scaffold_targets(
    proposal: PackProposal,
    project: FilesystemProject,
) -> tuple[
    list[tuple[str, Path, Path]],
    list[tuple[str, Path, Path]],
    list[tuple[ProposedDashboard, Path, Path]],
]:
    """Validate every output path before the first filesystem mutation."""
    _check_duplicate_data_urls(proposal)

    partial_targets: list[tuple[str, Path, Path]] = []
    landing_targets: list[tuple[str, Path, Path]] = []
    dashboard_targets: list[tuple[ProposedDashboard, Path, Path]] = []

    for partial_filename in proposal.partials:
        if not partial_filename.startswith("_"):
            partial_filename = f"_{partial_filename}"
        _require_bare_filename(partial_filename, "partial filename")
        partial_path, rel = _charts_target(
            project,
            CHARTS_SUBDIR,
            "partials",
            partial_filename,
            label="partial filename",
        )
        partial_targets.append((partial_filename, partial_path, rel))

    for folder in proposal.folders:
        _charts_target(project, folder.path, label="folder path")
        if folder.landing != "index.yml":
            raise ValueError(f"folder landing must be index.yml: {folder.landing!r}")
        _require_bare_filename(folder.landing, "folder landing")
        landing_path, rel_landing = _charts_target(
            project,
            folder.path,
            folder.landing,
            label="folder landing",
        )
        landing_targets.append((folder.path, landing_path, rel_landing))

        for dashboard in folder.dashboards:
            _require_bare_filename(dashboard.name, "dashboard name")
            dash_path, rel_dash = _charts_target(
                project,
                folder.path,
                f"{dashboard.name}.yml",
                label="dashboard name",
            )
            dashboard_targets.append((dashboard, dash_path, rel_dash))

    return partial_targets, landing_targets, dashboard_targets


def apply_proposal(
    proposal: PackProposal,
    project: FilesystemProject,
    overwrite: bool = False,
) -> ScaffoldResult:
    """Apply an approved PackProposal, writing a sparse ``charts/`` folder tree.

    Writes:
    - One ``index.yml`` landing per proposed folder.
    - One ``<name>.yml`` per proposed dashboard.
    - One ``charts/partials/<name>`` per partial listed in the proposal.

    Every generated non-partial board file is validated through
    :func:`~dbt_charts.agent_api.validate.validate_paths`. If any file fails
    validation, its errors are collected in ``ScaffoldResult.errors`` and a
    ``ValueError`` is raised so the caller never silently accepts a broken tree.

    Args:
        proposal: An approved :class:`~dbt_charts.core.pack.models.PackProposal`.
        project: dbt-charts project (root is the directory containing
            ``dbt_charts.yml``).
        overwrite: When ``False`` (default), existing non-empty files are
            skipped and recorded in ``ScaffoldResult.skipped_files``. When
            ``True``, existing files are replaced.

    Returns:
        A :class:`ScaffoldResult` with ``created_files``, ``skipped_files``,
        and ``errors`` (relative to ``project.root``).

    Raises:
        ValueError: When any generated file fails ``validate_paths()``.
    """
    from dbt_charts.agent_api.validate import validate_paths

    result = ScaffoldResult()
    validated_paths: list[Path] = []
    partial_targets, landing_targets, dashboard_targets = _validate_scaffold_targets(
        proposal,
        project,
    )

    # Ensure base dirs exist
    project.charts_dir.mkdir(exist_ok=True)
    (project.charts_dir / "partials").mkdir(exist_ok=True)

    # Write partials — skipped by validate_paths (_*.yml convention)
    for partial_filename, partial_path, rel in partial_targets:
        if partial_path.exists() and partial_path.stat().st_size > 0 and not overwrite:
            result.skipped_files.append(rel)
            continue
        # Write a minimal partial YAML comment block
        partial_path.write_text(
            f"# Shared partial: {partial_filename}\n"
            f"# Add shared query fragments, variables, or chart definitions here.\n",
            encoding="utf-8",
        )
        result.created_files.append(rel)

    # Write folder landings and dashboards
    for folder, (_folder_path, landing_path, rel_landing) in zip(
        proposal.folders,
        landing_targets,
        strict=True,
    ):
        if landing_path.exists() and landing_path.stat().st_size > 0 and not overwrite:
            result.skipped_files.append(rel_landing)
        else:
            _write_board(
                landing_path, _build_index_dict(folder.path, folder.dashboards)
            )
            result.created_files.append(rel_landing)
            validated_paths.append(landing_path)

    # Entity dashboards
    for dashboard, dash_path, rel_dash in dashboard_targets:
        if dash_path.exists() and dash_path.stat().st_size > 0 and not overwrite:
            result.skipped_files.append(rel_dash)
        else:
            _write_board(dash_path, _build_board_dict(dashboard))
            result.created_files.append(rel_dash)
            validated_paths.append(dash_path)

    # Validate gate — validate all newly written board files
    if validated_paths:
        validate_results = validate_paths(validated_paths, project=project)
        for vr in validate_results:
            if not vr.success:
                for err in vr.errors:
                    result.errors.append(f"{vr.path}: {err.message}")

    if result.errors:
        raise ValueError(
            f"apply_proposal: {len(result.errors)} generated file(s) failed validation:\n"
            + "\n".join(result.errors)
        )

    return result
