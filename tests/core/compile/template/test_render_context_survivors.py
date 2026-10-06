"""Pin the exact set of names reachable in a board template render context.

A function reached from a template context is one call away from executing
arbitrary code with whatever it closes over, and the sandbox cannot judge
what a context-injected callable *does* or *returns* once invoked — it only
intercepts ``getattr``/``getitem`` traversal (see ``test_jinja_sandbox.py``,
which pins that attribute traversal off ``BoardTemplateEnvironment``
callables is already blocked). Context contents are therefore a control held
here, by enumeration, not by the sandbox. This suite fails whenever a name is
added to a board template context, or to an environment's globals, filters,
or tests, without a matching update here, per ``compile/template/AGENTS.md``.

Each context dict is built inline at its render call site and never
returned, so the only way to see it is to intercept the render call itself
— reconstructing the context independently would assert this test's own
copy, not the real one.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import pytest
from jinja2.environment import Template

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.template.environment import BoardTemplateEnvironment
from dbt_charts.core.compile.template.jinja import (
    RESERVED_FILTER_HELPER_NAMES,
    _filter_date_range_helper,
    _filter_helper,
    _jinja_env,
    _jinja_env_lenient,
    resolve_jinja_template,
)
from dbt_charts.core.compile.template.labels_env import _format_filter, label_jinja_env
from dbt_charts.core.compile.template.parameterized import (
    make_filter_date_range_helper,
    make_filter_helper,
    render_parameterized,
)
from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.registered_views.expander import _TEMPLATE_ENV, render_template

# A stock instance of the one sandbox class every path in this file must
# build from — NOT jinja2.Environment(): SandboxedEnvironment.__init__
# itself rebinds `globals["range"]`, so diffing against a plain Environment
# would false-positive that rebinding as ours. Every namespace check below
# (globals, filters, tests) diffs against this one instance, so a rebind of
# an *existing* name (not just a new one) is always caught — the gap a
# name-only diff has, and exactly what `format` in labels_env.py does to
# Jinja's own builtin filter of the same name.
_BASELINE_ENV = BoardTemplateEnvironment()


def _new_or_overridden(
    mapping: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    """Names in `mapping` that are new, or resolve to a different object
    than in `baseline`. Survives a jinja2 upgrade adding/renaming a
    default, since nothing here pins the default set by name."""
    return {
        name: value
        for name, value in mapping.items()
        if name not in baseline or baseline[name] is not value
    }


class _RenderCall(NamedTuple):
    context: dict[str, Any]
    globals: dict[str, Any]
    filters: dict[str, Any]
    tests: dict[str, Any]

    @property
    def context_keys(self) -> frozenset[str]:
        return frozenset(self.context)

    @property
    def new_or_overridden_globals(self) -> dict[str, Any]:
        return _new_or_overridden(self.globals, _BASELINE_ENV.globals)

    @property
    def new_or_overridden_filters(self) -> dict[str, Any]:
        return _new_or_overridden(self.filters, _BASELINE_ENV.filters)

    @property
    def new_or_overridden_tests(self) -> dict[str, Any]:
        return _new_or_overridden(self.tests, _BASELINE_ENV.tests)


def _capture_render_calls(monkeypatch: pytest.MonkeyPatch) -> list[_RenderCall]:
    """Intercept every ``Template.new_context`` call.

    ``Template.render`` builds its context by calling ``self.new_context``;
    ``compile_expression``'s returned ``TemplateExpression.__call__`` (the
    ``where:`` evaluation path) calls ``self._template.new_context``
    directly, never ``Template.render`` — so hooking ``render`` alone would
    have a blind spot on that path. Hooking ``new_context`` covers both with
    one seam. ``self.environment`` is read from the actual instance driving
    that render — including for ``render_parameterized``, which builds a
    fresh ``BoardTemplateEnvironment`` per call rather than reusing a
    singleton, so there is no fixed instance to inspect ahead of a render.
    The full context dict is kept (not just its keys) so a test can also
    assert *which* object occupies a name, not only that the name is
    present.
    """
    captured: list[_RenderCall] = []
    original_new_context = Template.new_context

    def _spy(
        self: Template,
        vars: dict[str, Any] | None = None,
        shared: bool = False,
        locals: Any = None,
    ) -> Any:
        captured.append(
            _RenderCall(
                dict(vars or {}),
                dict(self.environment.globals),
                dict(self.environment.filters),
                dict(self.environment.tests),
            )
        )
        return original_new_context(self, vars, shared, locals)

    monkeypatch.setattr(Template, "new_context", _spy)
    return captured


class TestResolveJinjaTemplateContext:
    """compile/template/jinja.py: resolve_jinja_template's board-text context."""

    def test_context_with_queries_is_exactly_variables_plus_survivors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_render_calls(monkeypatch)
        resolve_jinja_template(
            "{{ region }}",
            variables={"region": "north"},
            queries={"orders": {"sql": "SELECT 1"}},
            strict=True,
        )
        [call] = captured
        assert call.context_keys == {"region", "queries", "filter", "filter_date_range"}
        assert call.new_or_overridden_globals == {}
        assert call.new_or_overridden_tests == {}

    def test_context_without_queries_omits_the_queries_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_render_calls(monkeypatch)
        resolve_jinja_template("{{ region }}", variables={"region": "north"})
        [call] = captured
        assert call.context_keys == {"region", "filter", "filter_date_range"}

    def test_bound_filter_helpers_replace_the_stubs_not_just_the_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A caller-bound filter_helpers mapping must actually replace the
        stub functions, not merely occupy the same two keys — a key-set-only
        assertion would stay green if ``context.update(filter_helpers)`` were
        deleted, since the stubs already occupy those names."""
        captured = _capture_render_calls(monkeypatch)
        dialect = get_dialect("postgres")
        bound_filter = make_filter_helper(lambda v: "$1", dialect)
        bound_filter_date_range = make_filter_date_range_helper(lambda v: "$1", dialect)
        resolve_jinja_template(
            "{{ region }}",
            variables={"region": "north"},
            strict=True,
            filter_helpers={
                "filter": bound_filter,
                "filter_date_range": bound_filter_date_range,
            },
        )
        [call] = captured
        assert call.context_keys == {"region", "filter", "filter_date_range"}
        assert call.context["filter"] is bound_filter
        assert call.context["filter_date_range"] is bound_filter_date_range

    def test_bound_filter_helpers_rejects_an_unreserved_name(self) -> None:
        """filter_helpers is not a general mechanism for adding a new context
        callable — only RESERVED_FILTER_HELPER_NAMES may be bound. Without
        this guard, a future caller widening the mapping (today's one
        production caller is execute/adapters/dbt_adapter.py) would pass
        every helper it names into every board-SQL render silently."""
        assert {"filter", "filter_date_range"} == RESERVED_FILTER_HELPER_NAMES
        with pytest.raises(CompilationError, match="filter_helpers"):
            resolve_jinja_template(
                "{{ region }}",
                variables={"region": "north"},
                strict=True,
                filter_helpers={"boom": lambda: "x"},
            )

    def test_singleton_environments_add_or_override_nothing(self) -> None:
        """jinja.py's two module-level environments are singletons, so a
        stray mutation in one test (e.g. registering a global) would leak
        into every other test in the process — cheap enough to pin
        directly rather than only through a render's own environment."""
        for env in (_jinja_env, _jinja_env_lenient):
            assert _new_or_overridden(env.globals, _BASELINE_ENV.globals) == {}
            assert _new_or_overridden(env.filters, _BASELINE_ENV.filters) == {}
            assert _new_or_overridden(env.tests, _BASELINE_ENV.tests) == {}


class TestRenderParameterizedContext:
    """compile/template/parameterized.py: the parameterized SQL render context."""

    def test_context_is_exactly_variables_plus_survivors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_render_calls(monkeypatch)
        render_parameterized(
            "SELECT * FROM t WHERE region = '{{ region }}'",
            variables={
                "region": "north",
                "queries": {"orders": {"sql": "SELECT 1"}},
            },
        )
        [call] = captured
        assert call.context_keys == {"region", "queries", "filter", "filter_date_range"}
        # render_parameterized builds a fresh BoardTemplateEnvironment per
        # call rather than reusing a singleton, so every check reads off the
        # live environment through the spy rather than a fixed instance.
        assert call.new_or_overridden_globals == {}
        assert call.new_or_overridden_filters == {}
        assert call.new_or_overridden_tests == {}

    def test_context_without_queries_variable_omits_the_queries_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_render_calls(monkeypatch)
        render_parameterized(
            "SELECT * FROM t WHERE region = '{{ region }}'",
            variables={"region": "north"},
        )
        [call] = captured
        assert call.context_keys == {"region", "filter", "filter_date_range"}


class TestLabelsEnvContext:
    """compile/template/labels_env.py + resolve/chart/label_data.py: chart-label
    templates and where: expressions.

    ``prepare_pie_label_data`` is the one production caller of
    ``prepare_label_data`` today; its ``context_extras`` adds ``percent``,
    ``value``, ``total``, ``color`` on top of the raw row and the three
    always-present index markers.
    """

    def test_environment_adds_no_global_and_overrides_only_format(self) -> None:
        env = label_jinja_env()
        assert _new_or_overridden(env.globals, _BASELINE_ENV.globals) == {}
        assert _new_or_overridden(env.filters, _BASELINE_ENV.filters) == {
            "format": _format_filter
        }
        assert _new_or_overridden(env.tests, _BASELINE_ENV.tests) == {}

    def test_format_filter_is_our_override_not_jinja_builtin_format(self) -> None:
        """``format`` is already a Jinja builtin (printf-style ``%``
        formatting) — labels_env replaces it with a d3-format callable of
        our own. Pinned by identity, since a name-diff can't see an
        override of an existing name."""
        assert label_jinja_env().filters["format"] is _format_filter
        assert BoardTemplateEnvironment().filters["format"] is not _format_filter

    def test_finalize_hook_renders_none_as_empty_not_the_word_none(self) -> None:
        """The registered finalize hook is itself a context-adjacent callable
        (runs on every rendered expression) — pin its one behavior change
        from Jinja's default (str(None) -> "None")."""
        assert label_jinja_env().from_string("{{ value }}").render(value=None) == ""

    def test_pie_label_context_is_exactly_row_plus_index_markers_plus_pie_extras(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.compile.models.style.theme import (
            LabelsDefaultTemplate,
            SliceLabelsStyle,
        )
        from dbt_charts.core.compile.resolve.chart.label_data import (
            prepare_pie_label_data,
        )

        captured = _capture_render_calls(monkeypatch)
        labels = SliceLabelsStyle(
            offset=1,
            line_height=1,
            default_template=LabelsDefaultTemplate(
                with_color="{{ color }}", no_color="{{ value }}"
            ),
            template="{{ segment }}: {{ percent }}",
            where=None,
        )
        prepare_pie_label_data(
            theta_field="value",
            color_field="segment",
            data=[{"segment": "A", "value": 60}],
            labels=labels,
        )
        expected_keys = {
            "segment",
            "value",
            "index",
            "is_first",
            "is_last",
            "percent",
            "total",
            "color",
        }
        [call] = captured
        assert call.context_keys == expected_keys

    def test_where_expression_context_matches_the_template_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``where:`` expression is evaluated through
        ``compile_expression``'s returned ``TemplateExpression``, a
        different Jinja entry point from ``template.render`` — hooking
        ``Template.new_context`` (see ``_capture_render_calls``) covers it
        too, rather than trusting that both call sites share one dict."""
        from dbt_charts.core.compile.models.style.theme import (
            LabelsDefaultTemplate,
            SliceLabelsStyle,
        )
        from dbt_charts.core.compile.resolve.chart.label_data import (
            prepare_pie_label_data,
        )

        captured = _capture_render_calls(monkeypatch)
        labels = SliceLabelsStyle(
            offset=1,
            line_height=1,
            default_template=LabelsDefaultTemplate(
                with_color="{{ color }}", no_color="{{ value }}"
            ),
            template="{{ segment }}",
            where="{{ percent > 0 }}",
        )
        prepare_pie_label_data(
            theta_field="value",
            color_field="segment",
            data=[{"segment": "A", "value": 60}],
            labels=labels,
        )
        expected_keys = {
            "segment",
            "value",
            "index",
            "is_first",
            "is_last",
            "percent",
            "total",
            "color",
        }
        # A truthy where: still renders the template, so both the where_expr
        # call and the template.render call fire — both must carry the
        # identical key set.
        assert len(captured) == 2
        assert {call.context_keys for call in captured} == {frozenset(expected_keys)}


class TestExpanderTemplateEnvContext:
    """registered_views/expander.py: the [[ ]] / [% %] registered-view renderer.

    Named explicitly as a ``BoardTemplateEnvironment`` consumer in
    ``compile/template/environment.py`` (neither of its two stated
    exemptions applies here — this site renders user-reachable path params
    and query results, it does not only parse), so it is in scope for the
    same enumeration discipline as the board-SQL paths above.
    """

    def test_environment_adds_exactly_the_four_view_helper_globals(self) -> None:
        from dbt_charts.core.registered_views.expander import (
            _column_header_styles,
            _pivot_column_profiles,
            _plan_key_variables,
            _plan_variables,
        )

        added = _new_or_overridden(_TEMPLATE_ENV.globals, _BASELINE_ENV.globals)
        assert added == {
            "plan_variables": _plan_variables,
            "plan_key_variables": _plan_key_variables,
            "pivot_column_profiles": _pivot_column_profiles,
            "column_header_styles": _column_header_styles,
        }

    def test_environment_adds_or_overrides_no_filter(self) -> None:
        assert _new_or_overridden(_TEMPLATE_ENV.filters, _BASELINE_ENV.filters) == {}

    def test_environment_adds_or_overrides_no_test(self) -> None:
        assert _new_or_overridden(_TEMPLATE_ENV.tests, _BASELINE_ENV.tests) == {}

    def test_render_context_is_exactly_path_queries_and_sql_identifier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_render_calls(monkeypatch)
        render_template(
            "[[ path.source ]]", path_params={"source": "sf"}, query_results={}
        )
        [call] = captured
        assert call.context_keys == {"path", "queries", "sql_identifier"}


class TestSurvivorsValidateAndReturnStrings:
    """Every context-injected callable we register validates its own
    arguments and returns a plain string — never an object a template can
    keep traversing. This is the shape that makes enumeration sufficient: a
    survivor that returned a traversable object would need its own review,
    not just a name on this list. (Jinja's own defaults are exempt — see
    ``_BASELINE_ENV``'s docstring above.)
    """

    def test_filter_stub_raises_a_coded_error_rather_than_leaking_anything(
        self,
    ) -> None:
        with pytest.raises(CompilationError):
            _filter_helper("region", "north")
        with pytest.raises(CompilationError):
            _filter_date_range_helper("region", "2024-01-01", "2024-01-31")

    def test_real_filter_helper_returns_string_and_validates_column(self) -> None:
        helper = make_filter_helper(lambda v: "$1", get_dialect("postgres"))
        result = helper("region", "north")
        assert isinstance(result, str)
        with pytest.raises(ValueError, match="Invalid column name"):
            helper("region; DROP TABLE x", "north")

    def test_real_filter_date_range_helper_returns_string_and_validates_column(
        self,
    ) -> None:
        from datetime import date as _date

        helper = make_filter_date_range_helper(lambda v: "$1", get_dialect("postgres"))
        result = helper("created_at", [_date(2024, 1, 1), _date(2024, 1, 31)])
        assert isinstance(result, str)
        with pytest.raises(ValueError, match="Invalid column name"):
            helper(
                "created_at; DROP TABLE x",
                [_date(2024, 1, 1), _date(2024, 1, 31)],
            )

    def test_format_filter_returns_string(self) -> None:
        env = label_jinja_env()
        result = env.from_string("{{ value | format('$,.0f') }}").render(value=1000)
        assert isinstance(result, str)
        assert result == "$1,000"

    def test_expander_view_helpers_return_plain_data_not_objects(self) -> None:
        """Not literally strings (lists/dicts of strings), but the same
        discipline: plain data a template can iterate/index, never a
        proxy or partial with methods of its own. Exercised with a real row
        so the assertion depends on the helper's own logic, not an
        empty-input identity ([] in, [] out regardless of the body)."""
        from dbt_charts.core.registered_views.expander import (
            _column_header_styles,
            _pivot_column_profiles,
            _plan_key_variables,
            _plan_variables,
        )

        variables = _plan_variables([{"name": "region", "actual_type": "string"}])
        assert variables == [{"name": "region", "input": "text"}]
        assert all(isinstance(v, dict) for v in variables)

        key_variables = _plan_key_variables([{"name": "id", "actual_type": "integer"}])
        assert key_variables == [{"name": "id", "input": "text"}]

        profiles = _pivot_column_profiles([{"name": "region"}], [["Name", "name"]])
        assert profiles == [["Name", "region"]]
        assert all(isinstance(row, list) for row in profiles)

        styles = _column_header_styles(["region"], "src", "sch", "tbl")
        assert styles == {
            "Attribute": {"align": "right"},
            "region": {"header_link": "/inspector/src/sch/tbl/region/"},
        }

    def test_query_namespace_attribute_access_returns_a_query_proxy_with_no_extra_surface(
        self,
    ) -> None:
        """queries.X isn't a plain string itself (it stringifies to one via
        __str__), but its attribute access is mediated by the sandbox's own
        getattr interception, unlike a bare context callable — so it does
        not need the same string-return discipline the callables above do.
        Its own public surface is pinned directly (``dir()``, not just
        ``isinstance``) so a future added public attribute is deliberate.

        This applies to ``resolve_jinja_template``'s ``queries`` (always a
        ``_QueryNamespace``); ``render_parameterized`` instead passes
        ``variables["queries"]`` through verbatim, so its shape there is
        whatever the caller put there — see AGENTS.md.
        """
        from dbt_charts.core.compile.template._helpers import (
            _QueryNamespace,
            _QueryProxy,
        )

        namespace = _QueryNamespace({"orders": {"sql": "SELECT 1"}})
        proxy = namespace.orders
        assert isinstance(proxy, _QueryProxy)
        assert str(proxy) == "(SELECT 1) AS orders"
        assert {name for name in dir(proxy) if not name.startswith("_")} == {"cache"}
