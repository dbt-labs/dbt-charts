"""Warning codes an earlier release shipped and a later one removed.

A project config that suppresses one must keep loading: the code can no longer
fire, so the entry is dead weight rather than a typo, and
``validate_suppression_codes`` says so once. Lives beside the ``codes_*``
declarations, which the registry-emission scan treats as declarations rather
than emit sites.
"""

RETIRED_WARNING_CODES: frozenset[str] = frozenset({"WARN-ADJACENT-TEXT-ROWS"})
