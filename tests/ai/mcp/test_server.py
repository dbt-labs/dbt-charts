"""Tests for the MCP server in dbt_charts.ai.mcp.server.

Covers the scoped resource handlers, the dct://guide/* design-guide
resources, create_server() construction, thin-shim LOC guards, the
post-collapse MCP wire surface, per-tool inputSchema property pins,
and per-resource / per-URI / ResourceTemplate coverage (FR-004, FR-005, FR-013).
"""

import asyncio
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.types import (
    ListResourcesRequest,
    ListResourceTemplatesRequest,
    ListToolsRequest,
)

import dbt_charts
from dbt_charts.agent_api import ProjectSession
from dbt_charts.agent_api.boards import CompiledBoard, get_board
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.mcp.server import (
    _BASE_RESOURCES,
    _docs_topic_resources,
    _is_domain_error,
    _read_resource_content,
    create_server,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import Project

from ..._paths import DBT_CHARTS_DIR

_FIXTURE_SINGLE_QUERY_BOARD = (
    DBT_CHARTS_DIR / "tests" / "fixtures" / "single-query-board"
)

DBT_CHARTS_SRC = Path(dbt_charts.__file__).parent


def _loc(rel: str) -> int:
    return len((DBT_CHARTS_SRC / rel).read_text().splitlines())


@pytest.fixture
def make_context(tmp_path: Path):
    """Factory: construct a DbtChartsAIContext over a scratch project."""

    def _make(dashboards_directory: Path | None = None) -> DbtChartsAIContext:
        return DbtChartsAIContext(
            project_session=ProjectSession.open(tmp_path, read_only=False),
            dashboards_directory=dashboards_directory,
        )

    return _make


@pytest.fixture
def mcp_server(make_context):
    """A constructed MCP server bound to a scratch adapter registry.

    Used by the surface, schema-shape, and resource/tool advertisement
    tests — none of them need anything project-specific, just a
    ready-to-introspect server.
    """
    return create_server(make_context())


def _tool_names(server) -> set[str]:
    handler = server.request_handlers[ListToolsRequest]
    result = asyncio.run(handler(None))
    return {t.name for t in result.root.tools}


class TestScopedMCPServerHelpers:
    """Scoped MCP server helpers should respect injected context."""

    def test_read_resource_uses_scoped_dashboard_directory(
        self, tmp_path: Path
    ) -> None:
        dashboard_dir = tmp_path / "dashboards"
        dashboard_dir.mkdir()
        boards = dashboard_dir / "charts"
        boards.mkdir()
        (boards / "sales.yml").write_text(
            """
title: Sales Dashboard
queries:
  q:
    sql: SELECT 1 as value
    source: test
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - c
"""
        )

        ctx = DbtChartsAIContext(
            project_session=ProjectSession.open(dashboard_dir, read_only=False),
            dashboards_directory=dashboard_dir,
        )
        result = _read_resource_content("dct://boards", context=ctx)

        assert "sales.yml" in result

    @pytest.mark.parametrize(
        ("uri", "expected_error_fragment"),
        [
            ("dct://board/../../etc/passwd", "would escape above the project root"),
            ("dct://board//etc/passwd", "must be relative"),
        ],
        ids=["path_escape", "absolute_path"],
    )
    def test_read_resource_rejects_bad_dashboard_path(
        self, tmp_path: Path, make_context, uri: str, expected_error_fragment: str
    ) -> None:
        result = _read_resource_content(uri, context=make_context(tmp_path))
        assert expected_error_fragment in result

    def test_boards_resource_uses_path_key_not_file(self, tmp_path: Path) -> None:
        """dct://boards JSON must use "path" (not "file") per wire contract.

        Regression for: DashboardSummary.file carries a ProjectPath with
        serialization_alias="path", requiring by_alias=True at the dump site.
        A dropped by_alias=True would silently emit "file" instead of "path",
        breaking every MCP client that reads the dashboards resource.
        """
        dashboard_dir = tmp_path / "dashboards"
        dashboard_dir.mkdir()
        boards = dashboard_dir / "charts"
        boards.mkdir()
        (boards / "sales.yml").write_text(
            "title: Sales Dashboard\n"
            "queries:\n  q:\n    sql: SELECT 1 as value\n    source: test\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: value\n"
            "rows:\n  - c\n"
        )

        ctx = DbtChartsAIContext(
            project_session=ProjectSession.open(dashboard_dir, read_only=False),
            dashboards_directory=dashboard_dir,
        )
        result = _read_resource_content("dct://boards", context=ctx)
        payload = json.loads(result)
        assert payload.get("success") is True
        boards = payload["boards"]
        assert len(boards) >= 1
        first = boards[0]
        assert "path" in first, f"Expected 'path' key in board entry, got: {first}"
        assert "file" not in first, (
            f"'file' key must not appear in wire output: {first}"
        )

    def test_get_dashboard_invalid_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test getting a dashboard with invalid YAML."""
        dashboard = tmp_path / "invalid.yml"
        dashboard.write_text(
            """
title: Invalid
queries:
  - this: is
  - not: valid
  - for: queries
"""
        )

        result = get_board(dashboard, project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) > 0

    def test_dashboard_resource_omits_null_raw_yaml_on_failure(
        self, tmp_path: Path, make_context
    ) -> None:
        """The dct://board/{path} resource must not emit "raw_yaml": null
        on the failure path — old wire shape simply omitted the field. Without
        exclude_none, callers parsing the JSON would see the explicit null.
        """
        import json as _json

        # Reference a path that does not exist — get_board fails, raw_yaml is None.
        result = _read_resource_content(
            "dct://board/missing.yml", context=make_context(tmp_path)
        )
        payload = _json.loads(result)
        assert payload.get("success") is False
        assert "raw_yaml" not in payload, (
            f"raw_yaml must be omitted on failure path, got {payload}"
        )


class TestDesignGuides:
    """Tests for shared design guide prompts under ``dbt_charts.ai.prompts``."""

    @pytest.mark.parametrize(
        ("slug", "must_contain_exact", "must_contain_lower"),
        [
            ("board-design", ("Dashboard Design", "Chart Selection"), ("kpi",)),
            ("report-design", ("Report Design", "Executive Summary"), ("narrative",)),
        ],
        ids=["board-design", "report-design"],
    )
    def test_design_guide_loads(
        self,
        slug: str,
        must_contain_exact: tuple[str, ...],
        must_contain_lower: tuple[str, ...],
    ) -> None:
        from dbt_charts.ai.prompts import load_shared_prompt

        guide = load_shared_prompt(slug)
        for token in must_contain_exact:
            assert token in guide
        lower = guide.lower()
        for token in must_contain_lower:
            assert token in lower

    def test_nonexistent_prompt_returns_empty(self) -> None:
        """Test that missing prompt returns empty string."""
        from dbt_charts.ai.prompts import load_shared_prompt

        result = load_shared_prompt("nonexistent_prompt")
        assert result == ""


class TestMCPServerCreation:
    """Tests for MCP server creation (without actually running it)."""

    @pytest.mark.parametrize(
        ("schema_name", "expected_props"),
        [
            ("RENDER_BOARD", {"path", "yaml_content"}),
        ],
        ids=["render_board"],
    )
    def test_canonical_schema_dict_has_required_properties(
        self, schema_name: str, expected_props: set[str]
    ) -> None:
        """Pin the hand-authored canonical schema dicts in dbt_charts.ai.tool_schemas.

        Distinct from TestMCPInputSchemaShape, which pins what the running
        MCP server exposes — this pins the source-of-truth dicts.
        """
        from dbt_charts.ai import tool_schemas

        schema = getattr(tool_schemas, schema_name)["input_schema"]
        assert schema["type"] == "object"
        assert expected_props.issubset(schema["properties"].keys())

    def test_mcp_server_does_not_advertise_schema_context_resource(
        self, mcp_server
    ) -> None:
        """The schema/context resource is gone.

        Resources aren't auto-loaded into agent shells; agents query the
        warehouse metadata views themselves via execute_query.
        """
        handler = mcp_server.request_handlers[ListResourcesRequest]
        result = asyncio.run(handler(None))
        uris = {str(r.uri) for r in result.root.resources}
        assert "dct://schema/context" not in uris, (
            f"schema/context resource should be gone: {uris}"
        )

    def test_read_resource_defaults_to_boards_directory_when_unscoped(
        self, monkeypatch, tmp_path: Path, make_context
    ) -> None:
        captured: dict[str, object] = {}

        def fake_get_board(
            path: Path, *, project: Project, include_raw: bool = False
        ) -> CompiledBoard:
            captured["path"] = path
            captured["include_raw"] = include_raw
            return CompiledBoard(
                success=True,
                errors=[],
                warnings=[],
                board=None,
                raw_yaml="title: Sales Dashboard",
            )

        monkeypatch.setattr("dbt_charts.agent_api.boards.get_board", fake_get_board)
        monkeypatch.chdir(tmp_path)
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "sales.yml").write_text("title: Sales Dashboard\n")

        result = _read_resource_content("dct://board/sales.yml", context=make_context())

        assert "Sales Dashboard" in result
        assert captured["path"] == boards_dir / "sales.yml"
        assert captured["include_raw"] is True


class TestRunServerPortPropagation:
    """`run_server` must thread the resolved preview port into the
    DbtChartsAIContext that handlers see — so URL-emitting tools point at
    this session's actual server, not the default 8765. URL formatting
    itself is covered by `dbt-charts/tests/agent_api/test_dashboards.py`,
    and the e2e proof for two concurrent sessions getting different
    ports lives in `tests/e2e/mcp/test_mcp_serve.py`.
    """

    def test_resolved_port_reaches_dbt_charts_ai_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        fixed_port = 18999  # arbitrary non-default

        captured: dict[str, object] = {}

        # Stub build_embedded_server so this test doesn't bind a port or
        # boot uvicorn — we only care that the resolved port reaches the
        # context. Real port resolution (including the taken-port increment
        # against a live socket) is covered by
        # dbt-charts/tests/core/test_serve_embedded.py; serving itself is
        # `asyncio.create_task(http_server.serve())` in run_server.
        class _StubServer:
            should_exit = False

            async def serve(self) -> None:
                return None

        def _stub_build(*args: object, **kwargs: object) -> tuple[_StubServer, int]:
            return _StubServer(), fixed_port

        monkeypatch.setattr(
            "dbt_charts.core.serve.embedded.build_embedded_server", _stub_build
        )

        # Stub stdio_server so run_server returns immediately after
        # constructing the MCP server with the resolved port.
        class _StubStdio:
            async def __aenter__(self) -> tuple[object, object]:
                return object(), object()

            async def __aexit__(self, *args: object) -> None:
                return None

        def _stub_stdio_factory() -> object:
            return _StubStdio()

        monkeypatch.setattr("mcp.server.stdio.stdio_server", _stub_stdio_factory)

        # Stub ProjectSession.from_project so run_server doesn't build a real
        # adapter registry for the injected project.
        class _StubProject:
            project = local_project(tmp_path)
            adapter_registry = object()

            def refresh(self) -> None: ...

            def close(self) -> None: ...

            def __enter__(self) -> "_StubProject":
                return self

            def __exit__(self, *_: object) -> None:
                self.close()

        def _from_project_stub(cls: type, *a: object, **kw: object) -> object:
            return _StubProject()

        monkeypatch.setattr(
            ProjectSession, "from_project", classmethod(_from_project_stub)
        )

        from dbt_charts.ai.mcp import server as mcp_server

        class _SpyServer:
            def create_initialization_options(self) -> object:
                return object()

            async def run(self, *args: object, **kwargs: object) -> None:
                return None

        def _spy_create_server(context: DbtChartsAIContext | None = None) -> object:
            assert context is not None
            captured["server_port"] = context.server_port
            return _SpyServer()

        monkeypatch.setattr(mcp_server, "create_server", _spy_create_server)

        asyncio.run(mcp_server.run_server(local_project(tmp_path), None))

        assert captured["server_port"] == fixed_port


# ---------------------------------------------------------------------------
# Thin-shim regression anchors (formerly test_server_thin.py).
# ---------------------------------------------------------------------------


class TestIsDomainError:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"success": False, "errors": ["boom"]}, True),
            ({"error": "Unknown tool: foo"}, True),
            ({"success": False}, True),
            ({}, False),
            ({"success": True, "columns": ["x"], "data": []}, False),
            ({"success": True, "error": "warn"}, False),
            ({"error": ""}, False),
            ({"error": None}, False),
            # render_board's BoardRenderResult.status tri-state (no
            # "success" key at all — status is the sole verdict field).
            ({"status": "failed", "board_error": {"message": "boom"}}, True),
            ({"status": "ok", "data": "<svg/>"}, False),
            ({"status": "partial", "data": "<svg/>", "chart_errors": [{}]}, False),
        ],
    )
    def test_classifies_payload_shape(
        self, payload: dict[str, object], expected: bool
    ) -> None:
        assert _is_domain_error(payload) is expected


class TestThinShimLOCGuards:
    """Regression anchors: MCP surface stays thin after the Phase 0 collapse.

    LOC guards prevent server.py, tools/__init__.py, and tool_schemas.py from
    re-accumulating domain logic. Each limit has slack; crossing it flags scope creep.
    """

    @pytest.mark.parametrize(
        ("rel", "limit"),
        [
            # server.py limit raised 265→275 after adding dct://docs/reference,
            # then 275→290 after wrapping tool returns in CallToolResult with isError,
            # then 290→300 after adding list_diagnostic_codes + get_diagnostic_code MCP
            # tools (originally two warning-only tools, generalized to cover both
            # levels), then 300→302 after reading DCT_CACHE_PATH via
            # agent_api.open_cache (in-memory-default cache-path plumbing), then
            # 302→303 for the comment documenting why the reused session stays
            # uncached unless opted in, then 303→308 for routing dct://boards
            # through Project.
            ("ai/mcp/server.py", 308),
            # This guard flags scope creep; raise it only alongside real new
            # domain logic (most recently: resolve_render_format, the
            # include_raw_data->format mapping every dispatch site calls).
            ("ai/tools/__init__.py", 409),
            # This guard flags scope creep; raise it only alongside real new
            # domain logic (most recently: INCLUDE_RAW_DATA_PROPERTY and
            # RENDER_BOARD's own include_raw_data/no-format shape).
            ("ai/tool_schemas.py", 209),
        ],
        ids=["server.py", "tools/__init__.py", "tool_schemas.py"],
    )
    def test_module_loc_under_limit(self, rel: str, limit: int) -> None:
        actual = _loc(rel)
        assert actual <= limit, f"{rel} is {actual} lines — re-grew domain logic"

    def test_no_extra_modules_in_tools_package(self) -> None:
        """ai/tools/ should only have __init__.py — no domain module re-grew there."""
        tools_pkg = DBT_CHARTS_SRC / "ai/tools"
        extra = {f.name for f in tools_pkg.glob("*.py") if not f.name.startswith("_")}
        assert extra == set(), f"Domain modules re-grew in ai/tools/: {extra}"


class TestMCPWireSurface:
    """The MCP server exposes exactly the post-collapse tool set."""

    def test_mcp_surface_matches_all_tools(self, mcp_server) -> None:
        """MCP tool list must equal ALL_TOOLS — no hand-maintained divergence."""
        from dbt_charts.ai.tool_schemas import ALL_TOOLS

        assert _tool_names(mcp_server) == {t["name"] for t in ALL_TOOLS}

    @pytest.mark.parametrize(
        "removed_tool",
        # ask_sql: would leak an OPENAI_API_KEY requirement into MCP; the host
        # agent is already an LLM and can do question→SQL via schema + execute_query.
        ["view_dashboard", "save_dashboard", "review_dashboard", "ask_sql"],
        ids=["view_dashboard", "save_dashboard", "review_dashboard", "ask_sql"],
    )
    def test_removed_tool_absent_from_mcp_surface(
        self, mcp_server, removed_tool: str
    ) -> None:
        assert removed_tool not in _tool_names(mcp_server)

    def test_render_board_schema_includes_as_link(self, mcp_server) -> None:
        handler = mcp_server.request_handlers[ListToolsRequest]
        result = asyncio.run(handler(None))
        render_tool = next(t for t in result.root.tools if t.name == "render_board")
        assert "as_link" in render_tool.inputSchema.get("properties", {}), (
            "render_board schema must expose as_link (folded view_dashboard)"
        )

    def test_skills_verbs_on_mcp_surface(self, mcp_server) -> None:
        names = _tool_names(mcp_server)
        assert "list_skills" in names
        assert "get_skill" in names

    def test_move_and_delete_file_absent_from_mcp_surface(self, mcp_server) -> None:
        """move_file/delete_file are chat-only (FILE_TOOLS) for v1 — external
        assistants bring their own file tools, same precedent as read/write/
        edit/glob/grep (MCP exposure is a noted follow-up, D-10)."""
        names = _tool_names(mcp_server)
        assert "move_file" not in names
        assert "delete_file" not in names


class TestMCPInputSchemaShape:
    """Pin the wire-visible inputSchema property names for each MCP tool.

    Switching from hand-written JSON Schema to `model.model_json_schema(by_alias=True)`
    means downstream MCP clients see whatever Pydantic emits — a snapshot at the
    properties/required level catches accidental renames or property removals.
    Field-level Pydantic-isms (`title`, `default: null`, `anyOf`) are not pinned;
    the contract is the property *names* a host can pass in.
    """

    @pytest.fixture
    def schemas(self, mcp_server) -> dict[str, dict[str, Any]]:
        handler = mcp_server.request_handlers[ListToolsRequest]
        result = asyncio.run(handler(None))
        return {t.name: t.inputSchema for t in result.root.tools}

    @pytest.mark.parametrize(
        ("tool_name", "expected_props", "required_field"),
        [
            (
                "render_board",
                {
                    "path",
                    "yaml_content",
                    "chart",
                    "variables",
                    "include_raw_data",
                    "as_link",
                },
                None,
            ),
            (
                "execute_query",
                {
                    "sql",
                    "variables",
                    "source",
                    "limit",
                    "lenient_variables",
                    "description",
                },
                "sql",
            ),
            ("search_boards", {"query", "tags", "limit"}, "query"),
            ("describe_board", {"path"}, "path"),
            ("validate_board", {"path", "yaml_content"}, None),
        ],
        ids=[
            "render_board",
            "execute_query",
            "search_boards",
            "describe_board",
            "validate_board",
        ],
    )
    def test_tool_input_properties(
        self,
        schemas,
        tool_name: str,
        expected_props: set[str],
        required_field: str | None,
    ) -> None:
        props = set(schemas[tool_name].get("properties", {}).keys())
        assert props == expected_props, (
            f"{tool_name} inputSchema property drift: {props ^ expected_props}"
        )
        if required_field is not None:
            assert required_field in schemas[tool_name].get("required", []), (
                f"{tool_name} must keep {required_field} as required"
            )


# ---------------------------------------------------------------------------
# FR-004: Every _BASE_RESOURCES URI + every _docs_topic_resources() URI
#         + both ResourceTemplate URIs read at least once.
# FR-005 / FR-013: Unknown-URI error paths return schematic envelope.
# FR-015: parametrize over live tables so renames cause test failures.
# ---------------------------------------------------------------------------


class TestBaseResourceCoverage:
    """FR-004 — every _BASE_RESOURCES URI returns non-empty string content."""

    @pytest.mark.parametrize(
        ("uri", "mime"),
        [pytest.param(uri, mime, id=uri) for uri, mime, *_ in _BASE_RESOURCES],
    )
    def test_base_resource_returns_content(
        self, uri: str, mime: str, tmp_path: Path, make_context
    ) -> None:
        # make_context roots the session at tmp_path, so dct://boards
        # lists from there rather than falling back to cwd (which may contain
        # invalid YAML fixtures).
        ctx = make_context()
        result = _read_resource_content(uri, context=ctx)
        assert isinstance(result, str) and result
        if mime == "application/json":
            assert json.loads(result).get("error") is None, (
                f"{uri} returned an error envelope: {result[:200]}"
            )
        else:
            assert not result.startswith('{"error"'), (
                f"{uri} returned an error envelope: {result[:200]}"
            )

    def test_base_resources_include_dashboards_uri(self) -> None:
        """FR-015 guard: the canonical dashboards URI must be present in _BASE_RESOURCES."""
        uris = [uri for uri, *_ in _BASE_RESOURCES]
        assert "dct://boards" in uris


class TestDocsTopicResourceCoverage:
    """FR-004 — every _docs_topic_resources() URI reads successfully.

    FR-011: sort by URI so test IDs are deterministic across platforms.
    """

    @pytest.mark.parametrize(
        "uri",
        [
            pytest.param(uri, id=uri.removeprefix("dct://docs/"))
            for uri in sorted(uri for uri, *_ in _docs_topic_resources())
        ],
    )
    def test_docs_topic_returns_content(self, uri: str, make_context) -> None:
        ctx = make_context()
        result = _read_resource_content(uri, context=ctx)
        assert isinstance(result, str) and result
        # Must not be a JSON error envelope (mirroring TestResourceTemplates pattern).
        assert not result.startswith('{"error"'), (
            f"{uri} returned an error envelope: {result[:200]}"
        )

    def test_at_least_one_docs_topic_exists(self) -> None:
        """Guard: if DBT_CHARTS_SYNTAX.md has no H2s, the parametrize yields 0 cases."""
        assert len(_docs_topic_resources()) >= 1

    def test_advertised_uris_are_unique(self) -> None:
        """The topic index carries the generated references too — one URI, one entry."""
        uris = [uri for uri, *_ in _BASE_RESOURCES + _docs_topic_resources()]
        assert len(uris) == len(set(uris))

    def test_docs_all_resource_matches_the_docs_verb(self, make_context) -> None:
        from dbt_charts.agent_api.docs import docs as docs_verb

        topic = docs_verb(topic="all").topic
        assert topic is not None
        assert _read_resource_content("dct://docs/all", context=make_context()) == (
            topic.content
        )


class TestResourceTemplates:
    """FR-004 — both ResourceTemplate URIs are advertised and readable."""

    def test_list_resource_templates_returns_expected_uris(self, mcp_server) -> None:
        handler = mcp_server.request_handlers[ListResourceTemplatesRequest]
        result = asyncio.run(handler(None))
        templates = result.root.resourceTemplates
        uri_templates = {t.uriTemplate for t in templates}
        assert "dct://board/{path}" in uri_templates
        assert "dct://docs/{topic}" in uri_templates

    def test_dashboard_template_readable_with_fixture(self, tmp_path: Path) -> None:
        """dct://board/{path} resolves a real board from single-query-board."""
        dest = tmp_path / "sqf"
        shutil.copytree(_FIXTURE_SINGLE_QUERY_BOARD, dest)
        ctx = DbtChartsAIContext(
            project_session=ProjectSession.open(dest, read_only=False),
            dashboards_directory=dest,
        )
        result = _read_resource_content("dct://board/charts/board.yml", context=ctx)
        payload = json.loads(result)
        assert payload.get("success") is True

    def test_docs_template_readable_with_known_topic(self, make_context) -> None:
        """dct://docs/{topic} resolves a known topic slug."""
        ctx = make_context()
        result = _read_resource_content("dct://docs/board", context=ctx)
        assert isinstance(result, str) and result
        # Not JSON — the content itself is markdown, not an error envelope.
        assert not result.startswith('{"error"')


class TestUnknownResourceErrorEnvelope:
    """FR-005 / FR-013 — unknown URIs return error envelopes with schematic assertions.

    Fallback URIs (unknown scheme, unknown docs topic, unknown guide) return
    {"error": <str>}.  Dashboard URIs that resolve but fail return the structured
    Pydantic shape {"success": False, "errors": [...]}.
    """

    @pytest.mark.parametrize(
        "uri",
        [
            pytest.param("dct://nonsense", id="unknown_scheme"),
            pytest.param("dct://docs/unknown-topic-xyz", id="unknown_docs_topic"),
            pytest.param("dct://guide/unknown-guide-xyz", id="unknown_guide"),
        ],
    )
    def test_fallback_uri_returns_error_string_envelope(
        self, uri: str, tmp_path: Path, make_context
    ) -> None:
        ctx = make_context(dashboards_directory=tmp_path)
        raw = _read_resource_content(uri, context=ctx)
        envelope = json.loads(raw)
        assert isinstance(envelope.get("error"), str) and envelope["error"], (
            f'Expected {{"error": <non-empty str>}} for {uri}, got: {envelope}'
        )

    def test_nonexistent_dashboard_returns_structured_failure(
        self, tmp_path: Path, make_context
    ) -> None:
        """dct://board/{path} failure → success=False + non-empty errors list."""
        ctx = make_context(dashboards_directory=tmp_path)
        raw = _read_resource_content("dct://board/nonexistent.yml", context=ctx)
        payload = json.loads(raw)
        assert payload.get("success") is False
        assert isinstance(payload.get("errors"), list) and payload["errors"]


class TestRunServerOwnsProjectSessionWithRefresh:
    """run_server constructs one ProjectSession at startup; tool dispatch
    calls project_session.refresh() before invoking the handler (per-call
    refresh policy for the MCP surface)."""

    def test_run_server_uses_the_injected_project_exactly_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """One session, built from the *injected* project — not a second one.

        `run_server` takes the project from its composition root (`dct mcp
        serve`), so it must reach `from_project`, never `open(project_dir)`:
        this surface consumes a Project, it does not construct FilesystemProject
        (tach rule on `dbt_charts.ai`).
        """
        from dbt_charts.ai.mcp import server as mcp_server

        sessions: list[object] = []

        class _FakeProject:
            project = local_project(tmp_path)
            adapter_registry = object()

            def refresh(self) -> None: ...

            def close(self) -> None: ...

            def __enter__(self) -> "_FakeProject":
                return self

            def __exit__(self, *_: object) -> None:
                self.close()

        def _from_project_fake(
            cls: type, project: object, **kw: object
        ) -> _FakeProject:
            sessions.append(project)
            return _FakeProject()

        monkeypatch.setattr(
            ProjectSession,
            "from_project",
            classmethod(_from_project_fake),
        )
        # No chdir: run_server must not read cwd — it is handed the project.

        # Stub embedded HTTP + stdio so run_server returns immediately.
        class _StubServer:
            should_exit = False

            async def serve(self) -> None:
                return None

        def _build_stub_server(*a: object, **kw: object) -> tuple[object, int]:
            return _StubServer(), 19000

        monkeypatch.setattr(
            "dbt_charts.core.serve.embedded.build_embedded_server",
            _build_stub_server,
        )

        class _StubStdio:
            async def __aenter__(self) -> tuple[object, object]:
                return object(), object()

            async def __aexit__(self, *_: object) -> None:
                return None

        def _stub_stdio_factory() -> object:
            return _StubStdio()

        monkeypatch.setattr("mcp.server.stdio.stdio_server", _stub_stdio_factory)

        class _SpyServer:
            def create_initialization_options(self) -> object:
                return object()

            async def run(self, *_: object, **__: object) -> None:
                return None

        def _create_spy_server(context: object) -> object:
            return _SpyServer()

        monkeypatch.setattr(mcp_server, "create_server", _create_spy_server)

        injected = local_project(tmp_path)
        asyncio.run(mcp_server.run_server(injected, None))

        assert sessions == [injected], (
            f"built {len(sessions)} session(s) — must be exactly one, from the "
            "injected project instance"
        )

    def test_tool_dispatch_calls_project_session_refresh_before_handler(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Each MCP tool call calls project_session.refresh() before the handler
        runs. Proves rotated credentials surface on the next call without
        restarting the server."""
        from dbt_charts.ai.context import DbtChartsAIContext
        from dbt_charts.ai.tools import dispatch_tool_call
        from dbt_charts.core.execute.adapters import build_adapter_registry

        refresh_calls: list[str] = []
        registry = build_adapter_registry(local_project(tmp_path), read_only=True)

        class _SpyProjectSession:
            project = local_project(tmp_path)
            adapter_registry = registry

            def refresh(self) -> None:
                refresh_calls.append("refresh")

        context = DbtChartsAIContext(project_session=_SpyProjectSession())  # type: ignore[arg-type]

        result = dispatch_tool_call("docs", {"topic": "board"}, context=context)

        assert refresh_calls == ["refresh"], (
            f"dispatch_tool_call must call project_session.refresh() exactly once "
            f"before the handler runs; got {refresh_calls!r}, result={result!r}"
        )
