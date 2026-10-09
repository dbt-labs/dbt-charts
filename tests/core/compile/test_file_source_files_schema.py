"""Tests for the files: namespace schema on file source configs.

Covers the D-02/D-03 change: CsvSourceConfig / JsonSourceConfig / ParquetSourceConfig
now require a non-empty `files: dict[str, str]` mapping (table_name → path).
The old `file`, `url`, and `headers` fields are rejected (extra_forbidden).
DuckDBSourceConfig.path is unchanged.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    DuckDBSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
)


class TestCsvSourceConfig:
    def test_empty_files_raises(self) -> None:
        """Empty files mapping must raise a clear ValidationError."""
        with pytest.raises(
            ValidationError, match="files\n  Dictionary should have at least 1 item"
        ):
            CsvSourceConfig(type="csv", files={})

    def test_legacy_file_key_rejected(self) -> None:
        """Legacy `file:` field must be rejected (extra_forbidden)."""
        with pytest.raises(ValidationError):
            CsvSourceConfig(type="csv", file="data.csv")  # type: ignore[call-arg]

    def test_legacy_url_key_rejected(self) -> None:
        """Legacy `url:` field must be rejected (extra_forbidden)."""
        with pytest.raises(ValidationError):
            CsvSourceConfig(  # type: ignore[call-arg]
                type="csv", url="https://example.com/data.csv"
            )

    def test_legacy_headers_key_rejected(self) -> None:
        """Legacy `headers:` field must be rejected (extra_forbidden)."""
        with pytest.raises(ValidationError):
            CsvSourceConfig(  # type: ignore[call-arg]
                type="csv",
                url="https://example.com/data.csv",
                headers={"Authorization": "Bearer tok"},
            )

    def test_single_entry_files_valid(self) -> None:
        """A single-entry files: mapping parses successfully."""
        cfg = CsvSourceConfig(type="csv", files={"sales": "assets/data/sales.csv"})
        assert cfg.files == {"sales": "assets/data/sales.csv"}
        assert cfg.type == "csv"

    def test_multi_entry_files_valid(self) -> None:
        """Multi-entry files: mapping parses successfully."""
        cfg = CsvSourceConfig(
            type="csv",
            files={
                "sales": "data/sales.csv",
                "returns": "data/returns.csv",
            },
        )
        assert len(cfg.files) == 2
        assert cfg.files["returns"] == "data/returns.csv"

    def test_per_format_fields_preserved(self) -> None:
        """delimiter and encoding survive alongside files:."""
        cfg = CsvSourceConfig(
            type="csv",
            files={"data": "data.csv"},
            delimiter=";",
            encoding="latin-1",
        )
        assert cfg.delimiter == ";"
        assert cfg.encoding == "latin-1"


class TestJsonSourceConfig:
    def test_empty_files_raises(self) -> None:
        with pytest.raises(
            ValidationError, match="files\n  Dictionary should have at least 1 item"
        ):
            JsonSourceConfig(type="json", files={})

    def test_legacy_file_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JsonSourceConfig(type="json", file="data.json")  # type: ignore[call-arg]

    def test_legacy_url_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JsonSourceConfig(type="json", url="https://example.com/data.json")  # type: ignore[call-arg]

    def test_single_entry_files_valid(self) -> None:
        cfg = JsonSourceConfig(type="json", files={"products": "data/products.json"})
        assert cfg.files == {"products": "data/products.json"}


class TestParquetSourceConfig:
    def test_empty_files_raises(self) -> None:
        with pytest.raises(
            ValidationError, match="files\n  Dictionary should have at least 1 item"
        ):
            ParquetSourceConfig(type="parquet", files={})

    def test_legacy_file_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ParquetSourceConfig(type="parquet", file="data.parquet")  # type: ignore[call-arg]

    def test_legacy_url_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ParquetSourceConfig(type="parquet", url="https://example.com/data.parquet")  # type: ignore[call-arg]

    def test_single_entry_files_valid(self) -> None:
        cfg = ParquetSourceConfig(
            type="parquet", files={"events": "assets/data/events.parquet"}
        )
        assert cfg.files == {"events": "assets/data/events.parquet"}


class TestFileSourceTableNameValidation:
    """files: keys must be valid SQL identifiers."""

    def test_csv_source_rejects_invalid_table_name_with_dash(self) -> None:
        with pytest.raises(ValidationError, match="valid SQL identifier"):
            CsvSourceConfig(type="csv", files={"invalid-name": "data.csv"})

    def test_parquet_source_rejects_invalid_table_name_starts_with_digit(self) -> None:
        with pytest.raises(ValidationError, match="valid SQL identifier"):
            ParquetSourceConfig(type="parquet", files={"123bad": "data.parquet"})

    def test_json_source_rejects_invalid_table_name_with_dash(self) -> None:
        with pytest.raises(ValidationError, match="valid SQL identifier"):
            JsonSourceConfig(type="json", files={"bad-name": "data.json"})

    def test_csv_source_accepts_valid_table_name(self) -> None:
        src = CsvSourceConfig(type="csv", files={"valid_name": "data.csv"})
        assert "valid_name" in src.files

    def test_csv_source_accepts_table_name_with_leading_underscore(self) -> None:
        src = CsvSourceConfig(type="csv", files={"_private": "data.csv"})
        assert "_private" in src.files

    def test_csv_source_rejects_injection_attempt(self) -> None:
        with pytest.raises(ValidationError, match="valid SQL identifier"):
            CsvSourceConfig(type="csv", files={"x AS (evil": "data.csv"})


class TestFileSourcePathValidation:
    """files: values must be relative paths that don't escape the project root."""

    def test_csv_source_rejects_absolute_path(self) -> None:
        with pytest.raises(ValidationError, match="must be relative"):
            CsvSourceConfig(type="csv", files={"t": "/etc/passwd"})

    def test_csv_source_rejects_dotdot_escape(self) -> None:
        with pytest.raises(ValidationError, match="escape above the project root"):
            CsvSourceConfig(type="csv", files={"t": "../../../secret"})

    def test_csv_source_rejects_empty_path(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            CsvSourceConfig(type="csv", files={"t": ""})

    def test_csv_source_rejects_blank_path(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            CsvSourceConfig(type="csv", files={"t": "   "})

    def test_csv_source_accepts_collapsing_relative_path(self) -> None:
        """`a/../b.csv` normalizes inside the root, so it is accepted."""
        src = CsvSourceConfig(type="csv", files={"t": "data/../other.csv"})
        assert src.files["t"] == "data/../other.csv"

    def test_parquet_source_rejects_absolute_path(self) -> None:
        with pytest.raises(ValidationError, match="must be relative"):
            ParquetSourceConfig(type="parquet", files={"t": "/proc/self/environ"})

    def test_json_source_rejects_absolute_path(self) -> None:
        with pytest.raises(ValidationError, match="must be relative"):
            JsonSourceConfig(type="json", files={"t": "/etc/hostname"})

    def test_csv_source_accepts_relative_subdir_path(self) -> None:
        src = CsvSourceConfig(type="csv", files={"t": "assets/data/file.csv"})
        assert src.files["t"] == "assets/data/file.csv"

    def test_csv_source_accepts_simple_filename(self) -> None:
        src = CsvSourceConfig(type="csv", files={"t": "data.csv"})
        assert src.files["t"] == "data.csv"


class TestFileSourceExtensionMatchesType:
    """A file source's `files:` path must end in its type's canonical extension.

    Parsing dispatches on the declared `type`, not the extension, so this is
    the coupling that guarantees every servable data file's extension matches
    its declared type — which is what makes Cloud's extension-keyed git-blob
    fetch allowlist safe.
    """

    def test_csv_source_rejects_mismatched_extension(self) -> None:
        with pytest.raises(ValidationError, match=r"must end in '\.csv'"):
            CsvSourceConfig(type="csv", files={"t": "data.json"})

    def test_csv_source_accepts_matching_extension(self) -> None:
        src = CsvSourceConfig(type="csv", files={"t": "data.csv"})
        assert src.files["t"] == "data.csv"

    def test_csv_source_accepts_matching_extension_case_insensitively(self) -> None:
        src = CsvSourceConfig(type="csv", files={"t": "data.CSV"})
        assert src.files["t"] == "data.CSV"

    def test_json_source_rejects_mismatched_extension(self) -> None:
        with pytest.raises(
            ValidationError, match=r"must end in one of.*\.json.*\.jsonl"
        ):
            JsonSourceConfig(type="json", files={"t": "data.csv"})

    def test_json_source_accepts_matching_extension(self) -> None:
        src = JsonSourceConfig(type="json", files={"t": "data.json"})
        assert src.files["t"] == "data.json"

    def test_parquet_source_rejects_mismatched_extension(self) -> None:
        with pytest.raises(ValidationError, match=r"must end in '\.parquet'"):
            ParquetSourceConfig(type="parquet", files={"t": "data.csv"})

    def test_parquet_source_accepts_matching_extension(self) -> None:
        src = ParquetSourceConfig(type="parquet", files={"t": "data.parquet"})
        assert src.files["t"] == "data.parquet"


class TestDuckDBSourceConfigUnchanged:
    """DuckDB source config must remain unchanged — path: is its own namespace."""

    def test_duckdb_path_accepted(self) -> None:
        cfg = DuckDBSourceConfig(type="duckdb", path="./data/analytics.duckdb")
        assert cfg.path == "./data/analytics.duckdb"

    def test_duckdb_memory_accepted(self) -> None:
        cfg = DuckDBSourceConfig(type="duckdb", path=":memory:")
        assert cfg.path == ":memory:"
