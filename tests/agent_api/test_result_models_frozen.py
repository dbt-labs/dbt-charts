"""Every agent_api result model is frozen=True: mutation raises, never silently applies.

Companion to the C8 pydantic-conventions audit (workstream dft-core task
audit-pydantic-models-for-configdict-frozen-true-candidates-c8). Result models are
constructed once by a verb function and consumed read-only by CLI/MCP callers;
freezing catches an accidental in-place mutation that would otherwise silently
diverge from what was actually returned.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath

import pytest
from pydantic import BaseModel, ValidationError

from dbt_charts.agent_api.boards import (
    BoardSummary,
    CompiledBoard,
    ListBoardsResult,
    SkippedFile,
)
from dbt_charts.agent_api.describe import (
    ChartDescription,
    DescribeBoardResult,
    LayoutDescription,
    QueryDescription,
    VariableDescription,
)
from dbt_charts.agent_api.describe_query import DescribeQueryColumn, DescribeQueryResult
from dbt_charts.agent_api.diagnostics import (
    DiagnosticCodeDetail,
    DiagnosticCodeDetailResult,
    DiagnosticCodesListResult,
    DiagnosticCodeSummary,
)
from dbt_charts.agent_api.docs._loader import (
    DocsResult,
    DocsSearchHit,
    Topic,
    TopicEntry,
)
from dbt_charts.agent_api.doctor import DoctorCheck, DoctorReport
from dbt_charts.agent_api.file_refs import ExpandedPrompt, FileRef
from dbt_charts.agent_api.files import (
    EditFileResult,
    GlobResult,
    GrepMatch,
    GrepResult,
    ReadFileResult,
    WriteFileResult,
)
from dbt_charts.agent_api.init import InitResult
from dbt_charts.agent_api.inspect import InspectTemplate, ValidateTemplatesResult
from dbt_charts.agent_api.mcp_install import InstallResult
from dbt_charts.agent_api.pack import ScaffoldResult
from dbt_charts.agent_api.query import (
    BoardQueryLookupResult,
    ExecuteQueryResult,
    QueryBoardResult,
)
from dbt_charts.agent_api.schema_hints import ChartTypeDisplay, SchemaHints
from dbt_charts.agent_api.search import BoardSearchHit, SearchResult
from dbt_charts.agent_api.skill_install import InstallSkillsResult
from dbt_charts.agent_api.skills import (
    Skill,
    SkillExample,
    SkillList,
    SkillMetadata,
    SkillSearchHit,
    SkillSearchResult,
)
from dbt_charts.agent_api.validate import ContentValidateResult, ValidateResult
from dbt_charts.cli.filesystem_project import FilesystemProject

# (instance, attribute to mutate) — one representative field per model is enough;
# frozen=True blocks assignment to every field uniformly.
FROZEN_RESULT_INSTANCES: list[tuple[BaseModel, str]] = [
    (ReadFileResult(success=True, path="x"), "success"),
    (WriteFileResult(success=True, path="x"), "success"),
    (EditFileResult(success=True, path="x"), "success"),
    (GlobResult(success=True), "success"),
    (GrepResult(success=True), "success"),
    (ValidateTemplatesResult(success=True), "success"),
    (DescribeBoardResult(success=True, path="x"), "success"),
    (DiagnosticCodeSummary(code="X", summary="s"), "code"),
    (DiagnosticCodeDetail(code="X", level="warning", summary="s", doc="d"), "code"),
    (
        DiagnosticCodesListResult(success=True, mode="diagnostic_list", codes=[]),
        "success",
    ),
    (DiagnosticCodeDetailResult(success=True, mode="diagnostic_detail"), "success"),
    (ValidateResult(success=True, path="x"), "success"),
    (ContentValidateResult(success=True), "success"),
    (ScaffoldResult(), "created_files"),
    (DoctorReport(success=True, checks=[]), "success"),
    (DoctorCheck(code="install", status="pass", message="x"), "status"),
    (InitResult(project_dir=Path("x"), dbt_detected=False), "dbt_detected"),
    (BoardQueryLookupResult(success=True), "success"),
    (
        ExecuteQueryResult(
            success=True,
            columns=[],
            data=[],
            errors=[],
            row_count=0,
            truncated=False,
        ),
        "success",
    ),
    (QueryBoardResult(success=True, name="n", path=PurePosixPath("x")), "success"),
    (SearchResult(success=True, errors=[], results=[]), "success"),
    (
        ListBoardsResult(success=True, directory=FilesystemProject(Path()).directory()),
        "success",
    ),
    (CompiledBoard(success=True), "success"),
    (
        InstallResult(
            client_name="c",
            config_path=Path("x"),
            already_configured=False,
            updated=False,
            message="m",
        ),
        "updated",
    ),
    (InstallSkillsResult(), "installed"),
    (DescribeQueryColumn(name="n", type="t"), "name"),
    (DescribeQueryResult(success=True), "success"),
    (DocsResult(mode="index"), "mode"),
    (SkillSearchResult(query="q"), "success"),
    (GrepMatch(path="x", line_number=1, line="l"), "path"),
    (
        BoardSearchHit(
            title="t",
            summary="s",
            match_score=1.0,
            match_reasons=[],
            board_path="x",
            file_path="charts/x",
            query_names=[],
            charts=[],
            referenced_data_paths=[],
        ),
        "title",
    ),
    (
        SkillSearchHit(
            name="n", description="d", kind="workflow", has_examples=False, score=1.0
        ),
        "name",
    ),
    (QueryDescription(name="n", type="t", summary="s"), "name"),
    (ChartDescription(name="n", type="t", query="q"), "name"),
    (VariableDescription(name="n", type="t"), "name"),
    (LayoutDescription(primitive="p"), "primitive"),
    (Topic(id="i", title="t"), "id"),
    (TopicEntry(id="i", title="t"), "id"),
    (
        DocsSearchHit(topic="t", title="ti", section="ti", score=1.0, content="s"),
        "topic",
    ),
    (InspectTemplate(name="n"), "name"),
    (ChartTypeDisplay(label="l", icon="i"), "label"),
    (
        SchemaHints(
            chart_types=[],
            input_types=[],
            theme_names=[],
            chart_type_display={},
        ),
        "chart_types",
    ),
    (FileRef(token="@x", path=Path("x")), "token"),
    (ExpandedPrompt(text="t", references=[]), "text"),
    (SkillExample(path=Path("x"), name="n"), "name"),
    (SkillMetadata(), "author"),
    (
        Skill(
            name="n", description="d", kind="workflow", directory=Path("x"), body="b"
        ),
        "name",
    ),
    (SkillList(), "success"),
]


@pytest.mark.parametrize(
    ("instance", "attr"),
    FROZEN_RESULT_INSTANCES,
    ids=[type(instance).__name__ for instance, _ in FROZEN_RESULT_INSTANCES],
)
def test_result_model_is_frozen(instance: BaseModel, attr: str) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        setattr(instance, attr, getattr(instance, attr))


@pytest.mark.parametrize(
    "cls",
    [type(instance) for instance, _ in FROZEN_RESULT_INSTANCES],
    ids=[type(instance).__name__ for instance, _ in FROZEN_RESULT_INSTANCES],
)
def test_result_model_declares_frozen_config(cls: type[BaseModel]) -> None:
    assert cls.model_config.get("frozen") is True, (
        f"{cls.__name__} must declare model_config frozen=True "
        "(agent_api result models are constructed once and never mutated)."
    )


def test_scaffold_result_list_append_bypasses_frozen() -> None:
    """In-place list mutation (append) on a frozen model's field is legal.

    ``apply_proposal`` builds one ``ScaffoldResult()`` and appends to its list
    fields as it walks the proposal; frozen=True only blocks reassigning the
    attribute itself, not mutating the mutable object it points to.
    """
    result = ScaffoldResult()
    result.created_files.append(Path("charts/a.yaml"))
    assert result.created_files == [Path("charts/a.yaml")]


# BoardSummary/SkippedFile carry a ProjectPath field, which requires a real
# Project to construct — exercised separately rather than in the static
# FROZEN_RESULT_INSTANCES list (built at collection time, before fixtures exist).
@pytest.mark.parametrize("cls", [BoardSummary, SkippedFile])
def test_dashboard_summary_and_skipped_file_are_frozen(
    cls: type[BaseModel],
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    project = local_project(tmp_path)
    file = project.path_for_fspath(tmp_path / "charts" / "a.yaml")
    instance = (
        BoardSummary(file=file, title="t")
        if cls is BoardSummary
        else SkippedFile(file=file, reason="r")
    )
    assert cls.model_config.get("frozen") is True
    with pytest.raises(ValidationError, match="frozen"):
        instance.file = file
