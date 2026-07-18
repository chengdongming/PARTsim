#!/usr/bin/env python3
"""Statically verify the frozen EXT-1B B1 formal contract."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.ext1b_formal_contract import (
    FormalContractError,
    verify_formal_contract,
)


def main() -> int:
    try:
        result = verify_formal_contract(PROJECT_ROOT)
    except FormalContractError as exc:
        print(json.dumps({
            "status": "B1_FORMAL_CONTRACT_STATIC_VERIFICATION_FAILED",
            "error": str(exc),
            "native_simulation_invocations": 0,
            "formal_run_status": "FORMAL_NOT_RUN",
        }, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
