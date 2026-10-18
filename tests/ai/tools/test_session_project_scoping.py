"""Security regression: AI tool handlers scope to the session project.

A Cloud-shaped context has a non-filesystem session project
(``CloudManagedProject``, here doubled by ``InMemoryProject``). A prompt-injected
model must NOT be able to steer any file tool onto the host filesystem — neither
via a model-supplied ``project_dir`` argument nor via a ``path`` that resolves
outside the backing store. Both reads and writes resolve against the session
project's backing store, never disk.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.tools import dispatch_tool_call
from dbt_charts.core.project import Project


@pytest.fixture
def cloud_context(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], Project],
) -> DbtChartsAIContext:
    """A Cloud-shaped context: non-filesystem session project, no dashboards dir."""
    project = in_memory_project(
        tmp_path / "in-memory-root", {"charts/report.yml": "title: In Store\n"}
    )
    return DbtChartsAIContext(
        project_session=ProjectSession.from_project(project),
        dashboards_directory=None,
    )


class TestWriteToolsUseBackingStoreNotHost:
    def test_write_file_never_touches_host(
        self, cloud_context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        # A prompt-injected project_dir must be ignored: the write goes to the
        # session's backing store, never the attacker's host directory.
        attacker_root = tmp_path / "attacker"
        attacker_root.mkdir()
        result = dispatch_tool_call(
            "write_file",
            {
                "path": "charts/pwned.yml",
                "content": "owned: true\n",
                "project_dir": str(attacker_root),
            },
            context=cloud_context,
        )
        assert result["success"] is True
        assert not (attacker_root / "charts" / "pwned.yml").exists()
        # The write landed in the session's backing store.
        read_back = dispatch_tool_call(
            "read_file", {"path": "charts/pwned.yml"}, context=cloud_context
        )
        assert read_back["content"] == "owned: true\n"

    def test_edit_file_uses_backing_store(
        self, cloud_context: DbtChartsAIContext
    ) -> None:
        result = dispatch_tool_call(
            "edit_file",
            {"path": "charts/report.yml", "old_string": "In", "new_string": "Out"},
            context=cloud_context,
        )
        assert result["success"] is True
        read_back = dispatch_tool_call(
            "read_file", {"path": "charts/report.yml"}, context=cloud_context
        )
        assert read_back["content"] == "title: Out Store\n"


class TestReadToolsUseBackingStoreNotDisk:
    def test_describe_board_reads_backing_store(
        self, cloud_context: DbtChartsAIContext
    ) -> None:
        result = dispatch_tool_call(
            "describe_board", {"path": "charts/report.yml"}, context=cloud_context
        )
        assert result["success"] is True
        assert result["path"] == "charts/report.yml"

    def test_describe_board_ignores_host_files(
        self,
        cloud_context: DbtChartsAIContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "secret.yml").write_text("title: Secret\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        result = dispatch_tool_call(
            "describe_board", {"path": "secret.yml"}, context=cloud_context
        )
        assert result["success"] is False
        assert str(tmp_path) not in str(result)

    def test_read_file_reads_from_backing_store(
        self, cloud_context: DbtChartsAIContext
    ) -> None:
        result = dispatch_tool_call(
            "read_file", {"path": "charts/report.yml"}, context=cloud_context
        )
        assert result["success"] is True
        assert result["content"] == "title: In Store\n"

    def test_read_file_ignores_attacker_project_dir(
        self, cloud_context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        secret = tmp_path / "secret.yml"
        secret.write_text("host: secret\n", encoding="utf-8")
        result = dispatch_tool_call(
            "read_file",
            {"path": "secret.yml", "project_dir": str(tmp_path)},
            context=cloud_context,
        )
        # The attacker's project_dir is ignored; secret.yml is not in the store.
        assert result["success"] is False
        assert result.get("content") is None

    def test_search_files_searches_backing_store(
        self, cloud_context: DbtChartsAIContext
    ) -> None:
        result = dispatch_tool_call(
            "search_files", {"kind": "all", "text": "In Store"}, context=cloud_context
        )
        assert result["success"] is True
        assert [m["file_path"] for m in result["results"]] == ["charts/report.yml"]

    def test_execute_query_named_form_reads_from_backing_store(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        # cloud_context's report.yml has no queries; build a query-bearing
        # board directly in a non-filesystem project's store instead.
        board = (
            "queries:\n"
            "  inline:\n"
            "    type: values\n"
            "    rows:\n"
            "      - {a: 1, b: 2}\n"
            "charts:\n"
            "  c:\n"
            "    query: inline\n"
            "    type: table\n"
            "rows:\n"
            "  - c\n"
        )
        project = in_memory_project(tmp_path, {"charts/queryable.yml": board})
        context = DbtChartsAIContext(
            project_session=ProjectSession.from_project(project),
            dashboards_directory=None,
        )
        result = dispatch_tool_call(
            "execute_sql",
            {"query": "inline", "board": "charts/queryable.yml"},
            context=context,
        )
        assert result["success"] is True
        assert result["data"] == [{"a": 1, "b": 2}]
        assert result["csv"] == "a,b\n1,2\n"
        # The board exists only in the in-memory store, never on real disk.
        assert not (tmp_path / "charts" / "queryable.yml").exists()

    def test_execute_query_named_form_survives_ragged_rows(
        self, tmp_path: Path, in_memory_project: Callable[..., Project]
    ) -> None:
        board = (
            "queries:\n"
            "  ragged:\n"
            "    type: values\n"
            "    rows:\n"
            "      - {a: 1, b: 2}\n"
            "      - {a: 3}\n"
            "charts:\n"
            "  c:\n"
            "    query: ragged\n"
            "    type: table\n"
            "rows:\n"
            "  - c\n"
        )
        project = in_memory_project(tmp_path, {"charts/ragged.yml": board})
        context = DbtChartsAIContext(
            project_session=ProjectSession.from_project(project),
            dashboards_directory=None,
        )
        result = dispatch_tool_call(
            "execute_sql",
            {"query": "ragged", "board": "charts/ragged.yml"},
            context=context,
        )
        assert result["success"] is True, result
        assert result["csv"].startswith("a,b\n1,2\n3,")


class TestLocalFilesystemUnchanged:
    """CLI/MCP surfaces (session project IS a FilesystemProject) keep working."""

    def test_write_then_read_through_session_project(
        self,
        make_context: Callable[..., DbtChartsAIContext],
        tmp_path: Path,
    ) -> None:
        ctx = make_context()  # FilesystemProject session rooted at tmp_path
        w = dispatch_tool_call(
            "write_file",
            {"path": "charts/new.yml", "content": "title: New\n"},
            context=ctx,
        )
        assert w["success"] is True
        assert (tmp_path / "charts" / "new.yml").read_text() == "title: New\n"
        r = dispatch_tool_call("read_file", {"path": "charts/new.yml"}, context=ctx)
        assert r["success"] is True
        assert r["content"] == "title: New\n"
