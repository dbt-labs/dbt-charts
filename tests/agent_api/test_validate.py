"""Tests for dbt_charts.agent_api.validate — fast YAML validation, no DB."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject

_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
_needs_non_root = pytest.mark.skipif(
    _AS_ROOT, reason="root bypasses file permission bits"
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_BOARD = """
queries:
  revenue:
    sql: SELECT month, SUM(revenue) FROM orders GROUP BY 1
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: month
    y: revenue
rows:
  - revenue_trend
"""

_MISSING_QUERY_BOARD = """
queries:
  revenue:
    sql: SELECT month, SUM(revenue) FROM orders GROUP BY 1
    source: analytics
charts:
  revenue_trend:
    query: nonexistent_query
    type: bar
    x: month
    y: revenue
rows:
  - revenue_trend
"""

_INVALID_YAML = "charts:\n  bad: [\nnot_closed"

_ORPHAN_CHART_BOARD = """
queries:
  revenue:
    sql: SELECT month, SUM(revenue) FROM orders GROUP BY 1
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: month
    y: revenue
  orphan:
    query: revenue
    type: line
    x: month
    y: revenue
rows:
  - revenue_trend
"""


def _write_board(tmp_path: Path, content: str, name: str = "board.yml") -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidateSuccessOnValidBoard:
    """validate() returns success=True and no errors for a valid board."""

    def test_validate_success_on_valid_board(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        board = _write_board(tmp_path, _VALID_BOARD)
        result = validate(board, project=local_project(tmp_path))

        assert result.success is True
        assert result.errors == []
        assert result.path == "board.yml"


class TestValidateResultPathType:
    """result.path is a project-relative POSIX str.

    Pydantic serializes a ``Path`` field via ``str()``, which emits
    OS-native separators on Windows (``WindowsPath.__str__`` rejoins with
    backslash) — invisible on macOS/Linux, where ``str(PosixPath)`` is
    already POSIX. Pin the property that actually prevents the regression
    on any host: the field's runtime type must be ``str``, never a
    ``PurePath`` subclass.
    """

    def test_validate_result_path_is_str_not_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        board = _write_board(tmp_path, _VALID_BOARD)
        result = validate(board, project=local_project(tmp_path))

        assert isinstance(result.path, str), (
            f"result.path should be str, got {type(result.path)}"
        )

    @pytest.mark.windows
    def test_validate_result_path_for_nested_board_has_no_os_native_separator(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A nested board's ``result.path`` is forward-slash joined on every host."""
        from dbt_charts.agent_api.validate import validate

        nested = tmp_path / "sub"
        nested.mkdir()
        _write_board(nested, _VALID_BOARD)

        result = validate(Path("sub/board.yml"), project=local_project(tmp_path))

        assert result.success is True, result.errors
        assert result.path == "sub/board.yml"


class TestValidateReturnsErrorsForUnknownChartQuery:
    """validate() surfaces an error when a chart references a missing query."""

    def test_validate_returns_errors_for_unknown_chart_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        board = _write_board(tmp_path, _MISSING_QUERY_BOARD)
        result = validate(board, project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) >= 1
        assert any("nonexistent_query" in e.message for e in result.errors)


class TestValidateReturnsErrorsForInvalidPydanticShape:
    """validate() surfaces errors when board YAML violates the Pydantic schema."""

    def test_validate_returns_errors_for_invalid_pydantic_shape(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        # rows must be a list of strings or dicts; an integer triggers Pydantic
        # validation that surfaces as a ParseError wrapping a ValidationError.
        bad_board = """
queries:
  rev:
    sql: SELECT 1
    source: analytics
charts:
  trend:
    query: rev
    type: bar
    x: a
    y: b
rows:
  - trend
  - 123
"""
        board = _write_board(tmp_path, bad_board)
        result = validate(board, project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) >= 1


class TestValidateReturnsErrorsForYamlParseError:
    """validate() surfaces an error when the YAML is unparseable."""

    def test_validate_returns_errors_for_yaml_parse_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        board = _write_board(tmp_path, _INVALID_YAML)
        result = validate(board, project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) >= 1
        assert (
            "YAML" in result.errors[0].message
            or "parse" in result.errors[0].message.lower()
        )


class TestValidateDoesNotOpenWarehouse:
    """validate() never calls build_adapter_registry (no DB contract)."""

    def test_validate_does_not_open_warehouse(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        board = _write_board(tmp_path, _VALID_BOARD)
        with patch(
            "dbt_charts.core.execute.adapters.build_adapter_registry"
        ) as mock_registry:
            validate(board, project=local_project(tmp_path))

        mock_registry.assert_not_called()


class TestValidatePathResolutionRejectsEscape:
    """validate() returns an error when path escapes project_dir."""

    def test_validate_path_resolution_rejects_escape(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        result = validate(Path("../../etc/passwd"), project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) >= 1
        assert any("outside project root" in e.message for e in result.errors)


class TestValidateWarningsPassThrough:
    """validate() populates warnings and still returns success=True."""

    def test_validate_warnings_pass_through(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        board = _write_board(tmp_path, _ORPHAN_CHART_BOARD)
        result = validate(board, project=local_project(tmp_path))

        assert result.success is True
        assert result.errors == []
        assert len(result.warnings) >= 1


class TestValidateCarriesErrorCodeWhenRegistryPresent:
    """validate() error entries carry domain-specific codes when registry has landed."""

    def test_validate_carries_error_code_when_registry_present(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        board = _write_board(tmp_path, _MISSING_QUERY_BOARD)
        result = validate(board, project=local_project(tmp_path))

        assert result.success is False
        assert any(
            e.code is not None and e.code != "ERR-INTERNAL" for e in result.errors
        ), "Expected at least one domain-specific error code"


class TestValidateReturnsDiagnostics:
    """validate() on a broken board returns Diagnostic objects."""

    def test_validate_returns_structured_errors(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate
        from dbt_charts.core.diagnostics import Diagnostic
        from dbt_charts.core.diagnostics.registry import REGISTRY

        board = _write_board(tmp_path, _MISSING_QUERY_BOARD)
        result = validate(board, project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) >= 1
        for err in result.errors:
            assert isinstance(err, Diagnostic)
            # Every error must have an ERR- prefixed code
            assert err.code.startswith("ERR-"), f"Expected ERR- code, got {err.code!r}"
            # Uncoded errors fall back to ERR-INTERNAL, not an exception
            registered = REGISTRY.get(err.code)
            assert registered.domain
            assert registered.doc_url
            assert err.message

    def test_filesystem_errors_use_err_unknown_internal(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Filesystem errors (path escape) also produce a Diagnostic with ERR- code."""
        from dbt_charts.agent_api.validate import validate
        from dbt_charts.core.diagnostics import Diagnostic

        result = validate(Path("../../etc/passwd"), project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) >= 1
        err = result.errors[0]
        assert isinstance(err, Diagnostic)
        assert err.code.startswith("ERR-")

    def test_missing_file_stamps_typed_compile_code(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """File-not-found must stamp ERR-FILE-NOT-FOUND, not ERR-INTERNAL.

        validate() is often the first verb an agent calls; it must give the same
        clean typed code as describe_board() and get_board() for the same condition.
        """
        from dbt_charts.agent_api.validate import validate

        result = validate(tmp_path / "nonexistent.yml", project=local_project(tmp_path))

        assert result.success is False
        err = result.errors[0]
        assert err.code == "ERR-FILE-NOT-FOUND", (
            f"expected ERR-FILE-NOT-FOUND, got {err.code!r}"
        )
        assert "nonexistent.yml" in err.message


class TestValidatePathWalksDirectory:
    """validate_paths() walks a directory, returning one ValidateResult per file."""

    def test_validate_path_walks_directory(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:

        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "charts").mkdir()
        _write_board(tmp_path / "charts", _VALID_BOARD, "good.yml")
        _write_board(tmp_path / "charts", _VALID_BOARD, "also_good.yaml")

        results = validate_paths([tmp_path / "charts"], project=local_project(tmp_path))
        assert len(results) == 2
        assert all(r.success for r in results)


class TestValidatePathSkipsUnderscoreFiles:
    """validate_paths() respects the partials convention (leading underscore)."""

    def test_validate_path_skips_underscore_files(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:

        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "charts").mkdir()
        _write_board(tmp_path / "charts", _MISSING_QUERY_BOARD, "_partial.yml")
        _write_board(tmp_path / "charts", _VALID_BOARD, "good.yml")

        results = validate_paths([tmp_path / "charts"], project=local_project(tmp_path))
        assert len(results) == 1
        assert PurePosixPath(results[0].path).name == "good.yml"


class TestValidatePathSkipsEjectedInspectTemplates:
    """validate_paths() skips dirs carrying .inspect-template-manifest.json."""

    def test_validate_path_skips_ejected_inspect_templates(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:

        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "charts").mkdir()
        _write_board(tmp_path / "charts", _VALID_BOARD, "good.yml")
        inspect_dir = tmp_path / "charts" / "inspect"
        inspect_dir.mkdir()
        (inspect_dir / "model.yml").write_text(
            "theme: {{ 'neon' if theme == 'neon' else 'stark' }}\n"
            'title: "Model: {{ model }}"\n'
        )
        (inspect_dir / ".inspect-template-manifest.json").write_text(
            '{"schema_version": 1, "templates": {"model": '
            '{"filename": "model.yml", "ejected_at": "2026-01-01T00:00:00+00:00",'
            ' "source_version": "0.0.0", "source_sha256": "deadbeef"}}}\n'
        )

        results = validate_paths([tmp_path / "charts"], project=local_project(tmp_path))
        assert len(results) == 1
        assert PurePosixPath(results[0].path).name == "good.yml"


class TestValidatePathSkipsScanDirs:
    """validate_paths() prunes generated dirs (node_modules, build, .git, ...)."""

    def test_validate_path_skips_scan_dirs(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:

        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "charts").mkdir()
        _write_board(tmp_path / "charts", _VALID_BOARD, "good.yml")
        trap = tmp_path / "charts" / "node_modules" / "dep"
        trap.mkdir(parents=True)
        _write_board(trap, _VALID_BOARD, "vendored.yml")

        results = validate_paths([tmp_path / "charts"], project=local_project(tmp_path))
        assert len(results) == 1
        assert PurePosixPath(results[0].path).name == "good.yml"


class TestValidatePathSingleFile:
    """validate_paths() routes a file argument straight through, no walk."""

    def test_validate_path_single_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:

        from dbt_charts.agent_api.validate import validate_paths

        board = _write_board(tmp_path, _VALID_BOARD)

        results = validate_paths([board], project=local_project(tmp_path))
        assert len(results) == 1
        assert results[0].success is True


class TestValidatePathEmptyDirectory:
    """validate_paths() returns a single error result for an empty directory."""

    def test_validate_path_empty_directory(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:

        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "empty").mkdir()

        results = validate_paths([tmp_path / "empty"], project=local_project(tmp_path))
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].errors


class TestValidatePathsDispatchDriftOnNoSuffixMissingPath:
    """Deliberate drift (owner-approved): pure-suffix (``is_yaml``) dispatch
    walks a no-suffix, nonexistent argv path as an empty directory instead of
    reporting ERR-FILE-NOT-FOUND (the old ``is_dir()`` dispatch's behavior).
    Both stay a single failure result — only the error differs."""

    def test_missing_no_suffix_path_becomes_empty_walk_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        results = validate_paths(
            [tmp_path / "missing"], project=local_project(tmp_path)
        )
        assert len(results) == 1
        assert results[0].success is False
        assert "No board files found" in results[0].errors[0].message


class TestValidatePathDefaultsToProjectBoardsDir:
    """validate_paths(None) defaults to <project_root>/boards."""

    def test_validate_path_defaults_to_project_boards_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "charts").mkdir()
        _write_board(tmp_path / "charts", _VALID_BOARD, "hello.yml")
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        monkeypatch.chdir(tmp_path)

        results = validate_paths(None, project=local_project(tmp_path))
        assert len(results) == 1
        assert PurePosixPath(results[0].path).name == "hello.yml"
        assert results[0].success is True


class TestValidatePathsConcatenatesArgvOrder:
    """validate_paths([a, b]) concatenates per-argv results, preserving order."""

    def test_validate_paths_concatenates_in_argv_order(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        a = _write_board(tmp_path, _VALID_BOARD, "a.yml")
        b = _write_board(tmp_path, _VALID_BOARD, "b.yml")

        results = validate_paths([a, b], project=local_project(tmp_path))
        assert [PurePosixPath(r.path).name for r in results] == ["a.yml", "b.yml"]
        assert all(r.success for r in results)

    def test_validate_paths_expands_dir_per_argv(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """argv [file, dir] yields file's result plus the directory's walk."""
        from dbt_charts.agent_api.validate import validate_paths

        loose = _write_board(tmp_path, _VALID_BOARD, "loose.yml")
        (tmp_path / "more").mkdir()
        _write_board(tmp_path / "more", _VALID_BOARD, "one.yml")
        _write_board(tmp_path / "more", _VALID_BOARD, "two.yml")

        results = validate_paths(
            [loose, tmp_path / "more"], project=local_project(tmp_path)
        )
        assert len(results) == 3
        assert PurePosixPath(results[0].path).name == "loose.yml"
        assert {PurePosixPath(r.path).name for r in results[1:]} == {
            "one.yml",
            "two.yml",
        }


class TestValidatePathSkipsMetaYaml:
    """validate_paths() skips meta.yml/meta.yaml in directory walk."""

    def test_dir_walk_skips_meta_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "meta.yml").write_text("source: analytics\n")
        _write_board(tmp_path / "charts", _VALID_BOARD, "good.yml")

        results = validate_paths([tmp_path / "charts"], project=local_project(tmp_path))
        assert len(results) == 1
        assert PurePosixPath(results[0].path).name == "good.yml"
        assert results[0].success is True

    def test_dir_walk_skips_meta_yml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "meta.yml").write_text("source: analytics\n")
        _write_board(tmp_path / "charts", _VALID_BOARD, "good.yml")

        results = validate_paths([tmp_path / "charts"], project=local_project(tmp_path))
        assert len(results) == 1
        assert PurePosixPath(results[0].path).name == "good.yml"


class TestValidateBrokenMetaYamlTransitive:
    """A board in a dir with a broken meta.yml still fails validate."""

    def test_board_with_broken_meta_yaml_fails(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "charts").mkdir()
        # meta.yml with an unknown top-level key — AuthoredBoard extra="forbid" rejects it
        (tmp_path / "charts" / "meta.yml").write_text("not_a_board_key: 99\n")
        _write_board(tmp_path / "charts", _VALID_BOARD, "board.yml")

        results = validate_paths([tmp_path / "charts"], project=local_project(tmp_path))
        assert len(results) == 1
        assert results[0].success is False


class TestValidateContentInMemory:
    """validate_content() validates unsaved YAML with no path on disk."""

    def test_validate_content_checks_model_columns(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The agent-facing arm (Playground, MCP validate_board) runs the
        model-column drift check too."""
        import json

        from dbt_charts.agent_api.validate import validate_content

        (tmp_path / "target").mkdir()
        (tmp_path / "target" / "manifest.json").write_text(
            json.dumps(
                {
                    "metadata": {"adapter_type": "postgres"},
                    "nodes": {
                        "model.p.orders": {
                            "resource_type": "model",
                            "name": "orders",
                            "schema": "main",
                            "raw_code": "SELECT month, revenue FROM raw",
                        }
                    },
                }
            )
        )
        board = _VALID_BOARD.replace(
            "SELECT month, SUM(revenue) FROM orders GROUP BY 1",
            "SELECT month, SUM(gross) AS revenue FROM {{ ref('orders') }} GROUP BY 1",
        )
        result = validate_content(board, project=local_project(tmp_path))

        assert result.success is False
        assert any(e.code == "ERR-DBT-MODEL-COLUMN-MISSING" for e in result.errors)

    def test_validate_content_success_on_valid_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_content

        result = validate_content(_VALID_BOARD, project=local_project(tmp_path))

        assert result.success is True
        assert result.errors == []

    def test_validate_content_returns_errors_for_unknown_chart_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_content

        result = validate_content(_MISSING_QUERY_BOARD, project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) >= 1
        assert any("nonexistent_query" in e.message for e in result.errors)

    def test_validate_content_does_not_touch_disk(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """No file is written or read — proves it acts on unsaved content."""
        from dbt_charts.agent_api.validate import validate_content

        result = validate_content(_VALID_BOARD, project=local_project(tmp_path))

        assert result.success is True
        assert list(tmp_path.iterdir()) == []


class TestValidateMetaYamlSingleFile:
    """validate() on a standalone meta.yml validates as BoardPatch, not full board."""

    def test_valid_meta_yaml_succeeds(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        meta = tmp_path / "meta.yml"
        meta.write_text("source: analytics\n")

        result = validate(meta, project=local_project(tmp_path))
        assert result.success is True
        assert result.errors == []

    def test_schema_typo_reports_schema_error_not_layout_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        meta = tmp_path / "meta.yml"
        meta.write_text("not_a_real_key: 123\n")

        result = validate(meta, project=local_project(tmp_path))
        assert result.success is False
        # Must NOT be a layout/missing-rows error; must be a schema error
        assert not any("layout" in e.message.lower() for e in result.errors)
        assert not any("rows" in e.message.lower() for e in result.errors)

    def test_schema_error_uses_dedicated_code_not_unknown(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        meta = tmp_path / "meta.yml"
        meta.write_text("not_a_real_key: 123\n")

        result = validate(meta, project=local_project(tmp_path))
        assert result.success is False
        assert result.errors, "expected at least one error"
        assert all(e.code == "ERR-META-SCHEMA" for e in result.errors), (
            f"expected ERR-META-SCHEMA, got {[e.code for e in result.errors]}"
        )

    def test_nested_schema_error_uses_dot_separated_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Pydantic loc with ≥2 segments renders as dot-separated, not unicode arrow."""
        from dbt_charts.agent_api.validate import validate

        meta = tmp_path / "meta.yml"
        meta.write_text("style:\n  bogus_key: bad\n")

        result = validate(meta, project=local_project(tmp_path))
        assert result.success is False
        error_msgs = " ".join(e.message for e in result.errors)
        assert "style.bogus_key" in error_msgs
        assert " → " not in error_msgs


class TestValidateProjectConfigSingleFile:
    """validate() on dbt_charts.yml validates against the project config schema,
    not the board schema."""

    def test_valid_project_config_succeeds(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        config = tmp_path / "dbt_charts.yml"
        config.write_text(
            "sources:\n"
            "  local:\n"
            "    type: duckdb\n"
            "    path: repro.duckdb\n"
            "execution:\n"
            "  max_workers: 4\n"
        )

        result = validate(config, project=local_project(tmp_path))
        assert result.success is True
        assert result.errors == []

    def test_duplicate_yaml_key_reports_diagnostic_not_traceback(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A duplicate top-level key is a YAML parse error: it must come back
        as a clean diagnostic, not an unhandled yaml.YAMLError traceback."""
        from dbt_charts.agent_api.validate import validate

        config = tmp_path / "dbt_charts.yml"
        config.write_text(
            "execution:\n  max_workers: 1\nexecution:\n  max_workers: 2\n"
        )

        result = validate(config, project=local_project(tmp_path))
        assert result.success is False
        assert result.errors
        assert all(e.code == "ERR-PROJECT-CONFIG-SCHEMA" for e in result.errors)

    def test_unset_env_var_in_source_reports_diagnostic_not_traceback(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The sanctioned in-git credential form (`{{ env_var(...) }}`) with
        the var unset raises ValueError out of the dbt Jinja renderer, before
        _validate_source_registry gets a chance to wrap it into a coded
        CompilationError: it must still come back as a diagnostic."""
        from dbt_charts.agent_api.validate import validate

        config = tmp_path / "dbt_charts.yml"
        config.write_text(
            "sources:\n"
            "  warehouse:\n"
            "    type: duckdb\n"
            "    path: \"{{ env_var('DCT_TEST_VALIDATE_UNSET_VAR') }}\"\n"
        )

        result = validate(config, project=local_project(tmp_path))
        assert result.success is False
        assert result.errors
        assert all(e.code == "ERR-PROJECT-CONFIG-SCHEMA" for e in result.errors)

    def test_yaml_anchor_cycle_reports_diagnostic_not_traceback(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A self-referential YAML anchor anywhere in dbt_charts.yml raises
        RecursionError out of Config.model_validate's own recursive walk -
        before `sources:` is ever extracted: and must still come back as a
        diagnostic, not an unhandled traceback."""
        from dbt_charts.agent_api.validate import validate

        config = tmp_path / "dbt_charts.yml"
        config.write_text(
            "sources:\n  cyclic: &cyclic\n    type: duckdb\n    self: *cyclic\n"
        )

        result = validate(config, project=local_project(tmp_path))
        assert result.success is False
        assert result.errors
        assert all(e.code == "ERR-PROJECT-CONFIG-SCHEMA" for e in result.errors)

    def test_falsy_non_mapping_dbt_charts_yml_raises_not_silently_accepted(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """`false` is a real non-mapping document, not an empty file: must
        not be excused to `{}` the way an empty file's `None` is."""
        from dbt_charts.agent_api.validate import validate

        config = tmp_path / "dbt_charts.yml"
        config.write_text("false\n")

        result = validate(config, project=local_project(tmp_path))
        assert result.success is False
        assert result.errors
        assert all(e.code == "ERR-PROJECT-CONFIG-SCHEMA" for e in result.errors)

    @_needs_non_root
    def test_unreadable_dbt_charts_yml_reports_diagnostic_not_traceback(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """An existing-but-unreadable dbt_charts.yml (permissions) is a
        diagnostic, mirroring load_meta_file's OSError handling: not an
        unhandled traceback. `_validate_resolved`'s `exists()` guard only
        covers the missing-file case."""
        from dbt_charts.agent_api.validate import validate

        config = tmp_path / "dbt_charts.yml"
        config.write_text("execution:\n  max_workers: 4\n")
        config.chmod(0o000)
        try:
            result = validate(config, project=local_project(tmp_path))
        finally:
            config.chmod(0o644)

        assert result.success is False
        assert result.errors
        assert all(e.code == "ERR-PROJECT-CONFIG-SCHEMA" for e in result.errors)

    def test_unknown_top_level_key_fails(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        config = tmp_path / "dbt_charts.yml"
        config.write_text("not_a_real_key: 123\n")

        result = validate(config, project=local_project(tmp_path))
        assert result.success is False
        assert result.errors, "expected at least one error"
        assert all(e.code == "ERR-PROJECT-CONFIG-SCHEMA" for e in result.errors), (
            f"expected ERR-PROJECT-CONFIG-SCHEMA, got {[e.code for e in result.errors]}"
        )
        # Must NOT be a board-schema error like `ERR-EXTRA-FIELD`.
        assert not any(e.code == "ERR-EXTRA-FIELD" for e in result.errors)

    def test_multiple_unknown_keys_produce_one_diagnostic_each_not_one_blob(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Two bad top-level keys must produce two field-attributed
        diagnostics via Config.model_validate's pydantic fan-out (through
        _pydantic_diagnostics): not one opaque `str(ValidationError)` blob
        with pydantic internals and an errors.pydantic.dev URL leaking into a
        user-facing message. Pins the pydantic handler runs, not the broader
        (TypeError, ValueError, RecursionError) catch above it: ValidationError subclasses
        ValueError, so ordering matters."""
        from dbt_charts.agent_api.validate import validate

        config = tmp_path / "dbt_charts.yml"
        config.write_text("not_a_real_key: 123\nanother_bogus: 456\n")

        result = validate(config, project=local_project(tmp_path))
        assert result.success is False
        assert len(result.errors) == 2, (
            f"expected 2 field-attributed diagnostics, got {len(result.errors)}: "
            f"{[e.message for e in result.errors]}"
        )
        messages = " ".join(e.message for e in result.errors)
        assert "not_a_real_key: Extra inputs are not permitted" in messages
        assert "another_bogus: Extra inputs are not permitted" in messages
        assert "errors.pydantic.dev" not in messages

    def test_style_key_not_permitted_in_project_config(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Presentation config belongs in charts/meta.yml, not dbt_charts.yml."""
        from dbt_charts.agent_api.validate import validate

        config = tmp_path / "dbt_charts.yml"
        config.write_text("style:\n  frame:\n    max_width: 1200\n")

        result = validate(config, project=local_project(tmp_path))
        assert result.success is False
        assert all(e.code == "ERR-PROJECT-CONFIG-SCHEMA" for e in result.errors)


class TestValidatePrivatePartialSingleFile:
    """validate() on a `_`-prefixed private partial validates as a fragment
    with no layout requirement: never compiled as a standalone board."""

    def test_queries_only_partial_succeeds(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        charts = tmp_path / "charts"
        charts.mkdir()
        partial = charts / "_shared.yml"
        partial.write_text(
            "queries:\n"
            "  revenue_by_month:\n"
            "    columns: [month, revenue]\n"
            "    values:\n"
            "      - [Jan, 100]\n"
            "      - [Feb, 140]\n"
        )

        result = validate(partial, project=local_project(tmp_path))
        assert result.success is True
        assert result.errors == []

    def test_unknown_key_in_partial_fails_with_schema_error_not_layout_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        charts = tmp_path / "charts"
        charts.mkdir()
        partial = charts / "_shared.yml"
        partial.write_text("not_a_real_key: 123\n")

        result = validate(partial, project=local_project(tmp_path))
        assert result.success is False
        assert not any("layout" in e.message.lower() for e in result.errors)
        assert result.errors
        assert all(e.code == "ERR-META-SCHEMA" for e in result.errors)

    def test_private_markdown_is_not_forced_through_the_yaml_patch_schema(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """`is_private_name` is basename-only, suffix-agnostic: a private
        markdown file (e.g. `charts/matrix/_generator.md`) must validate the
        same way its public counterpart does, not get routed into the
        YAML-only BoardPatch schema."""
        from dbt_charts.agent_api.validate import validate

        charts = tmp_path / "charts"
        charts.mkdir()
        content = "# Notes\n\nSome prose.\n"
        public = charts / "draft.md"
        private = charts / "_draft.md"
        public.write_text(content)
        private.write_text(content)

        project = local_project(tmp_path)
        public_result = validate(public, project=project)
        private_result = validate(private, project=project)

        assert not any(e.code == "ERR-META-SCHEMA" for e in private_result.errors)
        assert private_result.success == public_result.success
        assert [e.code for e in private_result.errors] == [
            e.code for e in public_result.errors
        ]


class TestValidateCredentialLiteralBecomesError:
    """validate() returns ERR-SOURCE-CREDENTIAL-LITERAL instead of raising.

    A board in a project whose dbt_charts.yml contains a literal credential
    previously raised CompilationError out of compile_file(); the catch in
    _validate_resolved turns it into a ValidateResult with success=False so
    callers get a clean diagnostic rather than an unhandled exception.
    """

    def test_literal_password_in_dbt_charts_yml_returns_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  db:\n    type: postgres\n    password: s3cr3t\n"
        )
        board = _write_board(tmp_path, _VALID_BOARD)

        result = validate(board, project=local_project(tmp_path))

        assert result.success is False
        assert any(e.code == "ERR-SOURCE-CREDENTIAL-LITERAL" for e in result.errors), (
            f"expected ERR-SOURCE-CREDENTIAL-LITERAL, got {[e.code for e in result.errors]}"
        )


# ---------------------------------------------------------------------------
# Structural SQL lint — WARN-MISSING-JOIN-PREDICATE wired into validate
# ---------------------------------------------------------------------------

_CARTESIAN_JOIN_BOARD = """\
queries:
  revenue:
    sql: |
      SELECT SUM(o.amount), SUM(oi.qty)
      FROM orders o, order_items oi
      GROUP BY o.id
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: o.id
    y: revenue
rows:
  - revenue_trend
"""

_CARTESIAN_JOIN_BOARD_IGNORE = """\
queries:
  revenue:
    sql: |
      -- dct:ignore WARN-MISSING-JOIN-PREDICATE WARN-FANOUT-RISK
      SELECT SUM(o.amount), SUM(oi.qty)
      FROM orders o, order_items oi
      GROUP BY o.id
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: o.id
    y: revenue
rows:
  - revenue_trend
"""


class TestValidatePathsEmitsStructuralLintWarning:
    """validate_paths() emits WARN-MISSING-JOIN-PREDICATE for a cartesian join board.

    No super-schema package, no relationship context.  The warning must carry
    a source range stamped into the board file — proving stamp_diagnostics ran
    on the new lint warnings after validate_compiled_queries.
    """

    def test_cartesian_join_produces_warn_missing_join_predicate(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        board = _write_board(tmp_path, _CARTESIAN_JOIN_BOARD)
        results = validate_paths([board], project=local_project(tmp_path))

        assert len(results) == 1
        result = results[0]
        assert result.success is True, result.errors
        codes = [w.code for w in result.warnings]
        assert "WARN-MISSING-JOIN-PREDICATE" in codes, f"warnings: {codes}"

    def test_structural_lint_warning_carries_source_range(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        board = _write_board(tmp_path, _CARTESIAN_JOIN_BOARD)
        results = validate_paths([board], project=local_project(tmp_path))

        result = results[0]
        lint_warnings = [
            w for w in result.warnings if w.code == "WARN-MISSING-JOIN-PREDICATE"
        ]
        assert lint_warnings, "expected at least one WARN-MISSING-JOIN-PREDICATE"
        assert lint_warnings[0].range is not None, (
            "lint warning must carry a source range (stamp_diagnostics must have run)"
        )
        # Range must land on or after the sql: key line (1-based line 3), not on
        # the revenue: mapping-key line (1-based line 2).  _CARTESIAN_JOIN_BOARD
        # has "queries:" at line 1, "  revenue:" at line 2, "    sql: |" at line 3.
        # The buggy path="queries.revenue" resolves to the container and collapses
        # to its key line (start_line=2).  The fixed path="queries.revenue.sql"
        # resolves to the sql: scalar node (start_line=3), staying in the SQL block.
        assert lint_warnings[0].range.start_line > 2, (
            f"range must land in the sql: block (start_line >= 3), not on the "
            f"revenue: key (start_line=2); got start_line={lint_warnings[0].range.start_line}"
        )

    def test_dct_ignore_suppresses_lint_warning(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """``-- dct:ignore`` in SQL suppresses the lint warning on the validate path."""
        from dbt_charts.agent_api.validate import validate_paths

        board = _write_board(tmp_path, _CARTESIAN_JOIN_BOARD_IGNORE)
        results = validate_paths([board], project=local_project(tmp_path))

        result = results[0]
        codes = [w.code for w in result.warnings]
        assert "WARN-MISSING-JOIN-PREDICATE" not in codes, (
            f"suppressed warning must not appear in active warnings: {codes}"
        )


class TestValidateContentEmitsStructuralLintWarning:
    """validate_content() emits WARN-MISSING-JOIN-PREDICATE for in-memory YAML.

    No file identity: _stamp_compile_result returns early when file is None, so
    range is None by design — lint findings are present but carry no position.
    """

    def test_cartesian_join_produces_warn_missing_join_predicate(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_content

        result = validate_content(
            _CARTESIAN_JOIN_BOARD, project=local_project(tmp_path)
        )

        assert result.success is True, result.errors
        codes = [w.code for w in result.warnings]
        assert "WARN-MISSING-JOIN-PREDICATE" in codes, f"warnings: {codes}"
        # range is None — no file identity on in-memory YAML
        assert all(
            w.range is None
            for w in result.warnings
            if w.code == "WARN-MISSING-JOIN-PREDICATE"
        )


# ---------------------------------------------------------------------------
# Jinja templates: must not produce WARN-PARSE-ERROR
# ---------------------------------------------------------------------------

# The {% set %} block declares lim in the Jinja template, so
# variable_dependencies is empty and the guard doesn't fire. validate_query is
# called with the raw Jinja SQL, sqlglot fails to parse it, and the
# WARN-PARSE-ERROR filter at compiler.py removes it before it reaches
# result.warnings. This tests the FILTER path, not the guard.
_JINJA_BOARD = """\
queries:
  revenue:
    sql: "{% set lim = 100 %}SELECT month, SUM(revenue) FROM orders GROUP BY 1 LIMIT {{ lim }}"
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: month
    y: revenue
rows:
  - revenue_trend
"""

# SQL with {{ ref('fct_orders') }} — dbt builtins are stripped by
# _DBT_BUILTIN_CALLS so variable_dependencies is empty and the guard doesn't
# fire. validate_query runs, sqlglot fails, WARN-PARSE-ERROR is filtered.
# This tests that dbt builtins are handled by the filter path.
_DBT_REF_BOARD = """\
queries:
  revenue:
    sql: "SELECT month, revenue FROM {{ ref('fct_orders') }}"
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: month
    y: revenue
rows:
  - revenue_trend
"""


class TestValidateJinjaQuery:
    """Jinja block SQL ({% set %}) must not produce WARN-PARSE-ERROR.

    {% set lim = 100 %} declares lim in the Jinja template so
    variable_dependencies is empty — the skip guard doesn't fire. validate_query
    is called with the raw Jinja SQL, sqlglot fails to parse it, and the
    WARN-PARSE-ERROR filter removes it. Tests the filter path.
    """

    def test_jinja_query_does_not_produce_parse_error_warning(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        board = _write_board(tmp_path, _JINJA_BOARD)
        results = validate_paths([board], project=local_project(tmp_path))

        result = results[0]
        assert result.success, f"board must compile; errors: {result.errors}"
        codes = [w.code for w in result.warnings]
        assert "WARN-PARSE-ERROR" not in codes, (
            f"Jinja query must not produce WARN-PARSE-ERROR; got: {codes}"
        )


class TestValidateDbtRefJinja:
    """{{ ref() }} Jinja must not produce WARN-PARSE-ERROR.

    dbt builtins (ref, source, var, env_var, config, is_incremental) are
    stripped by _DBT_BUILTIN_CALLS, so variable_dependencies is empty and the
    skip guard doesn't fire. validate_query runs, sqlglot fails to parse the
    dbt-Jinja SQL, and the WARN-PARSE-ERROR filter at compiler.py removes it.
    """

    def test_dbt_ref_jinja_does_not_produce_parse_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        board = _write_board(tmp_path, _DBT_REF_BOARD)
        results = validate_paths([board], project=local_project(tmp_path))

        result = results[0]
        assert result.success, f"board must compile; errors: {result.errors}"
        codes = [w.code for w in result.warnings]
        assert "WARN-PARSE-ERROR" not in codes, (
            f"{{ ref() }} Jinja must not produce WARN-PARSE-ERROR; got: {codes}"
        )


# ---------------------------------------------------------------------------
# Synthetic query names: must not leak into user-facing warnings
# ---------------------------------------------------------------------------

_INLINE_QUERY_BOARD = """\
charts:
  trend:
    query:
      sql: |
        SELECT SUM(o.amount), SUM(oi.qty)
        FROM orders o, order_items oi
        GROUP BY o.id
      source: analytics
    type: bar
    x: o.id
    y: revenue
rows:
  - trend
"""


class TestValidateSyntheticQueryNames:
    """validate_compiled_queries must skip synthetic registry names (_inline_query_*).

    An inline chart query compiles into an ``_inline_query_<chart>`` entry in the
    query registry.  That name has no ``queries.<name>`` YAML node, so a warning
    quoting it would be positionless and confusing.  The filter must discard it.
    """

    def test_inline_query_does_not_leak_synthetic_name_in_warning(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        board = _write_board(tmp_path, _INLINE_QUERY_BOARD)
        results = validate_paths([board], project=local_project(tmp_path))

        result = results[0]
        assert result.success, f"board must compile; errors: {result.errors}"
        for w in result.warnings:
            assert "_inline_query_" not in w.message, (
                f"synthetic name leaked into warning message: {w.message!r}"
            )


# ---------------------------------------------------------------------------
# {{ var }}-only Jinja (no {%): must not produce WARN-PARSE-ERROR
# ---------------------------------------------------------------------------

# SQL with {{ var }} in a C-style comment — sqlglot ignores the comment so
# the SQL is parseable and the CROSS JOIN would trigger WARN-MISSING-JOIN-PREDICATE
# if validate_query ran. Jinja sees {{ year_filter }} as undeclared, so
# variable_dependencies is non-empty and the guard fires, skipping validation.
# If the guard is removed, validate_query runs on parseable SQL and the
# structural warning appears — making this test non-vacuous w.r.t. the guard.
_VARIABLE_ONLY_JINJA_BOARD = """\
variables:
  year_filter:
    default: 2024
queries:
  revenue:
    sql: "SELECT a, b FROM orders o CROSS JOIN items i /* {{ year_filter }} */"
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: a
    y: b
rows:
  - revenue_trend
"""


class TestValidateVariableOnlyJinja:
    """{{ var }}-only Jinja guard: validate_compiled_queries skips the query.

    SQL with {{ authored_var }} has non-empty variable_dependencies, so the
    guard at compiler.py fires and validate_query is never called. The SQL
    has a CROSS JOIN that would produce WARN-MISSING-JOIN-PREDICATE if parsed
    — asserting that code is absent verifies the guard fired (not merely that
    WARN-PARSE-ERROR was filtered).

    Note: dbt builtins ({{ ref() }}, {{ source() }}) are stripped from
    variable_dependencies by _DBT_BUILTIN_CALLS, so they bypass the guard.
    Those are handled by TestValidateDbtRefJinja below.
    """

    def test_variable_jinja_guard_skips_structural_lint(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate_paths

        board = _write_board(tmp_path, _VARIABLE_ONLY_JINJA_BOARD)
        results = validate_paths([board], project=local_project(tmp_path))

        result = results[0]
        assert result.success, f"board must compile; errors: {result.errors}"
        codes = [w.code for w in result.warnings]
        assert "WARN-MISSING-JOIN-PREDICATE" not in codes, (
            "Jinja guard must skip structural lint on unrendered SQL; "
            f"WARN-MISSING-JOIN-PREDICATE must not appear; got: {codes}"
        )


# BQ-syntax SQL with a backtick identifier fails sqlglot generic parse
# (dialect=None) → without the WARN-PARSE-ERROR filter, a false positive fires.
_DBT_PROFILE_SOURCE_BOARD = """\
queries:
  sales:
    sql: "SELECT year FROM `project.dataset.orders` o CROSS JOIN items i ON o.id = i.order_id"
    source: warehouse
charts:
  sales_trend:
    query: sales
    type: bar
    x: year
    y: year
rows:
  - sales_trend
"""

# Same BQ-syntax SQL + CROSS JOIN, but on a bigquery source so dialect='bigquery'
# is used → sqlglot parses OK → structural lint runs → WARN-MISSING-JOIN-PREDICATE.
_BIGQUERY_SOURCE_BOARD = """\
queries:
  sales:
    sql: "SELECT year FROM `project.dataset.orders` o CROSS JOIN items i"
    source: bq
charts:
  sales_trend:
    query: sales
    type: bar
    x: year
    y: year
rows:
  - sales_trend
"""


class TestValidateDialectHandling:
    """Dialect-aware lint: dialect=None must not produce false WARN-PARSE-ERROR;
    dialect='bigquery' must let structural lint run on BQ-syntax SQL."""

    def test_dbt_profile_source_no_parse_error_warning(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """dbt_profile source (dialect=None) with BQ-syntax SQL must not emit
        WARN-PARSE-ERROR — _sql_parse_warnings already owns that code with the
        calibrated gate; validate_compiled_queries filters it from its results."""
        from dbt_charts.agent_api.validate import validate_paths

        # dbt_profile → dialect_for_source returns None (real dialect lives in
        # the profile and isn't knowable until execute time).
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  warehouse:\n    type: dbt_profile\n    profile: test\n"
        )
        board = _write_board(tmp_path, _DBT_PROFILE_SOURCE_BOARD)
        results = validate_paths([board], project=local_project(tmp_path))

        result = results[0]
        assert result.success is True, f"board must compile; errors: {result.errors}"
        codes = [w.code for w in result.warnings]
        assert "WARN-PARSE-ERROR" not in codes, (
            f"dbt_profile source must not produce WARN-PARSE-ERROR from "
            f"validate_compiled_queries; got: {codes}"
        )

    def test_bigquery_source_dialect_honored(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """BigQuery source resolves dialect='bigquery'; backtick SQL that fails
        sqlglot generic parse must succeed with the correct dialect so structural
        lint runs and WARN-MISSING-JOIN-PREDICATE fires for the cartesian join.

        Deleting the dialect= kwarg from validate_query in validate_compiled_queries
        causes this test to fail: sqlglot fails on backtick → ParseError → filtered →
        no structural lint → WARN-MISSING-JOIN-PREDICATE absent."""
        from dbt_charts.agent_api.validate import validate_paths

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  bq:\n    type: bigquery\n"
            "    project: test-project\n    dataset: test_dataset\n"
        )
        board = _write_board(tmp_path, _BIGQUERY_SOURCE_BOARD)
        results = validate_paths([board], project=local_project(tmp_path))

        result = results[0]
        assert result.success is True, f"board must compile; errors: {result.errors}"
        codes = [w.code for w in result.warnings]
        assert "WARN-MISSING-JOIN-PREDICATE" in codes, (
            f"bigquery dialect must allow structural lint to run; got: {codes}"
        )
