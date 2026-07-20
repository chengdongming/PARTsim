#!/usr/bin/env python3
"""Rebuild CORE-5 resource/censoring summaries from persisted files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.core5_aggregation import analyze_core5_artifacts
from experiments.v9_3.core5_contract import Core5ContractError
from experiments.v9_3.core5_formal import (
    CORE5_FORMAL_RUN_SCHEMA,
    Core5FormalContractError,
    analyze_core5_formal_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    try:
        metadata = json.loads(
            (args.run_root / "run_metadata.json").read_text(encoding="utf-8")
        )
        summary = (
            analyze_core5_formal_artifacts(args.run_root)
            if metadata.get("schema") == CORE5_FORMAL_RUN_SCHEMA
            else analyze_core5_artifacts(args.run_root)
        )
    except (
        Core5ContractError, Core5FormalContractError, OSError, ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"CORE-5 analyzer rejected artifact root: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
