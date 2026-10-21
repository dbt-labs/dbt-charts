"""Guard the grouped Typer panels on `dct -h`.

Pins that every root command declares a `rich_help_panel` in the
allow-listed set, commands within each panel are alphabetically sorted, and
the three help triggers produce identical stdout -- all via Typer's
in-process CliRunner. The panel-order, recipe-block, and init-ordering pins
need Rich's real panel chrome (only rendered on a real PTY), so those three
live in tests/e2e/cli/test_root_help_panels.py instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

from dbt_charts.cli.main import PANEL_ORDER, app

runner = CliRunner()


@pytest.fixture(autouse=True)
def chdir_into_scaffolded_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Suppress the uninitialized-project banner so panel assertions are clean."""
    (tmp_path / "charts").mkdir()
    monkeypatch.chdir(tmp_path)


PANEL_LABELS = list(PANEL_ORDER)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _render_root_help() -> str:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    return _strip_ansi(result.output)


def test_every_visible_root_command_has_panel() -> None:
    """Every visible root command + sub-typer declares an allow-listed panel.

    Fails on a future command added without rich_help_panel — that's the
    drift guard.
    """
    allowed = set(PANEL_LABELS)

    for cmd in app.registered_commands:
        if getattr(cmd, "hidden", False):
            continue
        panel = cmd.rich_help_panel
        name = cmd.name or (cmd.callback.__name__ if cmd.callback else "<unnamed>")
        assert panel in allowed, (
            f"Root command {name!r} has rich_help_panel={panel!r}; "
            f"must be one of {sorted(allowed)}"
        )

    for group in app.registered_groups:
        if getattr(group, "hidden", False):
            continue
        panel = group.rich_help_panel
        name = group.name or "<unnamed>"
        assert panel in allowed, (
            f"Root sub-typer {name!r} has rich_help_panel={panel!r}; "
            f"must be one of {sorted(allowed)}"
        )


def test_bare_dash_h_and_long_help_are_identical() -> None:
    """`dct`, `dct -h`, `dct --help` produce identical stdout."""
    bare = runner.invoke(app, [])
    short = runner.invoke(app, ["-h"])
    long = runner.invoke(app, ["--help"])

    # `no_args_is_help=True` makes bare exit with the typer convention exit
    # code 2 (no command given); -h / --help exit 0. We only compare output.
    # Strip ANSI before substring checks — CI runs Rich in a more
    # color-aggressive terminal than local dev, and `Usage: dct` is
    # interleaved with SGR escapes there.
    bare_text = _strip_ansi(bare.output).rstrip()
    short_text = _strip_ansi(short.output).rstrip()
    long_text = _strip_ansi(long.output).rstrip()
    for trigger, text in (
        ("dct", bare_text),
        ("dct -h", short_text),
        ("dct --help", long_text),
    ):
        assert "Usage: dct" in text, f"{trigger}: {text!r}"
    assert bare_text == short_text == long_text


def _resolve_placeholder(value: object) -> object:
    """Unwrap a Typer DefaultPlaceholder to its underlying value."""
    if hasattr(value, "value"):
        return value.value  # type: ignore[union-attr]
    return value


def test_commands_alphabetical_within_panels() -> None:
    """Leaf commands and sub-typer groups are alphabetical *interleaved* within each panel.

    Typer's rich help iterates ``obj.list_commands(ctx)`` once and buckets each
    name into its rich_help_panel — so the per-panel order equals the order
    ``list_commands`` returns. The root group's ``list_commands`` override
    sorts the combined list, which interleaves sub-typer groups (e.g. ``init``)
    with leaf commands (e.g. ``doctor``, ``playground``, ``skills``).
    """
    from collections import defaultdict

    panel_rows: dict[str, list[str]] = defaultdict(list)
    for cmd in app.registered_commands:
        if _resolve_placeholder(getattr(cmd, "hidden", False)):
            continue
        panel = _resolve_placeholder(cmd.rich_help_panel)
        name = cmd.name or (cmd.callback.__name__ if cmd.callback else "<unnamed>")
        if panel in PANEL_LABELS:
            panel_rows[str(panel)].append(name)
    for group in app.registered_groups:
        if _resolve_placeholder(getattr(group, "hidden", False)):
            continue
        panel = _resolve_placeholder(group.rich_help_panel)
        name = group.name or "<unnamed>"
        if panel in PANEL_LABELS:
            panel_rows[str(panel)].append(name)

    click_root = typer.main.get_command(app)
    assert isinstance(click_root, TyperGroup), "Root Typer must be a TyperGroup"
    ctx = typer.Context(click_root)
    rendered_order = click_root.list_commands(ctx)

    for panel in PANEL_LABELS:
        # Reorder this panel's rows by the root group's list_commands order
        # — that's what typer.rich_utils.rich_format_help iterates.
        panel_set = set(panel_rows[panel])
        rows_in_render_order = [n for n in rendered_order if n in panel_set]
        assert rows_in_render_order == sorted(rows_in_render_order), (
            f"Panel {panel!r} rows are not alphabetical: {rows_in_render_order!r} "
            f"(expected {sorted(rows_in_render_order)!r})"
        )


def test_omitted_commands_absent() -> None:
    """`explain` / `new` removed; `inspect` hidden; `describe` listed."""
    output = _render_root_help()

    # Token-level absence using word boundaries (panel labels and command rows).
    assert not re.search(r"(^|\s)explain(\s|$)", output, flags=re.MULTILINE), (
        "`explain` should not appear in root help"
    )
    assert not re.search(r"(^|\s)new(\s|$)", output, flags=re.MULTILINE), (
        "`new` should not appear in root help"
    )
    # `inspect` is hidden via add_typer(..., hidden=True).
    assert not re.search(r"(^|\s)inspect(\s|$)", output, flags=re.MULTILINE), (
        "`inspect` should be hidden from root help"
    )

    # Visible commands under Dashboards.
    assert "validate" in output
    assert "describe" in output


def test_subcommand_help_unchanged() -> None:
    """`dct validate --help` still renders Typer's default panels (no regression)."""
    result = runner.invoke(app, ["validate", "--help"])
    assert result.exit_code == 0
    text = _strip_ansi(result.output)
    assert "Usage: " in text
    # Subcommand help still uses Typer's default Options panel.
    assert "Options" in text


def test_docs_command_is_retired() -> None:
    """Agents read `dct skills chart` and people read the docs site."""
    result = runner.invoke(app, ["docs"])
    assert result.exit_code == 2
    assert "No such command 'docs'" in _strip_ansi(result.output)
