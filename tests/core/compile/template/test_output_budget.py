"""Tests for the render-scoped cumulative output budget.

Covers the primitive (dbt_charts.core.compile.template.output_budget) and its
wiring into resolve_jinja_template (jinja.py) and render_parameterized
(parameterized.py) — the two highest-traffic Jinja render call sites, called
roughly fifteen times per board across compile/normalize/render/execute. A
third authored-template render site (compile/resolve/chart/label_data.py)
draws from the same shared budget directly; covered separately where it's
exercised, not repeated here.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor

import pytest

from dbt_charts.core.compile.errors import TemplateOutputTooLargeError
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.compile.template.output_budget import (
    TemplateOutputBudgetExceeded,
    draw_down_template_output,
    template_output_budget,
)
from dbt_charts.core.compile.template.parameterized import render_parameterized

# {% for i in range(99999) %}{% for j in range(99999) %}x{% endfor %}{% endfor %}
# is individually under Jinja's MAX_RANGE sandbox cap (100000) on each range(),
# but the product is 10**10 emitted characters.
_AMPLIFICATION_PAYLOAD = (
    "{% for i in range(99999) %}{% for j in range(99999) %}x{% endfor %}{% endfor %}"
)


def _loop_template(n: int) -> str:
    """A template with real Jinja syntax that emits exactly `n` literal
    characters — needed because a plain literal string with no `{{`/`{%`
    takes resolve_jinja_template's/render_parameterized's fast no-op path
    and never reaches the render call the budget guards."""
    return f"{{% for i in range({n}) %}}x{{% endfor %}}"


class TestDrawDownPrimitive:
    def test_no_op_when_no_budget_open(self) -> None:
        # Callers outside a render scope (e.g. a unit test exercising
        # resolve_jinja_template in isolation) render unbounded — no scope,
        # no bound. Mirrors record_table_overflow's no-op-when-unopened.
        draw_down_template_output(10**9)  # must not raise

    def test_raises_once_ceiling_crossed(self) -> None:
        with template_output_budget(100):
            draw_down_template_output(60)
            with pytest.raises(TemplateOutputBudgetExceeded):
                draw_down_template_output(60)

    def test_cumulative_across_many_small_draws(self) -> None:
        with template_output_budget(100):
            for _ in range(9):
                draw_down_template_output(10)  # 90 total, still under
            with pytest.raises(TemplateOutputBudgetExceeded):
                draw_down_template_output(20)  # 110 total, over

    def test_scope_resets_on_exit(self) -> None:
        with template_output_budget(100):
            draw_down_template_output(90)
        # A fresh scope starts its own budget, not a continuation of the last.
        with template_output_budget(100):
            draw_down_template_output(90)  # must not raise

    def test_reentering_an_open_scope_raises(self) -> None:
        with (
            template_output_budget(100),
            pytest.raises(RuntimeError, match="already open"),
            template_output_budget(100),
        ):
            pass


class TestBudgetCrossesThreadBoundary:
    """A ContextVar does not cross ThreadPoolExecutor.submit on its own — the
    open scope's binding stays with the thread that opened it unless the
    submitter explicitly hands a copy across. execute/run.py already
    does this (`pool.submit(contextvars.copy_context().run, fn, ...)`) for
    query execution, so a budget opened around a board render still holds for
    query-template rendering that happens off the main thread. This proves
    the mechanism itself, isolated from Executor's own caching/threading."""

    def test_copied_context_shares_the_open_budget(self) -> None:
        with template_output_budget(100):
            draw_down_template_output(60)
            ctx = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(ctx.run, draw_down_template_output, 60)
                with pytest.raises(TemplateOutputBudgetExceeded):
                    future.result()


class TestResolveJinjaTemplateBudget:
    """The nested-loop payload must raise a coded error without the process
    ever materializing the full 10**10-character output."""

    def test_amplification_payload_raises_without_materializing(self) -> None:
        with (
            template_output_budget(1000),
            pytest.raises(TemplateOutputTooLargeError) as exc_info,
        ):
            resolve_jinja_template(_AMPLIFICATION_PAYLOAD, {})

        # Streaming stops within a few chunks of the ceiling, not after
        # 10**10 characters: bounded peak, not the magic ceiling value itself.
        # A disengaged budget would hang here rather than fail this assert.
        assert exc_info.value.fields["emitted_bytes"] < 1000 * 10

    def test_small_template_under_ceiling_is_unaffected(self) -> None:
        with template_output_budget(1000):
            assert resolve_jinja_template("Hello {{ name }}", {"name": "World"}) == (
                "Hello World"
            )

    def test_cumulative_across_many_calls_one_scope(self) -> None:
        """Distinguishes a real render-scoped bound from a per-field cap:
        many renders each individually under the ceiling must still trip it
        in aggregate."""
        with template_output_budget(100):
            for _ in range(5):
                resolve_jinja_template(_loop_template(15), {})  # 75 total
            with pytest.raises(TemplateOutputTooLargeError):
                resolve_jinja_template(_loop_template(30), {})  # 105 total, over


class TestRenderParameterizedSharesTheSameBudget:
    """jinja.py and parameterized.py both draw down the same render-scoped
    budget."""

    def test_cumulative_across_both_call_sites(self) -> None:
        # parameterized.py turns a plain `{{ variable }}` into a placeholder
        # (not the value text), so the growth vector there is template
        # structure — a loop emitting literal text — not variable values.
        with template_output_budget(100):
            resolve_jinja_template(_loop_template(50), {})  # 50 bytes
            with pytest.raises(TemplateOutputTooLargeError):
                render_parameterized(_loop_template(60), {})  # 50 + 60 = 110, over

    def test_amplification_payload_via_parameterized_raises(self) -> None:
        with (
            template_output_budget(1000),
            pytest.raises(TemplateOutputTooLargeError) as exc_info,
        ):
            render_parameterized(_AMPLIFICATION_PAYLOAD, {})
        assert exc_info.value.fields["emitted_bytes"] < 1000 * 10
