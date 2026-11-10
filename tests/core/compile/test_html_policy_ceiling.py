"""Tests for html_policy ceiling enforcement.

The ceiling is applied once in the normalizer (compile/normalize/dispatch.py) when the
Board model is built, so sizing and rendering both see the capped value.
Precedence: DCT_HTML_POLICY_CEILING env var > top-level html_policy_ceiling in
project config > board's own html_policy (board can never raise above the ceiling).

Covers:
- Env var ceiling caps a board requesting a higher tier.
- Project config ceiling caps a board requesting a higher tier.
- Env var ceiling overrides a permissive project config ceiling.
- Board at or below the ceiling passes through unchanged.
- resolve_html_policy_ceiling() follows the correct precedence order.
- WARN_HTML_POLICY_CAPPED is emitted when the cap lowers the authored value.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import reset_config


@pytest.fixture(autouse=True)
def _reset_config() -> None:
    """Config is a global singleton; reset after each test."""
    yield
    reset_config()


_BOARD_TRUSTED_RAW = """\
title: Test
html_policy: "trusted-raw"
text: |
  <div>hello</div>
"""

_BOARD_SAFE_SUBSET = """\
title: Test
html_policy: "safe-subset"
text: |
  <div>hello</div>
"""

_BOARD_NONE = """\
title: Test
html_policy: "none"
text: |
  <div>hello</div>
"""


class TestHtmlPolicyCeilingEnvVar:
    def test_ceiling_safe_subset_caps_trusted_raw(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DCT_HTML_POLICY_CEILING=safe-subset downgrades trusted-raw."""
        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "safe-subset")
        result = compile(_BOARD_TRUSTED_RAW)
        assert result.board is not None
        assert result.board.html_policy == "safe-subset"

    def test_ceiling_none_caps_trusted_raw(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DCT_HTML_POLICY_CEILING=none downgrades trusted-raw to none."""
        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "none")
        result = compile(_BOARD_TRUSTED_RAW)
        assert result.board is not None
        assert result.board.html_policy == "none"

    def test_ceiling_none_caps_safe_subset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DCT_HTML_POLICY_CEILING=none downgrades safe-subset to none."""
        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "none")
        result = compile(_BOARD_SAFE_SUBSET)
        assert result.board is not None
        assert result.board.html_policy == "none"

    def test_ceiling_trusted_raw_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DCT_HTML_POLICY_CEILING=trusted-raw does not downgrade trusted-raw."""
        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "trusted-raw")
        result = compile(_BOARD_TRUSTED_RAW)
        assert result.board is not None
        assert result.board.html_policy == "trusted-raw"

    def test_ceiling_safe_subset_allows_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A board at or below the ceiling is not changed."""
        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "safe-subset")
        result = compile(_BOARD_NONE)
        assert result.board is not None
        assert result.board.html_policy == "none"

    def test_invalid_ceiling_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unrecognized DCT_HTML_POLICY_CEILING value raises ValueError."""
        from dbt_charts.core.compile.config import resolve_html_policy_ceiling

        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "not-a-policy")
        with pytest.raises(ValueError, match="DCT_HTML_POLICY_CEILING"):
            resolve_html_policy_ceiling()

    def test_cap_emits_warn_html_policy_capped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ceiling lowers the authored value, WARN-HTML-POLICY-CAPPED is emitted."""
        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "safe-subset")
        result = compile(_BOARD_TRUSTED_RAW)
        assert result.board is not None
        codes = [w.code for w in result.warnings]
        assert "WARN-HTML-POLICY-CAPPED" in codes

    def test_no_cap_no_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the board is at or below the ceiling, no WARN-HTML-POLICY-CAPPED is emitted."""
        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "trusted-raw")
        result = compile(_BOARD_TRUSTED_RAW)
        codes = [w.code for w in result.warnings]
        assert "WARN-HTML-POLICY-CAPPED" not in codes


class TestHtmlPolicyCeilingProjectConfig:
    def test_project_config_ceiling_caps_board(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """html_policy_ceiling in project config caps a board requesting a higher tier.

        Patches resolve_html_policy_ceiling at its import site in normalize/dispatch.py
        to simulate a project config ceiling without constructing a full Config.
        """
        import dbt_charts.core.compile.normalize.dispatch as normalizer_mod
        from dbt_charts.core.compile.config import HtmlPolicyCeiling

        monkeypatch.delenv("DCT_HTML_POLICY_CEILING", raising=False)
        monkeypatch.setattr(
            normalizer_mod,
            "resolve_html_policy_ceiling",
            lambda: HtmlPolicyCeiling("safe-subset", "project config"),
        )

        result = compile(_BOARD_TRUSTED_RAW)
        assert result.board is not None
        assert result.board.html_policy == "safe-subset"


class TestResolveCeilingFunction:
    def test_no_env_var_returns_config_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without DCT_HTML_POLICY_CEILING, ceiling falls back to project config."""
        from dbt_charts.core.compile.config import resolve_html_policy_ceiling

        monkeypatch.delenv("DCT_HTML_POLICY_CEILING", raising=False)
        # Default config has trusted-raw (most permissive — no ceiling locally).
        ceiling = resolve_html_policy_ceiling()
        assert ceiling.value == "trusted-raw"
        assert ceiling.source == "project config"

    def test_env_var_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from dbt_charts.core.compile.config import resolve_html_policy_ceiling

        monkeypatch.setenv("DCT_HTML_POLICY_CEILING", "safe-subset")
        ceiling = resolve_html_policy_ceiling()
        assert ceiling.value == "safe-subset"
        assert ceiling.source == "DCT_HTML_POLICY_CEILING env var"
