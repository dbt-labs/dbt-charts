"""An authored YAML syntax error has its own code, never ERR-INTERNAL."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api.import_closure import board_import_closure
from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile, compile_file
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.parse.meta import parse_meta_content
from dbt_charts.core.diagnostics.codes_compile import ERR_YAML_SYNTAX

COLON_TYPO = "title: Ten largest occupations in 1950: rank and workers in 2000"


def test_board_with_unquoted_colon_reports_the_yaml_syntax_code() -> None:
    result = compile(f"queries: {{}}\n{COLON_TYPO}\n", file="board.yml")

    assert not result.success
    (err,) = result.errors
    assert err.code == "ERR-YAML-SYNTAX"
    assert err.range is not None
    assert err.range.file == "board.yml"
    assert err.range.start_line == 2
    assert err.range.columns is not None
    assert err.range.columns.start_col == 39
    assert "board.yml" in err.message
    assert "line 2" in err.message
    assert COLON_TYPO in err.message
    assert err.fix is not None
    assert "quote" in err.fix.lower()
    assert "colon" in err.fix.lower()


def test_extends_fragment_with_unquoted_colon_reports_the_yaml_syntax_code(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "base.yml").write_text(f"{COLON_TYPO}\n")
    (tmp_path / "charts" / "variant.yml").write_text(
        "extends: ./base.yml\nrows:\n  - text: hi\n"
    )
    result = compile_file(
        local_project(tmp_path).path("charts/variant.yml").read_board()
    )

    assert not result.success
    (err,) = result.errors
    assert err.code == "ERR-YAML-SYNTAX"
    assert "base.yml" in err.message
    assert "line 1" in err.message
    assert COLON_TYPO in err.message
    assert err.range is None
    assert err.fix is not None
    assert "quote" in err.fix.lower()


def _project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir(exist_ok=True)
    return local_project(tmp_path)


def test_meta_yml_with_unquoted_colon_reports_the_yaml_syntax_code(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    (tmp_path / "charts" / "meta.yml").write_text(f"source: db\n{COLON_TYPO}\n")
    (tmp_path / "charts" / "b.yml").write_text("rows:\n  - text: hi\n")

    result = compile_file(project.path("charts/b.yml").read_board())

    assert not result.success
    (err,) = result.errors
    assert err.code == "ERR-YAML-SYNTAX"
    assert "charts/meta.yml" in err.message
    assert "line 2, column 39" in err.message
    assert COLON_TYPO in err.message
    assert err.range is None


def test_meta_content_with_unquoted_colon_reports_the_yaml_syntax_code() -> None:
    with pytest.raises(CompilationError) as exc_info:
        parse_meta_content(f"source: db\n{COLON_TYPO}\n", "meta.yml")

    assert exc_info.value.code is ERR_YAML_SYNTAX
    assert "meta.yml at line 2, column 39" in exc_info.value.message
    assert COLON_TYPO in exc_info.value.message


def test_cross_file_ref_to_a_file_with_unquoted_colon_reports_the_code(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    (tmp_path / "charts" / "data.yml").write_text(f"queries: {{}}\n{COLON_TYPO}\n")
    (tmp_path / "charts" / "b.yml").write_text(
        "charts:\n"
        "  c:\n"
        "    type: kpi\n"
        "    value: n\n"
        "    query: data.yml#raw\n"
        "rows:\n"
        "  - c\n"
    )

    result = compile_file(project.path("charts/b.yml").read_board())

    assert not result.success
    err = next(e for e in result.errors if e.code == "ERR-YAML-SYNTAX")
    assert "charts/data.yml" in err.message
    assert "line 2" in err.message
    assert COLON_TYPO in err.message


def test_import_closure_of_a_file_with_unquoted_colon_reports_the_code(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    (tmp_path / "charts" / "b.yml").write_text(f"{COLON_TYPO}\n")

    with pytest.raises(CompilationError) as exc_info:
        board_import_closure(project.path("charts/b.yml"))

    assert exc_info.value.code is ERR_YAML_SYNTAX
    assert "charts/b.yml at line 1" in exc_info.value.message
    assert COLON_TYPO in exc_info.value.message


def test_rows_import_of_a_file_with_unquoted_colon_reports_the_code(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    (tmp_path / "charts" / "part.yml").write_text(f"{COLON_TYPO}\n")
    (tmp_path / "charts" / "b.yml").write_text("rows:\n  - ./part.yml\n")

    result = compile_file(project.path("charts/b.yml").read_board())

    assert not result.success
    err = next(e for e in result.errors if e.code == "ERR-YAML-SYNTAX")
    assert "charts/part.yml at line 1" in err.message
    assert COLON_TYPO in err.message
    assert err.range is None


SECOND_TYPO = "  - text: Note to self: check the 2000 census"
BOARD_WITH_TYPO_AND_UNKNOWN_FIELD = f"{COLON_TYPO}\nbogus_key: 1\nrows:\n  - text: hi\n"


def test_colon_typo_and_schema_error_are_reported_in_one_compile() -> None:
    result = compile(BOARD_WITH_TYPO_AND_UNKNOWN_FIELD, file="board.yml")

    assert not result.success
    codes = [e.code for e in result.errors]
    assert codes[0] == "ERR-YAML-SYNTAX"
    assert "ERR-EXTRA-FIELD" in codes
    syntax = result.errors[0]
    assert syntax.range is not None
    assert syntax.range.start_line == 1
    assert "board.yml at line 1" in syntax.message
    assert COLON_TYPO in syntax.message
    assert "The rest of this report comes from the file with line 1 quoted." in (
        syntax.message
    )


def test_two_colon_typos_report_two_syntax_errors_and_the_rest() -> None:
    text = (
        f"{COLON_TYPO}\nbogus_key: 1\nrows:\n{SECOND_TYPO.replace('text', '- text')}\n"
    )
    result = compile(text, file="board.yml")

    codes = [e.code for e in result.errors]
    assert codes.count("ERR-YAML-SYNTAX") == 2
    assert codes[:2] == ["ERR-YAML-SYNTAX", "ERR-YAML-SYNTAX"]
    assert "ERR-EXTRA-FIELD" in codes
    assert [e.range.start_line for e in result.errors[:2] if e.range] == [1, 4]
    assert "line 4 quoted" in result.errors[1].message


def test_a_quoted_copy_survives_quotes_and_backslashes_in_the_value() -> None:
    result = compile(
        'title: Say "hi": a C:\\path\\n note\nbogus_key: 1\nrows:\n  - text: hi\n',
        file="board.yml",
    )

    codes = [e.code for e in result.errors]
    assert codes[0] == "ERR-YAML-SYNTAX"
    assert "ERR-EXTRA-FIELD" in codes


def test_a_line_that_is_not_key_value_reports_only_the_plain_syntax_error() -> None:
    # A mis-indented nested mapping: the failing line has no plain scalar to quote.
    result = compile("title: t\nrows:\n  - text: hi\n   bad: x: y\n", file="b.yml")

    assert [e.code for e in result.errors] == ["ERR-YAML-SYNTAX"]
    assert "quoted" not in result.errors[0].message.split("\n")[0]
    assert "The rest of this report" not in result.errors[0].message


def test_more_than_four_colon_typos_reports_only_the_plain_syntax_error() -> None:
    lines = "\n".join(f"k{i}: a: b" for i in range(5))
    result = compile(f"{lines}\n", file="b.yml")

    assert [e.code for e in result.errors] == ["ERR-YAML-SYNTAX"]
    assert "The rest of this report" not in result.errors[0].message


def test_compile_file_reports_the_typo_and_the_rest_in_one_pass(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    (tmp_path / "charts" / "b.yml").write_text(BOARD_WITH_TYPO_AND_UNKNOWN_FIELD)

    result = compile_file(project.path("charts/b.yml").read_board())

    codes = [e.code for e in result.errors]
    assert codes[0] == "ERR-YAML-SYNTAX"
    assert "ERR-EXTRA-FIELD" in codes
    assert "charts/b.yml at line 1" in result.errors[0].message


def test_render_of_a_board_with_the_typo_still_fails(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    (tmp_path / "charts" / "b.yml").write_text(f"{COLON_TYPO}\nrows:\n  - text: hi\n")

    result = ProjectSession(project=project).render_board(
        board=project.path("charts/b.yml").read_board(), format="svg"
    )

    assert result.status == "failed"
    assert [e.code for e in result.validation_errors] == ["ERR-YAML-SYNTAX"]
    assert (tmp_path / "charts" / "b.yml").read_text().startswith(COLON_TYPO)


def test_a_control_character_reports_the_syntax_error_without_raising() -> None:
    # PyYAML's ReaderError carries no position and is not a MarkedYAMLError.
    result = compile("title: hi\x07\ncharts: {}\n", file="charts/x.yml")

    assert [e.code for e in result.errors] == ["ERR-YAML-SYNTAX"]


def test_crlf_board_with_two_typos_reports_each_on_its_own_line() -> None:
    text = "title: a: b\r\nbogus_key: 1\r\nrows:\r\n  - text: c: d"
    result = compile(text, file="board.yml")

    codes = [e.code for e in result.errors]
    assert codes[:2] == ["ERR-YAML-SYNTAX", "ERR-YAML-SYNTAX"]
    assert "ERR-EXTRA-FIELD" in codes
    assert [e.range.start_line for e in result.errors[:2] if e.range] == [1, 4]
    extra = next(e for e in result.errors if e.code == "ERR-EXTRA-FIELD")
    assert extra.range is not None
    assert extra.range.start_line == 2


def test_line_separator_character_counts_as_a_line_break_for_the_repair() -> None:
    # libyaml breaks lines on U+2028 as well as on "\n".
    text = 'description: "a b"\ntitle: x: y'
    result = compile(text, file="board.yml")

    assert result.errors[0].code == "ERR-YAML-SYNTAX"
    assert result.errors[0].range is not None
    assert result.errors[0].range.start_line == 3


def test_markdown_front_matter_typo_reports_the_syntax_error_with_its_file_line(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project)
    (tmp_path / "charts" / "b.md").write_text(
        f"---\nboard:\n  {COLON_TYPO}\n---\n\n# Body\n"
    )

    result = compile_file(project.path("charts/b.md").read_board())

    (err,) = result.errors
    assert err.code == "ERR-YAML-SYNTAX"
    assert "charts/b.md at line 3" in err.message
    assert COLON_TYPO in err.message
    assert err.range is not None
    assert err.range.start_line == 3


def test_markdown_board_keeps_the_plain_syntax_error(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The copy would be re-read as markdown, so a markdown board is not repaired.
    # Its translated YAML is generated and does not carry the typo on its own,
    # so the translation is pinned to text that does.
    project = _project(tmp_path, local_project)
    (tmp_path / "charts" / "b.md").write_text("# Body\n")
    monkeypatch.setattr(
        "dbt_charts.core.compile.parse.markdown.markdown_to_yaml",
        lambda *_args, **_kwargs: f"{COLON_TYPO}\nbogus_key: 1\n",
    )
    monkeypatch.setattr(
        "dbt_charts.core.compile.parse.markdown.is_markdown_board_content",
        lambda *_args, **_kwargs: True,
    )

    result = compile_file(project.path("charts/b.md").read_board())

    assert [e.code for e in result.errors] == ["ERR-YAML-SYNTAX"]
