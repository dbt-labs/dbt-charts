"""Tests for dbt_charts.agent_api.examples registry."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from dbt_charts.agent_api import ProjectSession
from dbt_charts.agent_api.examples import (
    _EXAMPLES_DIR,
    ExampleNotFound,
    _parse_specimen,
    get_example,
    list_examples,
    search_examples,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import raise_on_dashboard_failure

# _EXAMPLES_DIR is a Traversable; every shipped install is a real directory, so
# str()/Path() round-trips for glob-based discovery (same trick as test_skills).
_EXAMPLES_DIR_PATH = Path(str(_EXAMPLES_DIR))
SPECIMENS = sorted(_EXAMPLES_DIR_PATH.glob("*/*.yml"))


class TestListExamples:
    def test_lists_every_bundled_specimen(self) -> None:
        result = list_examples()
        assert result.success is True
        assert len(result.examples) == len(SPECIMENS)

    def test_slug_is_category_and_stem(self) -> None:
        slugs = {e.slug for e in list_examples().examples}
        assert "boards/kpi-overview" in slugs

    def test_index_carries_metadata_without_yaml(self) -> None:
        example = next(
            e for e in list_examples().examples if e.slug == "boards/kpi-overview"
        )
        assert example.category == "boards"
        assert example.title == "Revenue Overview"
        assert example.notes
        assert example.line_count > 0

    def test_sorted_by_slug(self) -> None:
        slugs = [e.slug for e in list_examples().examples]
        assert slugs == sorted(slugs)


class TestGetExample:
    def test_returns_full_board_yaml(self) -> None:
        example = get_example("boards/kpi-overview")
        assert example.slug == "boards/kpi-overview"
        assert "charts:" in example.yaml
        assert example.yaml.splitlines()[1].startswith("title:")

    def test_line_count_matches_yaml(self) -> None:
        example = get_example("boards/kpi-overview")
        assert example.line_count == len(example.yaml.splitlines())

    def test_unknown_slug_raises(self) -> None:
        with pytest.raises(ExampleNotFound, match="dct examples"):
            get_example("boards/does-not-exist")

    @pytest.mark.parametrize(
        "slug", ["../skills/board-build/SKILL.md", "/etc/passwd", "boards"]
    )
    def test_traversal_and_partial_slugs_raise(self, slug: str) -> None:
        with pytest.raises(ExampleNotFound):
            get_example(slug)


class TestSearchExamples:
    def test_matches_slug(self) -> None:
        hits = search_examples("kpi").hits
        assert any(h.slug == "kpis/kpi-variants" for h in hits)

    def test_matches_body(self) -> None:
        """The YAML-body tier — how an agent finds a specimen by a field name it
        saw rather than by slug.

        The query must appear ONLY in a specimen's YAML: anything that is also a
        substring of a slug, title, or notes short-circuits on an earlier
        branch of search_examples' if/elif chain and leaves this tier untested.
        `row_role` is a column name inside table-style-variants' inline data.
        """
        hits = search_examples("row_role").hits

        assert [(h.slug, h.score) for h in hits] == [
            ("tables/table-style-variants", 0.5)
        ]

    def test_scoring_tiers_rank_slug_then_metadata_then_body(self) -> None:
        """Two queries that between them land in all three tiers, so each
        branch's score is pinned: deleting any of them changes a list."""
        assert [(h.slug, h.score) for h in search_examples("table").hits] == [
            ("tables/table-style-variants", 1.0),
            ("boards/kpi-overview", 0.8),
        ]
        assert [(h.slug, h.score) for h in search_examples("revenue").hits] == [
            ("boards/kpi-overview", 0.8),
            ("kpis/kpi-variants", 0.5),
            ("tables/table-style-variants", 0.5),
        ]

    def test_schema_modeline_is_not_searched(self) -> None:
        assert search_examples("yaml-language-server").hits == []

    def test_no_match_returns_empty(self) -> None:
        assert search_examples("zzzznotathing").hits == []

    def test_blank_query_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            search_examples("   ")

    def test_limit_is_honored(self) -> None:
        assert len(search_examples("e", limit=2).hits) == 2


def test_specimens_exist() -> None:
    assert SPECIMENS, f"no specimens found under {_EXAMPLES_DIR_PATH}"


def _source_keys(node: object, trail: str = "") -> list[str]:
    """Every path at which ``node`` carries a `source` key, at any depth."""
    if isinstance(node, dict):
        found = [f"{trail}.source" for key in node if key == "source"]
        return found + [
            hit
            for key, value in node.items()
            for hit in _source_keys(value, f"{trail}.{key}")
        ]
    if isinstance(node, list):
        return [
            hit
            for index, value in enumerate(node)
            for hit in _source_keys(value, f"{trail}[{index}]")
        ]
    return []


@pytest.mark.parametrize("specimen", SPECIMENS, ids=lambda p: str(p.name))
def test_specimen_declares_no_source(specimen: Path) -> None:
    """A specimen naming a `source:` teaches agents to copy a source that does
    not exist in their project — the cargo-cult hole this registry must not open.
    Specimens carry inline `columns`/`values` data instead.

    Walks the parsed mapping rather than scanning lines: a line scan misses
    `{source: db}` and `- source: db`, which are the shapes a future edit is
    most likely to introduce.
    """
    parsed = yaml.safe_load(specimen.read_text(encoding="utf-8"))
    offenders = _source_keys(parsed)
    assert offenders == [], f"{specimen}: declares a source at {offenders}"


@pytest.mark.parametrize("specimen", SPECIMENS, ids=lambda p: str(p.name))
def test_specimen_is_a_board_not_a_prose_page(specimen: Path) -> None:
    """The registry's promise is a working board. A text-only layout tutorial
    duplicates the skills, costs more tokens, and hands the agent nothing to
    copy — `dct skills` is the surface for prose."""
    parsed = yaml.safe_load(specimen.read_text(encoding="utf-8"))
    assert parsed.get("charts"), f"{specimen}: no charts:"
    assert parsed.get("queries"), f"{specimen}: no queries:"


@pytest.mark.parametrize("specimen", SPECIMENS, ids=lambda p: str(p.name))
def test_specimen_renders_standalone(
    specimen: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Every specimen must render in a project that declares nothing — that is
    what makes it copy-pasteable into a customer repo."""
    project = local_project(specimen.parent)
    board_path = project.path_for_fspath(specimen)

    session = ProjectSession.from_project(project)
    try:
        rendered = session.render_board(board=board_path.read_board(), format="html")
        raise_on_dashboard_failure(rendered)
        output = rendered.data
    finally:
        session.close()
    assert output


class TestSpecimenValidation:
    """A specimen that cannot describe itself is a packaging error.

    The module promises this raises at load time rather than listing a blank
    cell — without these, deleting the guard leaves `title` as None, which
    `str(title).strip()` turns into the literal string "None" and serves
    happily through `dct examples --json`.
    """

    def test_missing_title_raises(self) -> None:
        with pytest.raises(ValueError, match="missing top-level 'title'"):
            _parse_specimen("boards/x", "notes: has one\ncharts: {}\n")

    def test_missing_notes_raises(self) -> None:
        with pytest.raises(ValueError, match="missing top-level 'notes'"):
            _parse_specimen("boards/x", "title: Has One\ncharts: {}\n")

    def test_non_mapping_raises(self) -> None:
        with pytest.raises(ValueError, match="not a YAML mapping"):
            _parse_specimen("boards/x", "- just\n- a list\n")

    def test_blank_title_is_missing_not_empty(self) -> None:
        """An empty string is as useless in a listing as an absent key."""
        with pytest.raises(ValueError, match="missing top-level 'title'"):
            _parse_specimen("boards/x", "title: ''\nnotes: d\n")

    def test_valid_specimen_round_trips(self) -> None:
        detail = _parse_specimen("boards/x", "title: T\nnotes: D\nrows: []\n")
        assert (detail.slug, detail.category, detail.title) == (
            "boards/x",
            "boards",
            "T",
        )
        assert detail.line_count == 3
