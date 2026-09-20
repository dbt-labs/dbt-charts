"""Tests for the CLI render command."""

from __future__ import annotations

import contextlib
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.cli.main import app
from dbt_charts.core.diagnostics import Diagnostic

runner = CliRunner()

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


class TestRenderCommandDiagnosticErrors:
    """render_command routes through agent_api and emits Rich panels on error."""

    def test_render_emits_error_code_on_failure(self, tmp_path: Path) -> None:
        """On compile error, stderr's Rich panel includes an ERR- error code."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        bad_board = tmp_path / "bad.yml"
        bad_board.write_text(
            "title: Bad\nqueries:\n  - list_not_dict\nrows:\n  - chart\n"
        )

        result = runner.invoke(
            app,
            [
                "render",
                str(bad_board),
                "--format",
                "json",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1
        # The Rich panel must carry an ERR-* code; a regression that strips the
        # code prefix would slip past a "stderr is non-empty" check.
        assert "ERR-" in result.stderr

    def test_render_command_raises_render_failed_not_systemexit(
        self, tmp_path: Path
    ) -> None:
        """render_command signals a render failure with RenderFailed, not sys.exit.

        The batch driver (render_commands) relies on this: a per-board failure is a
        typed exception it can report and continue past, not a process exit it must
        intercept as SystemExit and second-guess by exit code.
        """
        from dbt_charts.cli.commands.render import RenderFailed, render_command

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        bad_board = tmp_path / "bad.yml"
        bad_board.write_text(
            "title: Bad\nqueries:\n  - list_not_dict\nrows:\n  - chart\n"
        )
        with pytest.raises(RenderFailed) as exc_info:
            render_command(bad_board, format="json", project_dir=tmp_path)
        assert exc_info.value.errors, "RenderFailed must carry the diagnostics"

    def test_render_command_with_diagnostics_json_carries_real_errors_not_sentinel(
        self, tmp_path: Path
    ) -> None:
        """Regression: _emit_result raised RenderFailed([]) (empty sentinel) under
        diagnostics_json=True, so any caller inspecting exc.errors got an empty list —
        violating RenderFailed's own contract.  Now _emit_result always raises with the
        real payload; JSONL emission belongs to the driver."""
        from dbt_charts.cli.commands.render import RenderFailed, render_command

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        bad_board = tmp_path / "bad.yml"
        bad_board.write_text(
            "title: Bad\nqueries:\n  - list_not_dict\nrows:\n  - chart\n"
        )
        with pytest.raises(RenderFailed) as exc_info:
            render_command(
                bad_board, format="json", project_dir=tmp_path, diagnostics_json=True
            )
        assert exc_info.value.errors, (
            "RenderFailed must carry the diagnostics even under --diagnostics-json"
        )

    def test_render_diagnostics_json_flag_emits_jsonl_on_stderr_on_error(
        self, tmp_path: Path
    ) -> None:
        """dct render bad.yml --diagnostics-json outputs JSONL to stderr on error."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        bad_board = tmp_path / "bad.yml"
        bad_board.write_text(
            "title: Bad\nqueries:\n  - list_not_dict\nrows:\n  - chart\n"
        )

        result = runner.invoke(
            app,
            [
                "render",
                str(bad_board),
                "--format",
                "json",
                "--diagnostics-json",
                "--project-dir",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 1
        stderr = result.stderr or ""
        lines = [ln for ln in stderr.splitlines() if ln.strip()]
        assert lines, "Expected at least one JSONL diagnostic on stderr"
        first_err = json.loads(lines[0])
        assert "code" in first_err
        assert "message" in first_err

    def test_render_does_not_auto_open_file_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dct render writes exports but leaves browser preview to dct serve."""
        import webbrowser

        output = tmp_path / "renders" / "sales.html"
        output.parent.mkdir()
        output.write_text("<html></html>")

        opened: list[str] = []
        monkeypatch.setattr(webbrowser, "open", opened.append)

        with patch(
            "dbt_charts.cli.main.render_cmd.render_command",
            return_value=str(output),
        ):
            result = runner.invoke(
                app,
                ["render", "charts/sales.yml", "--format", "html"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert opened == []


def test_cli_render_command_uses_project_with_block(tmp_path: Path) -> None:
    """dct render constructs ProjectSession.from_project(...) as a context manager
    and forwards to project.render_board — never to setup_render_for_board."""
    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    board = tmp_path / "board.yml"
    board.write_text(
        "title: T\ncharts:\n  c: {type: text, content: hi}\nrows:\n  - c\n"
    )

    fake_project = MagicMock(name="ProjectSession")
    fake_project.project = FilesystemProject(tmp_path)
    fake_project.render_board.return_value = MagicMock(
        status="ok",
        data="<svg/>",
        warnings=[],
        suppressed_warnings=[],
        chart_errors=[],
        validation_errors=None,
        board_error=None,
    )

    @contextmanager
    def fake_from_project(project, *, cache, **_kwargs):  # type: ignore[no-untyped-def]
        yield fake_project

    with patch(
        "dbt_charts.agent_api.ProjectSession.from_project",
        side_effect=fake_from_project,
    ) as open_mock:
        result = runner.invoke(
            app,
            [
                "render",
                str(board),
                "--format",
                "svg",
                "--output",
                "-",
                "--project-dir",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert open_mock.call_count == 1, (
        "render_command must construct exactly one ProjectSession.from_project(...)"
    )
    fake_project.render_board.assert_called_once()


class TestDftRenderFormatValueClass:
    """dct render --format exercises terminal, json, svg, html output paths."""

    def test_render_terminal_format_exits_0(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")

    def test_render_json_format_exits_0(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "json",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")

    def test_render_data_format_prints_flat_queries_and_charts(
        self, tmp_path: Path
    ) -> None:
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "data",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        payload = json.loads(result.stdout)
        # Charts are keyed by slug and reference their query rather than
        # carrying rows; queries own the rows. A chart entry carries `query`,
        # or `error`, or neither (a chart with no query, such as a callout).
        assert payload["charts"]
        referenced = [c["query"] for c in payload["charts"].values() if "query" in c]
        assert referenced, "expected at least one chart to reference a query"
        for query_name in referenced:
            assert payload["queries"][query_name]["rows"] is not None

    def test_render_svg_format_writes_file(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        out = tmp_path / "out.svg"
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "svg",
                "--output",
                str(out),
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert out.exists()

    def test_render_dash_o_is_an_alias_for_output(self, tmp_path: Path) -> None:
        """`-o` is the short alias for `--output` (a papercut: agents keep
        typing `dct render -o <path>` and hitting "No such option")."""
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        out = tmp_path / "out.svg"
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "svg",
                "-o",
                str(out),
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert out.exists()

    def test_render_html_format_writes_file(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        out = tmp_path / "out.html"
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "html",
                "--output",
                str(out),
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert out.exists()

    @pytest.mark.parametrize(
        ("fmt", "ext"), [("json", "json"), ("text", "txt"), ("yaml", "yaml")]
    )
    def test_render_text_format_writes_to_output_file(
        self, tmp_path: Path, fmt: str, ext: str
    ) -> None:
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        out = tmp_path / f"out.{ext}"
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                fmt,
                "--output",
                str(out),
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert out.exists(), f"--output ignored for --format {fmt}"
        assert out.read_text().strip(), f"output file empty for --format {fmt}"
        # When --output is given, stdout must contain exactly the output path
        # (not the file content — that would be duplication).
        assert result.stdout.strip() == str(out), (
            f"--format {fmt}: expected output path on stdout, got {result.stdout!r}"
        )

    def test_render_output_creates_missing_parent_directories(
        self, tmp_path: Path
    ) -> None:
        """`--output` pointing at a path whose parent directory does not yet
        exist must create it, not fail with a bare `[Errno 2]`."""
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        out = tmp_path / "nested" / "dir" / "out.html"
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "html",
                "--output",
                str(out),
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert out.exists()

    def test_render_terminal_format_always_goes_to_stdout(self, tmp_path: Path) -> None:
        """--format terminal ignores --output (terminal is a stdout-only format)."""
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        out = tmp_path / "should-not-be-written.txt"
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--output",
                str(out),
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert not out.exists(), "terminal format should not honor --output"
        assert result.stdout.strip(), "terminal output should land on stdout"


class TestDftRenderVarAndNoCache:
    """dct render --var key=value (repeatable) and --no-cache are accepted."""

    def test_render_var_repeated_exits_0(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "board-with-variables", tmp_path / "project")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--var",
                "region=US",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")

    def test_render_var_without_equals_raises_bad_parameter(
        self, tmp_path: Path
    ) -> None:
        shutil.copytree(_FIXTURE_DIR / "board-with-variables", tmp_path / "project")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--var",
                "region",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code != 0
        combined = (result.output or "") + (result.stderr or "")
        assert "key=value" in combined
        assert "'region'" in combined

    def test_render_no_cache_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Clear DCT_CACHE_PATH so --no-cache doesn't conflict with the env-backed flag.
        monkeypatch.delenv("DCT_CACHE_PATH", raising=False)
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--no-cache",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")


class TestDftRenderCacheFlag:
    """dct render --cache <path>: default is in-memory; --cache persists to a file."""

    def test_default_creates_no_cache_file_on_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --cache, the query-result cache is in-memory — no .duckdb file appears."""
        # --cache is env-backed by DCT_CACHE_PATH; a dev with it exported (it's a
        # documented knob) would otherwise flip this default and fail spuriously.
        monkeypatch.delenv("DCT_CACHE_PATH", raising=False)
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert not list(tmp_path.rglob("*.duckdb")), (
            "default render must not create a cache file on disk"
        )

    def test_cache_flag_creates_and_reuses_file(self, tmp_path: Path) -> None:
        """--cache <path> creates the file, and a second invocation reuses it."""
        import duckdb

        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        cache_path = tmp_path / "cache.duckdb"
        assert not cache_path.exists()

        args = [
            "render",
            "charts/board.yml",
            "--format",
            "terminal",
            "--cache",
            str(cache_path),
            "--project-dir",
            str(tmp_path / "project"),
        ]
        first = runner.invoke(app, args)
        assert first.exit_code == 0, first.output + (first.stderr or "")
        assert cache_path.exists(), "--cache must create the file if absent"

        # The persistent cache now holds the query's result — proof the file is
        # a real, populated result cache, not an empty placeholder.
        conn = duckdb.connect(str(cache_path), read_only=True)
        try:
            row = conn.execute(
                "SELECT count(*) FROM information_schema.tables"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] > 0

        # Second invocation against the same file: reuses it (no new file, no error).
        second = runner.invoke(app, args)
        assert second.exit_code == 0, second.output + (second.stderr or "")
        assert cache_path.exists()

    def test_cache_and_no_cache_are_mutually_exclusive(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--cache",
                str(tmp_path / "cache.duckdb"),
                "--no-cache",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 2, result.output + (result.stderr or "")
        combined = (result.output or "") + (result.stderr or "")
        assert "mutually exclusive" in combined.lower()


class TestDftRenderDiagnosticsJsonMalformed:
    """dct render --diagnostics-json on a malformed board emits JSONL on stderr."""

    def test_malformed_yaml_diagnostics_jsonl_on_stderr(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "malformed-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "json",
                "--diagnostics-json",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 1
        stderr = result.stderr or ""
        lines = [ln for ln in stderr.splitlines() if ln.strip()]
        assert lines, "Expected at least one JSONL diagnostic on stderr"
        for ln in lines:
            err = json.loads(ln)
            parsed = Diagnostic.model_validate(err)
            assert parsed.code
            assert parsed.message


class TestDftRenderStdinHappy:
    """dct render - reads YAML from stdin and renders successfully."""

    def test_stdin_terminal_format_exits_0(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board_yaml = (
            _FIXTURE_DIR / "no-warehouse-board" / "charts" / "board.yml"
        ).read_text()
        result = runner.invoke(
            app,
            ["render", "-", "--format", "terminal", "--project-dir", str(tmp_path)],
            input=board_yaml,
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")

    def test_empty_stdin_exits_1(self) -> None:
        result = runner.invoke(
            app,
            ["render", "-", "--format", "terminal"],
            input="",
        )
        assert result.exit_code == 1


class TestDftRenderStdinDiagnosticsJson:
    """dct render - --diagnostics-json emits JSONL on stderr on error."""

    def test_stdin_diagnostics_json_emits_jsonl_on_error(self, tmp_path: Path) -> None:
        """Regression: _emit_result raised RenderFailed([]) (empty sentinel) when
        diagnostics_json=True, so render_command_from_yaml's except block called
        print_diagnostics on an empty list and emitted nothing.  Now _emit_result always
        raises RenderFailed with the real payload and render_command_from_yaml owns the
        JSONL emission for stdin mode."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        bad_yaml = "title: Bad\nqueries:\n  - list_not_dict\nrows:\n  - chart\n"

        result = runner.invoke(
            app,
            [
                "render",
                "-",
                "--format",
                "json",
                "--diagnostics-json",
                "--project-dir",
                str(tmp_path),
            ],
            input=bad_yaml,
        )

        assert result.exit_code == 1
        stderr = result.stderr or ""
        lines = [ln for ln in stderr.splitlines() if ln.strip()]
        assert lines, "Expected at least one JSONL diagnostic on stderr"
        for ln in lines:
            parsed = json.loads(ln)
            assert parsed.get("code"), f"JSONL line missing 'code': {ln}"
            assert parsed.get("message"), f"JSONL line missing 'message': {ln}"


class TestDftRenderMissingBoard:
    """dct render on a nonexistent board file exits 1."""

    def test_missing_board_exits_1(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "render",
                str(tmp_path / "nonexistent.yml"),
                "--format",
                "terminal",
            ],
        )
        assert result.exit_code == 1

    def test_missing_board_diagnostics_json_on_stderr(self, tmp_path: Path) -> None:
        """A missing board emits ERR-FILE-NOT-FOUND as JSONL on stderr under
        --diagnostics-json. Regression: read_board raises FileNotFoundError, which
        must not degrade to a plain-text stderr line."""
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")

        result = runner.invoke(
            app,
            [
                "render",
                "does-not-exist.yml",
                "--format",
                "json",
                "--diagnostics-json",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 1
        stderr = result.stderr or ""
        lines = [ln for ln in stderr.splitlines() if ln.strip()]
        assert lines, "Expected at least one JSONL diagnostic on stderr"
        codes = {Diagnostic.model_validate(json.loads(ln)).code for ln in lines}
        assert "ERR-FILE-NOT-FOUND" in codes


class TestDftRenderBoardsFirst:
    """dct render <bare-name> resolves under charts/ (boards-first retry)."""

    def test_bare_name_resolves_under_boards(self, tmp_path: Path) -> None:
        """A bare `overview.yml` that lives at charts/overview.yml renders — the
        boards-first retry that core did on main must survive the BoardFile move."""
        proj = tmp_path / "project"
        (proj / "charts").mkdir(parents=True)
        (proj / "dbt_charts.yml").write_text("# project marker\n")
        (proj / "charts" / "overview.yml").write_text(
            "title: Overview\n"
            "queries:\n  q:\n    columns: [value]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: value\n"
            "rows:\n  - c\n"
        )

        result = runner.invoke(
            app,
            ["render", "overview.yml", "--format", "json", "--project-dir", str(proj)],
        )

        assert result.exit_code == 0, result.output


class TestRenderCommandWalkRootWhenNoProjectDir:
    """render_command uses the cwd-walk-derived root when project_dir=None."""

    def test_render_command_uses_walk_root_when_no_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With project_dir=None, the injected project is rooted at the cwd-walked root."""
        import shutil
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        board_file = tmp_path / "project" / "charts" / "board.yml"

        # sentinel must be a parent of the board file — resolve_project_dir returns it,
        # then build_board_render_context computes scoped_path = board_file.relative_to(sentinel).
        sentinel = tmp_path / "project"

        import dbt_charts.cli.commands.render as render_mod

        # resolve_project_dir calls find_dct_root via a direct import in _project.py;
        # patch the name as bound in that module, not the source module attribute.
        def _find_dct_root(*_args: object, **_kwargs: object) -> object:
            return sentinel

        monkeypatch.setattr(
            "dbt_charts.cli._project.find_dct_root",
            _find_dct_root,
        )

        fake_project = MagicMock(name="ProjectSession")
        fake_project.render_board.return_value = MagicMock(
            status="ok",
            data="",
            warnings=[],
            suppressed_warnings=[],
            chart_errors=[],
            validation_errors=None,
            board_error=None,
        )
        captured_project_dir: list[Path] = []

        @contextmanager
        def fake_from_project(project: FilesystemProject, **kwargs: object):  # type: ignore[no-untyped-def]
            captured_project_dir.append(project.root)
            yield fake_project

        with (
            patch(
                "dbt_charts.agent_api.ProjectSession.from_project",
                side_effect=fake_from_project,
            ),
            contextlib.suppress(SystemExit),
        ):
            render_mod.render_command(
                board_file,
                format="terminal",
                project_dir=None,
            )

        assert captured_project_dir, "ProjectSession.from_project was not called"
        assert captured_project_dir[0] == sentinel, (
            f"Expected sentinel walk root but got {captured_project_dir[0]!r}; "
            "project_dir=None must use the cwd-walked root"
        )

    def test_render_command_from_yaml_uses_walk_root_when_no_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With project_dir=None, ProjectSession's adapter registry is built from the cwd-walked root."""
        board_yaml = (
            _FIXTURE_DIR / "no-warehouse-board" / "charts" / "board.yml"
        ).read_text()

        sentinel = tmp_path / "sentinel-walk-root"
        captured: list[FilesystemProject] = []

        import dbt_charts.agent_api.project_session as project_mod
        import dbt_charts.cli.commands.render as render_mod

        # resolve_project_dir calls find_dct_root via a direct import in _project.py;
        # patch the name as bound in that module, not the source module attribute.
        def _find_dct_root(*_args: object, **_kwargs: object) -> object:
            return sentinel

        monkeypatch.setattr(
            "dbt_charts.cli._project.find_dct_root",
            _find_dct_root,
        )
        original_build = project_mod.build_adapter_registry

        def spy_build(project: FilesystemProject, **kwargs: Any) -> Any:
            captured.append(project)
            return original_build(project, **kwargs)

        monkeypatch.setattr(project_mod, "build_adapter_registry", spy_build)

        with contextlib.suppress(SystemExit):
            render_mod.render_command_from_yaml(
                board_yaml,
                format="terminal",
                project_dir=None,
            )

        assert captured, "build_adapter_registry was not called"
        assert captured[0].root == sentinel, (
            f"Expected sentinel walk root but got {captured[0]!r}; "
            "project_dir=None must use the cwd-walked root"
        )


class TestDftRenderFormatChoice:
    """dct render --format is a Choice — invalid values are rejected at parse time."""

    def test_invalid_format_rejected_at_parse(self, tmp_path: Path) -> None:
        """Unknown --format value must exit non-zero before entering the command body."""
        result = runner.invoke(
            app,
            ["render", str(tmp_path / "board.yml"), "--format", "invalid"],
        )
        assert result.exit_code == 2
        combined = result.output + result.stderr
        assert "Invalid value" in combined or "must be one of" in combined.lower()

    def test_help_shows_choice_token(self) -> None:
        """dct render --help must show the Choice token listing valid formats."""
        result = runner.invoke(app, ["render", "--help"])
        assert result.exit_code == 0
        combined = result.output + result.stderr
        assert "[svg|html|png|pdf|terminal|json|text|yaml|data]" in combined


class TestDftRenderPrintsOutputPathToStdout:
    """Single-board render prints produced output path to stdout when writing a file."""

    def test_render_to_file_prints_path_to_stdout(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        out = tmp_path / "out.svg"
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "svg",
                "--output",
                str(out),
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert result.stdout.strip() == str(out)

    def test_render_stdout_output_no_path_printed(self, tmp_path: Path) -> None:
        """With -o -, SVG content goes to stdout; no file path line is appended."""
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "svg",
                "--output",
                "-",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0
        assert "<svg" in result.stdout

    def test_render_terminal_format_no_path_on_stdout(self, tmp_path: Path) -> None:
        """terminal format goes to stdout — no extra file-path line appended."""
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0
        # Confirm content is present; no line looks like a file path ending in .svg
        assert result.stdout.strip()
        for line in result.stdout.splitlines():
            assert not line.endswith(".svg"), (
                f"unexpected path line on stdout: {line!r}"
            )


class TestDftRenderMultiPath:
    """dct render accepts N board paths and renders each independently."""

    def test_multi_path_renders_both_boards(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", proj)
        (proj / "dbt_charts.yml").write_text("# project marker\n")
        shutil.copy(proj / "charts" / "board.yml", proj / "charts" / "board2.yml")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "charts/board2.yml",
                "--output",
                str(tmp_path / "{stem}.svg"),
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert (tmp_path / "board.svg").exists(), "board.svg not written"
        assert (tmp_path / "board2.svg").exists(), "board2.svg not written"

    def test_multi_path_prints_all_paths_to_stdout(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", proj)
        (proj / "dbt_charts.yml").write_text("# project marker\n")
        shutil.copy(proj / "charts" / "board.yml", proj / "charts" / "board2.yml")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "charts/board2.yml",
                "--output",
                str(tmp_path / "{stem}.svg"),
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        paths = [line for line in result.stdout.splitlines() if line]
        assert len(paths) == 2

    def test_multi_path_print0_uses_nul_delimiter(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", proj)
        (proj / "dbt_charts.yml").write_text("# project marker\n")
        shutil.copy(proj / "charts" / "board.yml", proj / "charts" / "board2.yml")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "charts/board2.yml",
                "--output",
                str(tmp_path / "{stem}.svg"),
                "--print0",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "\0" in result.stdout
        paths = [p for p in result.stdout.split("\0") if p]
        assert len(paths) == 2

    def test_multi_path_ambiguous_output_rejected(self, tmp_path: Path) -> None:
        """Flat --output path with multiple inputs must be rejected at parse time."""
        proj = tmp_path / "project"
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", proj)
        shutil.copy(proj / "charts" / "board.yml", proj / "charts" / "board2.yml")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "charts/board2.yml",
                "--output",
                "out.svg",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code != 0
        combined = (result.output or "") + (result.stderr or "")
        assert "template" in combined.lower() or "ambiguous" in combined.lower()

    def test_stdin_with_multiple_paths_rejected(self, tmp_path: Path) -> None:
        """`-` (stdin) is rejected when combined with other board paths."""
        proj = tmp_path / "project"
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", proj)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "-",
                "--project-dir",
                str(proj),
            ],
            input="title: T\n",
        )
        assert result.exit_code != 0

    def test_multi_path_duplicate_destination_rejected(self, tmp_path: Path) -> None:
        """Two boards with the same stem expanding to the same output path are rejected early."""
        proj = tmp_path / "project"
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", proj)
        # Create a subdirectory board with the same stem as board.yml
        subdir = proj / "sub"
        subdir.mkdir()
        shutil.copy(proj / "charts" / "board.yml", subdir / "board.yml")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                str(subdir / "board.yml"),
                "--output",
                str(tmp_path / "{stem}.svg"),
                "--format",
                "svg",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code != 0
        combined = (result.output or "") + (result.stderr or "")
        assert "duplicate" in combined.lower() or "both expand" in combined.lower()

    def test_multi_path_continues_on_error_exits_1(self, tmp_path: Path) -> None:
        """One bad board doesn't abort the batch; exit code 1 at end."""
        proj = tmp_path / "project"
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", proj)
        (proj / "dbt_charts.yml").write_text("# project marker\n")
        bad = proj / "bad.yml"
        bad.write_text("title: Bad\nqueries:\n  - list_not_dict\nrows:\n  - chart\n")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "bad.yml",
                "--output",
                str(tmp_path / "{stem}.svg"),
                "--format",
                "svg",
                "--project-dir",
                str(proj),
            ],
        )
        # Good board was still rendered
        assert (tmp_path / "board.svg").exists()
        # Overall exit is 1 because one board failed
        assert result.exit_code == 1

    def test_multi_path_continues_on_non_system_exit_exception(
        self, tmp_path: Path
    ) -> None:
        """A board that raises (not SystemExit) still lets the rest of the batch render."""
        proj = tmp_path / "project"
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", proj)
        (proj / "dbt_charts.yml").write_text("# project marker\n")
        # nonexistent.yml raises (FileNotFoundError or similar) — not SystemExit(1)
        result = runner.invoke(
            app,
            [
                "render",
                "nonexistent.yml",
                "charts/board.yml",
                "--output",
                str(tmp_path / "{stem}.svg"),
                "--format",
                "svg",
                "--project-dir",
                str(proj),
            ],
        )
        # The good board was still rendered despite the earlier failure
        assert (tmp_path / "board.svg").exists()
        # Error reported to stderr
        combined = (result.output or "") + (result.stderr or "")
        assert "nonexistent" in combined.lower()
        # Overall exit is non-zero
        assert result.exit_code != 0

    def test_multi_path_fail_fast_stops_on_first_error(self, tmp_path: Path) -> None:
        """--fail-fast stops after the first failure without processing remaining boards."""
        proj = tmp_path / "project"
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", proj)
        bad = proj / "bad.yml"
        bad.write_text("title: Bad\nqueries:\n  - list_not_dict\nrows:\n  - chart\n")
        result = runner.invoke(
            app,
            [
                "render",
                "bad.yml",
                "charts/board.yml",
                "--output",
                str(tmp_path / "{stem}.svg"),
                "--format",
                "svg",
                "--fail-fast",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 1
        # board.yml was not rendered (fail-fast stopped before it)
        assert not (tmp_path / "board.svg").exists()


class TestDftRenderFormatInference:
    """dct render infers --format from the --output extension when --format is omitted."""

    def test_format_inferred_from_html_extension(self, tmp_path: Path) -> None:
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        out = tmp_path / "out.html"
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--output",
                str(out),
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<html" in content.lower() or "<!doctype" in content.lower()

    def test_no_format_no_output_defaults_to_svg(self, tmp_path: Path) -> None:
        """Without --format or --output, format falls back to svg."""
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--project-dir",
                str(tmp_path / "project"),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        renders_dir = tmp_path / "project" / "renders"
        svgs = list(renders_dir.glob("*.svg"))
        assert svgs, "expected an .svg file in renders/"

    def test_render_no_project_dir_and_no_marker_in_cwd_raises_clean_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Project discovery is a cwd walk only — a marker in a subdirectory
        (e.g. tmp_path/project/dbt_charts.yml) does not count.

        Project discovery no longer anchors on the board path's location: with
        no --project-dir and no marker walking up from cwd, the command exits
        1 with a clean message instead of a traceback.
        """
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("server:\n  port: 9999\n")

        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["render", "--format", "terminal", "project/charts/board.yml"],
        )
        assert result.exit_code == 1
        combined = result.output + (result.stderr or "")
        assert "Traceback" not in combined
        assert "No dbt charts project found" in combined

    def test_render_multi_board_no_project_reports_clean_error_not_per_board(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Batch render must not swallow the discovery exit into a per-board
        'Error rendering <board>: 1'. The no-project failure is batch-fatal:
        the clean message surfaces once and the mis-attributed per-board line
        never appears.
        """
        shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
        (tmp_path / "project" / "dbt_charts.yml").write_text("server:\n  port: 9999\n")

        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "--format",
                "terminal",
                "project/charts/board.yml",
                "project/charts/board.yml",
            ],
        )
        assert result.exit_code == 1
        combined = result.output + (result.stderr or "")
        assert "No dbt charts project found" in combined
        assert "Error rendering" not in combined


class TestDftRenderDiagnosticsJson:
    """--diagnostics-json: JSONL to stderr on both success and failure paths."""

    def test_warnings_on_success_go_to_stderr_as_jsonl(self, tmp_path: Path) -> None:
        """A successful render with warnings emits them as JSONL on stderr."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        # WARN-UNREFERENCED-CHART fires when a chart is defined but not placed.
        board_yaml = (
            "title: Test\n"
            "queries:\n"
            "  kpis:\n"
            "    type: values\n"
            "    columns: [label, value]\n"
            "    values:\n"
            "      - [X, 1]\n"
            "charts:\n"
            "  used:\n"
            "    type: table\n"
            "    query: kpis\n"
            "  unused:\n"
            "    type: table\n"
            "    query: kpis\n"
            "rows:\n"
            "  - cols:\n"
            "      - used\n"
        )
        board_path = tmp_path / "board.yml"
        board_path.write_text(board_yaml)

        result = runner.invoke(
            app,
            [
                "render",
                str(board_path),
                "--format",
                "html",
                "--output",
                "-",
                "--project-dir",
                str(tmp_path),
                "--diagnostics-json",
            ],
        )

        assert result.exit_code == 0, result.stderr or result.output

        # stderr carries at least one JSONL diagnostic line.
        stderr = result.stderr or ""
        jsonl_lines = [ln for ln in stderr.splitlines() if ln.strip().startswith("{")]
        assert jsonl_lines, "Expected at least one JSONL line on stderr"
        parsed = [json.loads(ln) for ln in jsonl_lines]
        codes = {d["code"] for d in parsed}
        assert "WARN-UNREFERENCED-CHART" in codes

        # Rich warning prose must NOT appear in stderr alongside JSONL.
        assert "⚠" not in stderr  # ⚠ symbol

    def test_errors_on_failure_go_to_stderr_as_jsonl(self, tmp_path: Path) -> None:
        """A failed render with --diagnostics-json emits JSONL on stderr."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        (tmp_path / "bad.yml").write_text(
            "title: Bad\nqueries:\n  - list_not_dict\nrows:\n  - chart\n"
        )
        result = runner.invoke(
            app,
            [
                "render",
                str(tmp_path / "bad.yml"),
                "--format",
                "json",
                "--diagnostics-json",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1
        stderr = result.stderr or ""
        jsonl_lines = [ln for ln in stderr.splitlines() if ln.strip().startswith("{")]
        assert jsonl_lines, "Expected at least one JSONL line on stderr"
        for ln in jsonl_lines:
            d = json.loads(ln)
            assert "code" in d
            assert "message" in d


class TestRenderCommandInlinesFonts:
    """`dct render --format html` writes a file nobody serves, so it carries its fonts.

    The engine only inlines when the caller says the output is standalone. That flag
    lives in this command, and every other test of the behavior calls the engine
    directly — so without this one, deleting `standalone=True` from render_command
    would break every HTML export and no test would notice.
    """

    _BOARD = (
        "title: Export\n"
        "text: Prose with *emphasis*.\n"
        "queries:\n"
        "  q: {type: values, rows: [{n: 1}]}\n"
        "charts:\n"
        "  t: {query: q, type: table}\n"
        "rows: [t]\n"
    )

    def _render_html(self, tmp_path: Path) -> str:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(self._BOARD)
        out = tmp_path / "board.html"
        result = runner.invoke(
            app,
            [
                "render",
                str(board),
                "--format",
                "html",
                "--output",
                str(out),
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        return out.read_text(encoding="utf-8")

    def test_export_carries_font_bytes(self, tmp_path: Path) -> None:
        assert "data:font/woff2;base64," in self._render_html(tmp_path)

    def test_export_names_no_server_path(self, tmp_path: Path) -> None:
        """/static/fonts/ resolves only while a server is running."""
        assert "/static/fonts/" not in self._render_html(tmp_path)

    def test_stdin_export_also_carries_font_bytes(self, tmp_path: Path) -> None:
        """`render_command_from_yaml` carries its own `standalone=True` literal.

        A board piped in on stdin lands in a file the same way, so the two entry
        points have to agree — and the flag is written out separately in each.
        """
        from dbt_charts.cli.commands.render import render_command_from_yaml

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        out = tmp_path / "piped.html"
        render_command_from_yaml(
            yaml_content=self._BOARD,
            output=out,
            format="html",
            project_dir=tmp_path,
        )
        assert "data:font/woff2;base64," in out.read_text(encoding="utf-8")
