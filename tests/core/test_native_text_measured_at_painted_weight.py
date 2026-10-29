"""Text the Rust engine paints is measured at the weight it paints at.

The weight-less ``get_font_measurer(family)`` measures the default instance,
which is what vl-convert measures for text it paints. A natively painted
family's classifier measures with ``get_weighted_font_measurer`` instead, or
the room it reserves is narrower than the ink.
"""

from __future__ import annotations

from pathlib import Path

import dbt_charts.core.compile.resolve.chart as resolve_chart

NATIVE_MODULES = ("pie.py", "pie_attachment.py")


def test_natively_painted_families_never_measure_at_the_default_instance() -> None:
    chart_dir = Path(resolve_chart.__file__).parent
    offenders = [
        f"{name}:{number}: {line.strip()}"
        for name in NATIVE_MODULES
        for number, line in enumerate((chart_dir / name).read_text().splitlines(), 1)
        if "get_font_measurer(" in line and "# unweighted:" not in line
    ]
    assert not offenders, (
        "measure with get_weighted_font_measurer (or mark the line "
        "`# unweighted: <why>`):\n" + "\n".join(offenders)
    )
