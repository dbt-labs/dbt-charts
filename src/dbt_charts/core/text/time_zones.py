"""The IANA zones ``regional.time_zone`` accepts.

Read from the pinned ``tzdata`` package, not the host's zone database, so every
host accepts the same names.
"""

from __future__ import annotations

from enum import StrEnum
from importlib.resources import files
from typing import TYPE_CHECKING, Final

import tzdata

TIME_ZONE_NAMES: Final[frozenset[str]] = frozenset(
    files(tzdata).joinpath("zones").read_text(encoding="utf-8").split()
)

# A closed vocabulary, so the schema offers every name to editors. Its members
# come from data, which a type checker cannot read, hence the stub.
if TYPE_CHECKING:

    class TimeZoneName(StrEnum): ...

else:
    TimeZoneName = StrEnum(
        "TimeZoneName", [(name, name) for name in sorted(TIME_ZONE_NAMES)]
    )
