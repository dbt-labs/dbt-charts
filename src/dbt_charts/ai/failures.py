"""The shared vocabulary for a failed AI turn.

Its own module, with no imports of its own, because both ends of the stream
need it: ``events.AgentError`` carries a member and ``llm.classify`` derives
one. Putting it in either of those would make the other import a cycle.
"""

from __future__ import annotations

from enum import StrEnum


class AITurnFailure(StrEnum):
    """Why an AI turn ended without an answer.

    One closed vocabulary for a production turn and an eval case, so an offline
    result can be checked against the field under the same names. Closed on
    purpose: this is a Prometheus label, and a member derived from user-supplied
    text would make its cardinality unbounded.

    ``tool_error`` is deliberately absent. ``dispatch_tool_call`` converts every
    handler exception into a result envelope, so no site could set it; the
    tool-surface task adds it when it has a producer and a test.
    """

    # Provider faults — classify() derives these from the wrapped exception.
    CONTEXT_WINDOW_EXCEEDED = "context_window_exceeded"
    RATE_LIMIT = "rate_limit"
    USAGE_LIMIT_EXCEEDED = "usage_limit_exceeded"
    PROVIDER_ERROR = "provider_error"
    SERVER_OVERLOADED = "server_overloaded"
    STREAM_DISCONNECTED = "stream_disconnected"
    # Terminals with no exception to classify — set literally at their sites.
    CANCELLED = "cancelled"
    LOOP_DETECTED = "loop_detected"
    STEP_LIMIT_EXCEEDED = "step_limit_exceeded"
    # A bug on our side of the line.
    INTERNAL = "internal"
