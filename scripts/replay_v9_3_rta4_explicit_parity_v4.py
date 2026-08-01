#!/usr/bin/env python3
"""Run the audit-only frozen-spotcheck/V4 explicit-manifest parity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_energy_service_v4 import (
    EXACT_LINEAR_SERVICE_V1,
    normalize_energy_service_v4,
)
from experiments.v9_3.rta4_explicit_parity_v4 import (
    run_explicit_spotcheck_parity_v4,
)
from experiments.v9_3.rta4_task_source_v4 import (
    load_explicit_taskset_manifest_v4,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--e0", action="append", required=True)
    parser.add_argument("--method", action="append", required=True)
    parser.add_argument("--timeout-seconds", type=int, required=True)
    parser.add_argument(
        "--production-build-manifest-identity", required=True,
    )
    args = parser.parse_args()
    summary = run_explicit_spotcheck_parity_v4(
        task_source=load_explicit_taskset_manifest_v4(args.manifest),
        energy_service=normalize_energy_service_v4({
            "model": EXACT_LINEAR_SERVICE_V1, "rate": "1/10",
        }),
        e0_values=args.e0,
        methods=args.method,
        timeout_seconds=args.timeout_seconds,
        production_build_manifest_identity=(
            args.production_build_manifest_identity
        ),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["parity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
