"""Tests for propagation-backed re-aggregation detection.

Covers: detecting aggregate-over-aggregate patterns through subqueries,
CTEs, and nested scopes where outer queries re-aggregate already-aggregated
columns.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dbt_charts.cli.main import app
from dbt_charts.core.inspect.query_validator import (
    QueryDiagnostic,
    validate_query,
)


def _has_reaggregation(diags: list[QueryDiagnostic]) -> bool:
    return any(d.code == "WARN-REAGGREGATION" for d in diags)


runner = CliRunner()


# ---------------------------------------------------------------------------
# Re-aggregation through subqueries
# ---------------------------------------------------------------------------


class TestSubqueryReaggregation:
    """Detect aggregation applied to columns that are already aggregate-derived
    in a subquery."""

    def test_avg_of_derived_sum_by_group(self) -> None:
        """AVG(total) where total = SUM(amount) in subquery."""
        sql = """
            SELECT region, AVG(total) AS grand_total
            FROM (
                SELECT region, SUM(amount) AS total
                FROM orders
                GROUP BY region
            ) sub
            GROUP BY region
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" in codes

    def test_avg_of_derived_avg(self) -> None:
        """AVG(avg_score) where avg_score = AVG(score) in subquery."""
        sql = """
            SELECT AVG(avg_score)
            FROM (
                SELECT department, AVG(score) AS avg_score
                FROM reviews
                GROUP BY department
            ) sub
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" in codes

    def test_avg_of_derived_count(self) -> None:
        """AVG(cnt) where cnt = COUNT(*) in subquery."""
        sql = """
            SELECT AVG(cnt) AS mean_count
            FROM (
                SELECT customer_id, COUNT(*) AS cnt
                FROM orders
                GROUP BY customer_id
            ) sub
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" in codes

    def test_avg_of_derived_sum(self) -> None:
        """AVG(total) where total = SUM(amount) — mixing aggregate types."""
        sql = """
            SELECT AVG(total)
            FROM (
                SELECT store_id, SUM(amount) AS total
                FROM sales
                GROUP BY store_id
            ) sub
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" in codes

    def test_severity_is_warning(self) -> None:
        sql = """
            SELECT AVG(total)
            FROM (
                SELECT AVG(amount) AS total
                FROM orders
                GROUP BY region
            ) sub
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) >= 1
        assert reagg[0].severity == "warning"

    def test_recommendation_present(self) -> None:
        sql = """
            SELECT AVG(total)
            FROM (
                SELECT AVG(amount) AS total FROM orders GROUP BY region
            ) sub
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) >= 1
        assert reagg[0].recommendation is not None


# ---------------------------------------------------------------------------
# CTE re-aggregation
# ---------------------------------------------------------------------------


class TestCTEReaggregation:
    """Detect re-aggregation through Common Table Expressions."""

    def test_avg_of_cte_aggregate(self) -> None:
        sql = """
            WITH totals AS (
                SELECT region, SUM(amount) AS total
                FROM orders
                GROUP BY region
            )
            SELECT AVG(total) FROM totals
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" in codes

    def test_avg_of_cte_count(self) -> None:
        sql = """
            WITH counts AS (
                SELECT department, COUNT(*) AS cnt
                FROM employees
                GROUP BY department
            )
            SELECT AVG(cnt) FROM counts
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" in codes


# ---------------------------------------------------------------------------
# Safe patterns — no false positives
# ---------------------------------------------------------------------------


class TestSafeAggregationPatterns:
    """Queries that should NOT trigger re-aggregation diagnostics."""

    def test_aggregate_over_raw_column(self) -> None:
        """SUM on a raw (non-aggregated) column from subquery is fine."""
        sql = """
            SELECT SUM(amount)
            FROM (
                SELECT id, amount FROM orders WHERE status = 'active'
            ) sub
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" not in codes

    def test_group_by_key_not_flagged(self) -> None:
        """Re-using a GROUP BY key in outer aggregate is fine."""
        sql = """
            SELECT COUNT(region)
            FROM (
                SELECT region, SUM(amount) AS total
                FROM orders
                GROUP BY region
            ) sub
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) == 0

    def test_simple_cte_no_aggregation(self) -> None:
        """CTE without aggregation — outer aggregate is fine."""
        sql = """
            WITH filtered AS (
                SELECT id, amount FROM orders WHERE amount > 100
            )
            SELECT SUM(amount) FROM filtered
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" not in codes

    def test_single_table_no_subquery(self) -> None:
        """Plain aggregation — no subquery, no re-aggregation possible."""
        sql = "SELECT SUM(amount) FROM orders GROUP BY region"
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" not in codes

    def test_non_aggregate_alias_reused(self) -> None:
        """Column aliased in subquery but not aggregate-derived."""
        sql = """
            SELECT MAX(val)
            FROM (
                SELECT amount * 2 AS val FROM orders
            ) sub
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" not in codes

    def test_window_function_not_flagged(self) -> None:
        """Window function output is per-row, not a collapsed aggregate.
        Aggregating a windowed column is valid."""
        sql = """
            SELECT SUM(row_total)
            FROM (
                SELECT SUM(amount) OVER (PARTITION BY region) AS row_total
                FROM orders
            ) sub
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" not in codes

    def test_unqualified_column_ambiguous_sources(self) -> None:
        """Unqualified column present in both aggregate and non-aggregate
        sources should NOT be flagged — ambiguous ownership."""
        sql = """
            WITH a AS (SELECT region, SUM(amount) AS total FROM orders GROUP BY region),
                 b AS (SELECT region, amount AS total FROM refunds)
            SELECT SUM(total) FROM a JOIN b ON a.region = b.region
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) == 0


class TestAliasReuseAcrossScopes:
    """Reusing a column name across independent CTEs must not cause a false
    re-aggregation finding purely because the names collide (FR-79).

    ``_inherit_aggregate_lineage`` propagates aggregate-derived status through
    a pass-through CTE's unqualified columns. When that CTE joins two other
    known sources, an unqualified column must only inherit aggregate status
    from a source that actually defines it — matching by name against *any*
    joined source (regardless of whether that source is the real origin) is
    exactly the trap: a join used only for filtering can carry an unrelated
    column that happens to share a name.
    """

    def test_unrelated_join_partner_does_not_leak_aggregate_status(self) -> None:
        """`net_usd` is genuinely aggregate-derived in `ltv` and genuinely a
        raw column in `iap`. `per_player` selects the `iap` value (unqualified)
        while joining `ltv` only to filter by player — it must not inherit
        `ltv`'s aggregate-ness just because the names collide. The outer
        query's own aggregate (SUM) is real, but it aggregates the `iap`
        value, not a pre-aggregated one."""
        sql = """
            WITH
              ltv AS (
                SELECT player_id, SUM(revenue) AS net_usd
                FROM transactions
                GROUP BY player_id
              ),
              iap AS (
                SELECT player_id, amount AS net_usd
                FROM iap_purchases
              ),
              per_player AS (
                SELECT iap.player_id, net_usd
                FROM iap
                JOIN ltv ON ltv.player_id = iap.player_id
              )
            SELECT COUNT(*) AS iap_payers, SUM(net_usd) AS total_iap
            FROM per_player
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert reagg == [], f"expected no reaggregation finding, got: {reagg}"

    def test_join_partners_agreeing_on_aggregate_status_still_flagged(self) -> None:
        """When the unqualified column IS aggregate-derived in every joined
        source that defines it, re-aggregation is real and must still fire —
        the fix must not blanket-silence unqualified passthrough columns."""
        sql = """
            WITH
              a AS (SELECT k, SUM(x) AS total FROM t1 GROUP BY k),
              b AS (SELECT k, SUM(y) AS total FROM t2 GROUP BY k),
              combined AS (
                SELECT a.k, total
                FROM a JOIN b ON a.k = b.k
              )
            SELECT AVG(total) FROM combined
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) == 1


class TestAggregateSourceReachedViaCteInternalJoin:
    """The FR-79 scope guard must exclude a join only when it belongs to a
    *different* scope than the select under analysis — never the select's
    own JOIN clauses, even when that select is itself a CTE body.

    The source lookup runs both on the outer query (where a CTE's internal
    join must be excluded) and, via lineage propagation, on each CTE body
    itself (where that CTE's own joins must be *included*). Only a
    scope-relative walk gets both right.
    """

    def test_aggregate_joined_into_dimension_cte_then_reaggregated(self) -> None:
        """`agg_cte` is aggregate; `dim_cte` reaches it via its own internal
        JOIN (not its FROM) and passes `total_amount` through unqualified
        via alias. The outer query's AVG re-aggregates it — must fire."""
        sql = """
            WITH
              agg_cte AS (
                SELECT customer_id, SUM(amount) AS total_amount
                FROM orders
                GROUP BY customer_id
              ),
              dim_cte AS (
                SELECT c.customer_id, c.region, a.total_amount
                FROM customers c
                JOIN agg_cte a ON a.customer_id = c.customer_id
              )
            SELECT region, AVG(total_amount) AS avg_total
            FROM dim_cte
            GROUP BY region
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) == 1, f"expected reaggregation finding, got: {diags}"

    def test_aggregate_source_first_in_from_dimension_joined_in(self) -> None:
        """Same shape with join direction flipped — the aggregate source is
        in `dim_cte`'s FROM and the dimension table is the JOIN partner.
        Re-aggregation must still fire regardless of which side is which."""
        sql = """
            WITH
              agg_cte AS (
                SELECT customer_id, SUM(amount) AS total_amount
                FROM orders
                GROUP BY customer_id
              ),
              dim_cte AS (
                SELECT a.customer_id, c.region, a.total_amount
                FROM agg_cte a
                JOIN customers c ON a.customer_id = c.customer_id
              )
            SELECT region, AVG(total_amount) AS avg_total
            FROM dim_cte
            GROUP BY region
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) == 1, f"expected reaggregation finding, got: {diags}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestReaggregationEdgeCases:
    """Nested scopes, aliases, and mixed patterns."""

    def test_deeply_nested_reaggregation(self) -> None:
        """Two levels of nesting — inner aggregate re-aggregated at outer."""
        sql = """
            SELECT AVG(mid_total)
            FROM (
                SELECT SUM(total) AS mid_total
                FROM (
                    SELECT region, SUM(amount) AS total
                    FROM orders
                    GROUP BY region
                ) inner_q
            ) outer_q
        """
        diags = validate_query(sql)
        codes = [d.code for d in diags]
        assert "WARN-REAGGREGATION" in codes

    def test_mixed_safe_and_unsafe(self) -> None:
        """One aggregate on raw column, one on derived aggregate."""
        sql = """
            SELECT AVG(total), MAX(region)
            FROM (
                SELECT region, SUM(amount) AS total
                FROM orders
                GROUP BY region
            ) sub
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        # Should flag AVG(total) but not MAX(region)
        assert len(reagg) == 1
        assert "total" in reagg[0].message.lower()

    def test_same_column_name_different_sources_both_flagged(self) -> None:
        """Same column name from two sources — both should be flagged."""
        sql = """
            SELECT AVG(a.total), AVG(b.total)
            FROM (SELECT SUM(x) AS total FROM t1 GROUP BY k) a
            JOIN (SELECT SUM(y) AS total FROM t2 GROUP BY k) b ON a.k = b.k
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) == 2

    def test_having_reaggregation(self) -> None:
        """Re-aggregation in HAVING clause should also be detected."""
        sql = """
            SELECT region
            FROM (
                SELECT region, SUM(amount) AS total
                FROM orders
                GROUP BY region
            ) sub
            GROUP BY region
            HAVING AVG(total) > 100
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) >= 1

    def test_detail_mentions_column(self) -> None:
        """Diagnostic detail should mention the offending column."""
        sql = """
            SELECT AVG(total)
            FROM (
                SELECT SUM(amount) AS total FROM orders GROUP BY region
            ) sub
        """
        diags = validate_query(sql)
        reagg = [d for d in diags if d.code == "WARN-REAGGREGATION"]
        assert len(reagg) >= 1
        assert "total" in (reagg[0].detail or "").lower()

    def test_window_function_in_cte_not_flagged_as_aggregate(self) -> None:
        """Window output is per-row. Covers the CTE branch of scope collection;
        `TestSafeAggregationPatterns` covers the inline-subquery branch."""
        sql = """
            WITH ranked AS (
                SELECT id, amount,
                       ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY created_at) AS rn
                FROM orders
            )
            SELECT SUM(amount) FROM ranked WHERE rn = 1
        """
        diags = validate_query(sql)
        assert not _has_reaggregation(diags)

    def test_chained_cte_reaggregation(self) -> None:
        """Aggregate lineage survives a CTE selecting from an earlier CTE."""
        sql = """
            WITH base AS (
                SELECT region, SUM(amount) AS total FROM orders GROUP BY region
            ),
            passthrough AS (
                SELECT region, total FROM base
            )
            SELECT AVG(total) FROM passthrough
        """
        diags = validate_query(sql)
        assert _has_reaggregation(diags)

    def test_inline_subquery_passthrough_from_cte(self) -> None:
        """Lineage is inherited through an inline subquery over a CTE."""
        sql = """
            WITH base AS (
                SELECT region, SUM(amount) AS total FROM orders GROUP BY region
            )
            SELECT AVG(total) FROM (SELECT region, total FROM base) sub
        """
        diags = validate_query(sql)
        assert _has_reaggregation(diags)


# ---------------------------------------------------------------------------
# Composable vs non-composable pairs
# ---------------------------------------------------------------------------

_MONTHLY = """
    WITH monthly AS (
        SELECT month, SUM(amount) AS total, COUNT(*) AS n,
               MAX(amount) AS peak, MIN(amount) AS low, AVG(amount) AS mean
        FROM orders
        GROUP BY month
    )
"""


def _reaggregation_messages(sql: str) -> list[str]:
    return [d.message for d in validate_query(sql) if d.code == "WARN-REAGGREGATION"]


class TestComposablePairs:
    """Only pairs whose result depends on the grouping fire."""

    def test_lag_over_a_monthly_total_is_not_flagged(self) -> None:
        sql = (
            _MONTHLY
            + "SELECT month, total / LAG(total) OVER (ORDER BY month) - 1 FROM monthly"
        )
        assert _reaggregation_messages(sql) == []

    def test_running_sum_over_a_total_is_not_flagged(self) -> None:
        sql = _MONTHLY + "SELECT month, SUM(total) OVER (ORDER BY month) FROM monthly"
        assert _reaggregation_messages(sql) == []

    def test_windowed_avg_over_a_total_is_not_flagged(self) -> None:
        sql = (
            _MONTHLY
            + "SELECT month, AVG(total) OVER (ORDER BY month ROWS 2 PRECEDING) "
            + "FROM monthly"
        )
        assert _reaggregation_messages(sql) == []

    def test_filter_pivot_is_not_flagged(self) -> None:
        sql = (
            _MONTHLY
            + "SELECT AVG(total) FILTER (WHERE month = DATE '2026-07-01') FROM monthly"
        )
        assert _reaggregation_messages(sql) == []

    def test_sum_of_count_is_not_flagged(self) -> None:
        assert _reaggregation_messages(_MONTHLY + "SELECT SUM(n) FROM monthly") == []

    def test_sum_of_sum_is_not_flagged(self) -> None:
        assert (
            _reaggregation_messages(_MONTHLY + "SELECT SUM(total) FROM monthly") == []
        )

    def test_count_of_sum_is_not_flagged(self) -> None:
        assert (
            _reaggregation_messages(_MONTHLY + "SELECT COUNT(total) FROM monthly") == []
        )

    def test_max_of_max_and_min_of_min_are_not_flagged(self) -> None:
        sql = _MONTHLY + "SELECT MAX(peak), MIN(low) FROM monthly"
        assert _reaggregation_messages(sql) == []


class TestDistinctOverAggregate:
    """DISTINCT collapses equal pre-aggregated values, so it never composes."""

    def test_sum_distinct_of_sum_names_both(self) -> None:
        messages = _reaggregation_messages(
            _MONTHLY + "SELECT SUM(DISTINCT total) FROM monthly"
        )
        assert len(messages) == 1
        assert "SUM DISTINCT over SUM" in messages[0]

    def test_count_distinct_of_sum_fires(self) -> None:
        messages = _reaggregation_messages(
            _MONTHLY + "SELECT COUNT(DISTINCT total) FROM monthly"
        )
        assert len(messages) == 1
        assert "COUNT DISTINCT over SUM" in messages[0]

    def test_avg_distinct_of_count_fires(self) -> None:
        messages = _reaggregation_messages(
            _MONTHLY + "SELECT AVG(DISTINCT n) FROM monthly"
        )
        assert len(messages) == 1
        assert "AVG DISTINCT over COUNT" in messages[0]

    def test_plain_sum_of_the_same_column_stays_quiet(self) -> None:
        assert (
            _reaggregation_messages(_MONTHLY + "SELECT SUM(total) FROM monthly") == []
        )

    def test_distinct_over_a_raw_column_stays_quiet(self) -> None:
        sql = "SELECT SUM(DISTINCT amount) FROM (SELECT amount FROM orders) s"
        assert _reaggregation_messages(sql) == []


class TestNonComposablePairs:
    """The message names the outer function and the aggregate it sits on."""

    def test_avg_of_avg_names_both(self) -> None:
        messages = _reaggregation_messages(_MONTHLY + "SELECT AVG(mean) FROM monthly")
        assert len(messages) == 1
        assert "AVG over AVG" in messages[0]

    def test_avg_of_sum_names_both(self) -> None:
        messages = _reaggregation_messages(_MONTHLY + "SELECT AVG(total) FROM monthly")
        assert len(messages) == 1
        assert "AVG over SUM" in messages[0]

    def test_median_of_count_names_both(self) -> None:
        messages = _reaggregation_messages(_MONTHLY + "SELECT MEDIAN(n) FROM monthly")
        assert len(messages) == 1
        assert "MEDIAN over COUNT" in messages[0]

    def test_percentile_of_sum_names_both(self) -> None:
        sql = (
            _MONTHLY
            + "SELECT PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY total) FROM monthly"
        )
        messages = _reaggregation_messages(sql)
        assert len(messages) == 1
        assert "PERCENTILE_CONT over SUM" in messages[0]

    def test_sum_of_avg_names_both(self) -> None:
        messages = _reaggregation_messages(_MONTHLY + "SELECT SUM(mean) FROM monthly")
        assert len(messages) == 1
        assert "SUM over AVG" in messages[0]

    def test_inner_function_survives_a_passthrough_cte(self) -> None:
        sql = (
            _MONTHLY
            + ", pass AS (SELECT month, mean FROM monthly) SELECT SUM(mean) FROM pass"
        )
        messages = _reaggregation_messages(sql)
        assert len(messages) == 1
        assert "SUM over AVG" in messages[0]


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestReaggregationCLI:
    """Re-aggregation diagnostics appear through the CLI."""

    def test_cli_shows_reaggregation_warning(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        sql = (
            "SELECT AVG(total) FROM "
            "(SELECT SUM(amount) AS total FROM orders GROUP BY region) sub"
        )
        result = runner.invoke(
            app, ["query", "db", sql, "--validate", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0  # warnings don't cause exit 1
        assert "WARN-REAGGREGATION" in result.output

    def test_cli_json_includes_reaggregation(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        sql = (
            "SELECT AVG(total) FROM "
            "(SELECT SUM(amount) AS total FROM orders GROUP BY region) sub"
        )
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                sql,
                "--validate",
                "--json",
                "--project-dir",
                str(tmp_path),
            ],
        )
        parsed = json.loads(result.output)
        assert any(d["code"] == "WARN-REAGGREGATION" for d in parsed)


class TestDbtTemplatedReaggregation:
    """The lineage reads ref()/source() and Jinja through the shared SQL front end."""

    def test_ref_source_under_a_cte(self) -> None:
        sql = """
            WITH monthly AS (
                SELECT region, SUM(amount) AS total
                FROM {{ ref('orders') }}
                GROUP BY region
            )
            SELECT AVG(total) FROM monthly
        """
        assert _has_reaggregation(validate_query(sql))

    def test_import_cte_named_like_its_model(self) -> None:
        """The CTE body's ref() is the model; only later reads see the CTE."""
        sql = """
            WITH orders AS (SELECT * FROM {{ ref('orders') }}),
            monthly AS (
                SELECT region, SUM(amount) AS total FROM orders GROUP BY region
            )
            SELECT AVG(total) FROM monthly
        """
        assert _has_reaggregation(validate_query(sql))

    def test_ref_named_like_a_cte_is_the_model(self) -> None:
        """`{{ ref('orders') }}` after `WITH orders` is the model, whose `total`
        is no aggregate."""
        sql = """
            WITH orders AS (
                SELECT region, SUM(amount) AS total FROM main.raw GROUP BY region
            )
            SELECT AVG(total) FROM {{ ref('orders') }}
        """
        assert not _has_reaggregation(validate_query(sql))

    def test_if_block_inside_one_query(self) -> None:
        sql = """
            SELECT AVG(total) FROM (
                SELECT region, SUM(amount) AS total FROM main.orders
                {% if only_eu %} WHERE region = 'eu' {% endif %}
                GROUP BY region
            ) sub
        """
        assert _has_reaggregation(validate_query(sql))

    def test_templated_table_name_does_not_leak_a_placeholder(self) -> None:
        diags = validate_query("SELECT * FROM main.orders, main.events_{{ env }}")
        assert diags
        assert all("__dct" not in d.message for d in diags)


class TestScopeBoundReaggregation:
    """Names bind the way the engine binds them, or nothing is reported."""

    def test_cte_referenced_in_another_case(self) -> None:
        sql = """
            WITH Monthly AS (
                SELECT region, SUM(amount) AS total FROM main.orders GROUP BY region
            )
            SELECT AVG(total) FROM monthly
        """
        assert _has_reaggregation(validate_query(sql))

    def test_qualified_column_of_a_base_table_does_not_inherit_a_cte_aggregate(
        self,
    ) -> None:
        sql = """
            WITH monthly AS (
                SELECT region, SUM(amount) AS total FROM main.orders GROUP BY region
            ), mid AS (
                SELECT m.region, o.total
                FROM monthly m JOIN main.other o ON o.region = m.region
            )
            SELECT AVG(total) FROM mid
        """
        assert not _has_reaggregation(validate_query(sql))

    def test_name_used_twice_in_a_from_does_not_raise(self) -> None:
        sql = "SELECT COUNT(*) FROM s1.t JOIN s2.t ON s1.t.id = s2.t.id"
        assert not _has_reaggregation(validate_query(sql))
        assert not _has_reaggregation(validate_query("SELECT * FROM t, t"))

    def test_set_operation_cte_is_not_tracked(self) -> None:
        sql = """
            WITH u AS (
                SELECT SUM(a) AS total FROM main.x
                UNION ALL
                SELECT SUM(b) AS total FROM main.y
            )
            SELECT AVG(total) FROM u
        """
        assert not _has_reaggregation(validate_query(sql))

    def test_detail_names_the_cte_not_its_alias(self) -> None:
        sql = """
            WITH monthly AS (
                SELECT region, SUM(amount) AS total FROM main.orders GROUP BY region
            )
            SELECT AVG(m.total) FROM monthly m
        """
        (diag,) = [d for d in validate_query(sql) if d.code == "WARN-REAGGREGATION"]
        assert "in source 'monthly'" in (diag.detail or "")

    def test_one_cte_under_two_aliases_reports_once(self) -> None:
        sql = """
            WITH monthly AS (
                SELECT region, SUM(amount) AS total FROM main.orders GROUP BY region
            )
            SELECT AVG(a.total), AVG(b.total)
            FROM monthly a JOIN monthly b ON a.region = b.region
        """
        diags = [d for d in validate_query(sql) if d.code == "WARN-REAGGREGATION"]
        assert len({d.message for d in diags}) == len(diags) == 1

    def test_deep_self_joining_cte_chain_is_fast(self) -> None:
        ctes = [
            "c0 AS (SELECT region, SUM(amount) AS total FROM main.o GROUP BY region)"
        ]
        ctes += [
            f"c{i} AS (SELECT p.region, p.total FROM c{i - 1} p"
            f" JOIN c{i - 1} q ON p.region = q.region)"
            for i in range(1, 25)
        ]
        sql = f"WITH {', '.join(ctes)} SELECT AVG(total) FROM c24"
        assert _has_reaggregation(validate_query(sql))


class TestNonSelectStatements:
    """Valid SQL that is not a lone SELECT has nothing to report."""

    def test_no_diagnostics(self) -> None:
        for sql in (
            "DESCRIBE orders",
            "PRAGMA table_info('t')",
            "SHOW TABLES",
            "VALUES (1, 2)",
            "SELECT 1; SELECT 2",
        ):
            assert validate_query(sql) == [], sql

    def test_real_parse_error_keeps_its_message(self) -> None:
        (diag,) = validate_query("SELECT FROM WHERE")
        assert diag.code == "WARN-PARSE-ERROR"
        assert diag.message.startswith("SQL parse error: ")
        assert "could not be parsed" not in diag.message


class TestRefusalsAndMasking:
    def test_unanalyzable_valid_sql_is_not_a_parse_error(self) -> None:
        """sqlglot drops the inner WITH here; the query runs, so nothing is wrong."""
        sql = "WITH a AS (SELECT 1 AS x) (WITH a AS (SELECT 2 AS x) SELECT x FROM a)"
        assert validate_query(sql) == []

    def test_templated_names_never_leak_into_any_diagnostic_text(self) -> None:
        for sql in (
            "SELECT * FROM (SELECT * FROM a) {{ al }}, b",
            "SELECT * FROM {{ ref('orders') }}, {{ source('raw', 'events') }}",
            "SELECT AVG(s) FROM (SELECT d, SUM(x) AS s FROM t GROUP BY d)"
            " AS q_{{ env }}",
            "SELECT strftime({{ ref('t') }}.d, '%Y-%m') AS month FROM x",
        ):
            diags = validate_query(
                sql, dialect="duckdb", x_columns=frozenset({"month"})
            )
            for d in diags:
                text = " ".join(
                    [d.message, d.detail or "", d.recommendation or "", *d.evidence]
                )
                assert "__dct" not in text, (sql, text)

    def test_ref_is_spelled_as_authored(self) -> None:
        diags = validate_query("SELECT * FROM {{ ref('orders') }}, b")
        assert any("{{ ref('orders') }}" in d.message for d in diags)


def test_parse_error_message_never_leaks_a_placeholder() -> None:
    (diag,) = validate_query("SELECT x FROM {{ ref('t') }} WHERE (")
    assert diag.code == "WARN-PARSE-ERROR"
    assert "__dct" not in diag.message
