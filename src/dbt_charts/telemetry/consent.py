"""Consent resolution.

Precedence: ``DO_NOT_TRACK=1`` > ``--no-send-anonymous-usage-stats`` >
``DBT_SEND_ANONYMOUS_USAGE_STATS`` > ``flags.send_anonymous_usage_stats`` in
``dbt_project.yml`` > default on. The bottom four are dbt's own order;
``DO_NOT_TRACK`` is our addition.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from dbt_charts.telemetry.dbt_project import read_dbt_project

ENV_VAR = "DBT_SEND_ANONYMOUS_USAGE_STATS"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def send_anonymous_usage_stats(
    no_send_flag: bool, env: Mapping[str, str], project_dir: Path
) -> bool:
    """Resolve whether this invocation may emit usage events.

    Raises:
        ValueError: A consent setting holds a non-boolean value.
    """
    if env.get("DO_NOT_TRACK") == "1" or no_send_flag:
        return False
    if ENV_VAR in env:
        value = env[ENV_VAR].strip().lower()
        if value not in _TRUE | _FALSE:
            raise ValueError(f"{ENV_VAR}={env[ENV_VAR]!r} is not a boolean")
        return value in _TRUE
    project = read_dbt_project(project_dir)
    flags = project.get("flags") if project else None
    if flags and "send_anonymous_usage_stats" in flags:
        setting = flags["send_anonymous_usage_stats"]
        if not isinstance(setting, bool):
            raise ValueError(
                f"{project_dir / 'dbt_project.yml'}: "
                f"flags.send_anonymous_usage_stats must be true or false, "
                f"got {setting!r}"
            )
        return setting
    return True
