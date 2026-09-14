"""Run only the canonical A-implicit U_C=0.9 supplement.

The generic runner owns preparation, exact energy materialization, retries, and
result contracts.  This wrapper makes the one permitted new simulation slice
explicit and keeps the canonical full-grid generation metadata in the run.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_scheduler_load_cross  # noqa: E402
from experiments.v9_3 import scheduler_load_cross as experiment  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260906)
    parser.add_argument("--samples-per-cell", type=int, default=120)
    parser.add_argument("--workers", type=int, default=30)
    parser.add_argument("--prepare-workers", type=int, default=30)
    parser.add_argument("--parse-concurrency", type=int, default=30)
    parser.add_argument("--simulator", type=Path, default=None)
    args = parser.parse_args(argv)
    command = [
        "--output", str(args.output), "--seed", str(args.seed),
        "--experiment-version", "a-implicit", "--campaign",
        experiment.A_IMPLICIT_UC_FIXED_SUPPLY_CAMPAIGN,
        "--samples-per-cell", str(args.samples_per_cell),
        "--workers", str(args.workers), "--prepare-workers", str(args.prepare_workers),
        "--parse-concurrency", str(args.parse_concurrency), "--uc09-supplement",
    ]
    if args.simulator is not None:
        command.extend(["--simulator", str(args.simulator)])
    return run_scheduler_load_cross.main(command)


if __name__ == "__main__":
    raise SystemExit(main())
