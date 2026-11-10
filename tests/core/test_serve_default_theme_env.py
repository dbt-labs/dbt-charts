"""Tests for default-theme resolution.

After removing the global-mutable pattern:
- get_default_theme_name() reads DCT_DEFAULT_THEME at call time — no set_default_theme_name
- Serve startup validates DCT_DEFAULT_THEME early and raises DbtChartsError on bad values
- dbt_charts.yml: theme: is gone; project theme lives in charts/meta.yml: extends:
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import (
    SHIPPED_DEFAULT_THEME_NAME,
    get_default_theme_name,
    list_built_in_themes,
)
from dbt_charts.core.diagnostics.base import DbtChartsError


class TestGetDefaultThemeName:
    def test_reads_dct_default_theme_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DCT_DEFAULT_THEME", "neon")
        assert get_default_theme_name() == "neon"

    def test_returns_shipped_constant_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DCT_DEFAULT_THEME", raising=False)
        assert get_default_theme_name() == SHIPPED_DEFAULT_THEME_NAME

    def test_shipped_default_is_a_built_in_theme(self) -> None:
        assert SHIPPED_DEFAULT_THEME_NAME in list_built_in_themes()

    def test_empty_env_treated_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DCT_DEFAULT_THEME="" is unset, not an error — falls back to shipped.

        serve.py's startup validation and get_default_theme_name() must agree:
        an empty value means "use the shipped default", never "invalid theme".
        """
        monkeypatch.setenv("DCT_DEFAULT_THEME", "")
        assert get_default_theme_name() == SHIPPED_DEFAULT_THEME_NAME


class TestServeStartupThemeValidation:
    """Serve command validates DCT_DEFAULT_THEME before serving any request."""

    def test_invalid_dct_default_theme_not_in_built_in_list(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An invalid DCT_DEFAULT_THEME is not in list_built_in_themes()."""
        monkeypatch.setenv("DCT_DEFAULT_THEME", "nonexistent-theme-xyz")
        assert "nonexistent-theme-xyz" not in list_built_in_themes()

    def test_private_base_fragment_is_not_in_built_in_list(self) -> None:
        assert not [t for t in list_built_in_themes() if t.startswith("_")]

    def test_valid_dct_default_theme_passes_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Valid DCT_DEFAULT_THEME must be in built-in themes."""
        monkeypatch.setenv("DCT_DEFAULT_THEME", "neon")
        assert get_default_theme_name() == "neon"
        assert "neon" in list_built_in_themes()


class TestDbtChartsYmlThemeRemoved:
    """dbt_charts.yml: theme: is no longer accepted — raises pydantic ValidationError."""

    def test_theme_key_in_dbt_charts_yml_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from pydantic import ValidationError

        from dbt_charts.core.compile.config import load_config, reset_config

        (tmp_path / "dbt_charts.yml").write_text("theme: stark\n")
        monkeypatch.delenv("DCT_DEFAULT_THEME", raising=False)
        reset_config()
        try:
            with pytest.raises(ValidationError):
                load_config(local_project(tmp_path))
        finally:
            reset_config()


@pytest.mark.parametrize("bad", ["_base", "nonesuch"])
class TestInvalidDefaultThemeRefusedEverywhere:
    """The default theme is validated where it is resolved, so every surface refuses."""

    def test_resolver_raises(self, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
        monkeypatch.setenv("DCT_DEFAULT_THEME", bad)
        with pytest.raises(DbtChartsError) as exc:
            get_default_theme_name()
        assert exc.value.to_diagnostic().code == "ERR-INVALID-DEFAULT-THEME"
        assert "clarity" in str(exc.value)
        assert "_base" not in str(exc.value).split("Available built-in themes:")[1]

    def test_normalize_raises(self, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard
        from dbt_charts.core.compile.normalize.dispatch import normalize_board

        monkeypatch.setenv("DCT_DEFAULT_THEME", bad)
        with pytest.raises(DbtChartsError) as exc:
            normalize_board(AuthoredBoard(title="t", rows=[]))
        assert exc.value.to_diagnostic().code == "ERR-INVALID-DEFAULT-THEME"

    def test_validate_reports_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        bad: str,
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        (tmp_path / "board.yml").write_text(
            "queries:\n  q:\n    sql: SELECT 1 AS x\n    source: analytics\n"
            "charts:\n  c:\n    query: q\n    type: bar\n    x: x\n    y: x\n"
            "rows:\n  - c\n"
        )
        monkeypatch.setenv("DCT_DEFAULT_THEME", bad)
        result = validate(tmp_path / "board.yml", project=local_project(tmp_path))
        assert not result.success
        assert result.errors[0].code == "ERR-INVALID-DEFAULT-THEME"
