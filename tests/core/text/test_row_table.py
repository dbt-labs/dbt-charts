from __future__ import annotations

from typing import Any

from dbt_charts.core.text.row_table import row_table, rows_csv_lines


def test_a_row_missing_a_column_renders_as_null() -> None:
    table = row_table([{"a": 1, "b": 2}, {"a": 3}], ["a", "b"])
    assert table.split("\n")[3] == "| 3 | null |"


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    return list(rows_csv_lines(rows, columns))


def test_csv_header_is_always_present() -> None:
    assert _csv([], ["a", "b"]) == ["a,b"]


def test_csv_null_empty_string_and_none_string_differ() -> None:
    rows: list[dict[str, Any]] = [
        {"v": None, "w": "x"},
        {"v": "", "w": "x"},
        {"v": "None", "w": "x"},
    ]
    assert _csv(rows, ["v", "w"])[1:] == [",x", '"",x', "None,x"]


def test_csv_literal_two_quote_string_does_not_collide_with_empty_string() -> None:
    lines = _csv([{"v": ""}, {"v": '""'}], ["v"])
    assert lines[1] != lines[2]
    assert lines[2] == '""""""'


def test_csv_quotes_commas_and_embedded_quotes() -> None:
    assert _csv([{"v": 'a,"b"'}], ["v"])[1] == '"a,""b"""'


def test_csv_newline_in_cell_stays_on_one_line() -> None:
    assert _csv([{"v": "a\nb\r\nc"}], ["v"]) == ["v", "a\\nb\\nc"]


def test_csv_ragged_row_yields_empty_cell() -> None:
    assert _csv([{"a": 1, "b": 2}, {"a": 3}], ["a", "b"])[2] == "3,"


def test_csv_float_noise_is_trimmed() -> None:
    assert _csv([{"v": 13356.789999999998}], ["v"])[1] == "13356.79"


def test_header_states_each_column_once() -> None:
    rows: list[dict[str, Any]] = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    table = row_table(rows, ["a", "b"])
    assert table.split("\n") == [
        "| a | b |",
        "| --- | --- |",
        "| 1 | x |",
        "| 2 | y |",
    ]


def test_null_none_string_and_empty_string_render_differently() -> None:
    rows: list[dict[str, Any]] = [{"v": None}, {"v": "None"}, {"v": ""}]
    cells = [line.strip("| ") for line in row_table(rows, ["v"]).split("\n")[2:]]
    assert len(set(cells)) == 3


def test_newline_in_cell_does_not_start_a_row() -> None:
    rows: list[dict[str, Any]] = [{"v": "a\nb\r\nc"}]
    table = row_table(rows, ["v"])
    assert len(table.split("\n")) == 3


def test_pipe_in_cell_is_escaped() -> None:
    table = row_table([{"v": "a|b"}], ["v"])
    assert table.split("\n")[2] == "| a\\|b |"


def test_zero_rows_still_yield_a_header() -> None:
    table = row_table([], ["a", "b"])
    assert table == "| a | b |\n| --- | --- |"


def test_gap_after_inserts_a_marker_row() -> None:
    rows: list[dict[str, Any]] = [{"v": i} for i in range(3)]
    lines = row_table(rows, ["v"], gap_after=1).split("\n")
    assert lines[3] == "| ... |"
    assert len(lines) == 6


def test_float_noise_is_trimmed() -> None:
    table = row_table([{"v": 13356.789999999998}], ["v"])
    assert table.endswith("| 13356.79 |")
