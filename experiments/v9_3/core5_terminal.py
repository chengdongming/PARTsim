"""Unified terminal classification for v9.3 CORE-5 evidence."""

from __future__ import annotations

from enum import Enum
from typing import Any


class Core5TerminalClass(str, Enum):
    SCIENTIFIC_COMPLETION = "SCIENTIFIC_COMPLETION"
    RIGHT_CENSORED = "RIGHT_CENSORED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"


SCIENTIFIC_TERMINALS = frozenset({"COMPLETED", "NO_CANDIDATE"})
TECHNICAL_TERMINALS = frozenset({
    "INTERNAL_CONFORMANCE_FAILURE",
    "NUMERIC_ERROR",
    "INVALID_RESULT",
})
KNOWN_TERMINALS = SCIENTIFIC_TERMINALS | TECHNICAL_TERMINALS | {"TIMEOUT"}


def truth(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def classify_core5_terminal(
    solver_status: Any, *, outer_timeout: Any = False,
) -> Core5TerminalClass:
    """Classify only the strict solver-status whitelist.

    ``outer_timeout`` is retained for call-site compatibility and evidence
    validation, but it never overrides the solver terminal.  In particular,
    a technical or unknown status cannot be disguised as right-censored by a
    contradictory outer-timeout flag.
    """

    status = solver_status if isinstance(solver_status, str) else ""
    if status in SCIENTIFIC_TERMINALS:
        return Core5TerminalClass.SCIENTIFIC_COMPLETION
    if status == "TIMEOUT":
        return Core5TerminalClass.RIGHT_CENSORED
    if status in TECHNICAL_TERMINALS:
        return Core5TerminalClass.TECHNICAL_FAILURE
    return Core5TerminalClass.TECHNICAL_FAILURE
