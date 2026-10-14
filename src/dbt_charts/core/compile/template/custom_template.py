"""Header, channels, and options for ``type: custom`` chart templates.

A custom chart is drawn by a project template (``templates/<name>.svg.j2``) or
an inline ``svg:`` source. The template may open with a Jinja comment holding
YAML — ``description``, ``channels``, and ``options`` — which declares the
channel columns (``x``, ``y``, ``color``, ...) and the options the chart's own
fields are validated against.

The engine renders the template (``libs/chart-svg/chart-renderer/src/families/
custom.rs`` owns the render context); this module reads the header and checks
the syntax in that same engine, so a template the engine cannot parse fails at
compile.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, get_args

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from dbt_charts import _engine
from dbt_charts.core.colors import is_color_token, is_sanitizable_color

if TYPE_CHECKING:
    from dbt_charts.core.project import Project

OptionType = Literal[
    "column", "columns", "string", "number", "integer", "boolean", "color"
]
ChannelName = Literal["x", "y", "color", "size", "shape", "theta", "value"]
CHANNEL_NAMES: tuple[ChannelName, ...] = get_args(ChannelName)
ChannelValue = str | list[str] | None
# What a declared option holds: one value per `OptionType`.
OptionValue = str | int | float | bool | list[str] | None

_HEADER = re.compile(r"\A\s*\{#---\r?\n(.*?)\r?\n---#\}", re.DOTALL)

TEMPLATES_DIR = "templates"
TEMPLATE_FILE_SUFFIX = ".svg.j2"
# Error-message label for an `svg:` chart; never a public value.
INLINE_TEMPLATE_NAME = "inline svg:"


class CustomTemplateError(Exception):
    """The template or its fields are unusable; ``str(exc)`` is the reason."""


class CustomChannelSpec(BaseModel):
    """One channel a template declares in its header."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(default="", description="What the channel encodes.")
    multiple: bool = Field(default=False, description="True when a list is allowed.")
    required: bool = Field(description="True when the header declares no `default`.")

    @model_validator(mode="before")
    @classmethod
    def _derive_required(cls, data: JsonValue) -> JsonValue:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("a channel must be a mapping")
        if "required" in data:
            raise ValueError("`required` is not a key; omit `default` to require it")
        if data.get("default") is not None:
            raise ValueError("a channel's only `default` is null (optional)")
        return {k: v for k, v in data.items() if k != "default"} | {
            "required": "default" not in data
        }


class CustomOptionSpec(BaseModel):
    """One option a template declares in its header."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: OptionType = Field(description="Value type the option accepts.")
    description: str = Field(default="", description="What the option controls.")
    required: bool = Field(description="True when the header declares no `default`.")
    default: OptionValue = Field(default=None, description="Value used when omitted.")

    @model_validator(mode="before")
    @classmethod
    def _derive_required(cls, data: JsonValue) -> JsonValue:
        if not isinstance(data, dict):
            raise ValueError("an option must be a mapping with a `type:`")
        if "required" in data:
            raise ValueError("`required` is not a key; omit `default` to require it")
        return {**data, "required": "default" not in data}


class CustomTemplateHeader(BaseModel):
    """The parsed ``{#--- ... ---#}`` header; empty when the template has none."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(default="", description="What the template draws.")
    channels: dict[ChannelName, CustomChannelSpec] = Field(
        default_factory=dict, description="Declared channels by name."
    )
    options: dict[str, CustomOptionSpec] = Field(
        default_factory=dict, description="Declared options by name."
    )


def available_template_names(project: Project) -> list[str]:
    """Names usable as ``template:`` in the project, sorted."""
    return [
        relpath.removeprefix(f"{TEMPLATES_DIR}/").removesuffix(TEMPLATE_FILE_SUFFIX)
        for relpath in project.iter_files(TEMPLATES_DIR, recursive=False)
        if relpath.endswith(TEMPLATE_FILE_SUFFIX)
    ]


def parse_template(source: str) -> CustomTemplateHeader:
    """Parse the header and check the syntax in the engine; raise ``CustomTemplateError``."""
    match = _HEADER.match(source)
    if match is None and source.lstrip().startswith("{#---"):
        raise CustomTemplateError(
            "template header must open with `{#---` on its own line and "
            "close with `---#}` on its own line"
        )
    try:
        _engine.check_template(source)
    except ValueError as exc:
        raise CustomTemplateError(str(exc)) from exc
    if match is None:
        return CustomTemplateHeader()
    try:
        raw = yaml.safe_load(match.group(1))
        header = CustomTemplateHeader.model_validate(raw if raw is not None else {})
    except (yaml.YAMLError, ValidationError) as exc:
        raise CustomTemplateError(f"invalid template header: {exc}") from exc
    for name, spec in header.options.items():
        if not spec.required and spec.default is not None:
            _check_value(f"option {name!r}", spec.type, spec.default, where="default")
    return header


def validate_fields(
    header: CustomTemplateHeader,
    channels: dict[ChannelName, ChannelValue],
    options: Mapping[str, OptionValue],
) -> tuple[dict[str, ChannelValue], dict[str, OptionValue]]:
    """Authored channels and options against the header; defaults filled.

    ``channels`` holds the chart's channel fields (unset ones as ``None``). A
    ``multiple`` channel always comes back as a list, an optional unset one as
    ``None``. Raises ``CustomTemplateError`` on anything undeclared, missing,
    or of the wrong type.
    """
    return _validate_channels(header, channels), _validate_options(header, options)


def _validate_channels(
    header: CustomTemplateHeader, authored: dict[ChannelName, ChannelValue]
) -> dict[str, ChannelValue]:
    set_names = sorted(name for name, value in authored.items() if value is not None)
    undeclared = [name for name in set_names if name not in header.channels]
    if undeclared:
        raise CustomTemplateError(
            f"the chart sets channel(s) {undeclared}, but the template declares "
            f"channels {sorted(header.channels)}"
        )
    channels: dict[str, ChannelValue] = {}
    for name, spec in header.channels.items():
        value = authored[name]
        if value is None:
            if spec.required:
                raise CustomTemplateError(f"missing required channel {name!r}")
            channels[name] = None
            continue
        if isinstance(value, list) and not spec.multiple:
            raise CustomTemplateError(
                f"channel {name!r} takes one column; a list needs `multiple: true`"
                " in the template header"
            )
        names = [value] if isinstance(value, str) else value
        if not names or not all(c != "" for c in names):
            raise CustomTemplateError(
                f"channel {name!r} must name column(s); {value!r} is not"
            )
        channels[name] = names if spec.multiple else value
    return channels


def _validate_options(
    header: CustomTemplateHeader, authored: Mapping[str, OptionValue]
) -> dict[str, OptionValue]:
    if not header.options and authored:
        raise CustomTemplateError(
            f"the template declares no options, but options: names {sorted(authored)}"
        )
    unknown = sorted(set(authored) - set(header.options))
    if unknown:
        raise CustomTemplateError(
            f"unknown option(s) {unknown}; the template declares "
            f"{sorted(header.options)}"
        )
    options: dict[str, OptionValue] = {}
    for name, spec in header.options.items():
        if name in authored:
            _check_value(f"option {name!r}", spec.type, authored[name], where="value")
            options[name] = authored[name]
        elif spec.required:
            raise CustomTemplateError(f"missing required option {name!r} ({spec.type})")
        else:
            options[name] = spec.default
    return options


def _check_value(
    label: str, kind: OptionType, value: OptionValue, *, where: str
) -> None:
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    ok = {
        "column": isinstance(value, str) and value != "",
        "columns": isinstance(value, list)
        and bool(value)
        and all(c != "" for c in value),
        "string": isinstance(value, str),
        "number": is_number,
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "color": isinstance(value, str)
        and (is_color_token(value) or is_sanitizable_color(value)),
    }[kind]
    if not ok:
        raise CustomTemplateError(f"{label} expects {kind}; {where} {value!r} is not")
