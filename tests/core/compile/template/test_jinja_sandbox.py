"""Regression tests: board Jinja must render inside a sandboxed environment.

A plain jinja2.Environment lets standard SSTI payloads reach __globals__,
__class__.__mro__, and Jinja's own cycler/joiner/namespace/lipsum globals.
This suite pins the fix at every renderer of user-authored board text, and
pins that it does not fail closed on real board features.

Failure mode is not uniform, which is why the corpus is split rather than
asserted against one exception type. Under StrictUndefined every blocked
chain surfaces as SecurityError, because StrictUndefined's __str__ re-raises
the sandbox's stored exception on render. Under the lenient undefined used
for interactive editing, only a chain ending in a *call* raises (lenient
__call__ is not overridden); a chain that only chases attributes renders ''.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from jinja2.exceptions import SecurityError
from typer.testing import CliRunner

from dbt_charts.cli.main import app
from dbt_charts.core.compile.errors import JinjaError
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.compile.template.labels_env import label_jinja_env
from dbt_charts.core.compile.template.parameterized import render_parameterized
from dbt_charts.core.compile.template.query_result import QueryResult
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.registered_views.expander import render_template

runner = CliRunner()

# Split by how the sandbox stops each one: an attribute-only chain renders as
# Undefined; a chain ending in a call raises SecurityError on any undefined
# class, because __call__ is never given lenient/silent handling.
ATTRIBUTE_ONLY_PAYLOADS: dict[str, str] = {
    "cycler_globals": "{{ cycler.__init__.__globals__ }}",
    "joiner_globals": "{{ joiner.__init__.__globals__ }}",
    "namespace_globals": "{{ namespace.__init__.__globals__ }}",
    "lipsum_globals": "{{ lipsum.__globals__ }}",
    "attr_hex_escape": "{{ filter|attr('\\x5f\\x5fglobals\\x5f\\x5f') }}",
    "attr_join_bypass": "{{ filter|attr(['_'*2,'globals','_'*2]|join) }}",
    "attr_set_indirection": (
        "{% set a = '__' ~ 'globals' ~ '__' %}{{ filter|attr(a) }}"
    ),
}

CALL_ENDING_PAYLOADS: dict[str, str] = {
    "helper_globals_builtins": (
        "{{ filter.__globals__['__builtins__']['__import__']"
        "('os').popen('id -un').read() }}"
    ),
    "class_mro_subclasses": "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "for_subclasses_warning": (
        "{% for x in ().__class__.__base__.__subclasses__() %}"
        '{% if "warning" in x.__name__ %}'
        "{{ x()._module.__builtins__['__import__']('os').popen('id').read() }}"
        "{% endif %}{% endfor %}"
    ),
}

ALL_PAYLOADS: dict[str, str] = {**ATTRIBUTE_ONLY_PAYLOADS, **CALL_ENDING_PAYLOADS}


def _filter_stub(*args: object, **kwargs: object) -> str:
    """Stand-in for the injected filter() helper the payloads traverse."""
    return ""


class TestResolveJinjaTemplateSandbox:
    """compile/template/jinja.py: the main board-text render path."""

    @pytest.mark.parametrize("payload", ALL_PAYLOADS.values(), ids=ALL_PAYLOADS.keys())
    def test_strict_blocks_escape_corpus(self, payload: str) -> None:
        """StrictUndefined re-raises the sandbox's SecurityError on render, so
        every shape in the corpus surfaces the same way in strict mode."""
        with pytest.raises(JinjaError) as exc_info:
            resolve_jinja_template(payload, variables={}, queries={}, strict=True)
        assert isinstance(exc_info.value.__cause__, SecurityError)

    @pytest.mark.parametrize(
        "payload", ATTRIBUTE_ONLY_PAYLOADS.values(), ids=ATTRIBUTE_ONLY_PAYLOADS.keys()
    )
    def test_lenient_attribute_only_never_leaks_dangerous_object(
        self, payload: str
    ) -> None:
        """The lenient undefined swallows a blocked attribute chain silently —
        it must render empty, never the globals dict or class list it reached
        for."""
        result = resolve_jinja_template(payload, variables={}, queries={}, strict=False)
        assert result == ""

    @pytest.mark.parametrize(
        "payload", CALL_ENDING_PAYLOADS.values(), ids=CALL_ENDING_PAYLOADS.keys()
    )
    def test_lenient_call_ending_still_raises(self, payload: str) -> None:
        """A call at the end of a blocked chain raises even under the lenient
        undefined, because it does not override __call__."""
        with pytest.raises(JinjaError) as exc_info:
            resolve_jinja_template(payload, variables={}, queries={}, strict=False)
        assert isinstance(exc_info.value.__cause__, SecurityError)


class TestBoardTextQueryResultSandbox:
    """query_results= exposes QueryResult/QueryRow; only data attributes survive."""

    @pytest.mark.parametrize(
        "payload",
        [
            "{{ queries.q.__class__ }}",
            "{{ queries.q.one.__class__ }}",
            "{{ queries.q.__init__.__globals__ }}",
        ],
    )
    def test_dunder_access_renders_empty(self, payload: str) -> None:
        result = resolve_jinja_template(
            payload,
            strict=False,
            query_results={"q": QueryResult("q", [{"x": 1}])},
            format_cell=lambda value, spec: str(value),
        )
        assert result == ""

    @pytest.mark.parametrize(
        "payload",
        [
            "{{ queries.q|attr('_rows') }}",
            "{{ queries.q.rows[0]|attr('_cells') }}",
            "{{ queries|attr('_results') }}",
        ],
    )
    def test_private_state_access_raises(self, payload: str) -> None:
        with pytest.raises(ChartDataError) as exc:
            resolve_jinja_template(
                payload,
                strict=False,
                query_results={"q": QueryResult("q", [{"x": 1}])},
                format_cell=lambda value, spec: str(value),
            )
        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-TEXT-QUERY-ACCESS"


class TestResolveJinjaTemplatePositiveControls:
    """The sandbox swap must not fail closed on real board features."""

    def test_queries_ref_still_renders_aliased_subquery(self) -> None:
        result = resolve_jinja_template(
            "{{ queries.orders }}",
            variables={},
            queries={"orders": {"sql": "SELECT 1"}},
            strict=True,
        )
        assert result == "(SELECT 1) AS orders"

    def test_queries_cache_ref_still_renders_sentinel(self) -> None:
        result = resolve_jinja_template(
            "{{ queries.orders.cache }}",
            variables={},
            queries={"orders": {"sql": "SELECT 1"}},
            strict=True,
        )
        assert result == "__dct_cache_ref__orders__"

    def test_plain_variable_and_filter_chain_still_work(self) -> None:
        result = resolve_jinja_template(
            "{{ name | upper | trim }}", variables={"name": " world "}, strict=True
        )
        assert result == "WORLD"

    def test_for_if_and_concat_still_work(self) -> None:
        result = resolve_jinja_template(
            "{% for x in items %}{% if x > 1 %}{{ x ~ ',' }}{% endif %}{% endfor %}",
            variables={"items": [1, 2, 3]},
            strict=True,
        )
        assert result == "2,3,"

    def test_attribute_access_on_real_object_still_works(self) -> None:
        result = resolve_jinja_template(
            "{{ d.year }}", variables={"d": date(2024, 1, 1)}, strict=True
        )
        assert result == "2024"

    def test_lenient_missing_deep_chain_still_renders_empty(self) -> None:
        result = resolve_jinja_template(
            "{{ missing.deep.chain }}", variables={}, strict=False
        )
        assert result == ""

    def test_dict_subscript_and_loop_index_still_work(self) -> None:
        result = resolve_jinja_template(
            "{% for x in items %}{{ loop.index }}:{{ x.name }} {% endfor %}",
            variables={"items": [{"name": "a"}, {"name": "b"}]},
            strict=True,
        )
        assert result == "1:a 2:b "


class TestRenderParameterizedSandbox:
    """compile/template/parameterized.py: the parameterized SQL render path.

    A blocked chain must surface as a coded JinjaError, not a raw
    SecurityError: SecurityError is a TemplateRuntimeError sibling of
    UndefinedError, so an uncaught one reaches the executor uncoded and stamps
    ERR-INTERNAL — which this repo treats as a bug, not an author-facing error.
    """

    @pytest.mark.parametrize("payload", ALL_PAYLOADS.values(), ids=ALL_PAYLOADS.keys())
    def test_strict_blocks_escape_corpus(self, payload: str) -> None:
        with pytest.raises(JinjaError) as exc_info:
            render_parameterized(payload, variables={}, strict=True)
        assert isinstance(exc_info.value.__cause__, SecurityError)

    @pytest.mark.parametrize(
        "payload", ATTRIBUTE_ONLY_PAYLOADS.values(), ids=ATTRIBUTE_ONLY_PAYLOADS.keys()
    )
    def test_lenient_attribute_only_never_leaks_dangerous_object(
        self, payload: str
    ) -> None:
        result = render_parameterized(payload, variables={}, strict=False)
        assert result.sql == ""

    @pytest.mark.parametrize(
        "payload", CALL_ENDING_PAYLOADS.values(), ids=CALL_ENDING_PAYLOADS.keys()
    )
    def test_lenient_call_ending_still_raises(self, payload: str) -> None:
        with pytest.raises(JinjaError) as exc_info:
            render_parameterized(payload, variables={}, strict=False)
        assert isinstance(exc_info.value.__cause__, SecurityError)

    def test_positive_control_parameterizes_value(self) -> None:
        result = render_parameterized(
            "SELECT * FROM orders WHERE region = '{{ region }}'",
            variables={"region": "North"},
            profile_type="postgres",
        )
        assert result.params == ["North"]
        assert "$1" in result.sql


class TestLabelsEnvSandbox:
    """compile/template/labels_env.py: chart-label templates and ``where:`` exprs.

    Always StrictUndefined, so every corpus entry surfaces as SecurityError
    on render regardless of attribute-only vs. call-ending shape.
    """

    @pytest.mark.parametrize("payload", ALL_PAYLOADS.values(), ids=ALL_PAYLOADS.keys())
    def test_blocks_escape_corpus(self, payload: str) -> None:
        env = label_jinja_env()
        with pytest.raises(SecurityError):
            env.from_string(payload).render(filter=_filter_stub)

    def test_positive_control_format_filter_still_works(self) -> None:
        env = label_jinja_env()
        result = env.from_string("{{ value | format('$,.0f') }}").render(value=1000)
        assert result == "$1,000"

    def test_positive_control_finalize_none_still_works(self) -> None:
        env = label_jinja_env()
        result = env.from_string("{{ value }}").render(value=None)
        assert result == ""


class TestExpanderTemplateEnvSandbox:
    """registered_views/expander.py: the [[ ]] / [% %] view-template renderer.

    Always StrictUndefined, same as labels_env.
    """

    def test_blocks_escape_corpus(self) -> None:
        payload = "[[ ''.__class__.__mro__[1].__subclasses__() ]]"
        with pytest.raises(SecurityError):
            render_template(payload, path_params={}, query_results={})

    def test_board_sql_jinja_still_passes_through_unchanged(self) -> None:
        """{{ }} board-SQL Jinja is a different delimiter set and must survive."""
        tmpl = 'sql: "SELECT {{ queries.revenue }}"'
        result = render_template(tmpl, path_params={}, query_results={})
        assert result == 'sql: "SELECT {{ queries.revenue }}"'


class TestImmutability:
    """The one thing ImmutableSandboxedEnvironment adds over the plain sandbox.

    Every payload above targets *attribute* safety, which plain
    SandboxedEnvironment blocks identically — so without this class, swapping
    the environment class back to the mutable sandbox would leave the suite
    green.
    """

    MUTATION = "{% set xs = [] %}{% set _ = xs.append(1) %}{{ xs }}"

    def test_container_mutation_is_blocked(self) -> None:
        with pytest.raises(JinjaError) as exc_info:
            resolve_jinja_template(self.MUTATION, variables={}, queries={}, strict=True)
        assert isinstance(exc_info.value.__cause__, SecurityError)

    def test_container_mutation_blocked_on_parameterized_path(self) -> None:
        with pytest.raises(JinjaError) as exc_info:
            render_parameterized(self.MUTATION, variables={}, strict=True)
        assert isinstance(exc_info.value.__cause__, SecurityError)

    def test_reading_a_container_is_still_allowed(self) -> None:
        result = resolve_jinja_template(
            "{{ xs[1] }}-{{ xs|length }}-{{ m['k'] }}",
            variables={"xs": [10, 20], "m": {"k": "v"}},
            queries={},
        )
        assert result == "20-2-v"


class TestBoardRenderEndToEnd:
    """dct render on a title-only board must not execute shell commands."""

    def test_title_ssti_payload_does_not_execute_shell(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        # A filesystem sentinel, not a string match: the board id slug derives
        # from the unrendered title and --format json echoes the title
        # verbatim, so asserting the payload text is absent would pass even if
        # the shell had run. Only the marker's absence proves it did not.
        marker = tmp_path / "shell-ran.marker"
        board = tmp_path / "evil.yml"
        board.write_text(
            "title: \"{{ filter.__globals__['__builtins__']['__import__']"
            f"('os').popen('touch {marker}').read() }}}}\"\n"
            "rows:\n  - text: hello\n"
        )

        result = runner.invoke(
            app,
            [
                "render",
                str(board),
                "--format",
                "json",
                "--project-dir",
                str(tmp_path),
            ],
        )

        assert not marker.exists()
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "ERR-JINJA-ERROR" in combined
