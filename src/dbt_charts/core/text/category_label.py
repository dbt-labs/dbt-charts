"""What Vega paints for one CATEGORY (ordinal/nominal) axis label.

Leaf module — no dependency on compile/ or render/ — so both layers can
measure a category label's real painted text without crossing the module
boundary. ``cadence_label_text`` (``render/chart/time_unit_detect.py``) is
this function's TEMPORAL twin; the two exist separately because a category
label's authored ``format`` is a d3 NUMBER spec (this module's
``format_d3``), not a time spec.
"""

from __future__ import annotations

from dbt_charts.core.text.case import CaseValue, apply_case
from dbt_charts.core.text.format_d3 import format_d3, is_time_format, reads_as_number


def category_label_text(
    value: str, label_format: str | None, case: CaseValue | None
) -> str:
    """The text Vega paints for one category/ordinal axis label.

    ``label_format`` is ``axis.labels.format``. Only a d3 number format
    applies here, and only to a value d3 reads as a number
    (``reads_as_number``, the rule ``gate_label_format`` uses); a time
    format is ``cadence_label_text``'s job. ``case`` is
    ``axis.labels.font.case``; only "upper"/"lower" change the paint (see
    ``inject_axis_label_case``).

    An authored ``axis.labels.expr`` paints text no Python function can
    compute; callers check for it themselves.
    """
    text = value
    if (
        label_format is not None
        and not is_time_format(label_format)
        and reads_as_number(text)
    ):
        # JS reads +"" and +" " as 0; d3 paints the formatted zero.
        number = float(text.strip()) if text.strip() else 0.0
        text = format_d3(number, label_format)
    if case in ("upper", "lower"):
        text = apply_case(text, case)
    return text
