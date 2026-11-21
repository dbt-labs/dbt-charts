"""Shared JSON output helper for dct verbs."""

from __future__ import annotations

from pydantic import BaseModel


def print_json_result(result: BaseModel, *, exclude_none: bool = True) -> None:
    """Write result as JSON to stdout — stable wire contract for all dct verbs.

    ``exclude_none`` defaults True for local project verbs (query/impact/docs/
    describe), whose ``--json`` contract omits unset optional fields —
    pinned by ``tests/cli/test_json_output_parity.py``. ``dct cloud`` passes
    ``exclude_none=False``: its models are the exact wire contract
    ``apps/cloud/apps/api/transport.py`` serializes server-side
    (``model_dump(mode="json")``, no exclusion), and a caller parsing the
    CLI's JSON must see the same keys the HTTP API answers with — a ``null``
    ``last_test_success`` (untested) must not vanish into "the key is just
    missing."
    """
    print(result.model_dump_json(exclude_none=exclude_none, indent=2))
