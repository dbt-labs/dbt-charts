import pytest

from ._dangling_refs import find_dangling_refs


def test_reports_a_ref_with_no_defs_entry() -> None:
    schema = {
        "properties": {"a": {"$ref": "#/$defs/Missing"}, "b": {"$ref": "#/$defs/Ok"}},
        "$defs": {"Ok": {}},
    }
    assert find_dangling_refs(schema) == {"Missing"}


def test_rejects_a_local_ref_outside_defs() -> None:
    with pytest.raises(AssertionError, match="unrecognized local"):
        find_dangling_refs({"$ref": "#/definitions/X", "$defs": {}})
