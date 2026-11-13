"""The control runtime and stylesheet a host ships to bind a board's controls.

Stage: RENDER
Purpose: Hand a host the JS and CSS that make the *drawn* controls live.

No markup: the server draws every variable control into the board SVG
(``variables_strip.py``), so a host adds behavior, never elements. That is what
keeps every interactive surface out of the board's scaled coordinate system —
the defect this replaced was an HTML layer measured in page pixels sitting over
a band measured in board units.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING

from dbt_charts.core.compile.template.daterange import DaterangePreset
from dbt_charts.core.render.chart_interactivity import hover_runtime_source
from dbt_charts.core.render.comment_stripping import strip_js_comments
from dbt_charts.core.render.template_loader import render_template
from dbt_charts.core.text.format_d3 import board_word, language_scope

if TYPE_CHECKING:
    from collections.abc import Generator

_INTERACTIVE: ContextVar[bool] = ContextVar("dct_interactive_controls", default=False)


@contextmanager
def interactive_controls(enabled: bool) -> Generator[None]:
    """Hang the behavior payload on the drawn controls, for one render.

    The *drawing* never varies: a picture must not depend on who is looking at
    it, so the same board renders the same chrome live or exported. What varies
    is the payload a runtime needs and an artifact cannot use — a select's full
    option list, which is data the board fetched and deliberately did not draw.

    A context manager rather than a parameter because the decision is the
    host's and the reader is eight frames down; ``collect_table_overflows`` in
    the same ``with`` block scopes render-wide state the same way.
    """
    token = _INTERACTIVE.set(enabled)
    try:
        yield
    finally:
        _INTERACTIVE.reset(token)


def controls_are_interactive() -> bool:
    """True while a render is being done for a host that can act on it."""
    return _INTERACTIVE.get()


_PAGE_RUNTIME: ContextVar[bool] = ContextVar("dct_page_runtime", default=False)


@contextmanager
def page_runtime(enabled: bool) -> Generator[None]:
    """Draw for a page that ships ``page_runtime_source()``, for one render.

    A table then pools its rows for the table runtime instead of embedding a
    pager script of its own. An HTML export opens this scope; a host with
    interactive controls ships the page runtime too, so it implies the scope.
    """
    token = _PAGE_RUNTIME.set(enabled)
    try:
        yield
    finally:
        _PAGE_RUNTIME.reset(token)


def ships_page_runtime() -> bool:
    """True while a render is being done for a page that ships the page runtime."""
    return _INTERACTIVE.get() or _PAGE_RUNTIME.get()


_FLAT_EXPORT: ContextVar[bool] = ContextVar("dct_flat_export", default=False)


@contextmanager
def flat_export(enabled: bool) -> Generator[None]:
    """Draw every table to fit its slot, for one render to an image or PDF.

    A picture cannot scroll, so a table that would scroll sideways
    (`column_layout.overflow: scroll`) would hide its columns past the slot's
    edge. Under this scope every table squeezes to fit instead, frozen
    columns dropped with the scrolling they belong to.
    """
    token = _FLAT_EXPORT.set(enabled)
    try:
        yield
    finally:
        _FLAT_EXPORT.reset(token)


def is_flat_export() -> bool:
    """True while a render is being done for an image or a PDF."""
    return _FLAT_EXPORT.get()


# The words the controls and table runtimes (variables.js, table_runtime.js)
# print themselves, keyed by the last segment of their catalog id: the page
# cannot fetch the catalog, so the strip carries this slice and the engine
# publishes the table words on a pooled table's root.
_CONTROLS_RUNTIME_WORDS = (
    "today",
    "custom",
    "clear",
    "apply",
    "previous_month",
    "next_month",
    "calendar_header",
    "calendar_day",
    "date",
    "date_no_year",
    "range",
    "range_same_month",
    "sort_text_ascending",
    "sort_text_descending",
    "sort_number_ascending",
    "sort_number_descending",
    "sort_date_ascending",
    "sort_date_descending",
    "clear_sort",
    "remove_value",
    "search",
    "search_values",
    "select_all",
    "selection_summary",
    "show_all",
    "show_only_selected",
    "options_truncated",
    "sort",
    "sort_count_ascending",
    "sort_count_descending",
    "filter_by_range",
    "filter_by_values",
    "page_range",
)


# The calendar, date, and page-range patterns reach the page unfilled: the
# runtime puts a date's parts or a page's row numbers into them.
_PATTERN_PLACEHOLDERS = {
    "calendar_header": ("month", "year"),
    "calendar_day": ("weekday", "day", "month", "year"),
    "date": ("day", "month", "year"),
    "date_no_year": ("day", "month"),
    "range": ("start", "end"),
    "range_same_month": ("start_day", "end_day", "month", "year"),
    "page_range": ("first", "last", "total"),
    "show_only_selected": ("n",),
    "options_truncated": ("shown", "total"),
    "remove_value": ("value",),
    "selection_summary": ("first", "n"),
}


def preset_label(preset: DaterangePreset) -> str:
    """The picker's text for ``preset`` in the board's language."""
    unit = f"fiscal_{preset.unit}" if preset.fiscal else preset.unit
    match preset.kind:
        case "today" | "yesterday":
            return board_word(f"controls.{preset.kind}")
        case "this":
            return board_word(f"controls.this_{unit}")
        case "to_date":
            return board_word(f"controls.{unit}_to_date")
        case "last" | "previous" | "before_previous":
            return board_word(
                f"controls.preset_{preset.kind}",
                duration=board_word(f"duration.{unit}", preset.n),
            )


# Ticker notation (1D 5D 3M YTD 12M) is the same in every language a board is
# written in, so it is a table here rather than catalog words.
_COMPACT_UNIT = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}


def compact_preset_label(preset: DaterangePreset) -> str:
    """The button row's short text for ``preset``; forms without a ticker spelling
    (fiscal, ``previous_``, ``yesterday``) keep :func:`preset_label`."""
    if preset.fiscal:
        return preset_label(preset)
    match preset.kind:
        case "today":
            return "1D"
        case "last" if preset.n > 1 or preset.unit == "day":
            return f"{preset.n}{_COMPACT_UNIT[preset.unit]}"
        # `last_1_<unit>` is calendar-aligned, so its dates are the unit to date.
        case "last" | "to_date":
            return f"{_COMPACT_UNIT[preset.unit]}TD"
        case _:
            return preset_label(preset)


def controls_language() -> dict[str, str]:
    return {
        word: board_word(
            f"controls.{word}",
            None,
            **{
                name: f"{{{name}}}"
                for name in _PATTERN_PLACEHOLDERS.get(  # type-state: silent_fallback — most words have no placeholders
                    word, ()
                )
            },
        )
        for word in _CONTROLS_RUNTIME_WORDS
    }


@cache
def page_runtime_source() -> str:
    """The board runtimes that need no host (the English controls words, the calendar, chart hover, then table scroll, sort/filter/paging and cut-cell reveal) as JS.

    Everything a page can do with the board it already holds: a static HTML
    export ships exactly this. Strips the ``/*{# ... #}*/``-fenced comments,
    engineering notes with no audience once the runtime is shipped. Cached: a
    packaged asset can't change under a running process.
    """
    # The calendar before both of its users, the table and date controls.
    return "\n".join(
        [
            _read_script("controls_english.js"),
            strip_js_comments(_read_script("calendar.js")),
            hover_runtime_source(),
            strip_js_comments(_read_script("table_scroll.js")),
            strip_js_comments(_read_script("table_runtime.js")),
            strip_js_comments(_read_script("table_cell_reveal.js")),
        ]
    )


@cache
def controls_runtime_source() -> str:
    """The page runtimes, then the link modal and variable controls, as JS for a host that can re-run a board's queries."""
    # Hover and table first: the controls runtime's mount() hands each board to them.
    return "\n".join(
        [
            page_runtime_source(),
            link_modal_source(),
            strip_js_comments(_read_script("variables.js")),
        ]
    )


@cache
def link_modal_source() -> str:
    """The link modal host, for a page whose boards open modal links.

    Part of ``controls_runtime_source``; served alone to a host page that runs
    no board itself (the playground).
    """
    return strip_js_comments(_read_script("link_modal.js"))


def controls_english_source() -> str:
    """The English controls words as JS, generated from the catalog.

    Stored renders without ``data-dbt-language`` are English; every host that
    ships the controls or table runtime loads this module before them. Committed as
    ``templates/scripts/controls_english.js`` (``just gen-controls-english``).
    """
    with language_scope("en"):
        words = json.dumps(controls_language(), indent=4, ensure_ascii=False)
    return (
        "// Generated by `just gen-controls-english` from the English catalog.\n"
        f"window.dbtControlsEnglish = {words};\n"
    )


def _read_script(name: str) -> str:
    return (files("dbt_charts.core.render") / "templates" / "scripts" / name).read_text(
        encoding="utf-8"
    )


@cache
def controls_stylesheet() -> str:
    """The control styles, as CSS for a host to ship in its page.

    Rendered through Jinja, not read raw: the source authors its comments as
    ``{# ... #}``, which a browser would choke on. Cached for the same reason as
    the runtime above.
    """
    return render_template("controls/_styles.css")
