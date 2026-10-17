"""Compile-time warnings for verbose variable authoring."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile, compile_file
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.text.case import inferred_display_name


def _warning_codes(yaml: str) -> set[str]:
    result = compile(yaml)
    assert result.success, result.errors
    return {w.code for w in result.warnings}


def test_inferred_display_name_matches_slug_rules() -> None:
    assert inferred_display_name("signup_source", case="title") == "Signup Source"
    assert inferred_display_name("revenue_usd", case="title") == "Revenue ($)"
    # Slug normalization invariant: underscore and hyphen variants produce the same output.
    assert inferred_display_name(
        "api_latency_ms", case="title"
    ) == inferred_display_name("api-latency-ms", case="title")
    assert "api" in inferred_display_name("api_latency_ms", case="title").lower()
    assert inferred_display_name("signup-source", case="title") == "Signup Source"


def test_redundant_variable_label_warns() -> None:
    yaml = """
variables:
  signup_source:
    label: Signup Source
    options:
      static: [organic, paid]
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - c
"""
    result = compile(yaml)

    assert result.success
    warnings = [w for w in result.warnings if w.code == "WARN-REDUNDANT-AUTHORED-LABEL"]
    assert len(warnings) == 1
    assert warnings[0].path == "variables.signup_source.label"
    assert "already inferred" in warnings[0].message


def test_custom_variable_label_does_not_warn() -> None:
    yaml = """
variables:
  signup_source:
    label: Acquisition channel
    options:
      static: [organic, paid]
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - c
"""
    assert "WARN-REDUNDANT-AUTHORED-LABEL" not in _warning_codes(yaml)


def test_redundant_variable_label_warns_inside_layout_variables_block() -> None:
    yaml = """
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - cols:
      - rows:
          - c
        variables:
          region:
            label: Region
            options:
              static: [North, South]
"""
    result = compile(yaml)

    assert result.success
    warnings = [w for w in result.warnings if w.code == "WARN-REDUNDANT-AUTHORED-LABEL"]
    assert len(warnings) == 1
    assert warnings[0].path == "rows.0.cols.0.variables.region.label"


def test_explicit_variable_defaults_warn_only_when_authored() -> None:
    yaml = """
variables:
  region:
    input: auto
    required: false
    visible: true
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - c
"""
    result = compile(yaml)

    assert result.success
    fields = {
        w.path for w in result.warnings if w.code == "WARN-REDUNDANT-AUTHORED-DEFAULT"
    }
    assert fields == {
        "variables.region.input",
        "variables.region.required",
        "variables.region.visible",
    }


def test_implicit_variable_defaults_do_not_warn() -> None:
    yaml = """
variables:
  region:
    notes: Region filter
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - c
"""
    assert "WARN-REDUNDANT-AUTHORED-DEFAULT" not in _warning_codes(yaml)


def test_allow_null_is_rejected() -> None:
    result = compile(
        """
variables:
  region:
    allow_null: true
queries:
  q:
    type: values
    rows:
      - {x: 1}
charts:
  c:
    query: q
    type: kpi
    value: x
rows:
  - c
"""
    )

    assert not result.success
    assert result.errors
    assert "allow_null" in result.errors[0].message


def test_redundant_label_check_reads_the_language_inherited_from_meta(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """`Ø days` is what `avg_days` infers in German only."""
    reset_config()
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/meta.yml").write_text(
        "regional:\n  language: de\n", encoding="utf-8"
    )
    (tmp_path / "charts/board.yaml").write_text(
        "variables:\n  avg_days:\n    label: Ø days\n    options:\n"
        "      static: [a, b]\nrows:\n  - text: hi\n",
        encoding="utf-8",
    )
    project = local_project(tmp_path)
    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    assert result.success, result.errors
    assert [
        w.path for w in result.warnings if w.code == "WARN-REDUNDANT-AUTHORED-LABEL"
    ] == ["variables.avg_days.label"]
    reset_config()
