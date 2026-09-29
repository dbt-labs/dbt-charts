"""TDD tests for render_prompt: AuthorableSchema IR → AI prompt schema string."""

import re
from collections import Counter

from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema import get_schema_for_prompt
from dbt_charts.core.compile.schema.introspection import (
    AuthorableSchema,
    SchemaField,
    introspect,
    introspect_project_config,
)
from dbt_charts.core.compile.schema.renderers.prompt import (
    _SOURCE_CONFIG_NAMES,
    _display_name,
    _fallback_link,
    _ordered_model_names,
    render_project_config,
    render_prompt,
)


class TestRenderPromptStructure:
    def test_returns_string(self) -> None:
        assert isinstance(render_prompt(introspect()), str)

    def test_contains_root_model_header(self) -> None:
        result = render_prompt(introspect())
        # AuthoredBoard → display name "Board"
        assert "## Board" in result

    def test_contains_root_field_names(self) -> None:
        result = render_prompt(introspect())
        for field in ("title", "charts", "queries", "variables"):
            assert field in result, f"Missing field: {field}"


class TestRenderPromptDescriptions:
    def test_field_descriptions_present(self) -> None:
        # Read the expected text off the model rather than pinning a copy of it:
        # the description is contract prose that gets reworded, and a literal here
        # only ever fails for the rewording, never for the rendering.
        expected = AuthoredBoard.model_fields["title"].description
        assert expected
        result = render_prompt(introspect())
        assert expected in result

    def test_nested_model_content_present(self) -> None:
        result = render_prompt(introspect())
        # Variable and Query are display names after prefix/suffix stripping
        assert "## Variable" in result
        assert "## Query" in result

    def test_enum_values_shown(self) -> None:
        result = render_prompt(introspect())
        assert '"bar"' in result
        assert '"line"' in result


class TestRenderPromptPydanticNoise:
    def test_no_pydantic_metadata_in_output(self) -> None:
        result = render_prompt(introspect())
        assert "Tag(" not in result, "Pydantic Tag metadata leaked into prompt"
        assert "Discriminator(" not in result, (
            "Pydantic Discriminator leaked into prompt"
        )
        assert "strict=" not in result, "Pydantic Strict metadata leaked into prompt"

    def test_synthesized_patch_doc_in_output(self) -> None:
        result = render_prompt(introspect())
        # BarChartStylePatch → display name "BarChartStyle"
        section = re.search(r"## BarChartStyle\n([^\n]*)", result)
        assert section and section.group(1).strip(), "BarChartStyle doc empty in prompt"


class TestRenderPromptDisplayNames:
    def test_authored_prefix_stripped(self) -> None:
        assert _display_name("AuthoredBoard") == "Board"
        assert _display_name("AuthoredQuery") == "Query"

    def test_patch_suffix_stripped(self) -> None:
        assert _display_name("AuthoredChart") == "Chart"
        assert _display_name("BarStylePatch") == "BarStyle"

    def test_plain_name_unchanged(self) -> None:
        assert _display_name("Variable") == "Variable"
        assert _display_name("GridLayout") == "GridLayout"

    def test_no_raw_authored_or_patch_headings_in_output(self) -> None:
        result = render_prompt(introspect())
        assert "## AuthoredBoard" not in result
        assert "## AuthoredQuery" not in result
        assert "## AuthoredChart\n" not in result


class TestRenderPromptFieldAnnotations:
    @staticmethod
    def _model_block(result: str, heading: str) -> str:
        start = result.index(heading)
        end = result.find("\n## ", start + 1)
        return result[start:end] if end != -1 else result[start:]

    def test_no_required_column_or_checkmarks(self) -> None:
        result = render_prompt(introspect())
        assert "| Required |" not in result
        assert "| Optional |" not in result
        assert "✓" not in result

    def test_mixed_model_splits_into_labeled_tables(self) -> None:
        result = render_prompt(introspect())
        # GridLayout has required (items) and optional (columns) fields
        block = self._model_block(result, "## GridLayout")
        assert "**Required**" in block
        assert "**Optional**" in block
        assert block.index("**Required**") < block.index("| `items`")
        assert block.index("| `items`") < block.index("**Optional**")
        assert block.index("**Optional**") < block.index("| `columns`")

    def test_all_optional_model_gets_single_unlabeled_table(self) -> None:
        result = render_prompt(introspect())
        # SchemaQuery has no required fields — one plain table, no labels
        block = self._model_block(result, "## SchemaQuery")
        assert "**Required**" not in block
        assert "**Optional**" not in block
        assert "| Field | Type | Description |" in block

    def test_no_pipe_none_in_types(self) -> None:
        result = render_prompt(introspect())
        assert "| None" not in result, "| None should not appear in type cells"

    def test_reference_carries_requiredness_legend(self) -> None:
        """All-optional models render one unlabeled table; the legend is the
        only place their requiredness is stated."""
        result = render_prompt(introspect())
        header, _, _ = result.partition("\n## ")
        assert "optional unless" in header, (
            "the reference header must carry the requiredness legend"
        )

    def test_all_required_model_gets_labeled_required_table(self) -> None:
        """The legend says unlabeled = all optional, so an all-required model
        must carry the **Required** label."""
        result = render_prompt(introspect())
        block = self._model_block(result, "## VariableRef")
        assert "**Required**" in block
        assert "**Optional**" not in block
        assert "| Field | Type | Description |" in block

    def test_single_value_literal_rendered_as_const(self) -> None:
        result = render_prompt(introspect())
        assert 'const: "http"' in result
        assert 'enum: "http"' not in result

    def test_single_value_type_tag_has_no_description(self) -> None:
        """Inferable tags render bare; an ExplicitTag-marked tag keeps its
        description — keyed on the facet, never on the description's prose."""
        result = render_prompt(introspect())
        rows = [
            r
            for r in result.splitlines()
            if r.startswith("| `type` |") and "const:" in r
        ]
        assert rows, "no single-value type tag rows found"
        explicit_rows = [r for r in rows if 'const: "schema"' in r]
        assert explicit_rows, "SchemaQuery's type row must be present"
        for r in explicit_rows:
            assert r.split("|")[-2].strip(), (
                f"ExplicitTag-marked tag must keep its description: {r}"
            )
        for r in rows:
            if r in explicit_rows:
                continue
            assert r.split("|")[-2].strip() == "", (
                f"single-value type tag row should have an empty description: {r}"
            )


class TestRenderPromptOrdering:
    def test_authored_chart_union_not_in_reference(self) -> None:
        """AuthoredChart is a synthetic union — not an authored surface; must not appear."""
        result = render_prompt(introspect())
        assert "\n## Chart\n" not in result, (
            "## Chart (AuthoredChart) should not appear in the reference — "
            "per-family charts (BarChart, LineChart, …) are the authored surface"
        )

    def test_source_configs_after_variable_sub_models(self) -> None:
        """Source configs must come after VariableOptions in BFS order."""
        schema = introspect()
        order = _ordered_model_names(schema)
        source_indices = [order.index(n) for n in _SOURCE_CONFIG_NAMES if n in order]
        variable_options_idx = order.index("VariableOptions")
        assert source_indices, "No source configs found in order"
        assert all(i > variable_options_idx for i in source_indices), (
            "Source configs appear before VariableOptions — "
            "they should be deferred to after the authored sub-models"
        )

    def test_no_duplicate_display_name_sections(self) -> None:
        """No two models should render to the same ## heading (duplicate anchors)."""
        schema = introspect()
        order = _ordered_model_names(schema)
        display_names = [_display_name(n) for n in order]
        counts = Counter(display_names)
        dupes = {name: count for name, count in counts.items() if count > 1}
        assert not dupes, f"Duplicate section headings in reference: {dupes}"

    def test_sqlite_source_config_in_source_names(self) -> None:
        """SQLiteSourceConfig must be in _SOURCE_CONFIG_NAMES so it lands at the end."""
        assert "SQLiteSourceConfig" in _SOURCE_CONFIG_NAMES


class TestRenderPromptFamilyLinks:
    def _union_family_names(self, schema: AuthorableSchema) -> set[str]:
        """Class names that are members of a discriminated union family
        (e.g. BarChart, LineChart, ... under the AuthoredChart union).

        Scoped to union families, not every model.models key: ChartRef and
        VariableRef are plain leaf models excluded from nested_models by
        _is_authored_model (they live outside the .authored module tree) and
        are never linked today; that is a separate, pre-existing gap, not the
        union-alias regression this test guards against.
        """
        families: set[str] = set()
        for model in schema.models.values():
            if model.union is not None:
                families.update(model.union.variants.values())
        return families

    def test_authored_chart_family_names_linked_in_layout_fields(self) -> None:
        """AuthoredBoard.rows/cols spell out chart family names literally in
        type_repr (they do not collapse to the AuthoredChart alias name), so
        the anchor links must be attached to each family name individually.
        """
        result = render_prompt(introspect())
        board_start = result.index("## Board\n")
        board_block = result[board_start : result.index("\n## ", board_start + 1)]
        for row_name in ("rows", "cols"):
            rows = [
                r for r in board_block.splitlines() if r.startswith(f"| `{row_name}`")
            ]
            assert rows, f"{row_name} field not found in Board section"
            for family in ("BarChart", "LineChart", "PieChart"):
                assert f"[{family}](#{family.lower()})" in rows[0], (
                    f"{family} missing its anchor link in {row_name} type cell: "
                    f"{rows[0]}"
                )

    def test_no_known_model_name_unlinked_in_any_type_cell(self) -> None:
        """No family or model class name may appear bare in a type cell.

        Regression guard for the AuthoredChart union alias silently losing its
        anchor links: `_nested_model_names` collapses inline chart unions to
        the opaque "AuthoredChart" name, but `type_repr` for the same field
        spells out the twelve family class names verbatim, so a substitution
        keyed only on the literal "AuthoredChart" substring silently no-ops.
        """
        schema = introspect()
        result = render_prompt(schema)
        known_names = {_display_name(n) for n in self._union_family_names(schema)}
        for line in result.splitlines():
            if not line.startswith("| `"):
                continue
            cells = line.split(" | ")
            if len(cells) < 2:
                continue
            type_cell = cells[1]
            unlinked = re.sub(r"\[[^\]]+\]\(#[^)]+\)", "", type_cell)
            for name in known_names:
                assert not re.search(r"\b" + re.escape(name) + r"\b", unlinked), (
                    f"{name} appears unlinked in type cell: {type_cell}"
                )


class TestRenderPromptInheritLinks:
    """Inherit/InheritSlot markers must produce fallback links in the description cell."""

    # No production field currently has an InheritSlot whose target's parent is
    # the *same* section (ChartsStyle.axis_x used to, before axis_x/y/quantitative
    # moved to SkipInheritSlots — the render-time cascade in style_cascade.py
    # replaced the compile-time expansion). _fallback_link's same-section branch
    # is still live renderer behavior, so it's unit-tested directly here rather
    # than through a real model shape.

    def _synthetic_field(self, inherit_slot: str | None) -> SchemaField:
        return SchemaField(
            name="axis_x",
            description="X-axis style overrides applied after the shared axis.",
            type_repr="BaseAxisStyle",
            required=True,
            default=None,
            default_repr=None,
            enum_values=None,
            nested_models=["BaseAxisStyle"],
            inherit_slot=inherit_slot,
        )

    def test_inherit_slot_same_section_shows_plain_code(self) -> None:
        # Target's parent ("charts") resolves to the same model as the field's
        # own section ("ChartsStyle") — no useful navigation, so a plain code
        # span instead of a dead same-page anchor link.
        field = self._synthetic_field(inherit_slot="Style.charts.axis")
        path_map = {"": "Style", "charts": "ChartsStyle"}
        link = _fallback_link(field, field.inherit_slot, path_map, "ChartsStyle")
        assert link == "`style.charts.axis`", f"Expected plain-code path, got: {link}"

    def test_inherit_slot_uses_unset_fields_wording(self) -> None:
        result = render_prompt(introspect())
        # Any InheritSlot field in the rendered prompt uses this fixed wording —
        # AxisLabelStyle.font (InheritSlot from_path="Style.charts.font") is a
        # live example, exercised via the cross-section test below.
        assert "Unset fields fall back to" in result

    def test_inherit_slot_cross_section_links_to_parent_container(self) -> None:
        # AxisLabelStylePatch.font: InheritSlot(from_path="Style.charts.font").
        # parent of "Style.charts.font" = "Style.charts" = ChartsStyle ≠ AxisLabelStyle.
        # Must link to #chartsstyle (where the user sets the shared default), not #fontstyle.
        result = render_prompt(introspect())
        ae_start = result.index("## AxisLabelStyle\n")
        ae_block = result[ae_start : result.index("\n## ", ae_start + 1)]
        font_rows = [r for r in ae_block.splitlines() if r.startswith("| `font`")]
        assert font_rows, "font not found in AxisLabelStyle section"
        row = font_rows[0]
        assert "[`style.charts.font`](#chartsstyle)" in row, (
            f"Expected link to containing section #chartsstyle, got: {row}"
        )

    def test_inherit_slot_exclude_appended_to_description(self) -> None:
        # SliceLabelsStyle.font: InheritSlot(from_path="Style.charts.font",
        # exclude={"color"}). The rendered fallback note must call out the
        # exclusion so authors don't read it as covering every leaf.
        result = render_prompt(introspect())
        start = result.index("## SliceLabelsStyle\n")
        block = result[start : result.index("\n## ", start + 1)]
        font_rows = [r for r in block.splitlines() if r.startswith("| `font`")]
        assert font_rows, "font not found in SliceLabelsStyle section"
        assert "except `color`" in font_rows[0], font_rows[0]

    def test_inherit_leaf_produces_link_to_containing_model(self) -> None:
        # BarChartStylePatch.aspect_ratio: Inherit(from_path="Style.charts.aspect_ratio")
        # parent path "Style.charts" → ChartsStyle → link to #chartsstyle
        result = render_prompt(introspect())
        bar_start = result.index("## BarChartStyle\n")
        bar_block = result[bar_start : result.index("\n## ", bar_start + 1)]
        ar_rows = [
            r for r in bar_block.splitlines() if r.startswith("| `aspect_ratio`")
        ]
        assert ar_rows, "aspect_ratio not found in BarChartStyle section"
        row = ar_rows[0]
        assert "[`style.charts.aspect_ratio`](#chartsstyle)" in row, (
            f"Expected inherit leaf link in aspect_ratio description, got: {row}"
        )

    def test_inherit_leaf_uses_falls_back_to_wording(self) -> None:
        result = render_prompt(introspect())
        bar_start = result.index("## BarChartStyle\n")
        bar_block = result[bar_start : result.index("\n## ", bar_start + 1)]
        ar_rows = [
            r for r in bar_block.splitlines() if r.startswith("| `aspect_ratio`")
        ]
        assert "Falls back to" in ar_rows[0]

    def test_charts_font_slot_links_to_style_root(self) -> None:
        # ChartsStyle.font: InheritSlot(from_path="Style.font").
        # parent of "Style.font" = root ("Style") → link to #style, not #fontstyle.
        result = render_prompt(introspect())
        charts_start = result.index("## ChartsStyle")
        charts_block = result[charts_start : result.index("\n## ", charts_start + 1)]
        font_rows = [r for r in charts_block.splitlines() if r.startswith("| `font`")]
        assert font_rows, "font not found in ChartsStyle section"
        assert "[`style.font`](#style)" in font_rows[0], (
            f"Expected link to #style (containing section), got: {font_rows[0]}"
        )

    def test_inherit_slot_marks_links_to_chartsstyle(self) -> None:
        # BarChartStyle.marks: InheritSlot(from_path="Style.charts.marks").
        # parent of "Style.charts.marks" = "Style.charts" = ChartsStyle.
        # Link must go to #chartsstyle (where the user sets marks), not #globalmarksstyle.
        result = render_prompt(introspect())
        bar_start = result.index("## BarChartStyle\n")
        bar_block = result[bar_start : result.index("\n## ", bar_start + 1)]
        marks_rows = [r for r in bar_block.splitlines() if r.startswith("| `marks`")]
        assert marks_rows, "marks not found in BarChartStyle section"
        row = marks_rows[0]
        assert "[`style.charts.marks`](#chartsstyle)" in row, (
            f"Expected link to containing section #chartsstyle, got: {row}"
        )

    def test_no_self_referential_fallback_links(self) -> None:
        # ChartsStyle fields like aspect_ratio carry Inherit(from_path="Style.charts.aspect_ratio")
        # — the parent path resolves to ChartsStyle itself. Must not emit a fallback note.
        result = render_prompt(introspect())
        charts_start = result.index("## ChartsStyle\n")
        charts_block = result[charts_start : result.index("\n## ", charts_start + 1)]
        ar_rows = [
            r for r in charts_block.splitlines() if r.startswith("| `aspect_ratio`")
        ]
        assert ar_rows, "aspect_ratio not found in ChartsStyle section"
        assert "fall back" not in ar_rows[0].lower(), (
            f"ChartsStyle.aspect_ratio must not link back to itself: {ar_rows[0]}"
        )

    def test_no_bare_html_tags_in_description_cells(self) -> None:
        # html_policy field description contains <script>/event-handlers — must be escaped
        # so markdown previews don't interpret the table content as raw HTML.
        result = render_prompt(introspect())
        assert "<script>" not in result
        assert "</script>" not in result

    def test_no_duplicate_cascade_docs_on_inherit_fields(self) -> None:
        # Fields with InheritSlot/Inherit markers get a generated fallback annotation.
        # Any manual "cascades from X" in the source description must be removed to
        # avoid double-documentation in the same table cell.
        result = render_prompt(introspect())
        for line in result.splitlines():
            if not line.startswith("| `"):
                continue
            if "fall back to" in line.lower() and "cascades from" in line.lower():
                raise AssertionError(
                    f"Field row has both generated fallback and manual cascade doc: {line}"
                )

    def test_fields_without_inherit_unchanged(self) -> None:
        # ChartsStyle.palette has no inherit_slot; its description should have no fallback note
        result = render_prompt(introspect())
        # BarChart.orientation is a plain field with no inherit marker
        bar_start = result.index("## BarChartStyle\n")
        bar_block = result[bar_start : result.index("\n## ", bar_start + 1)]
        orient_rows = [
            r for r in bar_block.splitlines() if r.startswith("| `orientation`")
        ]
        assert orient_rows, "orientation not found in BarChartStyle section"
        assert "fall back to" not in orient_rows[0].lower()


class TestTypeCellEnumWithSiblingContainerArm:
    """extends: ThemeName | str | list[str] — the list[str] arm must survive
    into the docs Type column, not just the JSON Schema (test_render_json_schema
    pins the JSON Schema side of this same bug)."""

    def test_extends_row_keeps_list_arm(self) -> None:
        result = render_prompt(introspect())
        row = next(
            line for line in result.splitlines() if line.startswith("| `extends`")
        )
        assert "list[str]" in row, f"list[str] arm dropped from extends row: {row}"
        assert "enum:" in row

    def test_palettes_row_keeps_dict_shape_no_duplication(self) -> None:
        # Style.palettes: dict[str, PaletteName | str] | None — the outer dict
        # is field.container (the whole field, not a sibling arm), so this
        # falls through to the generic type_repr path instead of the
        # "enum: ..." prefix format — which would otherwise duplicate the
        # palette name list (once from type_repr, once from the enum prefix).
        result = render_prompt(introspect())
        row = next(
            line for line in result.splitlines() if line.startswith("| `palettes`")
        )
        assert row.startswith("| `palettes` | dict[str, ")
        # "dbt-grays" -- unlike "category-6-tonal-blue", never a *prefix* of
        # another entry (every categorical name now also derives four
        # `<name>.<variant>` completions, so a categorical canary like
        # "category-6-tonal-blue" legitimately appears 5 times: itself plus
        # its own .dark/.light/.pale/.deep). A non-categorical name has no
        # such derived siblings and still pins the "no duplicated list"
        # regression this test exists for.
        assert row.count("dbt-grays") == 1, (
            f"palette name list duplicated in row: {row}"
        )


class TestEnumCellTruncation:
    """A long closed set (ScaleTargetConfig.palette's ~116 names) shouldn't
    blow out the docs table — the JSON Schema still carries the full list,
    where completion actually reads it."""

    def test_short_enum_is_not_truncated(self) -> None:
        from dbt_charts.core.compile.schema.renderers.prompt import _enum_str

        assert _enum_str(["a", "b", "c"]) == 'enum: "a", "b", "c"'

    def test_bool_members_render_as_yaml_literals(self) -> None:
        from dbt_charts.core.compile.schema.renderers.prompt import _enum_str

        assert _enum_str([False, "x"]) == 'enum: false, "x"'

    def test_single_value_is_const(self) -> None:
        from dbt_charts.core.compile.schema.renderers.prompt import _enum_str

        assert _enum_str(["a"]) == 'const: "a"'

    def test_single_value_bool_is_yaml_literal_const(self) -> None:
        """`link: false` must document as YAML `false`, never Python "False"."""
        from dbt_charts.core.compile.schema.renderers.prompt import _enum_str

        assert _enum_str([False]) == "const: false"

    def test_long_enum_is_truncated_with_count(self) -> None:
        from dbt_charts.core.compile.schema.renderers.prompt import (
            _ENUM_CELL_TRUNCATE_AT,
            _enum_str,
        )

        values = [f"v{i}" for i in range(_ENUM_CELL_TRUNCATE_AT + 5)]
        result = _enum_str(values)
        assert result.count('"v') == _ENUM_CELL_TRUNCATE_AT
        assert "5 more" in result

    def test_scale_target_config_palette_row_is_truncated(self) -> None:
        result = render_prompt(introspect())
        start = result.index("## ScaleTargetConfig\n")
        block = result[start : result.index("\n## ", start + 1)]
        row = next(
            line for line in block.splitlines() if line.startswith("| `palette`")
        )
        assert "more" in row, f"palette row not truncated: {row}"

    def test_variable_input_row_not_collaterally_truncated(self) -> None:
        # Variable.input has 14 members — a pre-existing enum unrelated to this
        # task. It must stay under the truncation threshold: this task's own
        # first-pass 12-member threshold previously clipped it to 12, silently
        # dropping "checkbox" and "radio" from the published reference.
        result = render_prompt(introspect())
        start = result.index("## Variable\n")
        block = result[start : result.index("\n## ", start + 1)]
        row = next(line for line in block.splitlines() if line.startswith("| `input`"))
        assert "more" not in row, f"unrelated pre-existing enum got truncated: {row}"
        assert '"checkbox"' in row
        assert '"radio"' in row


class TestRenderProjectConfig:
    def test_section_names_the_file_it_documents(self) -> None:
        assert "dbt_charts.yml" in render_project_config(introspect_project_config())

    def test_renders_the_execution_keys(self) -> None:
        out = render_project_config(introspect_project_config())
        for key in ("max_template_output_bytes", "max_rows", "max_workers"):
            assert key in out, f"Missing project-config key: {key}"

    def test_schema_for_prompt_carries_both_sections(self) -> None:
        """The generated reference file is this string — board grammar first,
        project config appended."""
        out = get_schema_for_prompt()
        assert out.startswith(render_prompt(introspect()))
        assert "max_template_output_bytes" in out
