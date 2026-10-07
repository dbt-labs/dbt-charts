"""Regression test: CLI entrypoint reconfigures stdout/stderr to UTF-8.

On Windows, sys.stdout/sys.stderr default to the legacy cp1252 console
codepage. Diagnostic text containing non-ASCII glyphs (e.g. an arrow) then
raises UnicodeEncodeError when printed — crashing the very code path meant
to report an error (the render command's per-board error handler).
`dbt_charts.cli.main._reconfigure_console_encoding()`
forces both streams to UTF-8 so this never happens; it is a no-op on
Linux/macOS, where the streams are already UTF-8.

These tests build an explicit cp1252 stream rather than relying on the
process locale, so they fail deterministically on every platform before the
fix and pass on every platform after it — no Windows-only gating needed.
"""

from __future__ import annotations

import io

import pytest
import typer
from typer.core import TyperCommand

from dbt_charts.cli.main import _reconfigure_console_encoding, main


def _cp1252_stream() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252")


def test_cp1252_stream_raises_before_reconfigure() -> None:
    """Sanity check: printing a non-ASCII glyph to a raw cp1252 stream raises."""
    stream = _cp1252_stream()
    with pytest.raises(UnicodeEncodeError):
        print("→", file=stream)


def test_reconfigure_console_encoding_fixes_cp1252_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After reconfigure, a cp1252 stdout/stderr accepts non-ASCII output."""
    stdout = _cp1252_stream()
    stderr = _cp1252_stream()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)

    _reconfigure_console_encoding()

    assert stdout.encoding is not None and stdout.encoding.lower() == "utf-8"
    assert stderr.encoding is not None and stderr.encoding.lower() == "utf-8"
    print("Error rendering board.yml: → boom", file=stderr)  # must not raise


def test_reconfigure_console_encoding_skips_non_reconfigurable_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream without reconfigure() (e.g. one swapped in under test capture) is left alone."""
    monkeypatch.setattr("sys.stdout", object())
    monkeypatch.setattr("sys.stderr", object())

    _reconfigure_console_encoding()  # must not raise


def test_main_callback_reconfigures_through_real_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the real Typer callback, not the helper in isolation.

    This guards the wiring itself: `main()` must call
    `_reconfigure_console_encoding()`. Removing that call fails here even
    though the helper's own unit tests above keep passing.
    """
    stdout = _cp1252_stream()
    stderr = _cp1252_stream()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)

    main(ctx=typer.Context(TyperCommand("dct")), version=None)

    assert stdout.encoding is not None and stdout.encoding.lower() == "utf-8"
    assert stderr.encoding is not None and stderr.encoding.lower() == "utf-8"
