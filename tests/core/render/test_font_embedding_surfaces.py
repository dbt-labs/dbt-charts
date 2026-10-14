"""Which rendered surface carries font bytes, and which keeps ``/static/fonts/`` URLs.

The split is the whole design, so it is pinned here rather than left to be inferred:

- ``format="html"`` with ``standalone=True`` — what ``dct render`` writes — carries the
  bytes, because nothing serves the file it lands in.
- Every live host (``format="html"`` without the flag) keeps the URLs. It is serving
  ``/static/fonts/`` itself, and inlining would put ~0.5 MiB of base64 into every
  page view for no benefit.
- ``format="svg"`` follows the same rule as html, because it is what ``dct render``
  writes by default and a ``.svg`` opened from disk needs its fonts just as much. The
  visual goldens render through ``format="svg"`` as well but never set the flag, which
  is what keeps the golden tree free of payloads.
- ``png``/``pdf`` never embed. They go through resvg, which binds fonts from the
  registered directory and never reads ``@font-face``.

Also pinned: the bytes appear once per board. An exported page used to declare the
whole block twice — once from ``page.css`` and once inside the board SVG's own
``<style>`` — which is a doubled payload the moment those URLs become data URIs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.fonts import (
    DBT_SERIF_OLDSTYLE_PROPORTIONAL_FONT_FAMILY,
    DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY,
    FONT_REGISTRY,
    SOURCE_CODE_PRO_FONT_FAMILY,
)
from dbt_charts.core.project import Project
from dbt_charts.core.render.font_selection import DECLARES_ITALIC

_BOARD_YAML = """\
title: "Font Embedding Board"
text: |
  Body prose with *emphasis* in it.
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""

_DATA_URI = "data:font/woff2;base64,"
_STATIC_URL = "/static/fonts/"
# An @font-face block for an italic board, as opposed to the `font-style: italic`
# that mdsvg's own emphasis CSS class also carries.
_ITALIC_BOARD = re.compile(r"@font-face \{[^}]*font-style: italic[^}]*\}")

# Measured but never served (VendoredFace.web_file is None): the six static weight
# boards vl-convert selects by family name. Read off the registry rather than listed
# by hand, so serving one of them — as main did for Source Code Pro — updates this
# instead of leaving a name nothing matches and an assertion that cannot fail.
_UNSERVED_FAMILIES = tuple(
    board.family for board in FONT_REGISTRY if board.web_file is None
)


@pytest.fixture
def board_and_executor(tmp_path: Path, local_project: Callable[..., Project]):
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.adapters import build_adapter_registry

    result = compile(_BOARD_YAML)
    assert result.board is not None
    return result.board, Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(tmp_path)),
    )


def _render(board_and_executor, **options: object) -> str:
    from dbt_charts.core.render import render

    board, executor = board_and_executor
    output = render(board, executor, **options).output
    assert isinstance(output, str)
    return output


class TestStandaloneHtmlCarriesTheBytes:
    def test_declares_data_uris(self, board_and_executor) -> None:
        html = _render(board_and_executor, format="html", standalone=True)
        assert _DATA_URI in html

    def test_names_no_static_url(self, board_and_executor) -> None:
        """A root-relative URL resolves only while a server is running."""
        html = _render(board_and_executor, format="html", standalone=True)
        assert _STATIC_URL not in html

    def test_no_board_is_embedded_twice(self, board_and_executor) -> None:
        """One base64 payload per board. The page used to declare the whole block
        twice — once from page.css, once inside the board SVG — so the same font's
        bytes would land in the file twice over."""
        html = _render(board_and_executor, format="html", standalone=True)
        payloads = re.findall(r"data:font/woff2;base64,([A-Za-z0-9+/=]+)", html)
        assert payloads
        assert len(payloads) == len(set(payloads))

    def test_carries_no_unserved_board(self, board_and_executor) -> None:
        html = _render(board_and_executor, format="html", standalone=True)
        boards = re.findall(r"@font-face \{[^}]*\}", html)
        assert boards
        for family in _UNSERVED_FAMILIES:
            assert not any(f"'{family}'" in block for block in boards), family

    def test_omits_a_family_the_board_never_paints(self, board_and_executor) -> None:
        """Selection is the point: no built-in theme names the oldstyle serifs."""
        html = _render(board_and_executor, format="html", standalone=True)
        assert DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY not in html
        assert DBT_SERIF_OLDSTYLE_PROPORTIONAL_FONT_FAMILY not in html


class TestLiveHostKeepsUrls:
    def test_html_without_the_flag_names_urls(self, board_and_executor) -> None:
        html = _render(board_and_executor, format="html")
        assert _STATIC_URL in html
        assert _DATA_URI not in html

    def test_declared_once_now_that_page_css_dropped_its_copy(
        self, board_and_executor
    ) -> None:
        html = _render(board_and_executor, format="html")
        assert html.count("InterVariable.woff2") == 1


class TestSvgFollowsTheFlag:
    """`dct render` writes .svg by default, so that artifact carries its fonts too.

    The 132 visual goldens render through this same path and must never grow font
    payloads — which holds because they never set the flag, not because the format
    is excluded.
    """

    def test_svg_without_the_flag_names_urls(self, board_and_executor) -> None:
        svg = _render(board_and_executor, format="svg")
        assert _STATIC_URL in svg
        assert _DATA_URI not in svg

    def test_svg_with_the_flag_carries_the_bytes(self, board_and_executor) -> None:
        svg = _render(board_and_executor, format="svg", standalone=True)
        assert _DATA_URI in svg
        assert _STATIC_URL not in svg

    def test_the_golden_harness_call_shape_stays_on_urls(
        self, board_and_executor
    ) -> None:
        """Pinned as the harness calls it (discovery.py renders with no options), so
        a future default flip is caught here rather than in 132 rewritten goldens."""
        svg = _render(board_and_executor, format="svg")
        assert _DATA_URI not in svg


_NO_EMPHASIS_YAML = """\
title: "Plain Board"
text: |
  Body prose with no emphasis at all.
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""


class TestItalicFollowsTheText:
    """The one board a theme cannot predict.

    Italic is reached through markdown emphasis, so whether an export needs the
    italic file is a fact about the prose — and Source Serif's italic is the single
    largest served file at 240 KiB.
    """

    def _export(self, yaml_source: str, tmp_path, local_project) -> str:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        result = compile(yaml_source)
        assert result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        output = render(result.board, executor, format="html", standalone=True).output
        assert isinstance(output, str)
        return output

    def test_emphasis_pulls_in_the_italic_board(self, tmp_path, local_project) -> None:
        html = self._export(_BOARD_YAML, tmp_path, local_project)
        assert _ITALIC_BOARD.search(html) is not None

    def test_prose_without_emphasis_leaves_it_out(
        self, tmp_path, local_project
    ) -> None:
        html = self._export(_NO_EMPHASIS_YAML, tmp_path, local_project)
        assert _ITALIC_BOARD.search(html) is None

    def test_leaving_it_out_is_worth_bytes(self, tmp_path, local_project) -> None:
        """Not a byte threshold — the italic file's size is a tunable the subset
        recipe owns. What is pinned is that dropping the board drops its payload."""
        with_italic = self._export(_BOARD_YAML, tmp_path, local_project)
        without = self._export(_NO_EMPHASIS_YAML, tmp_path, local_project)
        assert with_italic.count(_DATA_URI) == without.count(_DATA_URI) + 1
        assert len(without) < len(with_italic)


_SPARK_TRUNCATED_YAML = """\
title: "Truncated"
queries:
  q:
    type: values
    rows:
      - {category: Row A, n: 12}
      - {category: Row B, n: 11}
      - {category: Row C, n: 10}
      - {category: Row D, n: 9}
      - {category: Row E, n: 8}
      - {category: Row F, n: 7}
      - {category: Row G, n: 6}
      - {category: Row H, n: 5}
      - {category: Row I, n: 4}
      - {category: Row J, n: 3}
      - {category: Row K, n: 2}
      - {category: Row L, n: 1}
charts:
  s:
    query: q
    type: spark_bar
    x: n
    y: category
rows: [s]
"""

_BLOCKQUOTE_YAML = """\
title: "Quoted"
text: |
  A plain line.

  > A quoted line.
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""

_TITLE_EMPHASIS_YAML = """\
title: "Only the *title* is emphasized"
text: |
  Body prose with none at all.
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""

_CONDITIONAL_ITALIC_YAML = """\
title: "Flagged"
queries:
  q:
    type: values
    rows: [{region: North, revenue: 10}, {region: South, revenue: 900}]
charts:
  t:
    query: q
    type: table
    conditional_formatting:
      region:
        when:
          - eq: South
            font:
              style: italic
rows: [t]
"""


_NARROW_PROSE_YAML = """\
title: "Narrow"
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  prose:
    type: callout
    message: placeholder
  t: {query: q, type: table}
cols:
  - text: |
      A narrow column of prose carrying *emphasis*, laid out under roughly half the
      board width so it takes the single-column path.
  - t
"""


class TestItalicOutsideMarkdownEmphasis:
    """Italic is not only reached through emphasis, and prose is not the only source.

    Each fixture below carries no ``*emphasis*`` in its prose, so the only thing that
    can pull an italic board into the export is the mechanism the test names. Each was
    checked to fail when that mechanism is removed — which is how the callout
    recording shipped broken in the first place: its test passed on prose emphasis
    that had nothing to do with the path being claimed.
    """

    def _export(self, yaml_source: str, tmp_path, local_project) -> str:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        result = compile(yaml_source)
        assert result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        output = render(result.board, executor, format="html", standalone=True).output
        assert isinstance(output, str)
        return output

    def test_spark_bar_more_indicator(self, tmp_path, local_project) -> None:
        """A renderer writing italic <text> with the family on the same element."""
        html = self._export(_SPARK_TRUNCATED_YAML, tmp_path, local_project)
        assert "+2<" in html, "fixture did not actually truncate"
        assert _ITALIC_BOARD.search(html) is not None

    def test_theme_default_blockquote(self, tmp_path, local_project) -> None:
        """stark sets text.blockquote.font.style: italic, so every built-in theme
        paints a blockquote italic — through a CSS class rule, not a measured run."""
        html = self._export(_BLOCKQUOTE_YAML, tmp_path, local_project)
        assert re.search(r"md-[0-9a-f]{8}-blockquote", html), (
            "fixture did not render a blockquote"
        )
        assert _ITALIC_BOARD.search(html) is not None

    def test_blockquote_rule_alone_does_not_pull_it_in(
        self, tmp_path, local_project
    ) -> None:
        """The other direction: mdsvg emits that rule for prose with no blockquote
        in it, and a rule nothing wears must not cost 240 KiB."""
        html = self._export(_NO_EMPHASIS_YAML, tmp_path, local_project)
        assert re.search(r"md-[0-9a-f]{8}-blockquote", html), (
            "rule is expected to be present but unworn"
        )
        assert _ITALIC_BOARD.search(html) is None

    def test_emphasis_in_the_title_only(self, tmp_path, local_project) -> None:
        """The title is markdown too, and it is rendered through its own mdsvg
        renderer — the one render_title was rewritten to be able to report."""
        html = self._export(_TITLE_EMPHASIS_YAML, tmp_path, local_project)
        assert _ITALIC_BOARD.search(html) is not None

    def test_conditional_formatting_italic_cell(self, tmp_path, local_project) -> None:
        """An authored feature: one cell painted italic by a rule on the data.

        Applied to a text column on purpose. A numeric column paints in dbt Sans
        Tabular, which ships no italic row at all, so there is nothing to carry and
        the browser synthesizes an oblique — on a live board too, not only in an
        export."""
        html = self._export(_CONDITIONAL_ITALIC_YAML, tmp_path, local_project)
        assert _ITALIC_BOARD.search(html) is not None

    def test_callout_hint_line(self) -> None:
        """The error placard's hint, checked against the renderer's real output.

        A callout renders during the sizing pass and the main pass replays it from
        cache, so no render-time sink can see it — reading the finished markup is
        what covers it. Its family arrives XML-escaped (`&apos;Inter Variable&apos;`),
        which is why the pairing unescapes before matching. `hint` is set only by the
        internal error-placard path, so this drives the renderer directly.
        """
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style import resolve_style
        from dbt_charts.core.render.engine import render_callout_card
        from dbt_charts.core.render.font_selection import italic_families_in

        style = resolve_style(get_theme_style())
        svg = render_callout_card(
            message="Something went wrong while loading this chart.",
            placard={
                "code": "ERR-INTERNAL",
                "code_url": "https://docs.dbtcharts.com/reference/errors/#err-internal",
                "hint": "Check the query name and try again.",
                "doc_url": None,
            },
            width=400.0,
            style=style.chart_defaults.callout_error,
            chart_id="callout",
        )
        assert 'font-style="italic"' in svg
        # Not just truthiness: without the unescape the set is still non-empty, it
        # just holds `&apos;…&apos;`, which matches no registry family — so every
        # export with a placard would silently drop the italic board while a bare
        # `assert italic_families_in(svg)` stayed green. Asserted as "every family
        # found is a real registry family" rather than pinning which one, since the
        # callout's family is a theme default.
        found = italic_families_in(svg)
        assert found
        assert found <= {board.family for board in FONT_REGISTRY}

    def test_emphasis_in_single_column_prose(self, tmp_path, local_project) -> None:
        """The other prose return.

        `render_prose_svg` returns early for a single column, and every other prose
        fixture here renders wide enough to take the multi-column path — so the
        single-column recording had no coverage at all. A narrow `cols:` slot is an
        ordinary board shape, and emphasis there is measured against the real italic,
        so an export without it wraps for a board ~14% narrower than the one painted.
        """
        html = self._export(_NARROW_PROSE_YAML, tmp_path, local_project)
        assert _ITALIC_BOARD.search(html) is not None


class TestItalicIsAlwaysAttributable:
    """The invariant the pairing rests on, enforced instead of assumed.

    Every renderer that writes italic ``<text>`` puts ``font-family`` on the same
    start tag. Nothing made that true, and a new site that split them would drop its
    board from every export silently — the failure mode this whole area exists to
    close. This asserts it over rendered boards rather than trusting it.
    """

    def test_every_italic_start_tag_names_a_family(
        self, tmp_path, local_project
    ) -> None:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        unattributed: list[str] = []
        for yaml_source in (
            _BOARD_YAML,
            _SPARK_TRUNCATED_YAML,
            _BLOCKQUOTE_YAML,
            _CONDITIONAL_ITALIC_YAML,
            _NARROW_PROSE_YAML,
        ):
            result = compile(yaml_source)
            assert result.board is not None, result.errors
            executor = Executor(
                result.board,
                adapter_registry=build_adapter_registry(local_project(tmp_path)),
            )
            svg = render(result.board, executor, format="svg").output
            assert isinstance(svg, str)
            body = svg.split("</style>")[-1]
            for tag in re.findall(r"<[^<>]*>", body):
                # Same spelling the matcher accepts, so a renderer using the
                # style="…" form (the table engine does) cannot slip past a
                # guard that only looked for the attribute form. <tspan> is excluded
                # deliberately: an emphasis run inherits its family from an ancestor
                # and is the case the mdsvg sink covers.
                if tag.lstrip("<").startswith("tspan"):
                    continue
                if re.search(DECLARES_ITALIC, tag) and "font-family" not in tag:
                    unattributed.append(tag[:120])
        assert not unattributed, (
            "these tags paint italic with no family on the same tag, so the board "
            f"cannot be attributed and would be dropped from an export: {unattributed}"
        )


class TestMonoBoardFollowsProse:
    """Source Code Pro became a served board on main, so an export has to carry it.

    It needs no recording of its own: mdsvg names the mono family in a CSS class
    rule, so the family scan picks it up like any other stack. It emits that rule
    for any text at all, code or not — and a board's title renders through mdsvg
    too, so in practice every board carries the board: ~35 KiB, 46 KiB inlined,
    that most boards never paint.

    Left that way on purpose. The italic pairing can demand its class be worn
    because a missing italic costs a synthesized oblique, but for a *family* the
    two failure directions are not symmetric: a spare board wastes bytes, a missing
    one silently paints the board in fallback type at wrap points measured for
    something else. The scan errs toward spare. This pins the real cost so it is
    visible rather than surprising.
    """

    _CODE_YAML = """\
title: "Code"
text: |
  Prose with `inline code` in it.
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""

    def _blocks(self, yaml_source: str, tmp_path, local_project) -> str:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        result = compile(yaml_source)
        assert result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        output = render(result.board, executor, format="html", standalone=True).output
        assert isinstance(output, str)
        return "".join(re.findall(r"@font-face \{[^}]*\}", output))

    def test_code_span_embeds_the_mono_board(self, tmp_path, local_project) -> None:
        assert SOURCE_CODE_PRO_FONT_FAMILY in self._blocks(
            self._CODE_YAML, tmp_path, local_project
        )

    def test_prose_without_code_still_carries_it(self, tmp_path, local_project) -> None:
        """The accepted over-inclusion, asserted rather than assumed."""
        assert SOURCE_CODE_PRO_FONT_FAMILY in self._blocks(
            _NO_EMPHASIS_YAML, tmp_path, local_project
        )


class TestSelectionCostIsBounded:
    """A board of decimals used to take 20 seconds to pick its fonts.

    `[^{}]*` in the CSS-rule pattern will walk the whole document from every `.` it
    finds when there are few braces to stop it — and a table of numbers offers
    thousands of dots. It tripped the 30s suite timeout in CI, not a benchmark, so
    the guard here is the same: a decimal-heavy table board that renders inside the
    ordinary timeout, with the right boards. No hand-written millisecond bound, which
    would only add a flake.
    """

    def test_a_table_of_decimals_selects_promptly(
        self, tmp_path, local_project
    ) -> None:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        rows = "\n".join(
            f"      - {{label: row {i}, value: {i}.{i:02d}}}" for i in range(200)
        )
        yaml_source = (
            'title: "Decimals"\nqueries:\n  q:\n    type: values\n    rows:\n'
            f"{rows}\ncharts:\n  t: {{query: q, type: table}}\nrows: [t]\n"
        )
        result = compile(yaml_source)
        assert result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        output = render(result.board, executor, format="html", standalone=True).output
        assert isinstance(output, str)
        assert _DATA_URI in output
