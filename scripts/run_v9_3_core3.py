#!/usr/bin/env python3
"""Run paired ASAP-BLOCK v9.3 CORE-3 RTA/simulation experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.config import load_config
from experiments.v9_3.core3_pairing import Core3PairingRunner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-cells", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config, expected_core="CORE-3")
    runner = Core3PairingRunner(config)
    if args.dry_run or args.list_cells:
        description = runner.describe()
        if not args.list_cells:
            description.pop("cells", None)
        print(json.dumps(description, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    outcome = runner.run(resume=args.resume or config["execution"]["resume"])
    print(json.dumps({
        "output_root": str(outcome.output_root),
        "requested_rta": outcome.requested_rta,
        "terminal_rta": outcome.terminal_rta,
        "requested_simulations": outcome.requested_simulations,
        "terminal_simulations": outcome.terminal_simulations,
        "stopped": outcome.stopped,
        "summary": outcome.summary,
    }, ensure_ascii=False, sort_keys=True))
    return 2 if outcome.stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
