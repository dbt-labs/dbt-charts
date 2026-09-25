"""A board is a picture: nothing interactive ships inside it.

The SVG a render produces is the same bytes on a live page, in a PNG/PDF
export, and in a golden. Interaction — hover, focus, cursors, hit-testing,
transitions — only exists on a page that also runs the controls runtime, and
that runtime is what adds `.dbt-interactive`. So every rule that reads it, or
styles anything the runtime mounts, belongs in the stylesheet the host ships
(`controls_stylesheet()`), never in the one embedded in every board
(`svg/styles.css`). Otherwise a focus-ring tweak re-bakes every golden and
every export carries dead chrome.
"""

import re

from dbt_charts.core.render.controls import controls_stylesheet
from dbt_charts.core.render.template_loader import render_template

from .._svg_render import render_board_to_svg

INTERACTION = re.compile(
    r":hover|:focus|:active\b|cursor\s*:|pointer-events\s*:|user-select\s*:"
    r"|::selection|transition\s*:|animation\s*:|@keyframes|@media[^{]*hover"
    r"|\.dbt-interactive|\.dbt-variable-overlay|\.dbt-chart-highlight-overlay",
    re.IGNORECASE,
)


def _board_stylesheet() -> str:
    return render_template("svg/styles.css", emoji_mode="none", font_face_css="")


def test_the_board_stylesheet_carries_no_interaction() -> None:
    # Rendered, so the source's Jinja comments are already gone: every line is a rule.
    offenders = [
        line.strip()
        for line in _board_stylesheet().splitlines()
        if INTERACTION.search(line)
    ]
    assert offenders == [], (
        "Interaction styling belongs in controls_stylesheet(), which hosts ship "
        "beside the runtime — not in the board SVG:\n  " + "\n  ".join(offenders)
    )


def test_the_host_stylesheet_carries_the_bound_control_affordances() -> None:
    """The rules did not vanish: the host stylesheet is where they live now."""
    css = controls_stylesheet()

    assert ".dbt-interactive [data-dbt-field]" in css
    assert ".dbt-interactive [data-dbt-variable]:is(" in css
    assert ".dbt-chart-highlight-overlay" in css

    # Themable, not a hardcoded literal: dbt-labs/dbt-charts#36 regression —
    # this overlay used to paint a literal `stroke: #667eea` instead of the
    # rest of this stylesheet's `var(--dbt-system-accent, ...)` pattern.
    overlay_rule = css.split(".dbt-chart-highlight-overlay")[1].split("}")[0]
    assert "stroke: var(--dbt-system-accent" in overlay_rule


_LINKED_PAGED_BOARD = """\
title: Every kind of interaction a board can draw
variables:
  region:
    input: select
    options:
      static: [US, EU]
queries:
  rows:
    type: values
    rows:
      - {ticket_id: "T1", status: "open"}
      - {ticket_id: "T2", status: "closed"}
      - {ticket_id: "T3", status: "open"}
      - {ticket_id: "T4", status: "closed"}
      - {ticket_id: "T5", status: "open"}
      - {ticket_id: "T6", status: "closed"}
      - {ticket_id: "T7", status: "open"}
      - {ticket_id: "T8", status: "closed"}
      - {ticket_id: "T9", status: "open"}
      - {ticket_id: "T10", status: "closed"}
      - {ticket_id: "T11", status: "open"}
      - {ticket_id: "T12", status: "closed"}
charts:
  tickets:
    type: table
    query: rows
    link: "/tickets/{{ ticket_id }}"
    style:
      pagination:
        enabled: true
        page_rows: 5
      columns:
        status:
          link: "/backlog/?status={{ status }}"
rows:
  - details:
      summary: Notes
      expanded: true
    rows:
      - tickets
"""


def test_a_rendered_board_ships_no_interaction_at_all() -> None:
    """Not only the board stylesheet: every <style> a chart emits, and every
    inline style attribute, in a board that draws a linked, paged table inside
    a details toggle with a variable strip — the surfaces that each used to
    carry a cursor, a hover or a pointer-events rule of their own."""
    # Rendered live: the rule is about the board a host serves. A static
    # multi-page export is the one place a script still ships (the documented
    # exception — table_pagination.js, so a downloaded artifact can page).
    svg = render_board_to_svg(_LINKED_PAGED_BOARD, controls=True)

    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", svg, re.DOTALL))
    offenders = [
        line.strip() for line in styles.splitlines() if INTERACTION.search(line)
    ]
    inline = re.findall(
        r'style="[^"]*(?:cursor|pointer-events|transition|user-select|animation)[^"]*"',
        svg,
    )
    # Presentation attributes too. Vega's own groups carry pointer-events and
    # are not ours to change, so the check is scoped to what we draw: anything
    # carrying a dbt- class.
    attrs = [
        tag
        for tag in re.findall(r"<\w+[^>]*>", svg)
        if re.search(r'class="[^"]*\bdbt-', tag) and " pointer-events=" in tag
    ]
    # Code never ships inside a board either: the hover runtime is host-shipped
    # and reads its theme facts off the root.
    assert "<script" not in svg
    assert 'data-dbt-tooltip-style="' in svg
    # Not vacuous: the surfaces the docstring names must actually be drawn.
    for marker in (
        "dbt-table-row-link",
        "dbt-table-cell-inert",
        "dbt-page-target",
        "dbt-details-toggle",
        "data-dbt-variable=",
    ):
        assert marker in svg, f"guard board did not draw {marker}"
    assert offenders == [] and inline == [] and attrs == [], (
        "a board is a picture — interaction ships with the host stylesheet:\n  "
        + "\n  ".join(offenders + inline + attrs)
    )
