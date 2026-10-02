"""Detector: WARN_SERIES_LABELS_REPEAT_WORD -- see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

A wide chart crossed with a `color:` dimension is skipped: its labels are
`<value> - <measure>` composites, and a fix that strips a word out of one
half would be rewriting a value the query owns.
"""

from __future__ import annotations

import re

from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_MEASURE_FAMILIES,
    WideMeasureChart,
    wide_measure_labels_for,
)
from dbt_charts.core.diagnostics import WARN_SERIES_LABELS_REPEAT_WORD, Diagnostic
from dbt_charts.core.render.chart.emitters._cartesian import distinct_series_values
from dbt_charts.core.render.warnings.base import (
    WarningContext,
    chart_series,
    listed_phrase,
)

# Raw `color:` cells are often snake_case or kebab-case, and a title carries
# punctuation ("Documents: Created vs Completed").
_WORD_BREAK = re.compile(r"[\W_]+")
# Filler ("of", "in") repeated across labels is not worth a warning.
_MIN_REPEAT_LEN = 3


def _is_word(token: str) -> bool:
    """A repeat worth moving to the title. A shared number is not one: the
    year of a date-valued `color:` column splits off as its own token."""
    return len(token) >= _MIN_REPEAT_LEN and any(c.isalpha() for c in token)


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_BREAK.split(text) if t]


def _normalize_token(token: str) -> str:
    """Casefold and strip one trailing 's', so a label's "Documents" lines
    up with a title's "Document"."""
    return token.casefold().removesuffix("s")


def _shared_run_length(label_tokens: list[list[str]], *, reverse: bool) -> int:
    """Length of the longest token run every label shares, from the front or
    (reverse) the back."""
    first = label_tokens[0]
    run = 0
    while run < min(len(tokens) for tokens in label_tokens):
        index = -(run + 1) if reverse else run
        candidate = _normalize_token(first[index])
        if any(_normalize_token(t[index]) != candidate for t in label_tokens[1:]):
            break
        run += 1
    return run


def _series_labels(
    chart: WideMeasureChart, ctx: WarningContext, chart_id: str
) -> tuple[str, list[str]] | None:
    """``(authored key, labels)``, or None when the chart draws no plain
    series labels or its query never ran."""
    if chart.wide_measures:
        if chart.color is not None:
            return None
        return "y", list(wide_measure_labels_for(chart.wide_measures).values())
    series = chart_series(chart)
    if series is None or chart_id not in ctx.chart_results:
        return None
    return series.authored_key, distinct_series_values(
        ctx.chart_results[chart_id], series.authored_field
    )


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart whose series labels all repeat a word."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, WIDE_MEASURE_FAMILIES):
            continue
        found = _series_labels(chart, ctx, chart_id)
        if found is None:
            continue
        authored_key, labels = found
        label_tokens = [_tokens(label) for label in labels]
        if len(labels) < 2 or not all(label_tokens):
            continue

        first = label_tokens[0]
        leading = {
            i
            for i in range(_shared_run_length(label_tokens, reverse=False))
            if _is_word(first[i])
        }
        # Counted from the end: labels differ in length, so a trailing word
        # has no common forward index.
        trailing = {
            back
            for back in range(1, _shared_run_length(label_tokens, reverse=True) + 1)
            if _is_word(first[-back])
        }
        if not leading and not trailing:
            continue

        shortened = [
            " ".join(
                t
                for i, t in enumerate(tokens)
                if i not in leading and len(tokens) - i not in trailing
            )
            for tokens in label_tokens
        ]
        # A fix that folds two series into one, or leaves a stub that can't
        # stand alone ("Region A" -> "A"), is no fix.
        if len(set(shortened)) != len(shortened) or any(
            len(label) < _MIN_REPEAT_LEN for label in shortened
        ):
            continue

        repeated = list(
            dict.fromkeys(
                [first[i] for i in sorted(leading)]
                + [first[-back] for back in sorted(trailing, reverse=True)]
            )
        )
        stated_in = next(
            (
                text
                for text in (chart.title, chart.y_label)
                if text
                and {_normalize_token(w) for w in repeated}
                <= {_normalize_token(w) for w in _tokens(text)}
            ),
            None,
        )
        # `color:` values are data, and a shared word is often part of the
        # name ("North America" / "South America"); only the chart restating
        # it proves it redundant. Wide measure aliases are the author's own.
        if stated_in is None and not chart.wide_measures:
            continue
        once = (
            f"{stated_in!r} already says it, so drop it from the labels."
            if stated_in is not None
            else "Say it once, in the chart title or `y_label`, not in every label."
        )

        warnings.append(
            Diagnostic.from_code(
                WARN_SERIES_LABELS_REPEAT_WORD,
                chart=chart_id,
                path=f"charts.{chart_id}.{authored_key}",
                message=WARN_SERIES_LABELS_REPEAT_WORD.message_template.format(
                    chart_id=chart_id,
                    authored_key=authored_key,
                    repeated=listed_phrase(repeated),
                    labels=listed_phrase(labels),
                ),
                fix=WARN_SERIES_LABELS_REPEAT_WORD.fix_template.format(
                    once=once,
                    shortened=listed_phrase(shortened),
                ),
            )
        )

    return warnings
