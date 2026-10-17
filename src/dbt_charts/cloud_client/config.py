"""The user-level config the ``dct cloud`` verbs read.

Auth is user-global and context is per-invocation (the initiative's spec), so
one file outside any project holds the credential, the host, and the default
org/project ``dct cloud use`` writes. It sits where the platform puts user
config — ``$XDG_CONFIG_HOME/dbt-charts/`` (``~/.config/dbt-charts/`` when that
is unset), ``%APPDATA%\\dbt-charts\\`` on Windows — and is written 0600, because
the token is in it.

The token comes from ``dct cloud login`` (the OAuth device grant), from
``DCT_CLOUD_TOKEN``, or from a hand-written ``token:`` line in this file;
every verb that needs one says so when it is missing.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dbt_charts.cloud_client.errors import CloudConfigError

CONFIG_DIR_NAME = "dbt-charts"
CONFIG_FILE_NAME = "config.yml"
CONFIG_LOCK_FILE_NAME = "config.lock"
PENDING_LOGIN_FILE_NAME = "pending-login.yml"
PENDING_CONNECT_FILE_NAME = "pending-connect.yml"
TOKEN_ENV_VAR = "DCT_CLOUD_TOKEN"
DEFAULT_HOST = "https://dbtcharts.com"


class CloudConfig(BaseModel):
    """Everything the config file may hold."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default=DEFAULT_HOST, description="Cloud deployment to talk to.")
    token: str = Field(default="", description="Bearer token; empty when signed out.")
    refresh_token: str = Field(
        default="",
        description="Renews the bearer token; Cloud rotates it on every use.",
    )
    org: str = Field(default="", description="Default organization slug.")
    project: str = Field(default="", description="Default project slug.")

    @field_validator("token", "refresh_token")
    @classmethod
    def _strip_trailing_newline(cls, value: str) -> str:
        """A YAML block scalar (``token: |``) and `export TOKEN=$(cat file)`
        both routinely carry a trailing newline nobody meant as part of the
        credential -- strip it here, once, regardless of whether the token
        came from the file (``read_config``'s ``model_validate``) or
        ``DCT_CLOUD_TOKEN`` (``load_config``'s direct construction runs this
        validator too). A control character anywhere else in the token is a
        different problem this can't guess how to fix -- CloudClient rejects
        that outright instead of stripping it.
        """
        return value.rstrip("\r\n")


class PendingLogin(BaseModel):
    """A device grant ``dct cloud login --start`` began but has not finished.

    Persisted next to the token so ``--wait`` can resume it from a separate
    process invocation -- an agent backgrounds ``--start``, hands the printed
    URL to the user, then runs ``--wait`` in its own foreground call.
    ``device_code``/``token_endpoint`` are exactly what ``poll_device_token``
    needs; ``interval``/``expires_in`` are the device authorization's own
    values, so ``--wait`` polls at the rate and for the window Cloud actually
    granted rather than guessing new ones.
    """

    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(description="The code to poll the token endpoint with.")
    token_endpoint: str = Field(description="Where to poll for the device token.")
    interval: float = Field(description="Minimum seconds between polls.")
    expires_in: float = Field(
        description="Seconds until device_code stops being valid."
    )
    host: str = Field(description="The Cloud deployment this login is against.")


class PendingConnect(BaseModel):
    """A GitHub repo pick ``dct cloud project connect github --start`` began but has
    not finished.

    Persisted next to the token so ``--wait`` can resume it from a separate
    process invocation, the same split as ``PendingLogin``. There is no
    device-code equivalent here: Cloud's repo-pick endpoint is keyed by
    ``(caller, org)``, not a token this client mints, so ``org``/``host`` are
    the whole server-side handle -- resuming means polling that same org
    again (``CloudClient.wait_for_pick``). The rest is exactly what
    ``create_project_from_pick`` needs once the pick lands.
    """

    model_config = ConfigDict(extra="forbid")

    org: str = Field(description="The org the repo pick is against.")
    host: str = Field(description="The Cloud deployment this connect is against.")
    name: str | None = Field(
        default=None, description="Project name (default: the repo's)."
    )
    slug: str | None = Field(
        default=None, description="Project slug (default: from the name)."
    )
    trunk: str | None = Field(
        default=None, description="Branch Cloud pulls from (default: the repo's)."
    )
    root: str | None = Field(
        default=None, description="Folder holding dbt_project.yml inside the repo."
    )


def config_dir() -> Path:
    """The directory this deployment's user config lives in."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / CONFIG_DIR_NAME
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / CONFIG_DIR_NAME


def config_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


def pending_login_path() -> Path:
    return config_dir() / PENDING_LOGIN_FILE_NAME


def pending_connect_path() -> Path:
    return config_dir() / PENDING_CONNECT_FILE_NAME


def _read_private_yaml(path: Path) -> dict[str, object] | None:
    """A private YAML file's top-level mapping, or ``None`` if it doesn't
    exist. Shared by every file in this module that holds a credential or
    sits next to one -- an absent file is silence, not an error, but one that
    exists and can't be parsed always is (see the note above `_where`)."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CloudConfigError(f"{path} could not be read: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CloudConfigError(f"{path} is not valid YAML{_where(exc)}.") from exc
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CloudConfigError(
            f"{path} must be a mapping of settings, found {type(raw).__name__}."
        )
    return raw


def _write_private_yaml(path: Path, data: dict[str, object]) -> Path:
    """Write *data* as YAML, owner-readable only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates 0600; os.replace means no reader ever sees a half-written
    # config, which would lose a just-rotated refresh token.
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(yaml.safe_dump(data, sort_keys=True))
        Path(temp_name).replace(path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return path


if sys.platform == "win32":
    import msvcrt

    def _lock(handle: BinaryIO) -> None:
        handle.seek(0)
        try:
            # LK_LOCK gives up after ~10 seconds of retries.
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        except OSError as exc:
            raise CloudConfigError(
                f"{handle.name} is locked by another dct process."
            ) from exc

    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle: BinaryIO) -> None:
        fcntl.flock(handle, fcntl.LOCK_EX)

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def config_lock() -> Generator[None]:
    """An exclusive lock across ``dct`` processes for a read-modify-write of
    the config. Cloud revokes the whole token family when a rotated refresh
    token is presented twice, so two processes must never refresh at once."""
    config_dir().mkdir(parents=True, exist_ok=True)
    with (config_dir() / CONFIG_LOCK_FILE_NAME).open("a+b") as handle:
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)


def read_config() -> CloudConfig:
    """The stored config, exactly as the file has it.

    An absent file is an empty config — nothing has been configured yet. A file
    that is there but unreadable is an error: silently treating a typo'd config
    as "no config" would send the next verb at the default host with no token.
    """
    raw = _read_private_yaml(config_path())
    if raw is None:
        return CloudConfig()
    try:
        return CloudConfig.model_validate(raw)
    except ValidationError as exc:
        raise CloudConfigError(
            f"{config_path()} has settings this version cannot use:"
            f" {rejected_fields(exc)}. Valid settings:"
            f" {', '.join(CloudConfig.model_fields)}."
        ) from exc


def read_pending_login() -> PendingLogin | None:
    """The login `--start` began, or ``None`` if there isn't one."""
    raw = _read_private_yaml(pending_login_path())
    if raw is None:
        return None
    try:
        return PendingLogin.model_validate(raw)
    except ValidationError as exc:
        raise CloudConfigError(
            f"{pending_login_path()} has settings this version cannot use:"
            f" {rejected_fields(exc)}."
        ) from exc


def save_pending_login(pending: PendingLogin) -> Path:
    """Persist the login `--start` began, owner-readable only."""
    return _write_private_yaml(pending_login_path(), pending.model_dump())


def clear_pending_login() -> None:
    """Remove the pending login, if there is one."""
    pending_login_path().unlink(missing_ok=True)


def read_pending_connect() -> PendingConnect | None:
    """The connect `--start` began, or ``None`` if there isn't one."""
    raw = _read_private_yaml(pending_connect_path())
    if raw is None:
        return None
    try:
        return PendingConnect.model_validate(raw)
    except ValidationError as exc:
        raise CloudConfigError(
            f"{pending_connect_path()} has settings this version cannot use:"
            f" {rejected_fields(exc)}."
        ) from exc


def save_pending_connect(pending: PendingConnect) -> Path:
    """Persist the connect `--start` began, owner-readable only."""
    return _write_private_yaml(pending_connect_path(), pending.model_dump())


def clear_pending_connect() -> None:
    """Remove the pending connect, if there is one."""
    pending_connect_path().unlink(missing_ok=True)


# Nothing derived from an exception's own text may reach these messages: every
# line of this file is a credential or sits next to one, and both parsers quote
# what they choked on — pydantic prints `input_value=`, and a YAML scanner error
# prints the offending source line. A mistyped or misspelled key is exactly as
# likely to hold a live token as the right one, and this message goes to stderr
# and into the `--json` error body. So: positions and key names only.


def _where(exc: yaml.YAMLError) -> str:
    """A YAML failure's position, never its quoted line."""
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return ""
    return f" (line {mark.line + 1}, column {mark.column + 1})"


def rejected_fields(exc: ValidationError) -> str:
    """The fields pydantic rejected, by name only -- never its own text, which
    quotes the offending value (see the note above)."""
    keys = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
    return ", ".join(keys) if keys else "the file's top level"


def load_config() -> CloudConfig:
    """The stored config with the token environment override applied."""
    stored = read_config()
    env_token = os.environ.get(TOKEN_ENV_VAR, "")
    if not env_token:
        return stored
    # Merge-and-revalidate, not model_copy: naming each carried field here
    # drops any setting added to CloudConfig later, but model_copy would skip
    # the validators -- and _strip_trailing_newline has to run on the env
    # token, which is the one most likely to carry a stray newline.
    return CloudConfig.model_validate({**stored.model_dump(), "token": env_token})


def credential_source() -> Literal["env", "config"]:
    """Which surface supplies the token ``load_config()`` returns.

    Mirrors ``load_config()``'s own precedence exactly (an unset or empty
    ``DCT_CLOUD_TOKEN`` falls through to the config file), so `whoami` and
    `logout` never disagree with the token every other verb actually used.
    """
    return "env" if os.environ.get(TOKEN_ENV_VAR, "") else "config"


def save_config(config: CloudConfig) -> Path:
    """Write the config back, owner-readable only."""
    return _write_private_yaml(config_path(), config.model_dump())
