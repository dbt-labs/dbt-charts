"""Tests for board link resolution.

Covers:
- Root-relative author paths → serve and Cloud URLs
- Relative paths (../, ./) resolution
- Suffix stripping (.md, .yml, .yaml)
- Query string preservation
- External URL passthrough (http, https, mailto, #)
- Branch merge for Cloud
- Markdown rewriting (full pipeline)
"""

import pytest

from dbt_charts.core.render.board_links import (
    LinkContext,
    resolve_href,
    rewrite_board_links,
)
from dbt_charts.core.render.errors import RenderError

# ---------------------------------------------------------------------------
# resolve_href — unit tests
# ---------------------------------------------------------------------------


class TestResolveHref:
    """Test individual href resolution."""

    # -- External URLs are untouched --

    @pytest.mark.parametrize(
        "href",
        [
            "https://example.com",
            "http://example.com",
            "mailto:user@example.com",
            "#section",
        ],
    )
    def test_external_urls_passthrough(self, href: str) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        assert resolve_href(href, ctx) == href

    @pytest.mark.parametrize(
        "href",
        [
            "?selected_region=west",
            "?var={{ x }}",
            "?status=open&region={{ x }}",
        ],
    )
    def test_query_string_only_passthrough(self, href: str) -> None:
        """Query-string-only links reach variables.js unmodified."""
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        assert resolve_href(href, ctx) == href

    @pytest.mark.parametrize(
        "href",
        [
            # Editor deeplinks: keep both scheme and ``.yaml`` suffix unchanged.
            # Compare UI uses ``cursor://file/{abs}/board.yaml`` to open the
            # migrator's emitted board.yaml directly in Cursor; without the
            # passthrough the rewriter mangled it to ``/cursor:/file/...`` and
            # stripped ``.yaml`` (treating it as a relative .yaml board link).
            "cursor://file//Users/me/repo/board.yaml",
            "cursor://file//abs/path/file.yml",
            "vscode://file/abs/path/file.md",
            "file:///Users/me/repo/notes.md",
        ],
    )
    def test_editor_deeplinks_passthrough(self, href: str) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        # Both scheme and .yaml/.yml/.md suffix must be preserved verbatim.
        assert resolve_href(href, ctx) == href

    # -- Serve: root-relative --

    def test_serve_root_relative(self) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = resolve_href("/zendesk/tickets/list", ctx)
        assert result == "/charts/zendesk/tickets/list"

    def test_serve_root_relative_with_query(self) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = resolve_href("/zendesk/tickets/list?status=open", ctx)
        assert result == "/charts/zendesk/tickets/list?status=open"

    def test_serve_root_relative_with_jinja_query(self) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = resolve_href("/zendesk/tickets/detail?id={{ ticket_id }}", ctx)
        assert result == "/charts/zendesk/tickets/detail?id={{ ticket_id }}"

    # -- Serve: relative paths --

    def test_serve_relative_sibling(self) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/tickets/list")
        result = resolve_href("../overview", ctx)
        assert result == "/charts/zendesk/overview"

    def test_serve_relative_current_dir(self) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/tickets/list")
        result = resolve_href("./detail", ctx)
        assert result == "/charts/zendesk/tickets/detail"

    def test_serve_relative_with_query(self) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/tickets/list")
        result = resolve_href("./detail?id=42", ctx)
        assert result == "/charts/zendesk/tickets/detail?id=42"

    def test_serve_relative_link_on_index_board_resolves_against_own_directory(
        self,
    ) -> None:
        """Regression: current_board_slug for an index board keeps its `index`
        leaf ("reports/index", not "reports"), so a relative link on
        charts/reports/index.yml must resolve against "reports" -- not one
        directory too high."""
        ctx = LinkContext(root="/charts", current_board_slug="reports/index")
        result = resolve_href("sibling", ctx)
        assert result == "/charts/reports/sibling"

    # -- Suffix stripping --

    def test_strip_md_suffix(self) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = resolve_href("/zendesk/tickets/list.md", ctx)
        assert result == "/charts/zendesk/tickets/list"

    def test_strip_yml_suffix(self) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = resolve_href("/zendesk/tickets/list.yml", ctx)
        assert result == "/charts/zendesk/tickets/list"

    def test_strip_yaml_suffix(self) -> None:
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = resolve_href("/zendesk/tickets/list.yaml", ctx)
        assert result == "/charts/zendesk/tickets/list"

    # -- Cloud: root-relative --

    def test_cloud_root_relative(self) -> None:
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/d",
        )
        result = resolve_href("/zendesk/tickets/list", ctx)
        assert result == "/acme/analytics/d/zendesk/tickets/list"

    def test_cloud_root_relative_with_query(self) -> None:
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/d",
        )
        result = resolve_href("/zendesk/tickets/list?status=open", ctx)
        assert result == "/acme/analytics/d/zendesk/tickets/list?status=open"

    # -- Cloud: branch in URL root, not query string --

    def test_cloud_branch_in_root_not_query_string(self) -> None:
        """When root already contains /b/<branch>/, links carry branch via path."""
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/b/feature-x/d",
        )
        result = resolve_href("/zendesk/tickets/list", ctx)
        assert "?branch=" not in result
        assert result == "/acme/analytics/b/feature-x/d/zendesk/tickets/list"

    def test_cloud_no_branch_root_emits_no_branch_param(self) -> None:
        """Default-branch context (no /b/ in root) emits no branch query param."""
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/d",
        )
        result = resolve_href("/zendesk/tickets/list", ctx)
        assert "?branch=" not in result
        assert result == "/acme/analytics/d/zendesk/tickets/list"

    # -- Cloud: relative paths --

    def test_cloud_relative(self) -> None:
        ctx = LinkContext(
            current_board_slug="zendesk/tickets/list",
            root="/acme/analytics/d",
        )
        result = resolve_href("../overview", ctx)
        assert result == "/acme/analytics/d/zendesk/overview"

    # -- Custom root (serve) --

    def test_serve_custom_root(self) -> None:
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/dashboards",
        )
        result = resolve_href("/zendesk/tickets/list", ctx)
        assert result == "/dashboards/zendesk/tickets/list"

    def test_serve_nested_root(self) -> None:
        ctx = LinkContext(
            current_board_slug="overview",
            root="/evals/charts",
        )
        result = resolve_href("/sql-leaderboard", ctx)
        assert result == "/evals/charts/sql-leaderboard"

    # -- Empty root (standard layout: charts/ at /) --

    def test_empty_root_root_link(self) -> None:
        """Link to / with empty root must produce / not //."""
        ctx = LinkContext(current_board_slug="review", root="")
        assert resolve_href("/", ctx) == "/"

    def test_empty_root_slug_link(self) -> None:
        ctx = LinkContext(current_board_slug="review", root="")
        assert resolve_href("/dashboards/overview", ctx) == "/dashboards/overview"

    def test_empty_root_with_query(self) -> None:
        ctx = LinkContext(current_board_slug="review", root="")
        result = resolve_href("/compare?dashboard_id=1", ctx)
        assert result == "/compare?dashboard_id=1"


# ---------------------------------------------------------------------------
# rewrite_board_links — markdown integration
# ---------------------------------------------------------------------------


class TestRewriteBoardLinks:
    """Test full markdown link rewriting."""

    def test_rewrites_root_relative_link(self) -> None:
        md = "[Open tickets](/zendesk/tickets/list?status=open)"
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = rewrite_board_links(md, ctx)
        assert result == "[Open tickets](/charts/zendesk/tickets/list?status=open)"

    def test_rewrites_multiple_links(self) -> None:
        md = (
            "See [tickets](/zendesk/tickets/list) "
            "or [detail](/zendesk/tickets/detail?id=1)"
        )
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = rewrite_board_links(md, ctx)
        assert "/charts/zendesk/tickets/list" in result
        assert "/charts/zendesk/tickets/detail?id=1" in result

    def test_preserves_external_links(self) -> None:
        md = "[Google](https://google.com) and [tickets](/zendesk/tickets/list)"
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = rewrite_board_links(md, ctx)
        assert "https://google.com" in result
        assert "/charts/zendesk/tickets/list" in result

    def test_preserves_vscode_command_links(self) -> None:
        md = "[Profile](command:profileTable)"
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        assert rewrite_board_links(md, ctx) == md

    def test_does_not_rewrite_image_links(self) -> None:
        md = "![Logo](/static/logo.png) and [tickets](/zendesk/tickets/list)"
        ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
        result = rewrite_board_links(md, ctx)
        assert "![Logo](/static/logo.png)" in result
        assert "/charts/zendesk/tickets/list" in result

    def test_preserves_non_link_text(self) -> None:
        md = "No links here, just text."
        ctx = LinkContext(current_board_slug="x")
        assert rewrite_board_links(md, ctx) == md

    def test_none_context_returns_unchanged(self) -> None:
        md = "[tickets](/zendesk/tickets/list)"
        assert rewrite_board_links(md, None) == md

    def test_cloud_rewrite(self) -> None:
        md = "[Tickets](/zendesk/tickets/list?status=open)"
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/b/dev/d",
        )
        result = rewrite_board_links(md, ctx)
        assert "/acme/analytics/b/dev/d/zendesk/tickets/list" in result
        assert "status=open" in result
        assert "?branch=" not in result


# ---------------------------------------------------------------------------
# Context variable integration
# ---------------------------------------------------------------------------


class TestLinkContextVar:
    """Test the contextvars-based link context."""

    def test_get_returns_none_by_default(self) -> None:
        from dbt_charts.core.render.board_links import (
            get_link_context,
            set_link_context,
        )

        set_link_context(None)
        assert get_link_context() is None

    def test_set_and_get(self) -> None:
        from dbt_charts.core.render.board_links import (
            get_link_context,
            set_link_context,
        )

        ctx = LinkContext(root="/charts", current_board_slug="x")
        set_link_context(ctx)
        assert get_link_context() is ctx
        set_link_context(None)  # cleanup


# ---------------------------------------------------------------------------
# Uniform root: dashboard and system-view links share the same root segment
# ---------------------------------------------------------------------------


class TestUniformRoot:
    """Dashboard links and system-view links from one LinkContext share the same root.

    This is the core invariant of the new design: core emits ``{root}/{slug}``
    for every slug, so the host's routing concern (``/d/`` reserved prefix) is
    expressed once in ``root``, not branched inside core.
    """

    def test_dashboard_and_data_link_share_root_segment(self) -> None:
        """A dashboard link and a /data/ link from the same context share the root."""
        ctx = LinkContext(root="/acme/proj/d", current_board_slug="sales/overview")
        dashboard_url = resolve_href("/sales/revenue", ctx)
        data_url = resolve_href("/data/wh/orders", ctx)
        # Both must begin with the same root
        assert dashboard_url.startswith("/acme/proj/d/")
        assert data_url.startswith("/acme/proj/d/")
        assert dashboard_url == "/acme/proj/d/sales/revenue"
        assert data_url == "/acme/proj/d/data/wh/orders"

    def test_no_system_view_prefix_special_casing(self) -> None:
        """Core no longer branches on data/ or inspector/ prefixes."""
        ctx = LinkContext(root="/o/p/d")
        data_url = resolve_href("/data/src/main/orders", ctx)
        inspector_url = resolve_href("/inspector/src/main/orders", ctx)
        # Both get the same root treatment — no bare-prefix output
        assert data_url == "/o/p/d/data/src/main/orders"
        assert inspector_url == "/o/p/d/inspector/src/main/orders"


# ---------------------------------------------------------------------------
# Root-based Cloud URL (replaces url_prefix + /d/ infix)
# ---------------------------------------------------------------------------


class TestRootBasedLinks:
    """root field maps canonical slug paths under a project scope.

    All slugs — dashboard or system view — get ``{root}/{slug}``; the host
    bakes ``/d/`` into root so core carries zero routing knowledge.
    """

    def test_dashboard_link_via_root(self) -> None:
        """Dashboard slugs are routed via the root, not an internal /d/ infix."""
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/d",
        )
        result = resolve_href("/zendesk/tickets/list", ctx)
        assert result == "/acme/analytics/d/zendesk/tickets/list"

    def test_data_link_via_root(self) -> None:
        """/data/.../ links get the same root prefix as dashboards."""
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/d",
        )
        result = resolve_href("/data/snowflake/analytics/sales/", ctx)
        assert result == "/acme/analytics/d/data/snowflake/analytics/sales"

    def test_inspector_link_via_root(self) -> None:
        """/inspector/.../ links also get the root prefix."""
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/d",
        )
        result = resolve_href("/inspector/snowflake/analytics/sales/id/", ctx)
        assert result == "/acme/analytics/d/inspector/snowflake/analytics/sales/id"

    def test_root_with_branch(self) -> None:
        """Branch lives in root, not in a query param."""
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/b/feature-x/d",
        )
        result = resolve_href("/zendesk/tickets/list", ctx)
        assert result == "/acme/analytics/b/feature-x/d/zendesk/tickets/list"
        assert "?branch=" not in result

    def test_root_with_branch_and_query(self) -> None:
        """Query params pass through unchanged when branch is already in root."""
        ctx = LinkContext(
            current_board_slug="zendesk/overview",
            root="/acme/analytics/b/feature-x/d",
        )
        result = resolve_href("/zendesk/tickets/list?status=open", ctx)
        assert "/acme/analytics/b/feature-x/d/zendesk/tickets/list" in result
        assert "status=open" in result
        assert "?branch=" not in result

    def test_empty_root_uses_slug_only(self) -> None:
        """With no root (boards-at-root), slug is emitted as /{slug}."""
        ctx = LinkContext(current_board_slug="zendesk/overview", root="")
        result = resolve_href("/zendesk/tickets/list", ctx)
        assert result == "/zendesk/tickets/list"


# ---------------------------------------------------------------------------
# Raw HTML anchor rewriting in html_policy text blocks
# ---------------------------------------------------------------------------


class TestRewriteRawAnchors:
    """Raw <a href="/path"> anchors in html_policy text must get the root prefix."""

    def test_cloud_raw_anchor_gets_prefixed(self) -> None:
        """Root-relative href in a raw anchor is rewritten under Cloud LinkContext."""
        html = '<a href="/zendesk/overview">Go</a>'
        ctx = LinkContext(
            current_board_slug="home",
            root="/org/proj/d",
        )
        result = rewrite_board_links(html, ctx)
        assert 'href="/org/proj/d/zendesk/overview"' in result
        assert 'href="/zendesk/overview"' not in result

    def test_cloud_raw_anchor_with_query(self) -> None:
        html = '<a href="/zendesk/tickets?status=open">Tickets</a>'
        ctx = LinkContext(
            current_board_slug="home",
            root="/org/proj/d",
        )
        result = rewrite_board_links(html, ctx)
        assert "/org/proj/d/zendesk/tickets" in result
        assert "status=open" in result

    def test_none_context_leaves_raw_anchor_unchanged(self) -> None:
        html = '<a href="/zendesk/overview">Go</a>'
        assert rewrite_board_links(html, None) == html

    def test_empty_root_raw_anchor_unchanged(self) -> None:
        """Empty-root context (boards-at-root serve) leaves raw anchors as-is."""
        html = '<a href="/zendesk/overview">Go</a>'
        ctx = LinkContext(current_board_slug="home", root="")
        result = rewrite_board_links(html, ctx)
        # empty root — raw anchors pass through unrewritten
        assert result == html

    def test_external_href_in_raw_anchor_untouched(self) -> None:
        html = '<a href="https://example.com">External</a>'
        ctx = LinkContext(
            current_board_slug="home",
            root="/org/proj/d",
        )
        result = rewrite_board_links(html, ctx)
        assert result == html

    def test_markdown_and_raw_anchor_both_rewritten(self) -> None:
        """Mixed content: both Markdown links and raw anchors are rewritten."""
        content = '[MD link](/zendesk/overview) and <a href="/zendesk/tickets">raw</a>'
        ctx = LinkContext(
            current_board_slug="home",
            root="/org/proj/d",
        )
        result = rewrite_board_links(content, ctx)
        assert "/org/proj/d/zendesk/overview" in result
        assert "/org/proj/d/zendesk/tickets" in result


# ---------------------------------------------------------------------------
# Origin-absolute links for exports
# ---------------------------------------------------------------------------


class TestLinkContextValidation:
    """LinkContext rejects malformed root and origin values at construction."""

    def test_root_must_start_with_slash(self) -> None:
        with pytest.raises(RenderError, match="root"):
            LinkContext(root="charts")

    def test_root_must_not_end_with_slash(self) -> None:
        with pytest.raises(RenderError, match="root"):
            LinkContext(root="/charts/")

    def test_origin_must_not_end_with_slash(self) -> None:
        with pytest.raises(RenderError, match="origin"):
            LinkContext(origin="https://example.com/")

    def test_empty_root_accepted(self) -> None:
        ctx = LinkContext(root="")
        assert ctx.root == ""

    def test_empty_origin_accepted(self) -> None:
        ctx = LinkContext(origin="")
        assert ctx.origin == ""

    def test_valid_root_accepted(self) -> None:
        ctx = LinkContext(root="/org/proj/d")
        assert ctx.root == "/org/proj/d"

    def test_valid_origin_accepted(self) -> None:
        ctx = LinkContext(origin="https://example.com")
        assert ctx.origin == "https://example.com"


class TestOriginAbsoluteLinks:
    """When LinkContext.origin is set, links are prefixed with the origin."""

    def test_origin_prepended_to_cloud_dashboard_link(self) -> None:
        """With origin set, resolve_href returns a fully-qualified URL."""
        ctx = LinkContext(
            root="/org/proj/d",
            origin="https://example.com",
        )
        result = resolve_href("/foo", ctx)
        assert result == "https://example.com/org/proj/d/foo"

    def test_no_origin_stays_root_relative(self) -> None:
        """Without origin, behavior is unchanged: root-relative only."""
        ctx = LinkContext(root="/org/proj/d")
        result = resolve_href("/foo", ctx)
        assert result == "/org/proj/d/foo"

    def test_origin_no_trailing_slash(self) -> None:
        """Origin must not produce double-slash at the join."""
        ctx = LinkContext(
            root="/org/proj/d",
            origin="https://example.com",
        )
        result = resolve_href("/zendesk/overview", ctx)
        assert result.startswith("https://example.com/")
        assert "//" not in result.replace("https://", "")

    def test_origin_with_serve_root(self) -> None:
        """Origin also works with a serve root."""
        ctx = LinkContext(
            current_board_slug="foo",
            root="/charts",
            origin="https://host.example",
        )
        result = resolve_href("/foo/bar", ctx)
        assert result == "https://host.example/charts/foo/bar"

    def test_origin_empty_string_no_prepend(self) -> None:
        """Empty-string origin (default) must not prepend anything."""
        ctx = LinkContext(
            root="/org/proj/d",
            origin="",
        )
        result = resolve_href("/zendesk/tickets", ctx)
        assert result == "/org/proj/d/zendesk/tickets"

    def test_origin_with_empty_root(self) -> None:
        """Origin + empty root for a boards-at-root export."""
        ctx = LinkContext(
            current_board_slug="foo",
            root="",
            origin="https://host.example",
        )
        result = resolve_href("/foo/bar", ctx)
        assert result == "https://host.example/foo/bar"
