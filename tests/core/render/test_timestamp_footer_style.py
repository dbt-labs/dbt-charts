"""Tests for timestamp + footer as authorable style fields.

These pin the contract:
  - Both render by default (theme supplies visible=True for both).
  - A board `style: {footer: {visible: false}}` hides the footer only.
  - A board `style: {timestamp: {visible: false}}` hides the timestamp only.
  - timestamp.format and font overrides (size, color) are honored in the SVG.

Tests do NOT pin theme literal values (hex, px) — they assert presence and
override behavior (AGENTS.md test rules).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import render

_BOARD = """\
title: Test board
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""


def _render_svg(yaml: str, local_project: Callable[..., Project]) -> str:
    result = compile(yaml)
    assert result.board is not None
    rendered = render(
        result.board,
        Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        ),
        format="svg",
    )
    assert isinstance(rendered.output, str)
    return rendered.output


class TestTimestampFooterDefaultVisibility:
    def test_timestamp_renders_by_default(
        self, local_project: Callable[..., Project]
    ) -> None:
        svg = _render_svg(_BOARD, local_project)
        assert 'data-role="render-timestamp"' in svg

    def test_footer_renders_by_default(
        self, local_project: Callable[..., Project]
    ) -> None:
        svg = _render_svg(_BOARD, local_project)
        # Footer is a <text> right-anchored at the bottom; checking "made with dbt Charts"
        # would pin a config value. Instead confirm the footer element group exists by
        # checking the footer rule or text after the layout group.
        # The footer is always present when visible=True; simply assert it is in the SVG.
        # We use the y_offset positioning as indirect evidence: both text and rule appear.
        assert 'data-role="render-timestamp"' in svg  # sanity: timestamp present
        # Footer text element appears last in SVG body.
        # Check SVG has more than just the timestamp (footer adds another <text>).
        text_count = svg.count("<text")
        assert text_count >= 2  # at least timestamp + footer text


class TestTimestampHidden:
    def test_timestamp_hidden_via_style(
        self, local_project: Callable[..., Project]
    ) -> None:
        yaml = _BOARD + "style:\n  timestamp:\n    visible: false\n"
        svg = _render_svg(yaml, local_project)
        assert 'data-role="render-timestamp"' not in svg

    def test_footer_still_visible_when_only_timestamp_hidden(
        self, local_project: Callable[..., Project]
    ) -> None:
        yaml = _BOARD + "style:\n  timestamp:\n    visible: false\n"
        svg = _render_svg(yaml, local_project)
        # Footer rule or text should still be there.
        # We can't pin the text value, but two <text> less one timestamp = at least 1.
        text_count = svg.count("<text")
        assert text_count >= 1


class TestFooterHidden:
    def test_footer_hidden_via_style(
        self, local_project: Callable[..., Project]
    ) -> None:
        yaml = _BOARD + "style:\n  footer:\n    visible: false\n"
        svg = _render_svg(yaml, local_project)
        # Timestamp still there, footer text gone. Since we can't pin the footer
        # text value, we assert <text> count drops compared to the default render.
        default_svg = _render_svg(_BOARD, local_project)
        default_text_count = default_svg.count("<text")
        hidden_text_count = svg.count("<text")
        assert hidden_text_count < default_text_count

    def test_timestamp_still_visible_when_only_footer_hidden(
        self, local_project: Callable[..., Project]
    ) -> None:
        yaml = _BOARD + "style:\n  footer:\n    visible: false\n"
        svg = _render_svg(yaml, local_project)
        assert 'data-role="render-timestamp"' in svg


class TestTimestampFormatOverride:
    def test_custom_format_appears_in_output(
        self, local_project: Callable[..., Project]
    ) -> None:
        # A minimal strftime format that produces a fixed-length prefix we can assert.
        # %Y always produces a 4-digit year — easy to verify without pinning the value.
        yaml = _BOARD + 'style:\n  timestamp:\n    format: "%Y"\n'
        svg = _render_svg(yaml, local_project)
        # The value is stamped in UTC, so compare against the UTC year.
        expected_year = str(datetime.now(UTC).year)
        assert 'data-role="render-timestamp"' in svg
        match = re.search(r'data-role="render-timestamp"[^>]*>([^<]+)<', svg)
        assert match is not None
        # The displayed value is just the current year.
        assert match.group(1).strip().startswith(expected_year)


class TestTimestampPlacementOverride:
    def test_timestamp_position_and_align_are_authored_style(self) -> None:
        # Author NON-default values (the defaults are footer/left) so this fails
        # if the authored overlay is ignored — proving authoring wins over theme.
        yaml = (
            _BOARD
            + "style:\n"
            + "  timestamp:\n"
            + "    position: top\n"
            + "    align: right\n"
        )
        result = compile(yaml)
        assert result.board is not None
        assert result.board.resolved_style.timestamp.position == "top"
        assert result.board.resolved_style.timestamp.align == "right"

    def test_timestamp_position_rejects_unknown_values(self) -> None:
        yaml = _BOARD + "style:\n  timestamp:\n    position: footer-left\n"
        result = compile(yaml)
        assert result.board is None
        assert any(
            "style.timestamp.position" in error.message for error in result.errors
        )

    def test_timestamp_align_rejects_unknown_values(self) -> None:
        yaml = _BOARD + "style:\n  timestamp:\n    align: center\n"
        result = compile(yaml)
        assert result.board is None
        assert any("style.timestamp.align" in error.message for error in result.errors)


def _timestamp_text(svg: str) -> str:
    match = re.search(r'<text[^>]*data-role="render-timestamp"[^>]*>([^<]+)<', svg)
    assert match is not None, "timestamp <text> element not found"
    return match.group(1)


# An explicit clock format so these tests assert against a KNOWN caption shape,
# not the theme default's wording (tunable — pinning it here is banned).
_BOARD_UTC_CLOCK = _BOARD + 'style:\n  timestamp:\n    format: "%H:%M %Z"\n'


class TestDataFreshnessStamp:
    """The stamp discloses how fresh the *data* is, not when the SVG was drawn.

    A fully-fresh render just queried, so the data is as of the render instant;
    a genuine persistent-cache hit shows the oldest cached query's time instead,
    so the board never reads as fresher than its stalest query. Always UTC.

    These author an explicit ``%H:%M %Z`` format and assert against it — the
    default caption's wording is theme-tunable and must not be pinned here.
    """

    def test_cache_hit_stamps_the_cached_time_not_now(
        self, local_project: Callable[..., Project]
    ) -> None:
        """A cache hit stamps the cached write time (min), not the render instant.

        This is the whole point of the change, and a presence-only assertion
        can't see it: the caption renders whichever datetime is selected, so
        swapping ``min(cache_hit_ats)`` for a bare ``render_time_utc`` would keep
        such a test green. Seed the executor's hit list with an aged aware-UTC
        instant and assert the stamped clock is THAT time, distinct from now.
        """
        result = compile(_BOARD_UTC_CLOCK)
        assert result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        )
        from dbt_charts.core.compile.models.cache import CachePolicy

        aged = datetime.now(UTC) - timedelta(hours=2, minutes=17)
        # Seed a cache-hit record so min(cache_hit_ats) sees `aged`. The board
        # query is inline values (no result_cache), so nothing appends to
        # _query_data_ages during render — the seeded record is what min() sees.
        executor._query_data_ages = [
            ("_seeded", aged, CachePolicy(enabled=True, ttl=None), True)
        ]

        rendered = render(result.board, executor, format="svg")
        assert isinstance(rendered.output, str)
        stamp = _timestamp_text(rendered.output)
        expected = aged.strftime("%H:%M %Z")  # authored format → "HH:MM UTC"
        assert expected in stamp, (
            f"stamp {stamp!r} does not show the aged cache time {expected!r} — "
            "min(cache_hit_ats) is not driving it"
        )

    def test_real_cache_hit_still_renders_a_stamp(
        self, local_project: Callable[..., Project], tmp_path: Path
    ) -> None:
        """End-to-end: a render served from a real persistent cache still stamps
        a UTC freshness line — the cache path neither errors nor blanks it."""
        from dbt_charts.core.execute.duckdb_cache import compute_cache_key
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        result = compile(_BOARD_UTC_CLOCK)
        assert result.board is not None
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            query = result.board.queries["q"]
            key = compute_cache_key(query)
            cache.put(*key, [{"n": 1}], board_slug="test", query_name="q")

            rendered = render(
                result.board,
                Executor(
                    result.board,
                    adapter_registry=build_adapter_registry(local_project(Path.cwd())),
                    result_cache=cache,
                ),
                format="svg",
            )
            assert isinstance(rendered.output, str)
            assert "UTC" in _timestamp_text(rendered.output)
        finally:
            cache.close()

    @pytest.mark.usefixtures("non_utc_tz")
    def test_stamp_is_utc_regardless_of_local_clock(
        self, local_project: Callable[..., Project]
    ) -> None:
        """The stamp reads in UTC, not the render machine's local zone.

        A static export can't know the viewer's timezone, and the render
        process's own zone is an accidental deployment artifact — so the one
        honest, reproducible choice is UTC. This pins that the displayed wall
        clock tracks UTC and not the (forced non-UTC) local clock; on a UTC
        machine the two coincide, which is why the fixture forces an offset.
        (This fresh render also pins that the fresh path stamps the render
        instant.)
        """
        svg = _render_svg(_BOARD_UTC_CLOCK, local_project)
        stamp = _timestamp_text(svg)
        assert "UTC" in stamp

        # Compare the displayed HH:MM to UTC-now, not local-now. Pull the clock
        # straight out of the label rather than round-tripping the whole format
        # (strptime's %Z / non-zero-padded parsing is unreliable). Minute
        # granularity and a possible rollover mean a small circular tolerance.
        clock = re.search(r"(\d{2}):(\d{2})", stamp)
        assert clock is not None
        stamp_minutes = int(clock.group(1)) * 60 + int(clock.group(2))
        now_utc = datetime.now(UTC)
        utc_minutes = now_utc.hour * 60 + now_utc.minute
        drift = abs(stamp_minutes - utc_minutes)
        drift = min(drift, 1440 - drift)
        assert drift <= 2, (
            f"stamp {stamp!r} reads {drift} min from UTC — the freshness stamp is "
            "being rendered in the local zone, not UTC"
        )


class TestTimestampFormatZoneGuard:
    """The stamp prints in the board's zone, so a custom format that prints a
    clock must disclose that zone; a zone-less clock is rejected at compile. A date-only
    caption needs none. Clock detection is behavioral, so directive-scan escapees
    (%T/%-H) are caught too."""

    def _compiles(self, fmt: str, zone: str = "UTC") -> bool:
        result = compile(
            _BOARD
            + f'style:\n  timestamp:\n    format: "{fmt}"\n'
            + f"regional:\n  time_zone: {zone}\n"
        )
        return result.board is not None

    def test_zoneless_clock_rejected(self) -> None:
        for fmt in ("%H:%M", "%T on %-d %b %Y", "%-H:%-M"):
            assert not self._compiles(fmt), f"{fmt!r} should be rejected"

    def test_clock_with_zone_accepted(self) -> None:
        assert self._compiles("%H:%M %Z")
        assert self._compiles("%H:%M %z")
        assert self._compiles("Data as of %H:%M UTC")

    def test_a_literal_zone_must_be_the_board_zone_all_year(self) -> None:
        assert self._compiles("%H:%M %Z", "America/New_York")
        assert not self._compiles("Data as of %H:%M UTC", "America/New_York")
        assert not self._compiles("%H:%M EST", "America/New_York")
        # Today's rules, not 2001's: Casablanca was UTC+0 then, +01 now.
        assert not self._compiles("Data as of %H:%M UTC", "Africa/Casablanca")
        assert self._compiles("%H:%M MSK", "Europe/Moscow")

    def test_date_only_needs_no_zone(self) -> None:
        assert self._compiles("%-d %b %Y")


class TestTimestampFontOverride:
    def test_custom_font_size_propagates_to_svg_attribute(
        self, local_project: Callable[..., Project]
    ) -> None:
        yaml = (
            _BOARD
            + "style:\n  timestamp:\n    font:\n      size: 17\n      color: '#aabbcc'\n"
        )
        svg = _render_svg(yaml, local_project)
        # The timestamp <text> element should carry font-size=17 and fill=#aabbcc.
        match = re.search(r'<text[^>]*data-role="render-timestamp"[^>]*>', svg)
        assert match is not None, "timestamp <text> element not found"
        element_tag = match.group(0)
        assert 'font-size="17"' in element_tag
        assert 'fill="#aabbcc"' in element_tag


class TestFooterTimestampNamedFont:
    @pytest.mark.parametrize("name", ["label", "body", "caption"])
    def test_family_follows_caption_only(
        self, name: str, local_project: Callable[..., Project]
    ) -> None:
        yaml = _BOARD + f"style:\n  fonts:\n    {name}:\n      family: Zork\n"
        svg = _render_svg(yaml, local_project)
        timestamp = re.search(r'<text data-role="render-timestamp"[^>]*>', svg)
        footer = re.search(r'<text[^>]*font-weight="600">dbt Charts', svg)
        assert timestamp is not None and footer is not None
        for chrome in (timestamp, footer):
            assert ("Zork" in chrome.group(0)) == (name == "caption")


def test_stamp_prints_in_the_board_zone(
    local_project: Callable[..., Project],
) -> None:
    result = compile(_BOARD_UTC_CLOCK + "regional:\n  time_zone: America/New_York\n")
    assert result.board is not None
    rendered = render(
        result.board,
        Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            clock=lambda: datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        ),
        format="svg",
    )
    assert isinstance(rendered.output, str)

    assert _timestamp_text(rendered.output) == "08:00 EDT"
