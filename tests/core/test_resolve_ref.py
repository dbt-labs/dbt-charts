"""The one resolver for authored file references (``resolve_ref``)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile.errors import CompilationError, ReferenceError
from dbt_charts.core.compile.file_refs import (
    anchor_images,
    resolve_file_source,
    resolve_image,
    resolve_ref,
)
from dbt_charts.core.compile.validate.authoring_warnings import YamlValue
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_FILE_SOURCE_AMBIGUOUS,
    ERR_UNRESOLVED_REFERENCE,
)
from dbt_charts.core.project import Project, ProjectDirectory, assert_relpath

FILES = {
    "charts/sub/board.yml": "",
    "charts/sub/child.yml": "",
    "charts/shared.yml": "",
    "charts/sub/_q.yml": "",
    "charts/sub/data/local.csv": "",
    "data/root.csv": "",
    "charts/sub/img/logo.PNG": "",
    "charts/assets/brand.svg": "",
    "charts/sub/notes.txt": "",
}


@pytest.fixture
def project(in_memory_project: Callable[..., Project]) -> Project:
    return in_memory_project(Path("/p"), dict(FILES))


def _sub(project: Project) -> ProjectDirectory:
    return project.directory("charts/sub")


class TestAssertRelpath:
    def test_returns_normalized_join(self) -> None:
        assert assert_relpath("../x/./y.yml", "charts/sub") == "charts/x/y.yml"

    def test_root_anchor(self) -> None:
        assert assert_relpath("a/../b.yml") == "b.yml"

    def test_escape_raises(self) -> None:
        with pytest.raises(ValueError, match="escape"):
            assert_relpath("../../x.yml", "charts")


class TestFileKind:
    def test_anchors_at_written_in_directory(self, project: Project) -> None:
        assert resolve_ref("child.yml", _sub(project), "file").relpath == (
            "charts/sub/child.yml"
        )

    def test_dotdot_within_project(self, project: Project) -> None:
        assert resolve_ref("../shared.yml", _sub(project), "file").relpath == (
            "charts/shared.yml"
        )

    def test_escape_rejected(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="escape above the project root") as e:
            resolve_ref("../../../x.yml", _sub(project), "file")
        assert e.value.code == ERR_UNRESOLVED_REFERENCE

    def test_absolute_rejected(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="must be relative"):
            resolve_ref("/charts/shared.yml", _sub(project), "file")

    def test_missing_file(self, project: Project) -> None:
        with pytest.raises(
            ReferenceError, match="'charts/sub/nope.yml' does not exist"
        ):
            resolve_ref("nope.yml", _sub(project), "file")

    def test_no_location(self) -> None:
        with pytest.raises(ReferenceError, match="no project location"):
            resolve_ref("child.yml", None, "file")

    def test_ref_path_rides_on_the_error(self, project: Project) -> None:
        with pytest.raises(ReferenceError) as e:
            resolve_ref("nope.yml", _sub(project), "file", ref_path=["rows", "0"])
        assert e.value.ref_path == ("rows", "0")


class TestSectionKinds:
    def test_appends_yml_to_the_file_part(self, project: Project) -> None:
        assert resolve_ref("_q.queries.a", _sub(project), "queries").relpath == (
            "charts/sub/_q.yml"
        )

    def test_keeps_explicit_suffix(self, project: Project) -> None:
        path = resolve_ref("../shared.yml.charts.c", _sub(project), "charts")
        assert path.relpath == "charts/shared.yml"

    def test_splits_on_the_section_not_the_last_dots(self, project: Project) -> None:
        # A raw chain name may carry dots: `_q.queries.rev.v2` is file `_q`.
        assert resolve_ref("_q.queries.rev.v2", _sub(project), "queries").relpath == (
            "charts/sub/_q.yml"
        )

    def test_missing_names_the_whole_ref(self, project: Project) -> None:
        with pytest.raises(
            ReferenceError, match=r"'_nope\.variables\.v'.*does not exist"
        ):
            resolve_ref("_nope.variables.v", _sub(project), "variables")


class TestQueryFileKind:
    def test_file_part_precedes_the_hash(self, project: Project) -> None:
        assert resolve_ref("_q.yml#rev", _sub(project), "query_file").relpath == (
            "charts/sub/_q.yml"
        )

    def test_missing_names_the_whole_ref(self, project: Project) -> None:
        with pytest.raises(ReferenceError) as e:
            resolve_ref("_nope.yml#rev", _sub(project), "query_file")
        assert e.value.ref == "_nope.yml#rev"


class TestFileSourceKind:
    def test_written_in_directory(self, project: Project) -> None:
        assert resolve_file_source("data/local.csv", _sub(project), "q")[0].relpath == (
            "charts/sub/data/local.csv"
        )

    def test_project_root(self, project: Project) -> None:
        assert resolve_file_source("data/root.csv", _sub(project), "q")[0].relpath == (
            "data/root.csv"
        )

    def test_escape_from_one_anchor_is_not_an_error(self, project: Project) -> None:
        # `../` escapes the root anchor but not the board directory's.
        path, _ = resolve_file_source("../sub/data/local.csv", _sub(project), "q")
        assert path.relpath == "charts/sub/data/local.csv"

    def test_both_anchors_existing_is_ambiguous(
        self, in_memory_project: Callable[..., Project]
    ) -> None:
        project = in_memory_project(
            Path("/p"), {"charts/data/x.csv": "", "data/x.csv": ""}
        )
        with pytest.raises(CompilationError) as e:
            resolve_file_source("data/x.csv", project.directory("charts"), "q")
        assert e.value.code == ERR_FILE_SOURCE_AMBIGUOUS

    def test_missing_keeps_written_in_candidate(self, project: Project) -> None:
        # Existence is deferred: execute reports the missing file per chart.
        assert resolve_file_source("data/gone.csv", _sub(project), "q")[0].relpath == (
            "charts/sub/data/gone.csv"
        )

    def test_type_comes_from_the_suffix(self, project: Project) -> None:
        assert resolve_file_source("x.JSON", _sub(project), "q")[1] == "json"

    def test_jsonl_is_not_an_inline_file_source(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="needs one of"):
            resolve_file_source("x.jsonl", _sub(project), "q")

    def test_unknown_suffix(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="an inline file source needs one of"):
            resolve_file_source("data/x.txt", _sub(project), "q")

    def test_escape_from_both_anchors(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="escape"):
            resolve_file_source("../../../x.csv", _sub(project), "q")


class TestImage:
    def test_anchors_at_written_in_directory(self, project: Project) -> None:
        assert resolve_image("img/logo.PNG", _sub(project)).relpath == (
            "charts/sub/img/logo.PNG"
        )

    def test_unsupported_suffix(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="unsupported image suffix"):
            resolve_image("notes.txt", _sub(project))

    def test_query_string_is_not_a_suffix(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="unsupported image suffix"):
            resolve_image("img/logo.PNG?v=1", _sub(project))

    def test_missing_file(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="does not exist"):
            resolve_image("img/gone.png", _sub(project))

    def test_escape_rejected(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="escape"):
            resolve_image("../../../x.png", _sub(project))

    def test_leading_slash_resolves_from_the_charts_directory(
        self, project: Project
    ) -> None:
        assert resolve_image("/assets/brand.svg", _sub(project)).relpath == (
            "charts/assets/brand.svg"
        )

    def test_a_charts_rooted_path_written_relative_hints_the_slash_form(
        self, project: Project
    ) -> None:
        with pytest.raises(ReferenceError, match="write '/assets/brand.svg'"):
            resolve_image("assets/brand.svg", _sub(project))

    def test_leading_slash_is_only_for_images(self, project: Project) -> None:
        with pytest.raises(ReferenceError, match="must be relative"):
            resolve_ref("/shared.yml", _sub(project), "file")

    def test_a_ref_escaping_charts_is_still_just_missing(
        self, project: Project
    ) -> None:
        with pytest.raises(ReferenceError, match="does not exist"):
            resolve_image("../../x.png", _sub(project))


class TestAnchorImages:
    def test_relative_paths_are_respelled_from_charts(self, project: Project) -> None:
        data: dict[str, YamlValue] = {
            "title": '![t](img/logo.PNG "Logo"){width=10} and ![u](img/logo.PNG)',
            "rows": [{"n": {"type": "callout", "message": "![m](../shared.png)"}}],
            "text": "![r](/assets/brand.svg) ![w](https://x.example/a.png)",
        }
        anchor_images(data, _sub(project))
        assert data == {
            "title": (
                '![t](/sub/img/logo.PNG "Logo"){width=10} and ![u](/sub/img/logo.PNG)'
            ),
            "rows": [{"n": {"type": "callout", "message": "![m](/shared.png)"}}],
            "text": "![r](/assets/brand.svg) ![w](https://x.example/a.png)",
        }

    def test_an_image_outside_charts_cannot_be_inherited(
        self, project: Project
    ) -> None:
        with pytest.raises(ReferenceError, match="outside 'charts'"):
            anchor_images({"text": "![l](data/logo.png)"}, project.directory("."))
