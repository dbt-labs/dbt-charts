"""Arrow-in/Arrow-out warehouse drivers, and the functions that pick one.

Public entry point for hosts outside ``dbt_charts.core`` (Cloud, the
super-schema profiler): import ``is_driver_source`` and ``open_driver`` from
here, never a driver module.

Drivers load in-process, so a native fault in one kills the host process: the
same posture as the DuckDB, pyarrow and psycopg2 native code already loaded
here. The CLI enables ``faulthandler`` for a traceback, and Cloud does not
install the ``lakecompute`` extra until drivers run in a subprocess. The Trino
arm is the exception: its client is pure Python over HTTP.
"""

from dbt_charts.core.execute.drivers.base import (
    DRIVER_SOURCE_TYPES,
    DriverConnection,
    is_driver_source,
    open_driver,
    require_driver_extra,
)

__all__ = [
    "DRIVER_SOURCE_TYPES",
    "DriverConnection",
    "is_driver_source",
    "open_driver",
    "require_driver_extra",
]
