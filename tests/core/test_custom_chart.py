"""``type: custom`` — a chart drawn by a sandboxed Jinja SVG template.

Compile-side contract (authored → normalized), render-side contract (template
context, wrapping, errors).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import raise_on_dashboard_failure  # noqa: F401
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.template.output_budget import template_output_budget
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

BOARD = """
queries:
  flows:
    type: values
    rows:
      - {{from_stage: visit, to_stage: signup, visitors: 100}}
      - {{from_stage: signup, to_stage: paid, visitors: 40}}
charts:
  flow:
    type: custom
    query: flows
    title: Where visitors go
    {template_line}
    x: from_stage
    y: visitors
rows:
  - flow
"""

HEADER = """{#---
description: Flow list.
channels:
  x:
    description: Origin column.
  y:
    description: Count column.
  color:
    default: null
options:
  label_size:
    type: number
    default: 11
  note:
    type: string
    default: null
---#}
"""

BODY = """<g id="{{ uid }}-g">
{% for r in rows %}<text x="4" y="{{ loop.index * 14 }}" font-size="{{ options.label_size }}">{{ r[channels.x] }}: {{ r[channels.y] | format(',.0f') }}</text>
{% endfor %}</g>"""


def _project(tmp_path: Path, template: str = HEADER + BODY) -> FilesystemProject:
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "flow.svg.j2").write_text(template)
    return FilesystemProject(tmp_path)


def _board_yaml(**chart_overrides: str) -> str:
    return BOARD.format(template_line=chart_overrides.get("line", "template: flow"))


def _compile(project: FilesystemProject, yaml_text: str) -> Any:
    return compile(yaml_text, base_dir=project.directory())


def _errors(result: Any) -> list[str]:
    return [d.code for d in result.errors]


def _render(project: FilesystemProject, yaml_text: str) -> Any:
    result = _compile(project, yaml_text)
    assert result.success, [d.message for d in result.errors]
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    return render(result.board, executor, format="svg")


class TestCompile:
    def test_template_file_compiles_and_carries_source_and_header(
        self, tmp_path: Path
    ) -> None:
        project = _project(tmp_path)
        result = _compile(project, _board_yaml())
        assert result.success, [d.message for d in result.errors]
        chart = result.board.charts["flow"]
        assert chart.type == "custom"
        assert "{{ uid }}" in chart.template_source
        assert chart.template_header.description == "Flow list."
        assert chart.channels == {"x": "from_stage", "y": "visitors", "color": None}
        assert chart.options == {"label_size": 11, "note": None}

    def test_inline_svg_compiles(self, tmp_path: Path) -> None:
        yaml_text = BOARD.format(template_line="svg: '<rect width=\"1\"/>'").replace(
            "    x: from_stage\n    y: visitors\n", ""
        )
        result = _compile(FilesystemProject(tmp_path), yaml_text)
        assert result.success, [d.message for d in result.errors]

    @pytest.mark.parametrize(
        "line",
        [
            "template: flow\n    svg: '<g/>'",
            "title: x",
        ],
    )
    def test_exactly_one_of_template_or_svg(self, tmp_path: Path, line: str) -> None:
        result = _compile(_project(tmp_path), _board_yaml(line=line))
        assert not result.success

    def test_missing_template_file_is_err_custom_template(self, tmp_path: Path) -> None:
        result = _compile(_project(tmp_path), _board_yaml(line="template: nope"))
        assert _errors(result) == ["ERR-CUSTOM-TEMPLATE"]
        assert "templates/nope.svg.j2" in result.errors[0].message

    def test_missing_template_lists_the_available_ones(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        (tmp_path / "templates" / "gauge.svg.j2").write_text("<g/>")
        result = _compile(project, _board_yaml(line="template: nope"))
        assert "available: flow, gauge" in result.errors[0].message

    def test_missing_template_in_an_empty_folder_says_so(self, tmp_path: Path) -> None:
        project = FilesystemProject(tmp_path)
        result = _compile(project, _board_yaml(line="template: nope"))
        assert "templates/ has no templates" in result.errors[0].message

    def test_template_without_project_is_err_custom_template(self) -> None:
        result = compile(_board_yaml())
        assert _errors(result) == ["ERR-CUSTOM-TEMPLATE"]

    def test_jinja_syntax_error_is_err_custom_template(self, tmp_path: Path) -> None:
        project = _project(tmp_path, HEADER + "{% for r in rows %}")
        assert _errors(_compile(project, _board_yaml())) == ["ERR-CUSTOM-TEMPLATE"]

    def test_bad_header_is_err_custom_template(self, tmp_path: Path) -> None:
        project = _project(tmp_path, "{#---\nchannels: [not, a, mapping]\n---#}<g/>")
        assert _errors(_compile(project, _board_yaml())) == ["ERR-CUSTOM-TEMPLATE"]

    def test_unknown_param_type_in_header_is_err_custom_template(
        self, tmp_path: Path
    ) -> None:
        header = "{#---\noptions:\n  a:\n    type: vector\n---#}<g/>"
        project = _project(tmp_path, header)
        assert _errors(_compile(project, _board_yaml())) == ["ERR-CUSTOM-TEMPLATE"]

    @pytest.mark.parametrize(
        ("fields", "needle"),
        [
            ("x: s", "'y'"),
            ("x: s\n    y: v\n    size: z", "size"),
            ("x: s\n    y: [a, b]", "multiple"),
            ("x: s\n    y: v\n    options:\n      bogus: 1", "bogus"),
            ("x: s\n    y: v\n    options:\n      label_size: big", "label_size"),
            ('x: ""\n    y: v', "must name column"),
        ],
    )
    def test_field_validation_is_err_custom_fields(
        self, tmp_path: Path, fields: str, needle: str
    ) -> None:
        yaml_text = _board_yaml().replace(
            "    x: from_stage\n    y: visitors\n", f"    {fields}\n"
        )
        result = _compile(_project(tmp_path), yaml_text)
        assert _errors(result) == ["ERR-CUSTOM-FIELDS"]
        assert needle in result.errors[0].message

    @pytest.mark.parametrize("value", ["[]", '[""]'])
    def test_empty_multiple_channel_is_err_custom_fields(
        self, tmp_path: Path, value: str
    ) -> None:
        header = "{#---\nchannels:\n  x: {}\n  y:\n    multiple: true\n---#}<g/>"
        yaml_text = _board_yaml().replace("    y: visitors\n", f"    y: {value}\n")
        result = _compile(_project(tmp_path, header), yaml_text)
        assert _errors(result) == ["ERR-CUSTOM-FIELDS"]
        assert "must name column" in result.errors[0].message

    def test_missing_required_option_is_err_custom_fields(self, tmp_path: Path) -> None:
        header = (
            "{#---\nchannels:\n  x: {}\n  y: {}\n"
            "options:\n  max:\n    type: number\n---#}<g/>"
        )
        result = _compile(_project(tmp_path, header), _board_yaml())
        assert _errors(result) == ["ERR-CUSTOM-FIELDS"]
        assert "missing required option 'max'" in result.errors[0].message

    @pytest.mark.parametrize(
        ("channel", "needle"),
        [
            ("x: 3", "must be a mapping"),
            ("x:\n    required: true", "`required` is not a key"),
            ("x:\n    default: from_stage", "only `default` is null"),
        ],
    )
    def test_bad_channel_header_is_err_custom_template(
        self, tmp_path: Path, channel: str, needle: str
    ) -> None:
        project = _project(tmp_path, "{#---\nchannels:\n  " + channel + "\n---#}<g/>")
        result = _compile(project, _board_yaml())
        assert _errors(result) == ["ERR-CUSTOM-TEMPLATE"]
        assert needle in result.errors[0].message

    def test_headerless_template_rejects_channels_and_options(
        self, tmp_path: Path
    ) -> None:
        project = _project(tmp_path, BODY)
        assert _errors(_compile(project, _board_yaml())) == ["ERR-CUSTOM-FIELDS"]
        inline = BOARD.format(template_line="svg: '<g/>'")
        assert _errors(_compile(project, inline)) == ["ERR-CUSTOM-FIELDS"]
        options = BOARD.format(template_line="svg: '<g/>'").replace(
            "    x: from_stage\n    y: visitors\n", "    options:\n      a: 1\n"
        )
        assert _errors(_compile(project, options)) == ["ERR-CUSTOM-FIELDS"]

    def test_params_is_no_longer_a_chart_key_or_header_key(
        self, tmp_path: Path
    ) -> None:
        chart = _board_yaml().replace(
            "    x: from_stage\n    y: visitors\n", "    params:\n      a: 1\n"
        )
        assert not _compile(_project(tmp_path), chart).success
        header = "{#---\nparams:\n  a:\n    type: number\n---#}<g/>"
        root = tmp_path / "h"
        root.mkdir()
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:", "rows:", _board_yaml()
        )
        assert _errors(_compile(_project(root, header), yaml_text)) == [
            "ERR-CUSTOM-TEMPLATE"
        ]

    def test_header_channel_names_come_from_the_fixed_set(self, tmp_path: Path) -> None:
        header = "{#---\nchannels:\n  label:\n    description: nope\n---#}<g/>"
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:", "rows:", _board_yaml()
        )
        assert _errors(_compile(_project(tmp_path, header), yaml_text)) == [
            "ERR-CUSTOM-TEMPLATE"
        ]

    def test_multiple_channel_always_arrives_as_a_list(self, tmp_path: Path) -> None:
        header = "{#---\nchannels:\n  x:\n    description: a\n  y:\n    multiple: true\n---#}"
        template = header + '<text id="a">{{ channels.y | join("+") }}</text>'
        project = _project(tmp_path, template)
        for given, expected in (
            ("visitors", "visitors"),
            ("[visitors, to_stage]", "visitors+to_stage"),
        ):
            yaml_text = _board_yaml().replace("y: visitors", f"y: {given}")
            chart = _compile(project, yaml_text).board.charts["flow"]
            assert isinstance(chart.channels["y"], list)
            svg = _render(project, yaml_text).output
            assert f">{expected}<" in svg

    def test_unset_optional_channel_is_none_in_the_template(
        self, tmp_path: Path
    ) -> None:
        template = HEADER + '<text id="a">{{ channels.color is none }}</text>'
        svg = _render(_project(tmp_path, template), _board_yaml()).output
        assert ">True<" in svg

    def test_style_accepts_only_sizing(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        ok = _board_yaml().replace(
            "    title: Where", "    style:\n      aspect_ratio: 2\n    title: Where"
        )
        assert _compile(project, ok).success
        bad = _board_yaml().replace(
            "    title: Where",
            "    style:\n      color:\n        static: red\n    title: Where",
        )
        assert not _compile(project, bad).success


class TestRender:
    def test_renders_rows_through_the_template(self, tmp_path: Path) -> None:
        svg = _render(_project(tmp_path), _board_yaml()).output
        assert "visit: 100" in svg
        assert "signup: 40" in svg
        assert "Where visitors go" in svg
        ElementTree.fromstring(svg)

    def test_uid_is_unique_per_chart(self, tmp_path: Path) -> None:
        chart = _board_yaml().split("charts:\n")[1].split("rows:\n")[0]
        yaml_text = (
            _board_yaml().split("charts:\n")[0]
            + "charts:\n"
            + chart
            + chart.replace("flow:", "flow2:")
            + "rows:\n  - flow\n  - flow2\n"
        )
        svg = _render(_project(tmp_path), yaml_text).output
        ids = re.findall(r'id="([^"]*-g)"', svg)
        assert len(ids) == 2 and len(set(ids)) == 2

    def test_text_width_measures_digits_as_tabular_numerals(
        self, tmp_path: Path
    ) -> None:
        template = (
            "{#---\ndescription: probe\n---#}"
            '<text id="a">{{ text_width("111", 12) == text_width("000", 12) }}</text>'
        )
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        svg = _render(_project(tmp_path, template), yaml_text).output
        assert re.search(r'<text id="a"[^>]*>True</text>', svg)

    def test_template_paints_with_the_engines_color_helpers(
        self, tmp_path: Path
    ) -> None:
        template = (
            "{#---\ndescription: probe\n---#}"
            '<text id="a">{{ label_ink("#e6b800") }}</text>'
            '<text id="b">{{ variant("#3366cc", "pale") }}</text>'
            '<text id="c">{{ style.palettes.chrome | length }}</text>'
        )
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        svg = _render(_project(tmp_path, template), yaml_text).output
        texts = dict(re.findall(r'<text id="(\w)"[^>]*>([^<]*)</text>', svg))
        assert re.fullmatch(r"#[0-9a-f]{6}", texts["a"], re.I)
        assert texts["a"].lower() != "#e6b800"
        assert re.fullmatch(r"#[0-9a-f]{6}", texts["b"], re.I)
        assert int(texts["c"]) > 0

    def test_template_sees_style_and_helpers(self, tmp_path: Path) -> None:
        template = (
            "{#---\ndescription: probe\n---#}"
            '<text id="a">{{ style.palettes.category | length }}</text>'
            '<text id="b">{{ style.palettes.info.solid }}</text>'
            '<text id="c">{{ style.font.family }}</text>'
            '<text id="d">{{ text_width("hello", 12, 400) > 10 }}</text>'
            '<text id="e">{{ color_for("x") }}</text>'
            '<text id="f">{{ width > 0 and height > 0 }}</text>'
            '<text id="g">{{ columns | join(",") }}</text>'
            '<text id="h">{{ style.fonts.secondary.color }}</text>'
        )
        project = _project(tmp_path, template)
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        svg = _render(project, yaml_text).output
        texts = dict(re.findall(r'<text id="(\w)"[^>]*>([^<]*)</text>', svg))
        assert int(texts["a"]) >= 2
        assert texts["b"].startswith("#")
        assert texts["d"] == "True"
        assert texts["e"].startswith("#")
        assert texts["f"] == "True"
        assert texts["g"] == "from_stage,to_stage,visitors"
        assert texts["h"].startswith("#")

    def test_missing_channel_column_names_available_columns(
        self, tmp_path: Path
    ) -> None:
        yaml_text = _board_yaml().replace("x: from_stage", "x: nope")
        out = _render(_project(tmp_path), yaml_text)
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]
        assert "channel 'x'" in out.chart_errors[0].message
        assert "from_stage" in out.chart_errors[0].message

    def test_raise_error_aborts_with_chart_level_error(self, tmp_path: Path) -> None:
        template = HEADER + '{{ raise_error("needs two flows") }}'
        out = _render(_project(tmp_path, template), _board_yaml())
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]
        assert "needs two flows" in out.chart_errors[0].message

    def test_undefined_name_is_err_custom_render(self, tmp_path: Path) -> None:
        template = HEADER + "<text>{{ options.typo }}</text>"
        out = _render(_project(tmp_path, template), _board_yaml())
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]

    def test_row_values_are_escaped(self, tmp_path: Path) -> None:
        yaml_text = _board_yaml().replace(
            "from_stage: visit", 'from_stage: "<script>alert(1)</script>"'
        )
        svg = _render(_project(tmp_path), yaml_text).output
        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg

    def test_disallowed_svg_is_err_custom_svg(self, tmp_path: Path) -> None:
        template = HEADER + '<rect width="1" onclick="x()"/>'
        out = _render(_project(tmp_path, template), _board_yaml())
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-SVG"]
        assert "onclick" in out.chart_errors[0].message

    def test_null_interpolated_into_an_attribute_is_err_custom_svg(
        self, tmp_path: Path
    ) -> None:
        template = HEADER + '<rect width="{{ options.note }}" height="2"/>'
        out = _render(_project(tmp_path, template), _board_yaml())
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-SVG"]
        assert "null" in out.chart_errors[0].message
        assert 'width="None"' not in out.output

    def test_null_in_text_content_still_prints(self, tmp_path: Path) -> None:
        template = HEADER + "<text>{{ options.note }}</text>"
        out = _render(_project(tmp_path, template), _board_yaml())
        assert not out.chart_errors
        assert ">None</text>" in out.output

    @pytest.mark.parametrize(
        "payload",
        [
            "<g><!--><img src=x onerror=alert(1)>--></g>",
            "<?x ><svg onload=alert(2)>?>",
            '<use HREF="https://evil.test/x.svg#a"/>',
        ],
    )
    def test_html_parser_differentials_never_reach_the_board(
        self, tmp_path: Path, payload: str
    ) -> None:
        out = _render(_project(tmp_path, HEADER + payload), _board_yaml())
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-SVG"]
        # The error placard may quote the rejected text, escaped; no live markup.
        for needle in ("<img", "onload=", 'href="https://evil'):
            assert needle not in out.output.lower()

    def test_aspect_ratio_sizes_the_chart(self, tmp_path: Path) -> None:
        def height(ratio: str) -> float:
            root = tmp_path / ratio
            root.mkdir()
            yaml_text = _board_yaml().replace(
                "    title: Where",
                f"    style:\n      aspect_ratio: {ratio}\n    title: Where",
            )
            svg = _render(_project(root), yaml_text).output
            match = re.search(r'data-chart-height="([\d.]+)"', svg)
            assert match is not None
            return float(match.group(1))

        assert height("1") > height("4")

    def test_inline_svg_is_never_run_through_board_templating(
        self, tmp_path: Path
    ) -> None:
        yaml_text = BOARD.format(
            template_line="svg: |-\n"
            '      {% for r in rows %}<text x="1" y="{{ loop.index * 9 }}">'
            "{{ r.from_stage }}</text>{% endfor %}"
        ).replace("    x: from_stage\n    y: visitors\n", "")
        svg = _render(FilesystemProject(tmp_path), yaml_text).output
        assert ">visit</text>" in svg
        assert ">signup</text>" in svg

    def test_math_namespace_is_available(self, tmp_path: Path) -> None:
        template = '<text id="a">{{ (math.cos(0) + math.sqrt(4) + math.pi) | round(2) }}</text>'
        project = _project(tmp_path, template)
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        svg = _render(project, yaml_text).output
        assert ">6.14<" in svg

    def test_color_for_uses_pins_then_palette_in_first_call_order(
        self, tmp_path: Path
    ) -> None:
        template = (
            '<text id="a">{{ color_for("visit", "stage") }}</text>'
            '<text id="b">{{ color_for("x") }}</text>'
            '<text id="c">{{ color_for("y") }}</text>'
            '<text id="d">{{ color_for("x") }}</text>'
            '<text id="e">{{ style.charts.color.categorical.palette[0] }}</text>'
            '<text id="f">{{ style.charts.color.categorical.palette[1] }}</text>'
        )
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        yaml_text = (
            "style:\n  charts:\n    category_colors:\n      stage:\n        values:\n"
            "          visit: '#ff0000'\n" + yaml_text
        )
        svg = _render(_project(tmp_path, template), yaml_text).output
        t = dict(re.findall(r'<text id="(\w)"[^>]*>([^<]*)</text>', svg))
        assert t["a"] == "#ff0000"
        assert t["b"] == t["d"] == t["e"]
        assert t["c"] == t["f"]

    def test_color_for_raises_when_palette_is_exhausted(self, tmp_path: Path) -> None:
        template = "{% for i in range(40) %}{{ color_for(i) }}{% endfor %}"
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        out = _render(_project(tmp_path, template), yaml_text)
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]
        assert "pin the extra values" in out.chart_errors[0].message

    def test_style_is_the_cascaded_tree_and_follows_the_theme(
        self, tmp_path: Path
    ) -> None:
        template = (
            '<text id="a">{{ style.background }}</text>'
            '<text id="b">{{ style.palettes.category[0] }}</text>'
        )
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )

        def values(prefix: str, root: Path) -> dict[str, str]:
            root.mkdir()
            svg = _render(_project(root, template), prefix + yaml_text).output
            return dict(re.findall(r'<text id="(\w)"[^>]*>([^<]*)</text>', svg))

        light = values("", tmp_path / "light")
        authored = values("style:\n  background: '#123456'\n", tmp_path / "authored")
        assert authored["a"] == "#123456"
        assert light["a"] != authored["a"]


class TestTemplateName:
    @pytest.mark.parametrize(
        "name", ["../../x", "a\\b", "/etc/x", "''", "a/../b", "a//b", "a.b", "a/"]
    )
    def test_path_like_name_is_a_validation_error(
        self, tmp_path: Path, name: str
    ) -> None:
        result = _compile(_project(tmp_path), _board_yaml(line=f"template: {name!r}"))
        assert not result.success
        assert "template" in result.errors[0].message

    def test_subfolder_name_is_allowed(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        (tmp_path / "templates" / "charts").mkdir()
        (tmp_path / "templates" / "charts" / "flow.svg.j2").write_text(HEADER + BODY)
        result = _compile(project, _board_yaml(line="template: charts/flow"))
        assert result.success, [d.message for d in result.errors]


COLOR_HEADER = "{#---\noptions:\n  tint:\n    type: color\n---#}"
COLOR_BOARD = BOARD.replace(
    "    x: from_stage\n    y: visitors\n", "    options:\n      tint: {tint}\n"
)


def _color_board(tint: str) -> str:
    return COLOR_BOARD.format(template_line="template: flow", tint=tint)


class TestColorParams:
    TEMPLATE = (
        COLOR_HEADER + '<rect id="r" fill="{{ options.tint }}" width="1" height="1"/>'
    )

    def test_hex_passes_through(self, tmp_path: Path) -> None:
        svg = _render(
            _project(tmp_path, self.TEMPLATE), _color_board("'#123456'")
        ).output
        assert 'fill="#123456"' in svg

    def test_token_resolves_to_hex_in_the_template(self, tmp_path: Path) -> None:
        svg = _render(
            _project(tmp_path, self.TEMPLATE), _color_board("category[1]")
        ).output
        assert re.search(r'id="r" fill="#[0-9a-fA-F]{6}"', svg)

    @pytest.mark.parametrize("tint", ["category.0", "nonsense.blue"])
    def test_unresolvable_token_is_a_chart_level_fields_error(
        self, tmp_path: Path, tint: str
    ) -> None:
        out = _render(_project(tmp_path, self.TEMPLATE), _color_board(tint))
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-FIELDS"]
        assert tint in out.chart_errors[0].message


class TestUntestedBranches:
    def test_header_default_is_type_checked(self, tmp_path: Path) -> None:
        header = (
            "{#---\noptions:\n  n:\n    type: integer\n    default: oops\n---#}<g/>"
        )
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        result = _compile(_project(tmp_path, header), yaml_text)
        assert _errors(result) == ["ERR-CUSTOM-TEMPLATE"]

    def test_required_key_in_header_is_rejected(self, tmp_path: Path) -> None:
        header = (
            "{#---\noptions:\n  n:\n    type: integer\n    required: true\n---#}<g/>"
        )
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        assert _errors(_compile(_project(tmp_path, header), yaml_text)) == [
            "ERR-CUSTOM-TEMPLATE"
        ]

    def test_output_budget_exceeded_is_err_custom_render(self, tmp_path: Path) -> None:
        template = "{% for i in range(5000) %}<g/>{% endfor %}"
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        project = _project(tmp_path, template)
        result = _compile(project, yaml_text)
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(project),
            query_registry=result.query_registry,
        )
        with template_output_budget(1000):
            out = render(result.board, executor, format="svg")
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]
        assert "exceeded" in out.chart_errors[0].message

    def test_tone_palette_role_is_an_alias_dict(self, tmp_path: Path) -> None:
        template = (
            '<text id="a">{{ style.palettes.info.solid }}</text>'
            '<text id="b">{{ style.palettes.info | items | map(attribute=0) | sort | join(",") }}</text>'
        )
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        svg = _render(_project(tmp_path, template), yaml_text).output
        t = dict(re.findall(r'<text id="(\w)"[^>]*>([^<]*)</text>', svg))
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", t["a"])
        assert t["b"] == "bg,border,solid,solid-hover,subtle,text"


class TestSizing:
    @pytest.mark.parametrize(
        "style",
        ["aspect_ratio: 2", "aspect_ratio: 1\n      min_height: 0", "min_height: 500"],
    )
    def test_layout_and_standalone_paths_agree(self, style: str) -> None:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )
        from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
        from dbt_charts.core.render.sizing import get_chart_content_height

        yaml_text = (
            BOARD.format(template_line="svg: '<g/>'")
            .replace(
                "    x: from_stage\n    y: visitors\n",
                f"    style:\n      {style}\n",
            )
            .replace("    title: Where visitors go\n", "")
        )
        board = compile(yaml_text).board
        assert board is not None
        resolved_style, context = resolve_style_and_context(get_theme_style())
        chart = board.charts["flow"]
        resolved = resolve(chart, [], context, width=600)
        artifact = render_resolved_chart(resolved, [], resolved_style, width=600)
        assert isinstance(artifact.payload, str)
        match = re.search(r'height="([\d.]+)"', artifact.payload)
        assert match is not None
        assert float(match.group(1)) == pytest.approx(
            get_chart_content_height(chart, width=600, resolved_style=resolved_style)
        )


class TestHelperArguments:
    @pytest.mark.parametrize(
        "call",
        [
            "text_width(5, 12, 400)",
            'text_width("a", "12", 400)',
            'text_width("a", 12, "heavy")',
            'text_width("a", 12, "600.5")',
            'text_width("a", 12, 400.5)',
            'text_width("a", 12, math.inf)',
            'text_width("a", 12, true)',
            'text_width("a", 12, 0)',
            'text_width("a", 12, "-5")',
            'text_width("a", 12, 1001)',
            'text_width("a", 12, 1e400)',
            'text_width("a", -1, 400)',
            'text_width("a", math.exp(709) * 10, 400)',
            'text_width("a", math.nan, 400)',
            'color_for("a", 3)',
        ],
    )
    def test_bad_argument_types_are_err_custom_render(
        self, tmp_path: Path, call: str
    ) -> None:
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        out = _render(_project(tmp_path, "{{ " + call + " }}"), yaml_text)
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]
        assert call.split("(")[0] in out.chart_errors[0].message

    @pytest.mark.parametrize(
        "weight",
        [
            "style.font.weight",
            "style.charts.title.font.weight",
            '"600"',
            "700.0",
            '"bold"',
            '"Normal"',
        ],
    )
    def test_text_width_takes_the_weights_the_style_tree_carries(
        self, tmp_path: Path, weight: str
    ) -> None:
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        template = (
            '<text id="w">{{ text_width("hello", 12, ' + weight + ") > 10 }}</text>"
        )
        out = _render(_project(tmp_path, template), yaml_text)
        assert out.chart_errors == []
        assert ">True</text>" in out.output


class TestD3Namespace:
    @staticmethod
    def _render_template(tmp_path: Path, template: str) -> Any:
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        return _render(_project(tmp_path, template), yaml_text)

    def test_scales_and_paths_reach_the_template(self, tmp_path: Path) -> None:
        out = self._render_template(
            tmp_path,
            "{% set x = d3.scale_linear([0, 97], [0, 100]).nice() %}"
            '<path id="p" d="{{ d3.line([[0, 0], [x(100), 10]]) }}"/>'
            "<text>{{ x.domain }} {{ x.ticks(2) }}</text>",
        )
        assert out.chart_errors == []
        assert 'd="M0,0L100,10"' in out.output
        assert "[0.0, 100.0] [0.0, 50.0, 100.0]" in out.output

    @pytest.mark.parametrize(
        ("call", "named"),
        [
            ("d3.scale_linear([1, 1], [0, 1])", "d3.scale_linear()"),
            ('d3.scale_band(["a"], [0, 1])("b")', "d3.scale_band()"),
            ('d3.line([[0, 0]], "bumpy")', "d3.line()"),
            ("d3.pie([1, -1])", "d3.pie()"),
        ],
    )
    def test_a_bad_call_is_err_custom_render_naming_it(
        self, tmp_path: Path, call: str, named: str
    ) -> None:
        out = self._render_template(tmp_path, "{{ " + call + " }}")
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]
        assert named in out.chart_errors[0].message


class TestStyleTreeWeights:
    def test_weight_tokens_reach_the_template_as_integers(self, tmp_path: Path) -> None:
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "rows:",
            _board_yaml(),
            flags=re.S,
        )
        template = (
            '<text id="w">{{ style.charts.title.font.weight }} '
            "{{ style.charts.legend.title.font.weight }} "
            "{{ style.charts.series_label.font.compact_weight }}</text>"
        )
        out = _render(_project(tmp_path, template), yaml_text)
        assert out.chart_errors == []
        [weights] = re.findall(r'<text id="w">([^<]*)</text>', out.output)
        assert re.fullmatch(r"\d+ \d+ \d+", weights), weights


class TestColumnOptions:
    def test_column_option_is_checked_against_the_result(self, tmp_path: Path) -> None:
        template = "{#---\noptions:\n  by:\n    type: column\n---#}<g/>"
        yaml_text = re.sub(
            r"    x: from_stage\n    y: visitors\nrows:",
            "    options:\n      by: nope\nrows:",
            _board_yaml(),
        )
        out = _render(_project(tmp_path, template), yaml_text)
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]
        assert "option 'by'" in out.chart_errors[0].message


QUERYLESS_BOARD = """
charts:
  flow:
    type: custom
    template: flow
    x: from_stage
    y: visitors
rows:
  - flow
"""

ROW_COUNT = '<text id="n">{{ rows | length }} {{ columns | length }}</text>'


class TestQuerylessChart:
    """No `query:` is a blank chart, as in every family: the template sees no rows."""

    def test_board_render_hands_the_template_empty_rows(self, tmp_path: Path) -> None:
        out = _render(_project(tmp_path, HEADER + ROW_COUNT), QUERYLESS_BOARD)
        assert out.chart_errors == []
        assert '<text id="n">0 0</text>' in out.output

    def test_placeholder_render_skips_placeholder_rows(self, tmp_path: Path) -> None:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )
        from dbt_charts.core.render.chart.vega_lite import render_chart

        project = _project(tmp_path, HEADER + ROW_COUNT)
        result = _compile(project, QUERYLESS_BOARD)
        assert result.success, [d.message for d in result.errors]
        style, context = resolve_style_and_context(get_theme_style())
        svg = render_chart(
            result.board.charts["flow"],
            style,
            context,
            data=[],
            format="svg",
            width=300,
            is_placeholder=True,
        )
        assert '<text id="n">0 0</text>' in svg


class TestLinks:
    def test_static_link_wraps_the_drawing(self, tmp_path: Path) -> None:
        yaml_text = _board_yaml().replace(
            "    template: flow\n",
            "    template: flow\n    link: https://x.test/flows\n",
        )
        svg = _render(_project(tmp_path), yaml_text).output
        assert '<a href="https://x.test/flows"><g transform="translate(' in svg

    def test_row_link_is_the_templates_through_href(self, tmp_path: Path) -> None:
        template = HEADER + (
            '{% for r in rows %}<a href="{{ href(r) }}"><rect y="{{ loop.index }}"/>'
            "</a>{% endfor %}"
        )
        yaml_text = _board_yaml().replace(
            "    template: flow\n",
            "    template: flow\n    link: https://x.test/stage/{{ x }}?v=1\n",
        )
        svg = _render(_project(tmp_path, template), yaml_text).output
        assert '<a href="https://x.test/stage/visit?v=1"><rect y="1"/></a>' in svg
        assert '<a href="https://x.test/stage/signup?v=1"><rect y="2"/></a>' in svg
        assert '<a href="https://x.test/stage/{{' not in svg

    def test_a_board_default_open_does_not_refuse_a_row_link(
        self, tmp_path: Path
    ) -> None:
        template = HEADER + (
            '{% for r in rows %}<a href="{{ href(r) }}"><rect/></a>{% endfor %}'
        )
        yaml_text = "links:\n  open: modal\n" + _board_yaml().replace(
            "    template: flow\n",
            "    template: flow\n    link: https://x.test/stage/{{ x }}\n",
        )
        out = _render(_project(tmp_path, template), yaml_text)
        assert not out.chart_errors
        assert "data-dbt-link-open" not in out.output

    def test_a_board_default_open_reaches_a_static_link(self, tmp_path: Path) -> None:
        yaml_text = "links:\n  open: modal\n" + _board_yaml().replace(
            "    template: flow\n",
            "    template: flow\n    link: https://x.test/flows\n",
        )
        svg = _render(_project(tmp_path), yaml_text).output
        assert 'data-dbt-link-open="modal"' in svg

    def test_template_anchor_scheme_follows_the_link_law(self, tmp_path: Path) -> None:
        template = HEADER + '<a href="javascript:alert(1)"><rect/></a>'
        out = _render(_project(tmp_path, template), _board_yaml())
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-SVG"]
        assert "<a>" in out.chart_errors[0].message


class TestVariablesAndFonts:
    def test_a_variable_filters_the_rows_the_template_draws(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text(
            'sources:\n  db:\n    type: duckdb\n    path: ":memory:"\n'
        )
        yaml_text = (
            "variables:\n  stage:\n    input: select\n    options:\n"
            "      static: [visit, signup]\n    default: visit\n"
            "queries:\n  flows:\n    source: db\n    sql: |\n"
            "      SELECT * FROM (VALUES ('visit', 100), ('signup', 40))"
            " t(from_stage, visitors)\n"
            "      WHERE from_stage = '{{ stage }}'\n"
            "charts:\n  flow:\n    type: custom\n    query: flows\n"
            "    title: Stage {{ stage }}\n    template: flow\n"
            "    x: from_stage\n    y: visitors\nrows:\n  - flow\n"
        )
        project = _project(
            tmp_path,
            HEADER
            + "<text>{{ variables.stage }}:{{ rows | length }}:"
            + "{{ rows[0][channels.x] }}</text>",
        )
        result = _compile(project, yaml_text)
        assert result.success, [d.message for d in result.errors]
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(project),
            query_registry=result.query_registry,
        )
        svg = render(
            result.board, executor, format="svg", variables={"stage": "signup"}
        ).output
        assert isinstance(svg, str)
        assert "<text>signup:1:signup</text>" in svg
        assert "Stage signup" in svg

    def test_the_board_font_reaches_the_template_and_the_export_embeds_it(
        self, tmp_path: Path
    ) -> None:
        template = HEADER + '<text font-family="{{ style.font.family }}">x</text>'
        project = _project(tmp_path, template)
        result = _compile(project, _board_yaml())
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(project),
            query_registry=result.query_registry,
        )
        html = render(result.board, executor, format="html", standalone=True).output
        assert isinstance(html, str)
        painted = re.search(r'<text font-family="([^"]+)">x</text>', html)
        assert painted, "the template paints the board font"
        first = painted[1].replace("&#x27;", "'").split(",")[0].strip("'\" ")
        embedded = re.findall(
            r"@font-face\s*\{[^}]*font-family:\s*['\"]([^'\"]+)['\"]", html
        )
        assert first in embedded, (first, embedded)
        assert "data:font/woff2;base64," in html


class TestDrawingBox:
    def test_the_template_draws_below_the_title_and_fills_an_untitled_card(
        self, tmp_path: Path
    ) -> None:
        template = HEADER + '<rect height="{{ height }}"/>'
        project = _project(tmp_path, template)
        titled = _render(project, _board_yaml()).output
        untitled = _render(
            project, _board_yaml().replace("    title: Where visitors go\n", "")
        ).output

        def chart_box_and_body(
            svg: str,
        ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
            """The card's padded box (`dbt-box-inner`) and the body the template
            drew into, each as ``(x, y, height)``."""
            body = re.search(
                r'<g transform="translate\(([\d.]+), ([\d.]+)\)"[^>]*>'
                r'<rect height="([\d.]+)"/>',
                svg,
            )
            assert body
            boxes = re.findall(
                r'class="dbt-box-inner" x="([\d.]+)" y="([\d.]+)"[^>]*'
                r'\bheight="([\d.]+)"',
                svg[: body.start()],
            )
            box = tuple(float(v) for v in boxes[-1])
            return (box[0], box[1], box[2]), (
                float(body[1]),
                float(body[2]),
                float(body[3]),
            )

        titled_box, titled_body = chart_box_and_body(titled)
        box, body = chart_box_and_body(untitled)
        assert body == box, "an untitled chart hands the template the whole padded box"
        assert titled_body[0] == titled_box[0], "the body keeps the box's left edge"
        assert titled_body[1] > titled_box[1], "a title takes its block off the top"
        assert titled_body[1] + titled_body[2] == titled_box[1] + titled_box[2]


class TestReviewRegressions:
    def test_two_custom_charts_share_the_board_output_budget(
        self, tmp_path: Path
    ) -> None:
        """The budget is cumulative per board: the second chart gets what is left."""
        template = HEADER + "{% for i in range(40) %}<g/>{% endfor %}"
        yaml_text = _board_yaml().replace(
            "rows:\n  - flow\n",
            "  flow2:\n    type: custom\n    query: flows\n    template: flow\n"
            "    x: from_stage\n    y: visitors\nrows:\n  - flow\n  - flow2\n",
        )
        project = _project(tmp_path, template)
        result = _compile(project, yaml_text)
        assert result.success, [d.message for d in result.errors]
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(project),
            query_registry=result.query_registry,
        )
        # One chart's output fits; two do not.
        with template_output_budget(800):
            out = render(result.board, executor, format="svg")
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]
        assert "exceeded" in out.chart_errors[0].message

    def test_a_header_that_opens_but_does_not_close_is_an_error(
        self, tmp_path: Path
    ) -> None:
        result = _compile(
            _project(tmp_path, "{#--- description: x\n<g/>"), _board_yaml()
        )
        assert _errors(result) == ["ERR-CUSTOM-TEMPLATE"]
        assert "close with `---#}`" in result.errors[0].message

    @pytest.mark.parametrize(
        "board",
        [
            None,
            "title: T\nrows:\n  - type: custom\n    query: q\n    template: flow\n",
            "title: T\nrows:\n  - title: Inner\n    charts:\n      c:\n"
            "        type: custom\n        query: q\n        template: flow\n"
            "    rows:\n      - c\n",
        ],
        ids=["charts", "inline", "nested"],
    )
    def test_a_template_file_is_in_the_boards_import_closure(
        self, tmp_path: Path, board: str | None
    ) -> None:
        from dbt_charts.agent_api.import_closure import board_import_closure

        project = _project(tmp_path)
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "flow.yml").write_text(board or _board_yaml())
        closure = [
            p.relpath for p in board_import_closure(project.path("charts/flow.yml"))
        ]
        assert closure == ["charts/flow.yml", "templates/flow.svg.j2"]

    def test_a_crlf_template_header_parses(self, tmp_path: Path) -> None:
        template = HEADER.replace("\n", "\r\n") + "<g/>"
        result = _compile(_project(tmp_path, template), _board_yaml())
        assert result.success, [d.message for d in result.errors]

    def test_the_framing_crossing_the_budget_is_the_charts_error(
        self, tmp_path: Path
    ) -> None:
        """The template fits the budget; the svg root and card around it do not."""
        template = HEADER + "<text>{{ 'x' * 480 }}</text>"
        yaml_text = _board_yaml().replace("    title: Where visitors go\n", "")
        project = _project(tmp_path, template)
        result = _compile(project, yaml_text)
        assert result.success, [d.message for d in result.errors]
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(project),
            query_registry=result.query_registry,
        )
        with template_output_budget(500):
            out = render(result.board, executor, format="svg")
        assert [d.code for d in out.chart_errors] == ["ERR-CUSTOM-RENDER"]
        assert "exceeded 500 bytes" in out.chart_errors[0].message

    def test_the_schema_states_the_template_or_svg_rule(self) -> None:
        from dbt_charts.core.compile.schema.introspection import introspect
        from dbt_charts.core.compile.schema.renderers.json_schema import (
            render_yaml_schema,
        )

        schema = render_yaml_schema(introspect())
        custom = schema["$defs"]["CustomChart"]
        rules = [r for r in custom["allOf"] if "x-dbt-charts-cross-field-rule" in r]
        one_of_them, not_both = rules
        assert [arm["required"] for arm in one_of_them["anyOf"]] == [
            ["template"],
            ["svg"],
        ]
        both = not_both["not"]["anyOf"][0]["allOf"]
        assert [arm["required"] for arm in both] == [["template"], ["svg"]]

    def test_a_custom_title_shares_the_band_with_the_mark_families(
        self, tmp_path: Path
    ) -> None:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )
        from dbt_charts.core.render.title_band import chart_title_metrics

        yaml_text = (
            _board_yaml()
            .replace(
                "rows:\n  - flow\n",
                "  bars:\n    type: bar\n    query: flows\n    title: Where visitors go\n"
                "    subtitle: by stage\n    x: from_stage\n    y: visitors\nrows:\n"
                "  - cols:\n      - flow\n      - bars\n",
            )
            .replace(
                "    title: Where visitors go\n    template: flow\n",
                "    title: Where visitors go\n    subtitle: by stage\n    template: flow\n",
            )
        )
        result = _compile(_project(tmp_path), yaml_text)
        assert result.success, [d.message for d in result.errors]
        _, context = resolve_style_and_context(get_theme_style())
        rows = [{"from_stage": "visit", "to_stage": "signup", "visitors": 100}]
        custom = resolve(result.board.charts["flow"], rows, context)
        bars = resolve(result.board.charts["bars"], rows, context)
        assert chart_title_metrics(custom, 16, 300, None) == chart_title_metrics(
            bars, 16, 300, None
        )


FORMATS_BOARD = """
{header}
queries:
  flows:
    type: values
    rows:
      - {{from_stage: visit, visitors: 1234567.5}}
      - {{from_stage: signup, visitors: 0.25}}
    formats:
      visitors: currency
charts:
  flow:
    type: custom
    query: flows
    template: flow
    x: from_stage
    y: visitors
rows:
  - flow
"""

FORMAT_BY_COLUMN = (
    "{#---\ndescription: probe\nchannels:\n  x:\n    description: x\n"
    "  y:\n    description: y\n---#}"
    '{% for r in rows %}<text id="{{ r[channels.x] }}">'
    "{{ r[channels.y] | format(column=channels.y) }}</text>{% endfor %}"
)


def _labels(tmp_path: Path, header: str = "") -> dict[str, str]:
    project = _project(tmp_path, FORMAT_BY_COLUMN)
    out = _render(project, FORMATS_BOARD.format(header=header))
    assert out.chart_errors == []
    return dict(re.findall(r'<text id="(\w+)"[^>]*>([^<]*)</text>', out.output))


class TestQueryFormats:
    def test_resolved_payload_carries_every_returned_column_format(
        self, tmp_path: Path
    ) -> None:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        project = _project(tmp_path, FORMAT_BY_COLUMN)
        board = _compile(project, FORMATS_BOARD.format(header="")).board
        assert board is not None
        _, context = resolve_style_and_context(get_theme_style())
        rows = [{"from_stage": "visit", "visitors": 5}]
        resolved = resolve(board.charts["flow"], rows, context, width=600)
        formats = resolved.column_formats
        assert set(formats) == {"from_stage", "visitors"}
        assert formats["visitors"].raw == "currency"
        assert formats["from_stage"].raw == "number"

    def test_query_formats_change_the_label(self, tmp_path: Path) -> None:
        assert _labels(tmp_path)["visit"].startswith("$")

    def test_style_formats_redefinition_changes_the_label(self, tmp_path: Path) -> None:
        header = 'style:\n  formats:\n    currency: "$,.0f"\n'
        assert _labels(tmp_path, header)["visit"] == "$1,234,568"

    def test_a_redefined_small_value_format_reaches_the_label(
        self, tmp_path: Path
    ) -> None:
        header = 'style:\n  formats:\n    currency_full: "$,.3f"\n'
        assert _labels(tmp_path, header)["signup"] == "$0.250"

    def test_regional_currency_changes_the_label(self, tmp_path: Path) -> None:
        header = "regional:\n  currency: EUR\n"
        assert "€" in _labels(tmp_path, header)["visit"]

    def test_an_unreturned_column_names_the_returned_ones(self, tmp_path: Path) -> None:
        template = FORMAT_BY_COLUMN.replace("column=channels.y", "column='nope'")
        out = _render(_project(tmp_path, template), FORMATS_BOARD.format(header=""))
        message = out.chart_errors[0].message
        assert "nope" in message and "'from_stage', 'visitors'" in message
