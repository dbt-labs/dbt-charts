"""Tests for apply_proposal — the scaffold apply path.

TDD: these tests are written before the implementation.

Covers:
- Connector-first Zendesk proposal creates charts/zendesk/index.yml + entity dashboards
- Every generated file passes validate_paths()
- Landing files are index.yml (assert no overview.yml emitted)
- Re-applying without --overwrite skips existing files and records them in ScaffoldResult
- Generated YAML is block-style (no flow-style objects)
- Proposal lacking source context (no evidence tables) emits notes-only dashboards
- ScaffoldResult has created_files, skipped_files, errors
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from dbt_charts.agent_api.pack import ScaffoldResult, apply_proposal
from dbt_charts.agent_api.validate import validate_paths
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.pack.models import PackProposal, ProposedDashboard, ProposedFolder
from dbt_charts.core.project import is_private_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zendesk_proposal() -> PackProposal:
    """Build a minimal connector-first Zendesk proposal with real evidence."""
    return PackProposal(
        organization_mode="connector-first",
        detected_sources=["fivetran_zendesk"],
        folders=[
            ProposedFolder(
                path="charts/zendesk",
                landing="index.yml",
                dashboards=[
                    ProposedDashboard(
                        name="ticket-overview",
                        title="Ticket Overview",
                        purpose="Track ticket volume, trends, and key metrics.",
                        primary_entity="ticket",
                        evidence=[],
                    ),
                    ProposedDashboard(
                        name="user-overview",
                        title="User Overview",
                        purpose="Understand user engagement and activity.",
                        primary_entity="user",
                        evidence=[],
                    ),
                ],
            )
        ],
        partials=["_date_filter.yml"],
        uncertainty_notes=[],
        planned_actions=[],
    )


def _no_evidence_proposal() -> PackProposal:
    """Proposal where dashboards have no evidence — no source context approved."""
    return PackProposal(
        organization_mode="connector-first",
        detected_sources=["unknown_source"],
        folders=[
            ProposedFolder(
                path="charts/unknown",
                landing="index.yml",
                dashboards=[
                    ProposedDashboard(
                        name="overview",
                        title="Overview",
                        purpose="Overview of the unknown data source.",
                        primary_entity="unknown",
                        evidence=[],
                    ),
                ],
            )
        ],
        partials=[],
        uncertainty_notes=[],
        planned_actions=[],
    )


def _escaping_proposal() -> PackProposal:
    """Proposal with a folder path that escapes the project tree."""
    proposal = _zendesk_proposal()
    proposal.folders[0].path = "../escaped"
    proposal.folders[0].dashboards[0].name = "pwned"
    proposal.folders[0].dashboards = proposal.folders[0].dashboards[:1]
    return proposal


# ---------------------------------------------------------------------------
# ScaffoldResult shape
# ---------------------------------------------------------------------------


def test_apply_proposal_returns_scaffold_result(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """apply_proposal returns a ScaffoldResult with created_files, skipped_files, errors."""
    (tmp_path / "dbt_charts.yml").write_text("")

    result = apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    assert isinstance(result, ScaffoldResult)
    assert len(result.created_files) > 0


# ---------------------------------------------------------------------------
# File creation — connector-first Zendesk
# ---------------------------------------------------------------------------


def test_apply_creates_folder_landing_index_yml(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Connector-first Zendesk proposal creates charts/zendesk/index.yml."""
    (tmp_path / "dbt_charts.yml").write_text("")

    apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    index_path = tmp_path / "charts" / "zendesk" / "index.yml"
    assert index_path.exists(), f"Expected {index_path} to exist"


def test_apply_creates_entity_dashboards(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Connector-first Zendesk proposal creates entity dashboard YAMLs."""
    (tmp_path / "dbt_charts.yml").write_text("")

    apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    ticket_path = tmp_path / "charts" / "zendesk" / "ticket-overview.yml"
    user_path = tmp_path / "charts" / "zendesk" / "user-overview.yml"
    assert ticket_path.exists(), f"Expected {ticket_path} to exist"
    assert user_path.exists(), f"Expected {user_path} to exist"


def test_apply_creates_partials(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Proposal with partials creates partial files under charts/partials/."""
    (tmp_path / "dbt_charts.yml").write_text("")

    apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    partial_path = tmp_path / "charts" / "partials" / "_date_filter.yml"
    assert partial_path.exists(), f"Expected {partial_path} to exist"


def test_apply_created_files_in_result(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """created_files lists every file written."""
    (tmp_path / "dbt_charts.yml").write_text("")

    result = apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    created_names = [p.name for p in result.created_files]
    assert "index.yml" in created_names
    assert "ticket-overview.yml" in created_names
    assert "user-overview.yml" in created_names
    assert "_date_filter.yml" in created_names


# ---------------------------------------------------------------------------
# D-03: index.yml, never overview.yml
# ---------------------------------------------------------------------------


def test_apply_no_overview_yml_emitted(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """No overview.yml should ever be emitted — only index.yml for landings."""
    (tmp_path / "dbt_charts.yml").write_text("")

    apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    all_yml = list((tmp_path / "charts").glob("**/*.yml"))
    names = [f.name for f in all_yml]
    assert "overview.yml" not in names, (
        f"overview.yml should not be emitted; found: {names}"
    )


# ---------------------------------------------------------------------------
# Validate gate — every generated file passes validate_paths()
# ---------------------------------------------------------------------------


def test_apply_all_generated_files_validate(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Every generated non-partial board file passes validate_paths()."""
    (tmp_path / "dbt_charts.yml").write_text("")

    result = apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    assert not result.errors, f"apply_proposal reported errors: {result.errors}"

    # Validate the boards folder (validate_paths skips _*.yml partials)
    validate_results = validate_paths(
        [tmp_path / "charts"],
        project=local_project(tmp_path),
    )
    failures = [r for r in validate_results if not r.success]
    assert not failures, f"Validation failures: {failures}"


def test_apply_rejects_escaping_folder_before_any_write(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """An escaping folder path raises before writing files outside project_dir."""
    (tmp_path / "project" / "dbt_charts.yml").parent.mkdir()
    project_dir = tmp_path / "project"
    (project_dir / "dbt_charts.yml").write_text("")

    import pytest

    with pytest.raises(ValueError, match="inside charts"):
        apply_proposal(_escaping_proposal(), local_project(project_dir))

    assert not (tmp_path / "escaped").exists()
    assert not (project_dir / "charts").exists()


# ---------------------------------------------------------------------------
# Block-style YAML (no flow-style objects)
# ---------------------------------------------------------------------------


def test_apply_yaml_is_block_style(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Generated YAML must be block-style — no {key: val} inline objects."""
    (tmp_path / "dbt_charts.yml").write_text("")

    apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    for yml_file in (tmp_path / "charts").glob("**/*.yml"):
        content = yml_file.read_text()
        # Flow-style objects are indicated by '{' on a line that isn't a comment.
        # A bare "{" that isn't in a YAML anchor/alias is a flow-style mapping.
        lines_with_braces = [
            line
            for line in content.splitlines()
            if "{" in line and not line.lstrip().startswith("#")
        ]
        assert not lines_with_braces, (
            f"{yml_file.name} contains flow-style objects: {lines_with_braces}"
        )


def test_apply_yaml_has_no_todo_placeholders(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Generated YAML must not contain TODO placeholders."""
    (tmp_path / "dbt_charts.yml").write_text("")

    apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    for yml_file in (tmp_path / "charts").glob("**/*.yml"):
        content = yml_file.read_text()
        assert "TODO" not in content, f"{yml_file.name} contains TODO placeholder"
        assert "todo" not in content.lower() or "todo" in yml_file.name.lower(), (
            f"{yml_file.name} contains 'todo' (case-insensitive)"
        )


# ---------------------------------------------------------------------------
# Idempotency / overwrite guard
# ---------------------------------------------------------------------------


def test_apply_skips_existing_files_without_overwrite(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Re-applying without overwrite=True skips existing files."""
    (tmp_path / "dbt_charts.yml").write_text("")
    proposal = _zendesk_proposal()

    # First apply
    first_result = apply_proposal(proposal, local_project(tmp_path))
    assert first_result.created_files, "First apply should create files"

    # Second apply without overwrite
    second_result = apply_proposal(proposal, local_project(tmp_path))
    assert not second_result.created_files, "Second apply should create no new files"
    assert second_result.skipped_files, "Second apply should report skipped files"


def test_apply_overwrite_replaces_existing_files(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Re-applying with overwrite=True replaces existing files."""
    (tmp_path / "dbt_charts.yml").write_text("")
    proposal = _zendesk_proposal()

    # First apply
    apply_proposal(proposal, local_project(tmp_path))

    # Mutate a file to confirm it gets overwritten
    index_path = tmp_path / "charts" / "zendesk" / "index.yml"
    index_path.write_text("title: MUTATED\ntext: mutated\n")

    # Second apply with overwrite
    second_result = apply_proposal(proposal, local_project(tmp_path), overwrite=True)
    assert second_result.created_files, "Overwrite apply should report created_files"

    # Confirm the file was actually replaced
    content = index_path.read_text()
    assert "MUTATED" not in content, "File should have been overwritten"


# ---------------------------------------------------------------------------
# No fabricated queries when evidence is absent
# ---------------------------------------------------------------------------


def test_apply_no_evidence_emits_note_only(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Proposal with no evidence emits boards with title+notes+text only, no queries."""
    (tmp_path / "dbt_charts.yml").write_text("")

    apply_proposal(_no_evidence_proposal(), local_project(tmp_path))

    for yml_file in (tmp_path / "charts").glob("**/*.yml"):
        if is_private_name(yml_file.name):
            continue
        data = yaml.safe_load(yml_file.read_text())
        assert isinstance(data, dict)
        assert "queries" not in data, (
            f"{yml_file.name} emits queries despite no evidence: {data}"
        )
        assert "charts" not in data, (
            f"{yml_file.name} emits charts despite no evidence: {data}"
        )


# ---------------------------------------------------------------------------
# Landing file content
# ---------------------------------------------------------------------------


def test_apply_index_yml_has_title_and_notes(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """The index.yml landing file has a title and notes."""
    (tmp_path / "dbt_charts.yml").write_text("")

    apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    index_path = tmp_path / "charts" / "zendesk" / "index.yml"
    data = yaml.safe_load(index_path.read_text())
    assert "title" in data
    assert "notes" in data or "text" in data


def test_apply_entity_dashboard_has_title(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Entity dashboard YAMLs have a title."""
    (tmp_path / "dbt_charts.yml").write_text("")

    apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    ticket_path = tmp_path / "charts" / "zendesk" / "ticket-overview.yml"
    data = yaml.safe_load(ticket_path.read_text())
    assert "title" in data, f"ticket-overview.yml missing title: {data}"


def test_apply_scaffolds_into_the_projects_boards_folder(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """With ``boards_dir: notes`` the boards land in ``notes/``, never in a
    ``charts/`` folder that boards_dir displaced."""
    (tmp_path / "dbt_charts.yml").write_text("boards_dir: notes\n")

    result = apply_proposal(_zendesk_proposal(), local_project(tmp_path))

    assert (tmp_path / "notes" / "zendesk" / "index.yml").exists()
    assert not (tmp_path / "charts").exists()
    assert "notes/zendesk/index.yml" in {p.as_posix() for p in result.created_files}
