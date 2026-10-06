"""Every way a ``dct cloud`` call can fail, in the API's own error shape.

A refusal decided here — no credential, an unreachable host, a repository that
backs two projects — is as real to a caller as one the server sent, so all of
them answer ``as_api_error()`` and reach an agent as the same ``{code, message,
field_errors}`` body. The alternative, two failure vocabularies depending on how
far the request got, means every client parses both.

Nothing in this module imports the rest of the package: the config and HTTP
layers raise these, never the other way around.
"""

from __future__ import annotations

import enum
from pathlib import Path
from typing import ClassVar

import httpx

from dbt_charts.cloud_client.contract import ApiError, ErrorCode

# httpx/httpcore exception families whose own ``str()`` embeds raw request
# bytes -- a malformed header value, most dangerously an Authorization
# header carrying a token with a stray control character. TransportFailed
# must never interpolate one of these directly; see its own docstring.
_UNSAFE_CAUSE_TYPES: tuple[type[Exception], ...] = (httpx.LocalProtocolError,)


class CloudError(Exception):
    """Base for every ``dct cloud`` failure."""

    code: ClassVar[ErrorCode]

    def as_api_error(self) -> ApiError:
        """This failure as the wire's one error body."""
        return ApiError(code=self.code, message=str(self))


class CloudConfigError(CloudError):
    """The user-level config file exists but could not be read."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST


class CredentialMissing(CloudError):
    """No token to authenticate with: neither ``dct cloud login`` has stored
    one, nor the env var or a config-file line supplied one."""

    code: ClassVar[ErrorCode] = ErrorCode.UNAUTHENTICATED

    def __init__(self, config_path: Path, token_env_var: str) -> None:
        super().__init__(
            "No dbt charts Cloud credential. Run `dct cloud login`, export"
            f" {token_env_var}, or write `token: <your token>` into {config_path}."
        )


class InvalidCredential(CloudError):
    """The stored token contains a control character and cannot be sent as
    an HTTP header value.

    Caught at ``CloudClient`` construction, before any request -- httpx and
    httpcore's own errors for an illegal header byte quote the raw header
    text (the bearer token) verbatim, which ``TransportFailed`` must never
    echo (see its docstring). Rejecting here means that text is never
    produced in the first place.
    """

    code: ClassVar[ErrorCode] = ErrorCode.UNAUTHENTICATED

    def __init__(self, config_path: Path) -> None:
        super().__init__(
            "The stored dbt charts Cloud credential contains a control"
            " character (a stray newline is the usual cause -- a copy-pasted"
            f" token, or a YAML block scalar) and cannot be sent. Re-save it,"
            f" trimmed, into {config_path} or the DCT_CLOUD_TOKEN env var."
        )


class TransportFailed(CloudError):
    """The request never got an answer.

    ``cause`` is rendered via ``str()`` UNLESS its type is in
    ``_UNSAFE_CAUSE_TYPES`` -- httpx.LocalProtocolError's own message quotes
    the raw bytes it refused to send, which for an Authorization header is
    the bearer token. ``CloudClient.__init__``'s control-character reject
    means that family should never reach here for a credential-shaped
    cause, but this is the second, independent barrier: it holds even if
    some other path lets a malformed header through.
    """

    code: ClassVar[ErrorCode] = ErrorCode.UNAVAILABLE

    def __init__(self, host: str, cause: Exception) -> None:
        detail = (
            "the request could not be sent (a header value was rejected by"
            " the HTTP client)"
            if isinstance(cause, _UNSAFE_CAUSE_TYPES)
            else str(cause)
        )
        super().__init__(f"Could not reach dbt charts Cloud at {host}: {detail}")


class SkewDirection(enum.Enum):
    """Which side of the wire is behind, inferred from a validation error's
    own error ``type``s -- not a guess, the error already carries the answer.

    ``CLOUD_OLDER``: this dct's contract requires a field Cloud's response
    simply did not send (every error is ``"missing"``) -- Cloud has not
    deployed it yet. ``CLOUD_NEWER``: Cloud sent a field in a shape this dct
    has never seen (a type mismatch, an unrecognized enum value, or --
    should ``ContractModel`` ever stop ignoring extras -- ``extra_forbidden``)
    -- Cloud is ahead of this dct. The two need opposite advice: retrying
    later (or matching Cloud's version) fixes the first, upgrading
    dbt-charts fixes the second, and upgrading in the first case only adds
    more fields Cloud still won't send.
    """

    CLOUD_OLDER = "cloud_older"
    CLOUD_NEWER = "cloud_newer"


class UnexpectedResponse(CloudError):
    """An answer that is not this API's contract — a proxy, a login page, a 502 —
    or, with ``skew`` set, well-formed JSON whose fields no longer match this
    dct's contract: the two ends disagree on a shape. ``skew`` names which
    side is behind, so the advice can name the actual remedy."""

    code: ClassVar[ErrorCode] = ErrorCode.UNAVAILABLE

    def __init__(
        self,
        url: str,
        status_code: int,
        detail: str,
        *,
        skew: SkewDirection | None = None,
    ) -> None:
        if skew is SkewDirection.CLOUD_OLDER:
            what = (
                "JSON that does not match this dct's Cloud contract — this"
                " looks like Cloud is older than this dct and has not"
                " deployed a field it expects yet: retry once Cloud"
                " redeploys, or install a dct build that matches Cloud's"
                " current version"
            )
        elif skew is SkewDirection.CLOUD_NEWER:
            what = (
                "JSON that does not match this dct's Cloud contract — if"
                " Cloud is newer than this dct, upgrade dbt-charts and retry"
            )
        else:
            what = "something that is not the dbt charts Cloud API"
        super().__init__(f"{url} answered {status_code} with {what}: {detail}")


class ApiFailed(CloudError):
    """The server refused, in the shape it promises."""

    def __init__(self, status_code: int, api_error: ApiError) -> None:
        self.status_code = status_code
        self.api_error = api_error
        detail = "\n".join(
            f"  {field}: {' '.join(messages)}"
            for field, messages in sorted(api_error.field_errors.items())
        )
        super().__init__(
            f"{api_error.message}\n{detail}" if detail else api_error.message
        )

    def as_api_error(self) -> ApiError:
        return self.api_error


class PickTimedOut(CloudError):
    """The browser hop did not complete inside the wait."""

    code: ClassVar[ErrorCode] = ErrorCode.UNAVAILABLE

    def __init__(self, connect_url: str, waited_seconds: float) -> None:
        super().__init__(
            f"No repository pick arrived within {waited_seconds:g}s. Finish the"
            f" pick at {connect_url}, then re-run this command — it resumes from"
            " the pick."
        )


class ContextUnresolved(CloudError):
    """Which org or project this call is about could not be decided."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST


class DeviceLoginFailed(CloudError):
    """The OAuth device grant (RFC 8628) did not end in a token.

    Covers every way `dct cloud login` can fail short of a transport error or
    an unparseable response: a host with no device_authorization_endpoint,
    the user denying the request, or the code expiring unapproved. The
    message never carries the device_code or a token -- callers pass one
    line describing what happened, nothing pulled from the wire body.
    """

    code: ClassVar[ErrorCode] = ErrorCode.UNAUTHENTICATED


class SessionExpired(CloudError):
    """The stored login can no longer be renewed."""

    code: ClassVar[ErrorCode] = ErrorCode.UNAUTHENTICATED

    def __init__(self) -> None:
        super().__init__(
            "Your dbt charts Cloud session expired or was revoked. Run `dct cloud"
            " login`."
        )


class NoPendingLogin(CloudError):
    """`dct cloud login --wait` found no login `--start` began."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST

    def __init__(self) -> None:
        super().__init__("No pending login. Run `dct cloud login --start` first.")


class NoPendingConnect(CloudError):
    """`dct cloud project connect --wait` found no connect `--start` began."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST

    def __init__(self) -> None:
        super().__init__(
            "No pending connect. Run `dct cloud project connect --start` first."
        )


class EnvCredentialActive(CloudError):
    """`dct cloud login`/`logout` refuse to touch a config token the env var
    shadows.

    ``DCT_CLOUD_TOKEN`` outranks the config file for every other verb
    (``config.load_config``), so a config-only login would store a credential
    nothing reads, and a config-only logout would do nothing about the one
    actually in effect.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST

    def __init__(self, token_env_var: str) -> None:
        super().__init__(
            f"{token_env_var} is set, so that is the credential in effect, not"
            f" the stored config. Unset {token_env_var} first."
        )
