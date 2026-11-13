"""The Cloud API's wire contract: its boundaries, and its strictness.

``contract.py`` is imported by two worlds — the ``dct cloud`` client and
``apps/cloud``'s API views (the shared wire contract). What keeps that legal is that it depends on
neither: no engine internals, no Django. These tests are what turns a stray
import into a failure instead of a circular-dependency surprise months later.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from dbt_charts.cloud_client import contract

PACKAGE = Path(contract.__file__).resolve().parent
MODULES = sorted(PACKAGE.glob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_there_are_modules_to_check() -> None:
    assert {p.name for p in MODULES} == {
        "__init__.py",
        "client.py",
        "config.py",
        "context.py",
        "contract.py",
        "errors.py",
        "published_to.py",
    }


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_module_imports_neither_django_nor_the_engine(path: Path) -> None:
    """A shape the CLI parses cannot depend on the server that emits it."""
    assert "django" not in _imported_roots(path)
    assert "from dbt_charts.core" not in path.read_text(encoding="utf-8")


def test_every_model_ignores_unknown_fields() -> None:
    """An installed ``dct`` lags the deployed Cloud, so every model -- base
    included -- ignores fields it has never heard of; ``ContractModel`` says
    why. A removed or renamed field still breaks older clients; that stays
    deliberate and is nothing this test can catch."""
    from pydantic import BaseModel

    models = [
        value
        for value in vars(contract).values()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value is not BaseModel
    ]
    assert models
    for model in models:
        assert model.model_config.get("extra") == "ignore", model.__name__


def test_an_unknown_error_code_does_not_parse() -> None:
    with pytest.raises(ValidationError):
        contract.ApiError.model_validate({"code": "teapot", "message": "no"})


def test_field_errors_default_to_empty_rather_than_absent() -> None:
    """A caller can always read ``field_errors``; a non-validation failure just
    has none. One shape, not two."""
    error = contract.ApiError(
        code=contract.ErrorCode.NOT_FOUND, message="No organization 'acme'."
    )
    assert error.field_errors == {}


def test_a_comment_thread_parses_and_ignores_fields_it_does_not_know() -> None:
    reply = {
        "id": "r1",
        "author": None,
        "author_kind": "member",
        "body": "Yes",
        "created_at": "2026-11-08T10:00:00Z",
        "edited_at": None,
    }
    thread = {
        **reply,
        "id": "t1",
        "author": "a@example.com",
        "board": "finance/revenue",
        "anchor": {"owner": "rev", "owner_label": "Revenue", "quote": "Rev"},
        "detached": False,
        "variables": {"region": "EU"},
        "branch": "main",
        "review": None,
        "resolved_at": None,
        "resolved_by": None,
        "replies": [reply],
        "added_later": 1,
    }

    parsed = contract.CommentList.model_validate({"threads": [thread]})

    assert parsed.threads[0].anchor.quote == "Rev"
    assert parsed.threads[0].replies[0].author is None
