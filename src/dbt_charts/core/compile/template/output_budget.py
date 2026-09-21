"""Render-scoped cumulative bound on Jinja-emitted template output.

Jinja's sandbox caps `range()` at `MAX_RANGE` (100000) but has no built-in
guard on total emitted output — its own docs disclaim this: "It is possible
to construct a relatively small template that renders to a very large amount
of output." A template with two nested `range(99999)` loops emits 10**10
characters while each `range()` individually stays under the cap.

A per-field cap does not close this: `resolve_jinja_template()` /
`render_parameterized()` are called roughly fifteen times per board (compile,
normalize, render, execute), so five hundred fields each just under a
per-field cap still blow up. The bound here is cumulative per board render
instead — a single budget every templated field in one render draws down,
opened once around the render and reset on exit. This module is the sink;
`compile/config.py`'s `resolve_max_template_output_bytes()` resolves the
ceiling it is opened with, the same project-config/env-ceiling/`min()`
pattern as `resolve_max_result_bytes()` and friends.

**Deliberate deviation from those siblings**: `max_rows`/`max_result_bytes`
truncate with a warning — a truncated query result is still a usable answer
with a caveat. A truncated SVG (or truncated SQL) is a corrupt document, so
this raises a hard, coded error the moment the ceiling is crossed instead of
returning a silently-cut result.

**Scope — read before extending.** This bounds emitted *output* only. It does
not and cannot bound CPU time, wall clock, or the memory held by an
intermediate expression value: `Template.generate()` yields per template
*node*, not per byte, so a loop body streams (caught here, cheaply) but a
single large expression — `{{ 'a'*10**7 }}` — is yielded as one
already-materialized 10MB chunk, and `{{ ('a'*10**7)|length }}` allocates
10MB while emitting 8 bytes, invisible to any output-byte cap. Those
exhaustion classes need a process boundary, not a budget over emitted bytes.

**Coverage — this is not opened on every path that renders a template.**
The one production opener is `render_dashboard()` (`core/board.py`), so the
scope is live for a full board render: Cloud, the CLI, and MCP. It is NOT
live for anything that only compiles or validates a board — `dct serve`'s
own pre-render compile step (including a nested board's `file:` path,
itself a `resolve_jinja_template()` call in `compile/normalize/layout.py`),
Cloud's compile-only request paths (`apps/cloud/apps/dashboards/service.py`,
`board_index.py`), a host's own authorizing compile run before it ever hands
a `compile_result` to `render_dashboard()` (Cloud's
`yaml_content_render.authorize_yaml_content_render`, whose compile templates
the same board text `render_dashboard()` goes on to render from that same
result), and `agent_api/query.py`'s `lookup_board_query_sql` — nor for the
registered-view template pipeline
(`registered_views/render_pipeline.py`), a separate top-level entry point
that never calls `render_dashboard` at all. This list is not exhaustive;
none of these are a regression — they were exactly as unbounded before this
module existed — but don't assume "opens the budget" from "renders a
template" anywhere outside `render_dashboard()` itself.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import jinja2


class TemplateOutputBudgetExceeded(Exception):
    """Cumulative emitted template output crossed the open render's ceiling.

    Raised by `draw_down_template_output()`; every caller of
    `render_with_budget()` (`compile/template/jinja.py`,
    `compile/template/parameterized.py`, `compile/resolve/chart/
    label_data.py`) catches this and re-raises `TemplateOutputTooLargeError`.
    Left as a plain exception (not a `DbtChartsError`) so this module stays
    free of the diagnostics layer — coding the error is the call sites' job.
    """

    def __init__(self, *, emitted_bytes: int, ceiling: int) -> None:
        self.emitted_bytes = emitted_bytes
        self.ceiling = ceiling
        super().__init__(
            f"template output exceeded {ceiling} bytes "
            f"(emitted {emitted_bytes} bytes before stopping)"
        )


class _Budget:
    """Mutable draw-down counter for one open render scope.

    `draw_down` locks around the read-modify-write: the query executor's
    `ThreadPoolExecutor` (`execute/parallel.py`) runs several queries'
    templates concurrently against this same instance (a budget is
    render-scoped, not per-thread), and an unlocked `self.consumed += nbytes`
    can lose an update under interleaving, undercounting the total and
    overshooting the ceiling.
    """

    __slots__ = ("ceiling", "consumed", "_lock")

    def __init__(self, ceiling: int) -> None:
        self.ceiling = ceiling
        self.consumed = 0
        self._lock = threading.Lock()

    def draw_down(self, nbytes: int) -> None:
        with self._lock:
            self.consumed += nbytes
            over = self.consumed > self.ceiling
        if over:
            raise TemplateOutputBudgetExceeded(
                emitted_bytes=self.consumed, ceiling=self.ceiling
            )


# The one open budget, if any. A single nullable slot: render_dashboard is
# the only opener and nothing re-enters it today (a nested board render runs
# inside the single already-open scope — see render_nested_board).
_budget: contextvars.ContextVar[_Budget | None] = contextvars.ContextVar(
    "template_output_budget", default=None
)


@contextmanager
def template_output_budget(ceiling: int) -> Generator[None]:
    """Open a fresh cumulative output budget for the duration of one board
    render.

    Every `resolve_jinja_template()` / `render_parameterized()` call inside
    this scope draws down the same `ceiling`-byte budget; the instant the
    cumulative total crosses it, the next chunk raises
    `TemplateOutputBudgetExceeded` instead of continuing to emit. Pops the
    scope on exit (even on error) so sibling renders never inherit a spent or
    partially-drawn budget.

    Raises `RuntimeError` if a scope is already open. Re-entering isn't a
    real case today (see the module-level `_budget` comment); failing loudly
    here beats silently picking a behavior — reuse the outer budget, or open
    an isolated inner one — for whichever future caller nests, without that
    caller having said which one it wants.
    """
    if _budget.get() is not None:
        raise RuntimeError(
            "template_output_budget() is already open — nested board renders "
            "must draw against the same budget as their parent; decide how "
            "before adding a caller that nests"
        )
    token = _budget.set(_Budget(ceiling))
    try:
        yield
    finally:
        _budget.reset(token)


def render_with_budget(
    jinja_template: jinja2.Template,
    context: dict[str, Any],  # type-state: explicit_any — runtime value
) -> str:
    """`jinja_template.render(context)`, but streamed through `.generate()` so
    the open budget (if any) is drawn down per chunk instead of after the
    full output is already materialized.

    Output-for-output, identical to `.render()` — this just joins the same
    chunks `.render()` would concatenate internally. The difference is
    timing: for the incremental-amplification shape this module exists to
    catch (a small template whose *product* of nested loops is enormous),
    `.generate()` yields one chunk per loop iteration, so
    `draw_down_template_output()` raises after a few kilobytes instead of
    after the loop finishes materializing everything. Exception semantics
    match `.render()` too — both `Template.render()` and `Template.generate()`
    route through the same `root_render_func()` and the same
    `handle_exception()` on failure, so a syntax/undefined/runtime error
    surfaces at the same point in either form.
    """
    chunks: list[str] = []
    for chunk in jinja_template.generate(context):
        draw_down_template_output(len(chunk.encode("utf-8", errors="surrogatepass")))
        chunks.append(chunk)
    return "".join(chunks)


def draw_down_template_output(nbytes: int) -> None:
    """Charge `nbytes` of emitted template output against the open budget.

    No-op when no `template_output_budget()` scope is open. `ContextVar`
    propagates into a worker thread only via an explicit
    `contextvars.copy_context().run(...)` at submit time; the query
    executor's `ThreadPoolExecutor.submit` already does this (see
    `execute/parallel.py`), so a budget opened around a board render still
    holds for query-template rendering that happens off the main thread.
    """
    budget = _budget.get()
    if budget is not None:
        budget.draw_down(nbytes)
