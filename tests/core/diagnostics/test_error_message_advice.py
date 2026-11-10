"""Error and warning text gives advice that works and lives where agents read it.

`Diagnostic` serializes message, fix and hint; a code's `doc` is never sent. So a
next step must be in the message or `fix`, and every file, key and flag a fix
names must exist.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.errors import CompilationError, ParseError
from dbt_charts.core.compile.resolve.chart.channel import _validate_channel_field
from dbt_charts.core.compile.resolve.style.tokens import _stops
from dbt_charts.core.diagnostics import build_diagnostic
from dbt_charts.core.diagnostics.codes_compile import WARN_HTML_POLICY_CAPPED
from dbt_charts.core.diagnostics.codes_render import (
    ERR_ENGINE_REFUSED,
    ERR_LAYERED_NO_PALETTE,
    WARN_PALETTE_UNSUPPORTED,
)
from dbt_charts.core.diagnostics.registry import REGISTRY

from ..._paths import DBT_CHARTS_PKG_DIR

SRC = DBT_CHARTS_PKG_DIR

_BAR = """
queries:
  q: {type: values, columns: [month, revenue], values: [[Jan, 100]]}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      color:
        static: "#fff"
rows: [revenue]
"""


def test_literal_color_advice_is_the_shape_that_compiles() -> None:
    with pytest.raises(ValueError, match="style") as caught:
        _validate_channel_field("color", "#fff", {"month"})
    advice = str(caught.value)
    assert "static" in advice
    assert "value:" not in advice
    assert compile(_BAR).success


def test_unknown_palette_name_stays_palette_unknown() -> None:
    with pytest.raises(CompilationError) as caught:
        _stops("nope-zz", "nope-zz", "style.palette", {})
    assert caught.value.code.code == "ERR-PALETTE-UNKNOWN"


def test_palette_overrequest_surfaces_the_resolver_message() -> None:
    with pytest.raises(CompilationError) as caught:
        _stops("vivid-10:14", "vivid-10:14", "style.palette", {})
    assert caught.value.code.code == "ERR-PALETTE-UNUSABLE"
    assert "10 stops" in str(caught.value)
    assert "cannot return 14" in str(caught.value)


def test_engine_refused_says_retrying_the_same_yaml_fails() -> None:
    err = ParseError.from_code(ERR_ENGINE_REFUSED, message="stacked foo")
    diagnostic = build_diagnostic(err)
    assert "retrying the same YAML fails the same way" in diagnostic.message
    assert diagnostic.message.endswith("stacked foo")
    assert "same YAML" in diagnostic.message
    assert diagnostic.fix


_FORMAT_BOARD = """
queries:
  q: {type: values, columns: [month, revenue], values: [[Jan, 100]]}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_y:
        labels:
          format: "%s"
rows: [revenue]
"""


@pytest.mark.parametrize("spec", ["0.0a", ".2x"])
def test_format_spec_the_engine_cannot_draw_is_a_format_error(spec: str) -> None:
    result = compile(_FORMAT_BOARD % spec)
    assert [e.code for e in result.errors] == ["ERR-FORMAT-INVALID"]
    assert spec in result.errors[0].message
    assert "axis_y.labels.format" in result.errors[0].message


def test_engine_implemented_format_spec_compiles() -> None:
    assert compile(_FORMAT_BOARD % ",.0f").success


@pytest.mark.parametrize(
    "code",
    [
        "ERR-WAREHOUSE-CONNECTION",
        "ERR-ENGINE-REFUSED",
        "ERR-WAREHOUSE-RESULT-WARNING",
        "ERR-RESULT-COLUMN-TYPE",
        "ERR-RESULT-NO-TYPE-READER",
        "ERR-DBT-PROFILE-UNRESOLVED",
        "ERR-LAYERED-NO-PALETTE",
    ],
)
def test_next_step_is_in_fix_not_only_doc(code: str) -> None:
    assert REGISTRY.get(code).fix_template


def test_layered_no_palette_names_chart_and_palette_key() -> None:
    err = CompilationError.from_code(ERR_LAYERED_NO_PALETTE, chart_id="revenue")
    diagnostic = build_diagnostic(err)
    assert "revenue" in diagnostic.message
    assert diagnostic.fix is not None
    assert "style.palette" in diagnostic.fix


def test_html_policy_fix_names_the_real_config_key() -> None:
    fix = WARN_HTML_POLICY_CAPPED.fix_template
    assert "markdown.html_policy_ceiling" not in fix
    assert "html_policy_ceiling" in fix


def test_palette_fixes_do_not_link_a_missing_guide() -> None:
    assert (
        "docs/guides/palette-resolver.md" not in WARN_PALETTE_UNSUPPORTED.fix_template
    )
    palette_py = (SRC / "core/compile/resolve/style/palette.py").read_text()
    assert "docs/guides/palette-resolver.md" not in palette_py


def test_no_message_sends_users_to_repo_tests_or_a_nonexistent_flag() -> None:
    loader = (SRC / "core/execute/backends/dbt_macro_loader.py").read_text()
    assert "test_dbt_macro_loader.py" not in loader
    assert (
        "--model" not in (SRC / "ai/llm.py").read_text().split("def create_client")[1]
    )
