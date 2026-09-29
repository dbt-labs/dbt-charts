"""`compile_editor_buffer` on files that are not boards: dbt_charts.yml and
private partials validate the live buffer against their own schema."""

from __future__ import annotations

from pathlib import Path

from dbt_charts.agent_api import compile_editor_buffer
from dbt_charts.core.diagnostics import Diagnostic


def _diagnostics(buffer: str, file: Path) -> list[Diagnostic]:
    out = compile_editor_buffer(buffer, file)
    assert out.config_error is None
    return [*out.result.errors, *out.result.warnings]


def test_dbt_charts_yml_opened_directly_does_not_validate_as_a_board(
    tmp_path: Path,
) -> None:
    """Opening dbt_charts.yml itself must not compile it as a board: its
    own top-level keys (`sources:`, `execution:`) are not board fields."""
    source = "sources:\n  local:\n    type: duckdb\n    path: repro.duckdb\nexecution:\n  max_workers: 4\n"
    config = tmp_path / "dbt_charts.yml"
    config.write_text(source)

    diagnostics = _diagnostics(source, config)

    assert diagnostics == []


def test_private_partial_opened_directly_does_not_validate_as_a_board(
    tmp_path: Path,
) -> None:
    """A `_`-prefixed partial holding only `queries:` must not be compiled
    as a standalone board: it has no layout and is not meant to."""
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    source = "queries:\n  revenue_by_month:\n    columns: [month, revenue]\n    values:\n      - [Jan, 100]\n"
    partial = tmp_path / "charts" / "_shared.yml"
    partial.write_text(source)

    diagnostics = _diagnostics(source, partial)

    assert diagnostics == []


def test_dbt_charts_yml_editor_buffer_error_is_squiggled_even_though_disk_is_clean(
    tmp_path: Path,
) -> None:
    """The LSP compiles the unsaved buffer, not the file on disk: an error
    just typed into dbt_charts.yml must show up before the file is saved."""
    config = tmp_path / "dbt_charts.yml"
    config.write_text("execution:\n  max_workers: 4\n")
    buffer = "not_a_real_key: 123\n"

    diagnostics = _diagnostics(buffer, config)

    assert [d.code for d in diagnostics] == ["ERR-PROJECT-CONFIG-SCHEMA"]


def test_dbt_charts_yml_editor_buffer_fix_clears_before_save(tmp_path: Path) -> None:
    """The inverse: disk holds a broken config, but the buffer has already
    been fixed: the stale disk error must not survive into the buffer's
    diagnostics."""
    config = tmp_path / "dbt_charts.yml"
    config.write_text("not_a_real_key: 123\n")
    buffer = "execution:\n  max_workers: 4\n"

    diagnostics = _diagnostics(buffer, config)

    assert diagnostics == []


def test_private_partial_editor_buffer_error_is_squiggled_even_though_disk_is_clean(
    tmp_path: Path,
) -> None:
    """Same buffer-not-disk contract for a private partial."""
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    partial = tmp_path / "charts" / "_shared.yml"
    partial.write_text("queries:\n  q:\n    columns: [a]\n    values: [[1]]\n")
    buffer = "not_a_real_key: 123\n"

    diagnostics = _diagnostics(buffer, partial)

    assert [d.code for d in diagnostics] == ["ERR-META-SCHEMA"]


def test_private_partial_editor_buffer_fix_clears_before_save(tmp_path: Path) -> None:
    """Inverse: disk holds a broken partial, the buffer has already fixed it."""
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    partial = tmp_path / "charts" / "_shared.yml"
    partial.write_text("not_a_real_key: 123\n")
    buffer = "queries:\n  q:\n    columns: [a]\n    values: [[1]]\n"

    diagnostics = _diagnostics(buffer, partial)

    assert diagnostics == []


def test_new_unsaved_private_partial_does_not_fabricate_a_missing_file_error(
    tmp_path: Path,
) -> None:
    """A brand-new, never-saved `_`-prefixed partial must validate its buffer
    content, not fail because nothing exists at that path on disk yet."""
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    new_partial = tmp_path / "charts" / "_new.yml"
    buffer = "queries:\n  q:\n    columns: [a]\n    values: [[1]]\n"

    diagnostics = _diagnostics(buffer, new_partial)

    assert diagnostics == []


def test_dbt_charts_yml_config_error_not_preempted_by_sources_cache_guard(
    tmp_path: Path,
) -> None:
    """A dbt_charts.yml with an unrecognized top-level key AND a source-level
    fault (`sources.default`) must still report the top-level schema error -
    the config-file dispatch must run before the project.sources/cache probe
    that would otherwise swallow it into a generic config_error and fall
    through to compiling the config text as a board."""
    config = tmp_path / "dbt_charts.yml"
    # Disk content must actually make project.sources raise when evaluated -
    # an empty file does not exercise the guard this test is named for.
    config.write_text(
        "sources:\n  default: db\n  db:\n    type: duckdb\n    path: a.duckdb\n"
    )
    buffer = "sources:\n  default: db\ncharts:\n  c1:\n    type: table\n"

    diagnostics = _diagnostics(buffer, config)

    assert not any(d.code == "ERR-EXTRA-FIELD" for d in diagnostics)
    assert any(d.code == "ERR-PROJECT-CONFIG-SCHEMA" for d in diagnostics)


def test_falsy_non_mapping_dbt_charts_yml_buffer_raises_not_silently_accepted(
    tmp_path: Path,
) -> None:
    """`false` is a real non-mapping document, not an empty buffer: must
    not be excused to `{}` the way an empty buffer's `None` is."""
    config = tmp_path / "dbt_charts.yml"
    config.write_text("execution:\n  max_workers: 4\n")
    buffer = "false\n"

    diagnostics = _diagnostics(buffer, config)

    assert [d.code for d in diagnostics] == ["ERR-PROJECT-CONFIG-SCHEMA"]
