#!/usr/bin/env python3
"""Independent CORE-0A rebuild2 verifier.

This entry point intentionally imports no evidence producer, report builder, or
producer-authored pass/count function.  The implementation it exposes reads raw
schemas and reconstructs all eleven gates in ``core0a_v9_3_independent_aggregator``.
"""

from core0a_v9_3_independent_aggregator import (  # independent raw verifier
    AggregationError,
    aggregate,
    compare_report,
    main,
    replay,
)

__all__ = ("AggregationError", "aggregate", "compare_report", "replay")


if __name__ == "__main__":
    raise SystemExit(main())
