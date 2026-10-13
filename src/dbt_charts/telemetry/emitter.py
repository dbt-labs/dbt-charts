"""Build ``CliInvocation`` events and hand them to the one send function.

Payloads carry the command path (names from the command tree, never
arguments), versions, and hashed/anonymous ids. Nothing else.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import typer
import yaml
from dbtlabs.proto.public.v1.common.vortex_telemetry_contexts_pb2 import (
    VortexTelemetryCommonContext,
)
from dbtlabs.proto.public.v1.events.charts_pb2 import CliInvocation as CliInvocationPb
from dbtlabs.proto.public.v1.events.vortex_pb2 import VortexMessageEnrichment
from dbtlabs_vortex.producer import log_proto, shutdown
from typer.core import TyperGroup

from dbt_charts.telemetry.consent import send_anonymous_usage_stats
from dbt_charts.telemetry.dbt_project import read_dbt_project

DOCS_URL = "https://docs.dbtcharts.com/reference/telemetry/"
FIRST_RUN_NOTICE = (
    "dct: sending anonymous usage stats to dbt Labs. Disable with DO_NOT_TRACK=1 "
    f"or --no-send-anonymous-usage-stats. {DOCS_URL}"
)

# The producer logs delivery failures at ERROR, and the CLI's root handler
# prints them to stderr; a failed send must never reach the user.
_producer_logger = logging.getLogger("dbtlabs_vortex")
_producer_logger.addHandler(logging.NullHandler())
_producer_logger.propagate = False

# A finished command must not hang on the network; unsent events are dropped.
SHUTDOWN_TIMEOUT_SECONDS = 1.0


def user_file_path(env: Mapping[str, str]) -> Path:
    """Locate ``.user.yml`` next to ``profiles.yml``, where dbt keeps it."""
    profiles_dir = env.get("DBT_PROFILES_DIR")
    return (Path(profiles_dir) if profiles_dir else Path.home() / ".dbt") / ".user.yml"


@dataclass(frozen=True)
class CliInvocation:
    event_id: str
    invocation_id: str
    progress: Literal["start", "end"]
    command: str
    version: str
    python_version: str
    os_name: str
    project_id: str | None
    dbt_local_cookie_user_id: str
    result_type: Literal["ok", "error"] | None
    duration_ms: int | None


def send(event: CliInvocation) -> None:
    """Queue the event for Vortex.

    proto3 scalars have no null: absent values go out as ``""`` / ``0``, and
    ``progress`` says which ones to expect. Vortex fills ``enrichment``
    server-side and dct has no dbt platform account, so both stay empty.
    """
    log_proto(
        CliInvocationPb(
            enrichment=VortexMessageEnrichment(),
            common_context=VortexTelemetryCommonContext(),
            event_id=event.event_id,
            invocation_id=event.invocation_id,
            progress=event.progress,
            command=event.command,
            version=event.version,
            python_version=event.python_version,
            os_name=event.os_name,
            project_id=event.project_id or "",
            dbt_local_cookie_user_id=event.dbt_local_cookie_user_id,
            result_type=event.result_type or "",
            duration_ms=event.duration_ms or 0,
        )
    )


def get_user_id(path: Path) -> str:
    """Return dbt's anonymous user cookie, creating it on first run."""
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            raise ValueError(f"{path}: expected a mapping with a string `id`")
        return data["id"]
    user_id = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"id": user_id}), encoding="utf-8")
    return user_id


def command_path(root: TyperGroup, args: list[str]) -> str:
    """Resolve the subcommand names in ``args``, dropping options and values.

    Only names found in the command tree are emitted, so no argument can leak.
    """
    names: list[str] = []
    command: object = root
    for token in args:
        if not isinstance(command, TyperGroup):
            break
        sub = command.commands.get(token)
        if sub is not None:
            names.append(token)
            command = sub
    return " ".join(names)


def _failed(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    if isinstance(exc, typer.Exit):
        return exc.exit_code != 0
    if isinstance(exc, SystemExit):
        return exc.code not in (0, None)
    return True


@dataclass(frozen=True)
class Invocation:
    """A started invocation; ``end`` emits the closing event."""

    start: CliInvocation
    started_at: float

    def end(self, exc: BaseException | None) -> None:
        send(
            replace(
                self.start,
                event_id=str(uuid.uuid4()),
                progress="end",
                result_type="error" if _failed(exc) else "ok",
                duration_ms=int((time.monotonic() - self.started_at) * 1000),
            )
        )
        # log_proto only enqueues; the worker batches every 5s and registers no
        # exit hook, so flush here, bounded.
        shutdown(timeout_seconds=SHUTDOWN_TIMEOUT_SECONDS)


def begin_invocation(
    command: str, no_send_flag: bool, project_dir: Path
) -> Invocation | None:
    """Emit the start event, or return None when consent is not given."""
    if not send_anonymous_usage_stats(no_send_flag, os.environ, project_dir):
        return None
    project = read_dbt_project(project_dir)
    project_id = None
    if project is not None:
        if not isinstance(project.get("name"), str):
            raise ValueError(f"{project_dir / 'dbt_project.yml'}: missing `name`")
        project_id = hashlib.md5(project["name"].encode()).hexdigest()
    user_file = user_file_path(os.environ)
    first_run = not user_file.exists()
    start = CliInvocation(
        event_id=str(uuid.uuid4()),
        invocation_id=str(uuid.uuid4()),
        progress="start",
        command=command,
        version=version("dbt-charts"),
        python_version=platform.python_version(),
        os_name=platform.system(),
        project_id=project_id,
        dbt_local_cookie_user_id=get_user_id(user_file),
        result_type=None,
        duration_ms=None,
    )
    if first_run:
        print(FIRST_RUN_NOTICE, file=sys.stderr)
    send(start)
    return Invocation(start, time.monotonic())
