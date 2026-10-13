"""Editorial theme must set a table row stripe color.

Editorial (the default theme when `theme:` is unset) previously left
`charts.table.row.stripe` unset, skipping the `stripes_enabled` render
guard in the table renderer entirely — tables rendered with no
zebra striping. `cream.yaml` (which extends editorial) already sets
`dbt-creams.surface-subtle` one scaffold step off its own canvas; editorial
should do the same with its own gray scaffold.

Assertions resolve through the palette token, never a pinned hex literal —
see `dbt-charts/AGENTS.md` § Testing.
"""

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.palette import color as resolve_palette_color


def test_editorial_theme_sets_table_row_stripe_color():
    editorial_table = get_theme_style("clarity").charts.table
    assert editorial_table.row.stripe is not None, (
        "Editorial theme should set charts.table.row.stripe"
    )
    assert editorial_table.row.stripe.color is not None


def test_editorial_row_stripe_is_one_scaffold_step_off_canvas():
    """Stripe sits at gray-scaffold `surface-subtle` (step 2), one step off
    `canvas` (step 1) — the same relationship cream has to its own canvas."""
    editorial_style = get_theme_style("clarity")
    stripe_color = editorial_style.charts.table.row.stripe.color
    canvas_color = editorial_style.background

    assert stripe_color == resolve_palette_color("dbt-grays.surface-subtle")
    assert stripe_color != canvas_color


def test_editorial_row_stripe_matches_cream_scaffold_step():
    """Editorial's gray stripe and cream's cream stripe sit at the same
    named scaffold step (`surface-subtle`) in their respective families."""
    editorial_stripe = get_theme_style("clarity").charts.table.row.stripe.color
    cream_stripe = get_theme_style("paper").charts.table.row.stripe.color

    assert editorial_stripe == resolve_palette_color("dbt-grays.surface-subtle")
    assert cream_stripe == resolve_palette_color("dbt-creams.surface-subtle")
