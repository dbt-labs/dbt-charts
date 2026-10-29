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
    "sort_ascending",
    "sort_descending",
    "clear_sort",
    "search",
    "search_values",
    "page_range",
)


# The calendar and page-range patterns reach the page unfilled: the runtime puts
# a date's parts or a page's row numbers into them.
_PATTERN_PLACEHOLDERS = {
    "calendar_header": ("month", "year"),
    "calendar_day": ("weekday", "day", "month", "year"),
    "page_range": ("first", "last", "total"),
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
        case "last" | "previous":
            return board_word(
                f"controls.preset_{preset.kind}",
                duration=board_word(f"duration.{unit}", preset.n),
            )


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
def controls_runtime_source() -> str:
    """The board runtimes (chart hover, table sort/filter, the link modal, the English controls words, then variable controls) as JS for a host to ship in its page.

    Strips the ``/*{# ... #}*/``-fenced comments — engineering notes with no
    audience once the runtime is shipped. Cached: hosts rebuild this per page
    render, and a packaged asset can't change under a running process.
    """
    # Hover and table first: the controls runtime's mount() hands each board to them.
    return "\n".join(
        [
            _read_script("controls_english.js"),
            hover_runtime_source(),
            strip_js_comments(_read_script("table_runtime.js")),
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
