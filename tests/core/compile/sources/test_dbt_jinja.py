"""dbt Jinja rendering of source/profile config values.

These pin the contract that dbt charts resolves `{{ env_var(...) }}` (and the rest
of dbt's profile-rendering Jinja) through dbt's own ``ProfileRenderer`` — exactly
as dbt would — rather than a hand-rolled regex.
"""

from __future__ import annotations

import contextlib

import pytest

from dbt_charts.core.compile.sources.dbt_jinja import (
    nothing_to_render,
    render_dbt_jinja_in_dict,
)


class TestRenderDbtJinjaInDict:
    def test_env_var_default_used_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DF_TEST_VAR", raising=False)
        out = render_dbt_jinja_in_dict(
            {"path": "{{ env_var('DF_TEST_VAR', 'fallback.db') }}"}
        )
        assert out["path"] == "fallback.db"

    def test_env_var_value_wins_over_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DF_TEST_VAR", "/real/path.db")
        out = render_dbt_jinja_in_dict(
            {"path": "{{ env_var('DF_TEST_VAR', 'fallback.db') }}"}
        )
        assert out["path"] == "/real/path.db"

    def test_embedded_env_var_in_larger_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DB_HOST", "db.example.com")
        out = render_dbt_jinja_in_dict(
            {"dsn": "postgres://{{ env_var('DB_HOST') }}:5432"}
        )
        assert out["dsn"] == "postgres://db.example.com:5432"

    def test_plain_string_passthrough(self) -> None:
        out = render_dbt_jinja_in_dict({"path": "just/a/path/file.db"})
        assert out["path"] == "just/a/path/file.db"

    def test_missing_env_var_no_default_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # dbt's EnvVarMissingError is adapted to ValueError at the render boundary
        # so Pydantic validators surface it as a ValidationError, not a raw dbt error.
        monkeypatch.delenv("DF_TEST_MISSING", raising=False)
        with pytest.raises(ValueError, match="DF_TEST_MISSING"):
            render_dbt_jinja_in_dict({"host": "{{ env_var('DF_TEST_MISSING') }}"})

    def test_malformed_jinja_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="(?i)compil|jinja|error"):
            render_dbt_jinja_in_dict({"host": "literal {{ unclosed"})

    def test_resolves_nested_and_preserves_non_string_types(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DF_TEST_HOST", "db.example.com")
        monkeypatch.delenv("DF_TEST_SCHEMA", raising=False)
        out = render_dbt_jinja_in_dict(
            {
                "type": "postgres",
                "host": "{{ env_var('DF_TEST_HOST') }}",
                "port": 5432,
                "nested": {"schema": "{{ env_var('DF_TEST_SCHEMA', 'main') }}"},
            }
        )
        assert out == {
            "type": "postgres",
            "host": "db.example.com",
            "port": 5432,
            "nested": {"schema": "main"},
        }
        # int preserved as int, not stringified
        assert isinstance(out["port"], int)

    def test_resolves_env_var_inside_list_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt's deep_map_render recurses into lists too (the old regex did not)."""
        monkeypatch.setenv("DF_TEST_ITEM", "resolved")
        out = render_dbt_jinja_in_dict(
            {"items": ["{{ env_var('DF_TEST_ITEM') }}", "plain"]}
        )
        assert out["items"] == ["resolved", "plain"]

    def test_password_keypath_defers_missing_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt's SecretRenderer defers `password` rendering — a missing password
        env_var is left as a literal (fails at connect, not parse). We match dbt
        rather than re-deriving a stricter rule; passwords may contain `{{`/`%`."""
        monkeypatch.delenv("DF_TEST_PGPASS", raising=False)
        out = render_dbt_jinja_in_dict({"password": "{{ env_var('DF_TEST_PGPASS') }}"})
        assert out["password"] == "{{ env_var('DF_TEST_PGPASS') }}"


class TestJinjaFreeFastPath:
    """A payload with nothing to render must not pay for dbt-core's import.

    ``render_dbt_jinja_in_dict`` is reached from ``Source``'s before-validator,
    which ``introspect()`` trips once per authored model — on ``{}`` every time.
    Importing ``dbt.config.renderer`` to render nothing costs ~500ms of process
    warm-up, and it lands on the first board view a fresh Cloud worker serves.

    The skip is only sound where dbt returns the value byte-identical, which is
    much narrower than "contains no ``{{``". ``renderer.py`` renders with
    ``native=True``, which disables dbt's own no-render-chars shortcut, so every
    string is compiled through Jinja and some come back rewritten. These pin
    each divergence found by differential-probing the real renderer.
    """

    def test_jinja_free_payload_does_not_import_dbt(self) -> None:
        # A subprocess, because dbt is already in sys.modules by the time any
        # in-process assertion could run — some earlier test rendered real Jinja.
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent("""
            import sys
            from dbt_charts.core.compile.sources.dbt_jinja import (
                render_dbt_jinja_in_dict,
            )

            render_dbt_jinja_in_dict({})
            render_dbt_jinja_in_dict(
                {"type": "duckdb", "path": "a.db", "port": 5432, "opts": ["x"]}
            )
            assert "dbt.config" not in sys.modules, "dbt-core was imported anyway"
            print("clean")
        """)
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "clean" in result.stdout

    def test_jinja_payload_still_imports_dbt_and_renders(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The skip must not widen: anything with a render char goes to dbt."""
        monkeypatch.setenv("DF_FASTPATH_HOST", "real.example.com")
        out = render_dbt_jinja_in_dict({"host": "{{ env_var('DF_FASTPATH_HOST') }}"})
        assert out["host"] == "real.example.com"

    def test_opening_delimiter_forms_reach_dbt(self) -> None:
        """Evidence of rendering: each opening form is *transformed*, not echoed.

        Undefined name, comment-stripped-to-empty, and a raise on a block with no
        matching ``endif`` — three observable proofs the value went to dbt.
        """
        assert str(render_dbt_jinja_in_dict({"h": "{{ nope }}"})["h"]) != "{{ nope }}"
        assert render_dbt_jinja_in_dict({"h": "{# c #}"})["h"] == ""
        with pytest.raises(ValueError, match="(?i)end of template|endif|jinja"):
            render_dbt_jinja_in_dict({"h": "{% if x %}"})

    def test_closing_delimiter_alone_is_still_routed_to_dbt(self) -> None:
        """``}}``/``%}``/``#}`` render to themselves, so the fast path *could*
        skip them and return the same answer — but only if we proved that
        independently of dbt. We use dbt's own pattern verbatim instead, which
        makes the skip sound by construction, so these fall through. Observable
        only as the import they trigger.
        """
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent("""
            import sys
            from dbt_charts.core.compile.sources.dbt_jinja import (
                render_dbt_jinja_in_dict,
            )

            assert render_dbt_jinja_in_dict({"h": "trailing }}"}) == {
                "h": "trailing }}"
            }
            assert "dbt.config" in sys.modules, "fast path narrowed past dbt's pattern"
            print("routed")
        """)
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "routed" in result.stdout

    def test_date_values_are_isoformatted(self) -> None:
        """dbt's ``render_value`` ISO-formats dates whether or not Jinja is present.

        YAML hands us real ``date`` objects for an unquoted ``2026-01-01``, so a
        fast path keyed on "no ``{{``" alone would leak a ``date`` where every
        caller has always seen a string.
        """
        from datetime import date, datetime

        out = render_dbt_jinja_in_dict(
            {"d": date(2026, 1, 1), "dt": datetime(2026, 1, 1, 12, 0)}
        )
        assert out == {"d": "2026-01-01", "dt": "2026-01-01T12:00:00"}

    @pytest.mark.parametrize("value", [{1, 2}, ("a", "b"), object()])
    def test_unmodeled_value_type_still_raises(self, value: object) -> None:
        """dbt's walk models dict/list/str/int/float/bool/None/date and raises on
        anything else. Passing such a value through silently would turn a loud
        error into a wrong config."""
        with pytest.raises(ValueError, match="expected one of"):
            render_dbt_jinja_in_dict({"x": value})

    def test_jinja_free_payload_round_trips_unchanged(self) -> None:
        payload = {
            "type": "postgres",
            "host": "db.example.com",
            "port": 5432,
            "threads": 4,
            "keepalive": False,
            "role": None,
            "ratio": 1.5,
            "search_path": ["public", "analytics"],
            "cache": {"ttl": "1h", "enabled": True},
        }
        assert render_dbt_jinja_in_dict(dict(payload)) == payload


class TestFastPathEquivalence:
    """Every case where ``ProfileRenderer`` does *not* return its input verbatim.

    Found by differential-probing the renderer, not by reading Jinja's source.
    Each of these passes on the pre-fast-path code, so a regression that widens
    the skip fails here rather than silently rewriting someone's credentials.
    """

    @staticmethod
    def _dbt_answer(value: object) -> object:
        """What ``ProfileRenderer`` alone makes of ``{"k": value}``."""
        from dbt.config.renderer import ProfileRenderer
        from dbt_common.context import set_invocation_context

        set_invocation_context({})
        return ProfileRenderer({}).render_data({"k": value})["k"]

    @pytest.mark.parametrize(
        "value",
        [
            "hunter2\n",  # a password or PEM key read from a file
            "-----BEGIN KEY-----\nabc\n-----END KEY-----\n",
            "a\r\nb",  # CRLF, e.g. a Windows-authored profiles.yml
            "a\rb",
            "\n",
            "DBT_ENV_SECRET",  # trips SecretRenderer's placeholder branch
        ],
    )
    def test_value_dbt_rewrites_is_not_fast_pathed(self, value: str) -> None:
        assert self._dbt_answer(value) != value, "probe no longer diverges"
        assert render_dbt_jinja_in_dict({"k": value}) == {"k": self._dbt_answer(value)}

    @pytest.mark.parametrize(
        "value", ["plain", "", " lead", "trail ", "a\nb", "tab\there", "café", "0755"]
    )
    def test_value_dbt_returns_verbatim_is_fast_pathed(self, value: str) -> None:
        assert self._dbt_answer(value) == value, "probe no longer agrees"
        assert render_dbt_jinja_in_dict({"k": value}) == {"k": value}
        # Not redundant with the line above: dbt returns these verbatim too, so
        # matching output alone cannot tell a taken skip from a widened guard.
        assert nothing_to_render({"k": value}), "value stopped being fast-pathed"

    def test_secret_prefix_matches_dbt(self) -> None:
        """The inlined constant exists to keep dbt off the import path; if dbt
        renames or re-values it, this is what says so."""
        from dbt_common.constants import SECRET_ENV_PREFIX

        from dbt_charts.core.compile.sources.dbt_jinja import _SECRET_ENV_PREFIX

        assert _SECRET_ENV_PREFIX == SECRET_ENV_PREFIX

    def test_yaml_anchor_cycle_raises_value_error_not_recursion_error(self) -> None:
        """``safe_load`` builds real cycles from anchors, and `profiles.yml` is
        untrusted input. dbt detects the cycle and raises; the fast path must not
        recurse into a ``RecursionError``, which is a ``RuntimeError`` and would
        sail past the ``except ValueError`` in ``detection.py``."""
        import yaml

        doc = yaml.safe_load("a: &x\n  b: *x\n")
        assert doc["a"]["b"] is doc["a"], "yaml stopped building cycles"
        with pytest.raises(ValueError, match="(?i)cycle"):
            render_dbt_jinja_in_dict(doc)

    def test_shared_alias_without_a_cycle_still_fast_paths(self) -> None:
        """Path-scoped, not global: the same node reachable twice is not a cycle.

        Asserted on the predicate, not the output — dbt returns this dict
        unchanged as well, so a global visited set would wrongly answer False
        and no output comparison would notice.
        """
        shared = {"host": "db.example.com"}
        assert nothing_to_render({"a": shared, "b": shared})

    def test_deep_acyclic_alias_chain_falls_through_instead_of_raising(self) -> None:
        """Depth is not self-reference. A chain of YAML aliases builds a deep
        acyclic graph from a shallow document, so the ``id(value) in path`` cycle
        check never fires and the walk recurses until it gives out.

        Do not restore an unconditional ``== doc`` here: dbt's own render
        overflows at this depth before 3.12, so that assertion passes only on 3.12+.
        What holds on every version is that the caller sees ``ValueError`` or a
        result — never ``RecursionError``, which is a ``RuntimeError`` and would
        sail past ``detection.py``'s guard on untrusted input.
        """
        import yaml

        chain = "\n".join(
            ["x0: &x0 [v]"] + [f"x{i}: &x{i} [*x{i - 1}]" for i in range(1, 900)]
        )
        doc = yaml.safe_load(chain)
        assert nothing_to_render(doc) is False
        # ValueError is dbt's own overflow, already adapted at this module's
        # boundary; a RecursionError would propagate and fail the test.
        with contextlib.suppress(ValueError):
            render_dbt_jinja_in_dict(doc)
