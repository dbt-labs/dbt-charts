"""Tests for the dct serve nav routing + HTML generation (nav_context, nav_html).

The nav is rendered as its own board through the normal pipeline (see
test_serve_nav for the serve-only integration), so it inherits the board's theme
and styling. This module only covers the pure path → HTML derivation:

- ``nav_context`` derives crumbs (dir links), the current label, and sibling
  file links from a (current_label, current_url, dir_ctx) triple.
- ``nav_html`` turns those facts into a single raw-HTML block with a home icon,
  slash-separated crumb links, and the current label as a custom menu trigger.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dbt_charts.core.project import Project
from dbt_charts.core.render.dir_context import lazy_dir_context
from dbt_charts.core.render.nav import nav_context

from ..._nav import nav_html
from ..._paths import DBT_CHARTS_PKG_DIR


def _dir_ctx(
    this_url: str,
    siblings: list[dict[str, Any]] | None = None,
    parent_url: str | None = None,
) -> dict[str, Any]:
    """Build a minimal dir_ctx dict for nav_html tests — no filesystem needed."""
    if siblings is None:
        siblings = []
    return {
        "this_dir": {"name": "test", "path": "", "url": this_url},
        "parent_dir": (
            {"name": "parent", "path": "", "url": parent_url}
            if parent_url is not None
            else None
        ),
        "siblings": siblings,
        "tree": "",
    }


def _file_siblings(*stems: str, url_prefix: str = "") -> list[dict[str, Any]]:
    """Build sibling dicts for non-directory board files."""
    return [
        {
            "name": f"{stem}.yml",
            "url": f"{url_prefix}/{stem}",
            "is_dir": False,
            "ext": ".yml",
            "label": stem,
        }
        for stem in stems
    ]


def _dir_sibling(name: str, url_prefix: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "url": f"{url_prefix}/{name}/",
        "is_dir": True,
        "ext": "",
        "label": name,
    }


class TestNavContext:
    def _project(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> Project:
        boards = tmp_path / "charts"
        (boards / "support").mkdir(parents=True)
        (boards / "sales").mkdir()
        (boards / "overview.yml").write_text("title: Overview\n")
        (boards / "support" / "tickets.yml").write_text("title: Tickets\n")
        return local_project(tmp_path)

    def test_root_file_current_label_is_stem(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        project = self._project(tmp_path, local_project)
        dir_ctx = lazy_dir_context(project.directory("charts"), url_mount_dir="charts")
        ctx = nav_context("overview", "/overview", dir_ctx)
        assert ctx is not None
        assert ctx["current_label"] == "overview"

    def test_root_file_has_no_crumbs(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        project = self._project(tmp_path, local_project)
        dir_ctx = lazy_dir_context(project.directory("charts"), url_mount_dir="charts")
        ctx = nav_context("overview", "/overview", dir_ctx)
        assert ctx is not None
        assert list(ctx["visible_crumbs"]) == []

    def test_nested_file_crumb_links_to_dir_index(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        project = self._project(tmp_path, local_project)
        dir_ctx = lazy_dir_context(
            project.directory("charts/support"), url_mount_dir="charts"
        )
        ctx = nav_context("tickets", "/support/tickets", dir_ctx)
        assert ctx is not None
        crumbs = [c for c in ctx["visible_crumbs"] if c is not None]
        assert len(crumbs) == 1
        assert crumbs[0]["label"] == "support"
        assert crumbs[0]["url"] == "/support/"
        assert ctx["current_label"] == "tickets"

    def test_deep_file_has_all_directory_crumbs(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        project_root = tmp_path / "proj"
        board = project_root / "charts" / "enterprise" / "finance" / "planning"
        board.mkdir(parents=True)
        (board / "cashflow.yml").write_text("title: Cashflow\n")
        project = local_project(project_root)
        dir_ctx = lazy_dir_context(
            project.directory("charts/enterprise/finance/planning"),
            url_mount_dir="charts",
        )
        ctx = nav_context("cashflow", "/enterprise/finance/planning/cashflow", dir_ctx)
        assert ctx is not None
        crumbs = [c for c in ctx["visible_crumbs"] if c is not None]
        assert crumbs == [
            {"label": "enterprise", "url": "/enterprise/"},
            {"label": "finance", "url": "/enterprise/finance/"},
            {"label": "planning", "url": "/enterprise/finance/planning/"},
        ]

    def test_empty_dir_ctx_returns_none(self) -> None:
        assert nav_context("anything", "/anything", {}) is None

    def test_directory_listing_label_and_empty_url(self) -> None:
        """For a directory listing the caller passes the directory's own name as
        the label and an empty URL — no peer file is selected."""
        dir_ctx = _dir_ctx(
            this_url="/sales/",
            siblings=_file_siblings("q3", "q4", url_prefix="/sales"),
        )
        ctx = nav_context("sales", "", dir_ctx)
        assert ctx is not None
        assert ctx["current_file_url"] == "/sales/"
        assert ctx["current_label"] == "sales"
        assert [f["label"] for f in ctx["files"]] == ["q3", "q4"]

    def test_directory_listing_uses_parent_crumbs_and_child_menu_entries(self) -> None:
        dir_ctx = _dir_ctx(
            this_url="/sales/",
            siblings=[_dir_sibling("q1", url_prefix="/sales")],
            parent_url="/",
        )
        ctx = nav_context("sales", "", dir_ctx)
        assert ctx is not None
        assert list(ctx["visible_crumbs"]) == []
        assert ctx["current_file_url"] == "/sales/"
        assert ctx["files"] == [{"label": "q1/", "url": "/sales/q1/"}]

    def test_nested_directory_exposes_parent_menu_item(self) -> None:
        dir_ctx = _dir_ctx(
            this_url="/sales/q1/",
            siblings=_file_siblings("summary", url_prefix="/sales/q1"),
            parent_url="/sales/",
        )
        ctx = nav_context("summary", "/sales/q1/summary", dir_ctx)
        assert ctx is not None
        assert ctx["parent"] == {"label": "Up one level", "url": "/sales/"}

    def test_root_directory_has_no_parent_menu_item(self) -> None:
        dir_ctx = _dir_ctx(
            this_url="/",
            siblings=_file_siblings("overview", url_prefix=""),
        )
        ctx = nav_context("overview", "/overview", dir_ctx)
        assert ctx is not None
        assert ctx["parent"] is None

    def test_root_directory_listing_hides_inspect_menu_entry(self) -> None:
        dir_ctx = _dir_ctx(
            this_url="/",
            siblings=[
                _dir_sibling("inspect"),
                _dir_sibling("sales"),
                *_file_siblings("overview"),
            ],
        )
        ctx = nav_context("/", "", dir_ctx)
        assert ctx is not None
        assert ctx["files"] == [
            {"label": "sales/", "url": "/sales/"},
            {"label": "overview", "url": "/overview"},
        ]

    def test_nested_directory_listing_does_not_hide_inspect_menu_entry(self) -> None:
        dir_ctx = _dir_ctx(
            this_url="/sales/",
            siblings=[_dir_sibling("inspect", url_prefix="/sales")],
            parent_url="/",
        )
        ctx = nav_context("sales", "", dir_ctx)
        assert ctx is not None
        assert ctx["files"] == [{"label": "inspect/", "url": "/sales/inspect/"}]


class TestNavHtml:
    def _nav_dir_ctx(self) -> dict[str, Any]:
        return _dir_ctx(
            this_url="/sales/",
            siblings=_file_siblings("q3", "q4", url_prefix="/sales"),
        )

    def test_root_home_link_present(self) -> None:
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert 'href="/"' in html
        assert 'aria-label="Project home"' in html

    def test_home_icon_is_house_not_dbt_charts_logo(self) -> None:
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert "<svg" in html
        assert "M2.35 7.15 8 2.25" in html
        assert 'x="14" y="32" width="16" height="24"' not in html
        assert 'x="34" y="12" width="16" height="44"' not in html

    def test_crumbs_are_anchor_links(self) -> None:
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert 'href="/sales/"' in html
        assert ">sales<" in html
        assert 'class="dbt-nav-crumb"' in html

    def test_current_file_is_custom_menu_not_native_select(self) -> None:
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert '<select class="dbt-nav-files"' not in html
        assert 'class="dbt-nav-file-trigger"' in html
        assert 'role="button"' in html
        assert 'aria-haspopup="listbox"' in html
        assert 'class="dbt-nav-file-menu"' in html
        assert 'role="listbox"' in html
        assert 'href="/sales/q3"' in html
        assert 'href="/sales/q4"' in html
        assert 'aria-selected="true"' in html

    def test_parent_directory_option_is_first_menu_item(self) -> None:
        ctx = _dir_ctx(
            this_url="/sales/q1/",
            siblings=_file_siblings("summary", "detail", url_prefix="/sales/q1"),
            parent_url="/sales/",
        )
        html = nav_html("summary", "/sales/q1/summary", ctx)
        menu_pos = html.index('class="dbt-nav-file-menu"')
        parent_pos = html.index(
            'class="dbt-nav-menu-option dbt-nav-menu-parent"', menu_pos
        )
        first_file_pos = html.index('href="/sales/q1/detail"', menu_pos)
        assert parent_pos < first_file_pos
        assert 'href="/sales/"' in html[parent_pos:first_file_pos]
        assert 'aria-label="Up one level"' in html[parent_pos:first_file_pos]
        assert ">Up one level<" in html[parent_pos:first_file_pos]

    def test_directory_listing_current_label_is_dropdown_with_child_dirs(self) -> None:
        ctx = _dir_ctx(
            this_url="/sales/",
            siblings=[_dir_sibling("q1", url_prefix="/sales")],
            parent_url="/",
        )
        html = nav_html("sales", "", ctx)
        assert html.count(">sales<") == 1
        assert 'class="dbt-nav-file-trigger"' in html
        assert 'href="/sales/"' in html
        assert 'aria-label="Up one level"' in html
        assert ">q1/<" in html

    def test_root_menu_has_no_parent_directory_option(self) -> None:
        ctx = _dir_ctx(
            this_url="/",
            siblings=_file_siblings("overview", url_prefix=""),
        )
        html = nav_html("overview", "/overview", ctx)
        assert 'class="dbt-nav-menu-parent"' not in html
        assert 'aria-label="Up one level"' not in html

    def test_sibling_folders_are_not_inline_pipe_links(self) -> None:
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert "/sales/east/" not in html
        assert "/sales/west/" not in html
        assert "|" not in html

    def test_board_view_dropdown_includes_sibling_directories(self) -> None:
        ctx = _dir_ctx(
            this_url="/sales/",
            siblings=[
                _dir_sibling("regional", url_prefix="/sales"),
                *_file_siblings("q3", "q4", url_prefix="/sales"),
            ],
        )
        ctx_result = nav_context("q3", "/sales/q3", ctx)
        assert ctx_result is not None
        labels = [f["label"] for f in ctx_result["files"]]
        assert "regional/" in labels
        assert "q3" in labels
        assert "q4" in labels

    def test_no_sibling_folder_pipe_section(self) -> None:
        ctx = _dir_ctx(
            this_url="/",
            siblings=_file_siblings("overview", url_prefix=""),
        )
        html = nav_html("overview", "/overview", ctx)
        assert "|" not in html
        assert "overview" in html

    def test_four_directory_crumbs_stay_visible(self) -> None:
        ctx = _dir_ctx(
            this_url="/enterprise/finance/planning/monthly/",
            siblings=_file_siblings("cashflow", url_prefix="/enterprise"),
        )
        html = nav_html("cashflow", "/enterprise/cashflow", ctx)
        assert ">enterprise<" in html
        assert ">finance<" in html
        assert ">planning<" in html
        assert ">monthly<" in html
        assert 'class="dbt-nav-ellipsis"' not in html

    def test_five_directory_crumbs_collapse_middle(self) -> None:
        ctx = _dir_ctx(
            this_url="/enterprise/finance/planning/monthly/north-america/",
            siblings=_file_siblings("cashflow", url_prefix="/enterprise"),
        )
        html = nav_html("cashflow", "/enterprise/cashflow", ctx)
        assert ">enterprise<" in html
        assert 'class="dbt-nav-ellipsis"' in html
        assert ">finance<" not in html
        assert ">monthly<" in html
        assert ">north-america<" in html

    def test_labels_are_html_escaped(self) -> None:
        # this_url "/a&b/" produces a crumb labeled "a&b"; the sibling label
        # "c<d>" exercises angle-bracket escaping in the file menu.
        ctx = _dir_ctx(
            this_url="/a&b/",
            siblings=[
                {
                    "name": "c<d>.yml",
                    "url": "/a&b/c<d>",
                    "is_dir": False,
                    "ext": ".yml",
                    "label": "c<d>",
                }
            ],
        )
        html = nav_html("c<d>", "/a&b/c<d>", ctx)
        assert "a&amp;b" in html
        assert "c&lt;d&gt;" in html
        # Raw & or < must not appear in text positions
        assert "&b" not in html
        assert "c<d" not in html

    def test_output_is_a_raw_html_block_with_div(self) -> None:
        """nav_html must return raw HTML (starting with <div), not markdown."""
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert html.strip().startswith("<div")

    def test_nav_html_contains_no_style_tag(self) -> None:
        """CSS is now in nav.css; nav_html must not contain inline <style>."""
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert "<style>" not in html
        assert "<style " not in html

    def test_nav_html_contains_no_script_tag(self) -> None:
        """JS is now in nav.js; nav_html must not contain inline <script>."""
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert "<script>" not in html
        assert "<script " not in html

    def test_download_select_has_class_and_options(self) -> None:
        """Download select must have dbt-nav-download class and svg/png/pdf options."""
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert 'class="dbt-nav-download"' in html
        assert 'value="svg"' in html
        assert 'value="png"' in html
        assert 'value="pdf"' in html

    def test_download_select_placeholder_is_empty_no_text(self) -> None:
        """The download control shows the icon only — no visible 'Download' label
        (an invisible aria-label is fine for accessibility)."""
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        # No visible label text — the first option is empty.
        assert ">Download<" not in html
        download_pos = html.index('class="dbt-nav-download"')
        empty_opt_pos = html.index('value="" selected></option>', download_pos)
        svg_opt_pos = html.index('value="svg"', download_pos)
        assert download_pos < empty_opt_pos < svg_opt_pos

    def test_download_control_uses_chrome_color(self) -> None:
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        assert 'class="dbt-nav-download-icon"' in html
        assert 'fill="currentColor"' in html

    def test_download_select_has_no_inline_style(self) -> None:
        """The <select> overlay positioning is in nav.css; no inline style= on it."""
        html = nav_html("q3", "/sales/q3", self._nav_dir_ctx())
        select_pos = html.index('class="dbt-nav-download"')
        style_end = html.index(">", select_pos)
        select_tag = html[select_pos:style_end]
        assert "style=" not in select_tag

    def test_download_control_gets_same_hover_surface_as_nav_triggers(self) -> None:
        css = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.css").read_text()
        hover_rule_start = css.index(".dbt-nav-home:hover")
        hover_rule = css[hover_rule_start : hover_rule_start + 320]
        assert ".dbt-nav-file-trigger:hover" in hover_rule
        assert ".dbt-nav-download-wrap:hover" in hover_rule
        assert "color-mix(in srgb,var(--dbt-system-muted)" in hover_rule

    def test_nav_path_surfaces_use_muted_theme_role(self) -> None:
        """Serve nav path items are chrome, so they should consume the softer
        variable/control text role instead of the board body text color."""
        css = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.css").read_text()
        for selector in [
            ".dbt-nav-home",
            ".dbt-nav-crumb",
            ".dbt-nav-file",
            ".dbt-nav-file-trigger",
        ]:
            rule_start = css.index(f"{selector}{{")
            rule = css[rule_start : css.index("}", rule_start)]
            assert "color:var(--dbt-system-muted)" in rule
            assert "color:var(--dbt-system-text)" not in rule

    def test_nav_text_line_height_leaves_room_for_descenders(self) -> None:
        """The nav file label uses overflow for ellipsis; a unit line-height
        can clip descenders like 'g' inside that flex item."""
        css = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.css").read_text()
        strip_start = css.index(".dbt-nav-strip{")
        strip_rule = css[strip_start : css.index("}", strip_start)]
        assert "line-height:1.2" in strip_rule
        assert "line-height:1;" not in strip_rule

    def test_nav_file_menu_uses_solid_menu_surface(self) -> None:
        """The file menu is an overlay; it must not inherit transparent input
        backgrounds from inline variable controls."""
        css = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.css").read_text()
        menu_start = css.index(".dbt-nav-file-menu{")
        menu_rule = css[menu_start : css.index("}", menu_start)]
        assert "background:var(--dbt-system-menu-background)" in menu_rule
        assert "background:var(--dbt-system-input-background)" not in menu_rule

    def test_nav_file_menu_selected_row_uses_neutral_surface(self) -> None:
        css = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.css").read_text()
        selector = ".dbt-nav-file-menu .dbt-nav-menu-option[aria-selected='true']{"
        selected_start = css.index(selector)
        selected_rule = css[selected_start : css.index("}", selected_start)]
        assert "color-mix(in srgb,var(--dbt-system-muted)" in selected_rule
        assert "var(--dbt-system-accent)" not in selected_rule

    def test_nav_file_menu_is_scroll_capped(self) -> None:
        css = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.css").read_text()
        menu_start = css.index(".dbt-nav-file-menu{")
        menu_rule = css[menu_start : css.index("}", menu_start)]
        assert "max-height:" in menu_rule
        assert "overflow-y:auto" in menu_rule

    def test_nav_menu_option_click_closes_menu(self) -> None:
        js = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.js").read_text()
        assert "opts(m).forEach" in js
        assert "closeOne(t,m)" in js

    def test_view_control_offers_the_three_modes(self) -> None:
        """Fit width is the default because it is what the board does with no
        control present — selecting it must be a no-op, not a mode change."""
        html = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.html").read_text()
        view_start = html.index('<span class="dbt-nav-view-wrap"')
        view = html[view_start : html.index("</span>", html.index("</select>"))]
        for value in ("fit-width", "fit-height", "actual"):
            assert f'value="{value}"' in view
        assert 'value="fit-width" selected' in view

    def test_view_control_sits_left_of_download(self) -> None:
        html = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.html").read_text()
        assert html.index("dbt-nav-view-wrap") < html.index("dbt-nav-download-wrap")

    def test_view_control_reuses_the_download_icon_affordance(self) -> None:
        """Same overlaid-select pattern as download — an invisible full-size
        select over an icon. A second interaction idiom in one nav is noise."""
        css = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.css").read_text()
        rule_start = css.index(".dbt-nav-view{")
        rule = css[rule_start : css.index("}", rule_start)]
        assert "opacity:0" in rule
        assert "position:absolute" in rule

    def test_actual_size_view_is_centered(self) -> None:
        """100% on a wide monitor otherwise pins a narrow board to the left
        edge with the rest of the window empty."""
        css = (DBT_CHARTS_PKG_DIR / "core/render/templates/page.css").read_text()
        # page.css is expanded, not minified like nav.css — compare unspaced, and
        # match the selector exactly so the `> svg` rule cannot satisfy this.
        packed = re.sub(r"\s+", "", css)
        centered: list[str] = []
        for rule in packed.split("}"):
            if "{" not in rule:
                continue
            selectors, body = rule.split("{", 1)
            # Auto margins, not text-align: the artifact sets display:block inline.
            if "margin-inline:auto" in body:
                centered += selectors.split(",")
        assert ".dbt-view-actual.dbt-charts-svg-container>svg" in centered

    def test_host_drops_the_inline_sizing_hints(self) -> None:
        """`boards.py` writes `max-width:100%; height:auto` onto the root SVG so a
        bare .svg embeds well. Inline style beats every stylesheet rule, so left
        in place they pin the board and no view mode can change its size."""
        js = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.js").read_text()
        assert "removeProperty('height')" in js
        assert "removeProperty('max-width')" in js

    def test_view_mode_persists_across_dashboards(self) -> None:
        """The nav walks between boards; re-picking the mode on every hop is
        the kind of papercut that makes the control not worth having."""
        js = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.js").read_text()
        assert "localStorage" in js

    def test_view_change_resyncs_the_nav_inset(self) -> None:
        """syncInset positions the nav off the rendered SVG rect, which every
        one of these modes changes — a stale inset leaves the nav misaligned."""
        js = (DBT_CHARTS_PKG_DIR / "core/render/templates/nav/nav.js").read_text()
        view_handler = js[js.index("dbt-nav-view") :]
        assert "resize" in view_handler


class TestRenderNavHtml:
    def _project(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> Project:
        (tmp_path / "charts" / "sales").mkdir(parents=True)
        (tmp_path / "charts" / "overview.yml").write_text("title: Overview\n")
        (tmp_path / "charts" / "sales" / "trend.yml").write_text("title: Trend\n")
        return local_project(tmp_path)

    def test_renders_the_strip_with_its_styles_and_script(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.render.nav import render_nav_html

        project = self._project(tmp_path, local_project)

        html = render_nav_html(
            project.directory("charts/sales"),
            "charts",
            "trend",
            None,
            "",
            show_download=True,
            show_actions=True,
        )

        assert 'class="dbt-nav"' in html
        assert 'href="/sales/"' in html
        assert "<style>" in html
        assert "<script>" in html
        assert 'class="dbt-nav-data"' in html

    def test_a_host_without_a_server_hides_the_actions(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        """The data browser, view modes and downloads all need dct serve's own
        routes; a host without them shows the paths only."""
        from dbt_charts.core.render.nav import render_nav_html

        project = self._project(tmp_path, local_project)

        html = render_nav_html(
            project.directory("charts/sales"),
            "charts",
            "trend",
            None,
            "",
            show_download=False,
            show_actions=False,
        )

        assert 'href="/sales/"' in html
        assert 'class="dbt-nav-actions"' not in html
