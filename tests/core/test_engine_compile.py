"""`_engine.compile` holds a board compiled in Rust behind an opaque handle."""

import json

import pytest

from dbt_charts import _engine

HOST = {
    "boards_root": ".",
    "today": "2026-11-06",
    "default_theme": "clarity",
    "html_policy_ceiling": None,
    "sources": [],
}


class Files:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def read(self, path: str) -> str | None:
        return self.files.get(path)

    def list(self, dir: str) -> list[str]:
        prefix = f"{dir}/" if dir else ""
        return [
            p
            for p in self.files
            if p.startswith(prefix) and "/" not in p[len(prefix) :]
        ]

    def exists(self, path: str) -> bool:
        return path in self.files or any(p.startswith(f"{path}/") for p in self.files)


def test_compile_folds_extends_and_returns_a_handle() -> None:
    files = Files(
        {
            "base.yml": "title: Base\n",
            "a.yml": "extends: ./base.yml\n",
        }
    )
    board = _engine.compile(files, "a.yml", **HOST)
    assert isinstance(board, _engine.CompiledBoard)
    assert board.diagnostics() == []
    assert json.loads(board.index_json()) == {"public_ids": {}, "declared_below": []}


def test_a_missing_board_raises_with_its_message() -> None:
    with pytest.raises(_engine.CompileError, match="a.yml does not exist"):
        _engine.compile(Files({}), "a.yml", **HOST)


def test_an_invalid_board_carries_wire_diagnostics() -> None:
    with pytest.raises(_engine.CompileError) as raised:
        _engine.compile(Files({"a.yml": "bogus_key: 1\n"}), "a.yml", **HOST)
    codes = [d["code"] for d in raised.value.diagnostics]
    assert codes and all(c.startswith("ERR-") for c in codes)


def test_the_handle_exposes_no_board_model() -> None:
    board = _engine.compile(Files({"a.yml": "title: A\n"}), "a.yml", **HOST)
    assert sorted(n for n in dir(board) if not n.startswith("_")) == [
        "diagnostics",
        "index_json",
    ]


def test_a_coded_refusal_outside_invalid_keeps_its_code() -> None:
    with pytest.raises(_engine.CompileError) as raised:
        _engine.compile(
            Files({"a.yml": "title: A\n"}), "a.yml", **{**HOST, "default_theme": "nope"}
        )
    assert [d["code"] for d in raised.value.diagnostics] == [
        "ERR-INVALID-DEFAULT-THEME"
    ]
