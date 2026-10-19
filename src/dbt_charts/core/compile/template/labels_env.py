"""Jinja env for chart-label templates and ``where:`` expressions.

Lives in compile/ so validators in both theme models (marks.py) and resolved
types (resolved/pie.py) can parse templates without reaching into the render
layer (the dependency direction stays compile → shared, render → shared).
"""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined

from dbt_charts.core.compile.template.environment import BoardTemplateEnvironment
from dbt_charts.core.text.format_d3 import format_d3


def label_jinja_env() -> BoardTemplateEnvironment:
    """Return the Jinja env used to compile and render label templates.

    Strict-undefined: a typo like ``{{ pct }}`` (instead of ``percent``)
    raises immediately rather than silently rendering empty. ``finalize``
    renders a Python ``None`` (e.g. a NULL category value, or a chart that
    has no identity field bound at all) as empty text instead of Jinja's
    default ``str(None)`` -> the literal word "None".
    """
    env = BoardTemplateEnvironment(
        undefined=StrictUndefined,
        autoescape=False,
        finalize=_finalize_none,
    )
    env.filters["format"] = _format_filter
    return env


def _finalize_none(value: Any) -> Any:
    """Jinja ``finalize`` hook: render ``None`` as empty text, not "None"."""
    return "" if value is None else value


def _format_filter(
    value: Any,  # type-state: explicit_any — any row value
    spec: str,
) -> str:
    """Jinja filter: ``{{ value | format('$,.0f') }}`` → d3-formatted."""
    return format_d3(value, spec)


def validate_label_template(value: str | None) -> str | None:
    """Pydantic-validator body: parse a Jinja2 label template string."""
    if value is None:
        return value
    try:
        label_jinja_env().parse(value)
    except Exception as exc:
        raise ValueError(f"Invalid Jinja template: {exc}") from exc
    return value


def validate_label_where(value: str) -> str:
    """Pydantic-validator body: compile a Jinja2 ``where:`` expression."""
    try:
        label_jinja_env().compile_expression(strip_jinja_braces(value))
    except Exception as exc:
        raise ValueError(f"Invalid Jinja expression: {exc}") from exc
    return value


def strip_jinja_braces(expr: str) -> str:
    """Accept ``{{ x }}`` or bare ``x`` for ``where:`` — return the bare expression.

    Authors naturally write ``where: "{{ value > 5 }}"`` (matching the
    ``template:`` syntax), but Jinja's ``compile_expression`` wants the
    bare expression (``value > 5``). Tolerate the single-outer-pair case;
    reject mixed forms (``"{{ a }} and {{ b }}"`` etc.) loudly.
    """
    s = expr.strip()
    if not (s.startswith("{{") and s.endswith("}}")):
        return s
    inner = s[2:-2]
    if "{{" in inner or "}}" in inner:
        raise ValueError(
            "where: expression contains nested {{ ... }}; use a bare Python "
            "expression instead (e.g. ``value > 5`` not "
            "``{{ value > 5 }} and {{ other }}``)."
        )
    return inner.strip()
