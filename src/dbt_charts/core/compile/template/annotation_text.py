"""Annotation text: `{{ value }}`, `{{ series }}` and a `format` filter whose spec is optional."""

from __future__ import annotations

from collections.abc import Callable

from jinja2 import StrictUndefined, TemplateError, meta

from dbt_charts.core.compile.format_value import (
    format_table_cell_value,
    format_value,
)
from dbt_charts.core.compile.models.primitives import FormatTable, ResolvedFormat
from dbt_charts.core.compile.template.environment import BoardTemplateEnvironment
from dbt_charts.core.compile.template.labels_env import _finalize_none

AnnotationValue = float | str | None


class AnnotationTextError(ValueError):
    """An annotation's text template cannot render."""


def annotation_text_env(
    number_format: ResolvedFormat | None, formats: FormatTable | None
) -> BoardTemplateEnvironment:
    """The env annotation text renders in; `format` overrides Jinja's builtin."""
    env = BoardTemplateEnvironment(
        undefined=StrictUndefined, autoescape=False, finalize=_finalize_none
    )
    env.filters["format"] = _format_filter(number_format, formats)
    return env


def render_annotation_text(
    text: str,
    value: AnnotationValue,
    series: str | None,
    colored: bool,
    number_format: ResolvedFormat | None,
    formats: FormatTable | None,
) -> str:
    """Render `text`; raises ``AnnotationTextError`` naming what went wrong.

    ``colored`` is whether the chart has a color channel: without one, `series`
    names nothing.
    """
    env = annotation_text_env(number_format, formats)
    try:
        if not colored and "series" in meta.find_undeclared_variables(env.parse(text)):
            raise AnnotationTextError(f"text {text!r}: series needs a chart with color")
        rendered = env.from_string(text).render(value=value, series=series)
    except AnnotationTextError:
        raise
    # ValueError: the format filter's bad spec or missing value, raised mid-render.
    except (TemplateError, ValueError) as exc:
        raise AnnotationTextError(f"text {text!r}: {exc}") from exc
    if "\n" in rendered or "\r" in rendered:
        raise AnnotationTextError(f"text {text!r} renders a line break")
    return rendered


def _format_filter(
    number_format: ResolvedFormat | None, formats: FormatTable | None
) -> Callable[..., str]:
    """`{{ value | format }}` prints a number in the y axis's format and a date
    in the board's date format; `format('<spec>')` names its own."""

    def format_annotation(value: AnnotationValue, spec: str | None = None) -> str:
        if value is None:
            raise ValueError(
                "format has no value: this annotation has no single anchor value"
            )
        if isinstance(value, str):
            return format_table_cell_value(value, spec, formats)
        return format_value(value, number_format if spec is None else spec, formats)

    return format_annotation
