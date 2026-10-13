"""Tests for agent_api.docs — single-file syntax slicer with isolated fixture corpus."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest

from dbt_charts.agent_api.docs import (
    DocsResult,
    TopicEntry,
    docs,
    read_full_text,
    slugify,
)


@pytest.fixture(autouse=True)
def generated_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Point ``_SYNTAX_FILE`` at a controlled corpus and the three generated
    references at empty text, for every test in this module.

    A test puts reference text in the returned dict to bring it into the
    corpus; autouse keeps the rest of the suite isolated from the real
    generators.
    """
    fake = tmp_path / "DBT_CHARTS_SYNTAX.md"
    fake.write_text(
        "# dbt charts YAML Syntax\n\n"
        "## Cheatsheet\nOne screen of essentials. Each topic below has a dedicated H2.\n\n"
        "## Board\nThe board is the root dashboard object.\nContains charts and layout.\n"
        "A board may pin one legend position for every chart.\n\n"
        "## Conditional formatting\nRule-driven style overrides applied per column.\n\n"
        "## Charts\nBar chart documentation.\nUse x and y fields.\n\n"
        "### Shared chart fields\n"
        "Every chart accepts `axis_y` and `legend` under style.\n"
        "Set `legend: false` to hide the legend.\n\n"
        "## Layout\nGrid layout documentation.\nArrange charts in a grid.\n\n"
    )
    import dbt_charts.agent_api.docs._loader as _loader

    monkeypatch.setattr(_loader, "_SYNTAX_FILE", fake)
    refs: dict[str, str] = dict.fromkeys(
        ("reference", "error-reference", "warning-reference"), ""
    )
    monkeypatch.setattr(_loader, "_generated_text", refs.__getitem__)
    return refs


# ---------------------------------------------------------------------------
# Topic index mode (default — bare `docs()`)
# ---------------------------------------------------------------------------


def test_docs_default_returns_topic_index() -> None:
    result = docs()
    assert result.success is True
    assert result.mode == "index"
    assert all(isinstance(entry, TopicEntry) for entry in result.topics)
    slugs = [entry.id for entry in result.topics]
    assert slugs == [
        "cheatsheet",
        "board",
        "conditional-formatting",
        "charts",
        "layout",
        "reference",
        "error-reference",
        "warning-reference",
    ]


def test_topic_index_describes_the_generated_topics() -> None:
    """The generated references are browsable topics, not `--help`-only ids."""
    by_id = {entry.id: entry for entry in docs().topics}
    for slug in ("reference", "error-reference", "warning-reference"):
        assert by_id[slug].description, f"{slug} has no description"


def test_docs_topic_descriptions_strip_markdown() -> None:
    result = docs()
    by_id = {entry.id: entry for entry in result.topics}
    assert by_id["board"].description == "The board is the root dashboard object."
    assert by_id["charts"].description == "Bar chart documentation."
    assert (
        by_id["conditional-formatting"].description
        == "Rule-driven style overrides applied per column."
    )


# ---------------------------------------------------------------------------
# Topic lookup
# ---------------------------------------------------------------------------


def test_docs_lookup_returns_topic_content() -> None:
    result = docs(topic="charts")
    assert result.success is True
    assert result.mode == "topic"
    assert result.topic is not None
    assert result.topic.id == "charts"
    assert "Charts" in result.topic.title
    assert "Bar chart documentation" in result.topic.content


def test_docs_kpi_resolves_to_the_charts_topic() -> None:
    """`kpi` is an authorable chart type but has no dedicated H2 of its own —
    it must resolve to the `charts` topic rather than "Unknown topic"."""
    result = docs(topic="kpi")
    assert result.success is True
    assert result.mode == "topic"
    assert result.topic is not None
    assert result.topic.id == "kpi"
    assert "Bar chart documentation" in result.topic.content


def test_docs_every_authorable_chart_type_resolves() -> None:
    """Every chart type in `SUPPORTED_AUTHORED_CHART_TYPES` must resolve to a
    topic, not just kpi -- the papercut is generic, not kpi-specific."""
    from dbt_charts.core.compile.models.chart.authored import (
        SUPPORTED_AUTHORED_CHART_TYPES,
    )

    for chart_type in SUPPORTED_AUTHORED_CHART_TYPES:
        result = docs(topic=chart_type)
        assert result.success is True, f"{chart_type}: {result.errors}"
        assert result.topic is not None


def test_docs_all_returns_whole_file() -> None:
    result = docs(topic="all")
    assert result.success is True
    assert result.mode == "topic"
    assert result.topic is not None
    assert result.topic.id == "all"
    assert "dbt charts YAML Syntax" in result.topic.content
    assert "## Cheatsheet" in result.topic.content
    assert "## Layout" in result.topic.content


def test_docs_all_includes_the_generated_field_reference(
    generated_refs: dict[str, str],
) -> None:
    """`all` is the unsliced read; a reader who greps it must find grammar keys
    that only the generated reference documents."""
    generated_refs["reference"] = (
        "# Generated\n\n## HoverEmphasisStyle\n\n`hover_emphasis`\n"
    )
    result = docs(topic="all")
    assert result.success is True
    assert result.topic is not None
    assert "## Cheatsheet" in result.topic.content
    assert "hover_emphasis" in result.topic.content


def test_docs_cheatsheet_is_a_topic() -> None:
    result = docs(topic="cheatsheet")
    assert result.success is True
    assert result.topic is not None
    assert "essentials" in result.topic.content


def test_docs_reference_returns_generated_spec(generated_refs: dict[str, str]) -> None:
    generated_refs["reference"] = "# Generated\n\n## Fields\n\nAll fields here.\n"
    result = docs(topic="reference")
    assert result.success is True
    assert result.mode == "topic"
    assert result.topic is not None
    assert result.topic.id == "reference"
    assert "Generated" in result.topic.content


def test_docs_error_reference_returns_generated_spec(
    generated_refs: dict[str, str],
) -> None:
    generated_refs["error-reference"] = (
        "# dbt charts Error Reference\n\n| Code | Domain |\n|---|---|\n"
    )
    result = docs(topic="error-reference")
    assert result.success is True
    assert result.mode == "topic"
    assert result.topic is not None
    assert result.topic.id == "error-reference"
    assert "dbt charts Error Reference" in result.topic.content


def test_docs_warning_reference_returns_generated_spec(
    generated_refs: dict[str, str],
) -> None:
    generated_refs["warning-reference"] = (
        "# dbt charts Warning Reference\n\n### WARN-FOO\n"
    )
    result = docs(topic="warning-reference")
    assert result.success is True
    assert result.mode == "topic"
    assert result.topic is not None
    assert result.topic.id == "warning-reference"
    assert "dbt charts Warning Reference" in result.topic.content


def test_docs_unknown_topic_offers_close_match() -> None:
    result = docs(topic="char")
    assert result.success is False
    assert any("char" in e for e in result.errors)
    assert any("charts" in h for h in result.hints)


def test_docs_unknown_topic_without_a_close_match_points_at_the_index() -> None:
    result = docs(topic="theme")
    assert result.success is False
    assert result.hints == ["Run `dct docs` for the topic index"]


@pytest.mark.parametrize(
    "bad_topic",
    [
        "../README",
        "../../etc/passwd",
        "/etc/hosts",
        "board/",
        "board\\",
        "Board",
        "",
        " board",
    ],
)
def test_docs_rejects_invalid_topic_ids(bad_topic: str) -> None:
    """Topic ids that contain path separators, '..', or non-grammar chars
    must be rejected before any filesystem read.
    """
    result = docs(topic=bad_topic)
    assert result.success is False
    assert result.topic is None
    assert any("Invalid topic id" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Slugify rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("## Conditional formatting", "conditional-formatting"),
        ("## Custom chart plugins", "custom-chart-plugins"),
        ("## Board", "board"),
        ("## Errors", "errors"),
        ("Charts (29 total)", "charts-29-total"),
        ("## Two   Spaces", "two-spaces"),
        ("## Punctuation, hello!", "punctuation-hello"),
    ],
)
def test_slugify_rule(heading: str, expected: str) -> None:
    assert slugify(heading) == expected


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_docs_search_returns_ranked_hits() -> None:
    result = docs(search="grid", limit=3)
    assert result.success is True
    assert result.mode == "search"
    assert len(result.search) <= 3
    topics_found = [h.topic for h in result.search]
    assert "layout" in topics_found


def test_docs_search_no_match_returns_empty() -> None:
    result = docs(search="xyzzy_no_such_term_42")
    assert result.success is True
    assert result.mode == "search"
    assert result.search == []


def test_docs_search_multi_word_query_matches_on_terms_not_phrase() -> None:
    """No line contains the phrase "hide legend"; both terms live in one section."""
    result = docs(search="hide legend")
    topics = [h.topic for h in result.search]
    assert topics[0] == "charts"
    assert result.search[0].section == "Shared chart fields"


def test_docs_search_hit_carries_the_whole_unit_body() -> None:
    hit = docs(search="hide legend").search[0]
    assert hit.content.startswith("### Shared chart fields\n")
    assert "Every chart accepts" in hit.content  # first line, no "hide" in it
    assert "hide the legend" in hit.content  # last line of the unit


def test_docs_search_hit_topic_is_a_fetchable_h2_slug() -> None:
    """A hit inside an H3 still points at the H2 topic `dct docs <topic>` accepts."""
    result = docs(search="axis_y")
    assert result.search[0].topic == "charts"
    assert docs(topic=result.search[0].topic).success is True


def test_docs_search_snake_case_field_matches_its_parts() -> None:
    result = docs(search="axis y")
    assert result.search[0].section == "Shared chart fields"


def test_docs_search_title_match_outranks_body_mention() -> None:
    """`board` appears in the Board title and in the Cheatsheet body only."""
    result = docs(search="board")
    assert result.search[0].topic == "board"


def test_docs_search_covers_generated_references(
    generated_refs: dict[str, str],
) -> None:
    generated_refs["reference"] = (
        "# dbt charts YAML Field Reference\n\n"
        "## BarChart\n\n| `tooltip` | bool | Show a hover tooltip on each bar |\n"
    )
    result = docs(search="tooltip")
    assert result.search[0].topic == "reference"
    assert result.search[0].section == "BarChart"
    assert "tooltip" in result.search[0].content


def test_docs_search_respects_limit() -> None:
    assert len(docs(search="chart", limit=1).search) == 1


# ---------------------------------------------------------------------------
# Bad combinations
# ---------------------------------------------------------------------------


def test_docs_topic_with_search_scopes_the_hits() -> None:
    result = docs(topic="charts", search="chart")
    assert result.success is True
    assert result.mode == "search"
    assert result.search
    assert {h.topic for h in result.search} == {"charts"}


def test_docs_search_with_all_topic_is_unscoped() -> None:
    assert docs(topic="all", search="chart").search == docs(search="chart").search


def test_docs_search_skips_heading_only_units(generated_refs: dict[str, str]) -> None:
    """A bare domain header in a diagnostics reference has no prose to show;
    it must not spend a result slot on an empty body."""
    generated_refs["error-reference"] = (
        "## charts\n\n### ERR-CHARTS-X: charts broke\n\nA charts error.\n"
    )
    hits = docs(search="charts", limit=10).search
    assert all(h.content for h in hits)
    assert [h.section for h in hits if h.topic == "error-reference"] == [
        "ERR-CHARTS-X: charts broke"
    ]


def test_docs_search_with_unknown_topic_errors() -> None:
    result = docs(topic="nope", search="chart")
    assert result.success is False
    assert result.errors == ["Unknown topic: nope"]


def test_docs_search_interleaves_sources(generated_refs: dict[str, str]) -> None:
    """A tiny keyword-dense diagnostic entry out-scores both syntax hits on
    its own; interleaving still puts the best syntax hit first and the second
    syntax hit ahead of nothing but the diagnostic's turn."""
    generated_refs["warning-reference"] = (
        "## charts\n\n### WARN-LEGEND-OVERFLOW: legend legend legend\n\nlegend\n"
    )
    hits = docs(search="legend").search
    assert [h.topic for h in hits] == ["charts", "warning-reference", "board"]
    assert hits[1].score > hits[0].score


def test_generated_topics_serve_the_generators_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The references are derived when read, not shipped as files. MkDocs'
    `{: #anchor }` attribute lists belong to the docs-site copy only; in
    terminal output or an MCP resource they are literal noise."""
    from dbt_charts.core.compile.schema import get_schema_for_prompt
    from dbt_charts.core.diagnostics.render_reference import render_reference

    monkeypatch.undo()
    expected = {
        "reference": get_schema_for_prompt() + "\n",
        "error-reference": render_reference("error", with_anchors=False),
        "warning-reference": render_reference("warning", with_anchors=False),
    }
    for topic, text in expected.items():
        result = docs(topic=topic)
        assert result.topic is not None, result.errors
        assert result.topic.content == text
        assert "{: #" not in text


# ---------------------------------------------------------------------------
# Missing source file crashes loud
# ---------------------------------------------------------------------------


def test_read_full_text_missing_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dbt_charts.agent_api.docs._loader as _loader
    from dbt_charts.agent_api.docs import DocsCorpusMissingError

    monkeypatch.setattr(_loader, "_SYNTAX_FILE", tmp_path / "nonexistent.md")
    with pytest.raises(DocsCorpusMissingError, match="source file missing"):
        read_full_text()


# ---------------------------------------------------------------------------
# Pydantic round-trip
# ---------------------------------------------------------------------------


def test_docs_result_json_round_trip() -> None:
    result = docs(topic="board")
    assert result.success is True
    round_tripped = DocsResult.model_validate_json(result.model_dump_json())
    assert round_tripped.success is True
    assert round_tripped.topic is not None
    assert round_tripped.topic.id == "board"


# ---------------------------------------------------------------------------
# DocsArgs forbids unknown keys
# ---------------------------------------------------------------------------


def test_docs_args_forbids_extra_keys() -> None:
    from dbt_charts.agent_api.docs import DocsArgs

    with pytest.raises(ValueError, match="extra"):
        DocsArgs.model_validate({"list": True})


# ---------------------------------------------------------------------------
# Real corpus integrity — guards against accidental wheel-shipping breakage
# ---------------------------------------------------------------------------


def test_real_syntax_file_present_in_wheel() -> None:
    syntax = importlib.resources.files("dbt_charts") / "DBT_CHARTS_SYNTAX.md"
    assert syntax.is_file(), (
        "DBT_CHARTS_SYNTAX.md must ship with the wheel for `dct docs`"
    )


def test_docs_package_attribute_is_not_shadowed() -> None:
    """`from dbt_charts.agent_api.docs import ... docs` must not rebind the
    `docs` submodule attribute on the parent package. Regression for the
    function/package name collision.
    """
    import dbt_charts.agent_api.docs as docs_pkg
    import dbt_charts.agent_api.docs._loader  # would AttributeError if shadowed

    assert docs_pkg.__name__ == "dbt_charts.agent_api.docs"
    assert dbt_charts.agent_api.docs._loader.__name__.endswith("._loader")
