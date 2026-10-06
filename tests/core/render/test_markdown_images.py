"""Local images in board markdown, end to end through ``render_dashboard``.

A relative image resolves against the directory of the file its markdown is
written in (the board's or the chart's ``base_dir``), through every surface
and both passes. Anchoring tests record every project read and give each
image a decoy of the same name in the directory it must not resolve against.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.core.board import BoardRenderResult, render_dashboard
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import BoardFile, InMemoryBoard

from ...conftest import InMemoryProject

_TALL = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="400"></svg>'
_WIDE = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="10"></svg>'
_EMBEDDED = 'href="data:image/svg+xml;base64,'


class _RecordingProject(InMemoryProject):
    def __init__(self, root: Path, files: dict[str, str]) -> None:
        super().__init__(root, files)
        self.reads: set[str] = set()

    def read_bytes(self, relpath: str) -> bytes:
        self.reads.add(relpath)
        return super().read_bytes(relpath)


def _render(
    project: InMemoryProject,
    board: BoardFile | None = None,
    variables: dict[str, Any] | None = None,
) -> BoardRenderResult:
    return render_dashboard(
        board=board or project.path("sales.yml").read_board(),
        variables=variables,
        project=project,
        adapter_registry=build_adapter_registry(project, read_only=False),
        result_cache=None,
        format="svg",
    )


def _image_reads(files: dict[str, str]) -> set[str]:
    project = _RecordingProject(Path("/proj"), files)
    result = _render(project)
    assert result.status == "ok", result.chart_errors or result.validation_errors
    return project.reads


def _svg(result: BoardRenderResult) -> str:
    assert result.status == "ok", result.chart_errors or result.validation_errors
    assert isinstance(result.data, str)
    return result.data


class TestAnchor:
    def test_nested_import_text_resolves_beside_the_imported_file(self) -> None:
        assert _image_reads(
            {
                "sales.yml": "title: Root\nrows:\n  - ./sub/nested.yml\n",
                "sub/nested.yml": "title: Nested\ntext: '![o](own.svg)'\n",
                "sub/own.svg": _TALL,
                "own.svg": _WIDE,
            }
        ) == {"sub/own.svg"}

    def test_inline_callout_in_an_imported_file_resolves_beside_it(self) -> None:
        assert _image_reads(
            {
                "sales.yml": "title: Root\nrows:\n  - ./sub/nested.yml\n",
                "sub/nested.yml": (
                    "rows:\n"
                    "  - note:\n"
                    "      type: callout\n"
                    "      message: '![s](shot.svg)'\n"
                ),
                "sub/shot.svg": _TALL,
                "shot.svg": _WIDE,
            }
        ) == {"sub/shot.svg"}

    def test_tab_text_resolves_beside_its_file(self) -> None:
        tabs = (
            "tabs:\n"
            "  items:\n"
            "    - title: A\n"
            "      text: '![a](logo.svg)'\n"
            "    - title: B\n"
            "      text: hello\n"
        )
        assert _image_reads({"sales.yml": tabs, "logo.svg": _TALL}) == {"logo.svg"}
        assert _image_reads(
            {
                "sales.yml": "title: Root\nrows:\n  - ./sub/nested.yml\n",
                "sub/nested.yml": tabs,
                "sub/logo.svg": _TALL,
                "logo.svg": _WIDE,
            }
        ) == {"sub/logo.svg"}

    def test_cross_file_chart_resolves_beside_its_declaring_file(self) -> None:
        assert _image_reads(
            {
                "sales.yml": "charts:\n  c: lib/notes.charts.c\nrows:\n  - c\n",
                "lib/notes.yml": (
                    "charts:\n"
                    "  c:\n"
                    "    type: callout\n"
                    "    title: '![t](shot.svg) Heads up'\n"
                    "    message: '![m](shot.svg)'\n"
                ),
                "lib/shot.svg": _TALL,
                "shot.svg": _WIDE,
            }
        ) == {"lib/shot.svg"}

    @pytest.mark.parametrize(
        ("inherited", "board", "written_in"),
        [
            (
                {"charts/base/base.yml": "text: '![l](logo.svg)'\n"},
                "extends: ../base/base.yml\ntitle: Ext\n",
                "charts/base",
            ),
            (
                {"charts/team/meta.yml": "text: '![l](logo.svg)'\n"},
                "title: Board\n",
                "charts/team",
            ),
            (
                {"charts/meta.yml": "title: '![l](logo.svg) Sales'\n"},
                "text: hi\n",
                "charts",
            ),
        ],
        ids=["extends", "meta", "parent-meta"],
    )
    def test_inherited_markdown_resolves_beside_the_file_that_wrote_it(
        self, inherited: dict[str, str], board: str, written_in: str
    ) -> None:
        project = _RecordingProject(
            Path("/proj"),
            {
                **inherited,
                "charts/team/sub/board.yml": board.replace("../base", "../../base"),
                f"{written_in}/logo.svg": _TALL,
                "charts/team/sub/logo.svg": _WIDE,
            },
        )
        result = _render(
            project, project.path("charts/team/sub/board.yml").read_board()
        )
        assert _EMBEDDED in _svg(result)
        assert project.reads == {f"{written_in}/logo.svg"}

    def test_pathless_board_resolves_against_the_charts_directory(self) -> None:
        project = InMemoryProject(Path("/proj"), {"charts/logo.svg": _TALL})
        result = _render(project, InMemoryBoard("text: '![l](logo.svg)'\n", path=None))
        assert _EMBEDDED in _svg(result)


def _templated(variable: str, default: str) -> list[str]:
    head = f"variables:\n  {variable}:\n    input: text\n    default: {default}\n"
    ref = f"![l]({{{{ {variable} }}}}/logo.svg)"
    return [
        f"{head}text: '{ref}'\n",
        f"{head}title: '{ref} Sales'\ntext: hello\n",
        f"{head}tabs:\n  items:\n    - title: A\n      text: '{ref}'\n",
        f"{head}rows:\n  - note:\n      type: callout\n      message: '{ref}'\n",
    ]


_SURFACES = ["text", "title", "tab", "callout"]


@pytest.mark.parametrize(
    "board_yaml", _templated("cdn", "https://cdn.example.com"), ids=_SURFACES
)
def test_a_templated_remote_image_renders(board_yaml: str) -> None:
    project = InMemoryProject(Path("/proj"), {"sales.yml": board_yaml})
    assert 'href="https://cdn.example.com/logo.svg"' in _svg(_render(project))


@pytest.mark.parametrize("board_yaml", _templated("folder", "img"), ids=_SURFACES)
def test_a_templated_path_that_renders_local_fails_its_block(board_yaml: str) -> None:
    """Every project file a board reads is named in its YAML, which is what lets a
    host tell a render is stale from the files alone: a template naming one is
    refused, not read."""
    project = _RecordingProject(
        Path("/proj"), {"sales.yml": board_yaml, "img/logo.svg": _TALL}
    )
    result = _render(project)
    assert result.status == "partial", result.validation_errors
    assert [e.code for e in result.chart_errors] == ["ERR-IMAGE-REF-UNRESOLVED"]
    assert "written out" in result.chart_errors[0].message
    assert project.reads == set()


def test_a_templated_alt_text_is_fine() -> None:
    project = InMemoryProject(
        Path("/proj"),
        {"sales.yml": "text: '![{{ 1 + 1 }}](logo.svg)'\n", "logo.svg": _TALL},
    )
    assert _EMBEDDED in _svg(_render(project))


def test_list_item_and_table_cell_images_are_embedded() -> None:
    project = InMemoryProject(
        Path("/proj"),
        {
            "sales.yml": (
                "text: |\n"
                "  - ![a](a.svg) first item\n"
                "\n"
                "  | Logo | Name |\n"
                "  | --- | --- |\n"
                "  | ![b](b.svg) | Acme |\n"
            ),
            "a.svg": _TALL,
            "b.svg": _WIDE,
        },
    )
    assert _svg(_render(project)).count(_EMBEDDED) == 2


def test_title_case_leaves_the_image_url_alone() -> None:
    project = InMemoryProject(
        Path("/proj"),
        {
            "sales.yml": (
                "title: '![logo](logo.svg) sales'\n"
                "text: hello\n"
                "style:\n  title:\n    font:\n      case: upper\n"
            ),
            "logo.svg": _TALL,
        },
    )
    svg = _svg(_render(project))
    assert _EMBEDDED in svg
    assert "SALES" in svg


def _zero_width_png_uri() -> str:
    ihdr = struct.pack(">IIBBBBB", 0, 16, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    png = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(ihdr))
        + chunk
        + struct.pack(">I", zlib.crc32(chunk))
    )
    return f"data:image/png;base64,{base64.b64encode(png).decode()}"


class TestErrorScope:
    """A bad image fails its own block or chart, never the board, and reaches
    ``chart_errors`` as ERR-IMAGE-REF-UNRESOLVED."""

    def _partial(self, board_yaml: str, files: dict[str, str] | None = None) -> str:
        project = InMemoryProject(Path("/proj"), files or {})
        result = _render(project, InMemoryBoard(board_yaml, path=None))
        assert result.status == "partial", result.validation_errors
        assert [e.code for e in result.chart_errors] == ["ERR-IMAGE-REF-UNRESOLVED"]
        assert isinstance(result.data, str)
        return result.data

    def test_missing_text_image(self) -> None:
        svg = self._partial("text: '![l](missing.svg)'\n")
        assert "Image error" in svg

    def test_malformed_data_uri_in_the_title(self) -> None:
        bad = "data:image/svg+xml;base64,bm90IGFuIHN2Zw=="
        svg = self._partial(f"title: '![s]({bad}) Sales'\ntext: hello\n")
        assert "Image error" in svg

    def test_zero_dimension_png_in_text_leaves_its_siblings(self) -> None:
        svg = self._partial(
            "rows:\n"
            f"  - text: '![s]({_zero_width_png_uri()})'\n"
            "  - note:\n"
            "      type: callout\n"
            "      message: still here\n"
        )
        assert "still here" in svg

    def test_bad_callout_image_fails_only_that_callout(self) -> None:
        svg = self._partial(
            "rows:\n"
            "  - bad:\n"
            "      type: callout\n"
            "      message: '![s](missing.svg)'\n"
            "  - good:\n"
            "      type: callout\n"
            "      message: a healthy callout\n"
        )
        assert "a healthy callout" in svg


def test_a_render_with_no_project_reports_the_image() -> None:
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.render.renderer import render

    result = compile("text: '![l](logo.svg)'\n")
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=AdapterRegistry(project=InMemoryProject(Path("/p"), {})),
        query_registry=result.query_registry,
        result_cache=None,
    )
    rendered = render(result.board, executor)
    assert [e.code for e in rendered.chart_errors] == ["ERR-IMAGE-REF-UNRESOLVED"]
    assert "no project" in rendered.chart_errors[0].message


def _charts_board(files: dict[str, str]) -> BoardRenderResult:
    project = _RecordingProject(Path("/proj"), files)
    return _render(project, project.path("charts/cards/board.yml").read_board())


def test_a_leading_slash_reads_from_the_charts_directory() -> None:
    result = _charts_board(
        {
            "charts/cards/board.yml": "text: '![l](/assets/logo.svg)'\n",
            "charts/assets/logo.svg": _TALL,
        }
    )
    assert _EMBEDDED in _svg(result)


def test_a_charts_rooted_path_without_the_slash_depends_on_the_tree() -> None:
    """Why no parse-time migration can rewrite `assets/x.svg` to `/assets/x.svg`:
    the same text names a file beside the board in one tree and only a file
    under charts/ in another, and a migration sees text, not files."""
    board = "text: '![l](assets/logo.svg)'\n"
    beside = _charts_board(
        {"charts/cards/board.yml": board, "charts/cards/assets/logo.svg": _TALL}
    )
    assert _EMBEDDED in _svg(beside)

    rooted = _charts_board(
        {"charts/cards/board.yml": board, "charts/assets/logo.svg": _TALL}
    )
    assert rooted.status == "partial"
    assert "write '/assets/logo.svg'" in rooted.chart_errors[0].message


def test_upper_case_title_with_a_lengthening_letter_renders() -> None:
    project = InMemoryProject(
        Path("/proj"),
        {
            "sales.yml": (
                "title: '**Straße** sales'\n"
                "text: hello\n"
                "style:\n  title:\n    font:\n      case: upper\n"
            )
        },
    )
    svg = _svg(_render(project))
    assert "STRASSE" in svg
    assert "SALES" in svg


def test_a_title_with_a_dotted_capital_i_renders_under_the_default_case() -> None:
    project = InMemoryProject(
        Path("/proj"), {"sales.yml": "title: 'İstanbul **sales**'\ntext: hello\n"}
    )
    assert "stanbul" in _svg(_render(project))


def test_json_output_does_not_expose_the_provenance_directory() -> None:
    """``base_dir`` is internal provenance, not authored schema: the json format
    round-trips back to a board, so a field there that YAML can't carry would
    leak into anything re-compiled from it."""
    project = InMemoryProject(
        Path("/proj"),
        {"sales.yml": ("rows:\n  - c:\n      type: callout\n      message: hello\n")},
    )
    result = render_dashboard(
        board=project.path("sales.yml").read_board(),
        project=project,
        adapter_registry=build_adapter_registry(project, read_only=False),
        result_cache=None,
        format="json",
    )

    assert result.status == "ok", result.chart_errors or result.validation_errors
    assert "base_dir" not in _keys(result.data)


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()
