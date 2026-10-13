"""Local image refs in board markdown resolve to embedded ``data:`` URIs."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.image_assets import (
    image_scope,
    resolve_image_ref,
    rewrite_markdown_images,
    rewrite_markdown_text_images,
)
from mdsvg import parse
from mdsvg.types import (
    Blockquote,
    ImageBlock,
    Paragraph,
    SpanType,
    Table,
    UnorderedList,
)

from ...conftest import InMemoryProject

_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="600"></svg>'
_EMBEDDED = "data:image/svg+xml;base64,"


def _bound(files: dict[str, str]) -> AbstractContextManager[None]:
    return image_scope(InMemoryProject(Path("/proj"), files))


class TestResolveImageRef:
    @pytest.mark.parametrize(
        "ref",
        [
            "https://example.com/a.png",
            "HTTP://example.com/a.png",
            "//cdn.example.com/a.png",
            "data:image/svg+xml;base64,"
            "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4=",
        ],
    )
    def test_remote_and_data_refs_pass_through(self, ref: str) -> None:
        assert resolve_image_ref(ref, None) == ref

    def test_relative_ref_embeds_the_file(self) -> None:
        with _bound({"charts/img/a.svg": _SVG}):
            assert resolve_image_ref("img/a.svg", "charts").startswith(_EMBEDDED)

    def test_no_project_bound_raises(self) -> None:
        with pytest.raises(CompilationError, match="no project") as e:
            resolve_image_ref("img/a.svg", "charts")
        assert e.value.code.code == "ERR-IMAGE-REF-UNRESOLVED"

    def test_the_scope_ends_with_its_block(self) -> None:
        with _bound({"charts/img/a.svg": _SVG}):
            resolve_image_ref("img/a.svg", "charts")
        with pytest.raises(CompilationError, match="no project"):
            resolve_image_ref("img/a.svg", "charts")

    def test_no_location_raises(self) -> None:
        with _bound({}), pytest.raises(CompilationError, match="no project location"):
            resolve_image_ref("img/a.svg", None)

    def test_missing_file_names_the_ref(self) -> None:
        with (
            _bound({}),
            pytest.raises(CompilationError, match="charts/img/missing.svg"),
        ):
            resolve_image_ref("img/missing.svg", "charts")

    def test_malformed_file_raises_not_a_bare_value_error(self) -> None:
        with (
            _bound({"charts/img/broken.svg": "<not xml at all"}),
            pytest.raises(CompilationError, match="malformed image data"),
        ):
            resolve_image_ref("img/broken.svg", "charts")

    def test_malformed_data_ref_is_named_by_header_and_size(self) -> None:
        uri = "data:image/png;base64," + "!" * 12_000
        with pytest.raises(CompilationError) as e:
            resolve_image_ref(uri, None)
        assert "data:image/png;base64,… (12 KB)" in e.value.message
        assert "!!!!" not in e.value.message


class TestRewriteMarkdownImages:
    def test_every_place_an_image_can_sit_is_rewritten(self) -> None:
        markdown = (
            "![b](a.svg)\n\n"
            "See ![s](a.svg) here.\n\n"
            "> ![q](a.svg)\n\n"
            "- ![l](a.svg) item\n\n"
            "| x |\n| --- |\n| ![c](a.svg) |\n"
        )
        with _bound({"charts/a.svg": _SVG}):
            blocks = rewrite_markdown_images(list(parse(markdown)), "charts")
        block, para, quote, items, table = blocks
        assert isinstance(block, ImageBlock)
        assert isinstance(para, Paragraph)
        assert isinstance(quote, Blockquote)
        assert isinstance(items, UnorderedList)
        assert isinstance(table, Table)
        quoted = quote.blocks[0]
        assert isinstance(quoted, ImageBlock)
        spans = [
            *para.spans,
            *items.items[0].spans,
            *table.rows[0].cells[0].spans,
        ]
        urls = [block.url, quoted.url]
        urls += [s.url for s in spans if s.span_type is SpanType.IMAGE and s.url]
        assert len(urls) == 5
        assert all(url.startswith(_EMBEDDED) for url in urls)

    def test_alt_text_survives(self) -> None:
        with _bound({"charts/a.svg": _SVG}):
            (block,) = rewrite_markdown_images(list(parse("![alt](a.svg)")), "charts")
        assert isinstance(block, ImageBlock)
        assert block.alt == "alt"


class TestRewriteMarkdownTextImages:
    def test_every_local_image_in_the_text_is_embedded(self) -> None:
        text = "a ![x](img/a.svg) b ![y](img/a.svg){height=9} ![r](https://e.com/r.png)"
        with _bound({"charts/img/a.svg": _SVG}):
            out = rewrite_markdown_text_images(text, "charts")
        assert out.count(f"]({_EMBEDDED}") == 2
        assert "](https://e.com/r.png)" in out
        assert "{height=9}" in out
        assert "img/a.svg" not in out

    def test_a_bad_ref_raises_the_same_chart_error(self) -> None:
        with _bound({}), pytest.raises(CompilationError) as e:
            rewrite_markdown_text_images("![x](img/missing.svg)", "charts")
        assert e.value.code.code == "ERR-IMAGE-REF-UNRESOLVED"

    def test_only_images_are_rewritten_beside_code_links_and_prefixed_urls(
        self,
    ) -> None:
        text = (
            "![x](img/a.svg) `![x](img/a.svg)` [l](img/a.svg) "
            "[o](img/a.svg.html) ![y](img/a.svg.png)"
        )
        with _bound({"charts/img/a.svg": _SVG, "charts/img/a.svg.png": _SVG}):
            out = rewrite_markdown_text_images(text, "charts")
        assert out.startswith(f"![x]({_EMBEDDED}")
        assert " `![x](img/a.svg)` [l](img/a.svg) [o](img/a.svg.html) " in out
        assert out.count("](data:") == 2

    def test_a_ref_that_prefixes_another_ref_is_its_own_image(self) -> None:
        with _bound({"charts/a.png": _SVG, "charts/a.png.png": _SVG}):
            out = rewrite_markdown_text_images("![a](a.png) ![b](a.png.png)", "charts")
        assert out.count("](data:") == 2 and "a.png" not in out

    def test_an_image_in_a_fence_is_not_an_image(self) -> None:
        text = "```\n![x](img/a.svg)\n```"
        with _bound({}):
            assert rewrite_markdown_text_images(text, "charts") == text

    def test_the_rewrite_parses_to_the_rewritten_blocks(self) -> None:
        text = "- a ![x](img/a.svg){height=9}\n- b\n\n![y](img/a.svg)"
        with _bound({"charts/img/a.svg": _SVG}):
            out = rewrite_markdown_text_images(text, "charts")
            assert list(parse(out)) == rewrite_markdown_images(
                list(parse(text)), "charts"
            )
