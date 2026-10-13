"""Tests for dbt_charts.agent_api.project_sources.read_project_sources.

See the module's own docstring for what it does and does not read.
"""

from collections.abc import Callable
from pathlib import Path

from dbt_charts.agent_api.project_sources import read_project_sources
from dbt_charts.cli.filesystem_project import FilesystemProject


class TestReadProjectSources:
    def test_no_dbt_charts_yml_is_an_empty_registry(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        result = read_project_sources(local_project(tmp_path))

        assert result.error is None
        assert result.sources == []

    def test_declared_sources_come_back_with_name_and_type(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  wh:\n    type: bigquery\n    project: acme\n"
            "    dataset: analytics\n"
        )

        result = read_project_sources(local_project(tmp_path))

        assert result.error is None
        assert len(result.sources) == 1
        assert result.sources[0].name == "wh"
        assert result.sources[0].type == "bigquery"
        assert result.sources[0].needs_connection is True

    def test_file_sources_do_not_need_a_connection(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  csvs:\n    type: csv\n    files: {t: t.csv}\n"
        )

        result = read_project_sources(local_project(tmp_path))

        assert result.sources[0].needs_connection is False

    def test_an_unset_env_var_never_renders_and_never_crashes(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The likeliest real-world `sources:` failure: a committed registry
        that requires secrets as `env_var()` references, read in a shell
        that lacks the warehouse env vars (the onboarding case this feature
        exists for). The raw type is a plain string here, so it still comes
        back -- only the credential-bearing field is left unrendered, and
        this reader never looks at it."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  wh:\n"
            "    type: snowflake\n"
            "    account: acct\n"
            "    user: user\n"
            "    database: DB\n"
            "    warehouse: WH\n"
            "    password: \"{{ env_var('DCT_REVIEW_UNSET_PASSWORD_XYZ') }}\"\n"
        )

        result = read_project_sources(local_project(tmp_path))

        assert result.error is None
        assert result.sources[0].name == "wh"
        assert result.sources[0].type == "snowflake"
        assert result.sources[0].needs_connection is True

    def test_a_templated_type_is_reported_as_unknown_not_the_raw_template(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  wh:\n    type: \"{{ env_var('DCT_REVIEW_UNSET_TYPE') }}\"\n"
        )

        result = read_project_sources(local_project(tmp_path))

        assert result.error is None
        assert result.sources[0].name == "wh"
        assert result.sources[0].type is None
        assert result.sources[0].needs_connection is False

    def test_a_non_mapping_entry_is_reported_by_name_not_silently_dropped(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """`sources: {warehouse: snowflake}` -- a string value, not a
        mapping -- must not read as "no sources declared". `dct validate`
        rejects this same file, so the author has declared a source;
        status must not say they have not."""
        (tmp_path / "dbt_charts.yml").write_text("sources:\n  warehouse: snowflake\n")

        result = read_project_sources(local_project(tmp_path))

        assert result.error is None
        assert len(result.sources) == 1
        assert result.sources[0].name == "warehouse"
        assert result.sources[0].type is None
        assert result.sources[0].needs_connection is False
        assert result.sources[0].error is not None
        assert "mapping" in result.sources[0].error

    def test_mixed_valid_and_non_mapping_entries_both_appear(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  a: snowflake\n  b:\n    type: bigquery\n"
        )

        result = read_project_sources(local_project(tmp_path))

        names = {s.name for s in result.sources}
        assert names == {"a", "b"}
        by_name = {s.name: s for s in result.sources}
        assert by_name["a"].error is not None
        assert by_name["b"].error is None
        assert by_name["b"].type == "bigquery"

    def test_no_declared_sources_is_an_empty_list(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text("")

        result = read_project_sources(local_project(tmp_path))

        assert result.error is None
        assert result.sources == []

    def test_a_non_mapping_sources_section_is_an_error_not_a_crash(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text("sources: not-a-mapping\n")

        result = read_project_sources(local_project(tmp_path))

        assert result.sources == []
        assert result.error is not None
        assert "mapping" in result.error

    def test_an_unparseable_dbt_charts_yml_is_an_error_not_a_crash(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text("sources: [unclosed\n")

        result = read_project_sources(local_project(tmp_path))

        assert result.sources == []
        assert result.error is not None

    def test_an_unreadable_dbt_charts_yml_is_an_error_not_a_crash(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        target = tmp_path / "dbt_charts.yml"
        target.write_text("sources: {}\n")
        target.chmod(0o000)
        try:
            result = read_project_sources(local_project(tmp_path))
        finally:
            target.chmod(0o644)

        assert result.sources == []
        assert result.error is not None

    def test_credential_shaped_fields_never_appear_in_the_result(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Only `name` and `type` are read -- a future edit that starts
        threading more of the entry through must not carry a credential
        field along with it."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  wh:\n"
            "    type: postgres\n"
            "    host: localhost\n"
            "    password: \"{{ env_var('DCT_REVIEW_UNSET_PW') }}\"\n"
        )

        result = read_project_sources(local_project(tmp_path))

        dumped = result.model_dump_json()
        assert "password" not in dumped
        assert "host" not in dumped

    def test_a_non_utf8_dbt_charts_yml_is_an_error_not_a_crash(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_bytes(
            b"sources:\n  wh:\n    type: caf\xe9\n"
        )

        result = read_project_sources(local_project(tmp_path))

        assert result.sources == []
        assert result.error is not None

    def test_a_numeric_source_name_is_reported_not_crashed(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A bare integer YAML key (`1:`) parses as an int, not a string --
        DeclaredSource.name: str rejects it outright unless the reader
        stringifies it first."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  1:\n    type: snowflake\n"
        )

        result = read_project_sources(local_project(tmp_path))

        assert result.error is None
        assert len(result.sources) == 1
        assert result.sources[0].name == "1"

    def test_mixed_int_and_string_source_names_do_not_crash_the_sort(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  1:\n    type: snowflake\n  wh:\n    type: bigquery\n"
        )

        result = read_project_sources(local_project(tmp_path))

        assert result.error is None
        names = {s.name for s in result.sources}
        assert names == {"1", "wh"}
