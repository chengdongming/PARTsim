#!/usr/bin/env python3
"""Generate, plan and execute new-model paired RTA experiments."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.same_slot import campaign


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) in (2, 3) and args[0] == "--worker":
        print(json.dumps(campaign.evaluate(json.load(sys.stdin), args[1],
                         args[2] if len(args) == 3 else "auto"),
                         ensure_ascii=False, allow_nan=False))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    prepare = command.add_parser("prepare", help="regenerate verified synthetic inputs")
    prepare.add_argument("--output", type=Path, required=True)
    target = command.add_parser("prepare-fourcore10", help="new 4-core/10-task D=ceil(0.9T) cohort")
    target.add_argument("--output", type=Path, required=True)
    target.add_argument("--samples-per-cell", type=int, required=True)
    target.add_argument("--seed", type=int, default=20261128)
    target.add_argument("--uc", nargs="+", default=[str(i/10) for i in range(1,10)])
    target.add_argument("--ue", nargs="+", default=["0.4", "0.6", "0.8"])
    target.add_argument("--period-min", type=int, default=40)
    target.add_argument("--period-max", type=int, default=200)
    target.add_argument("--harvest-rate", default="3")
    target.add_argument("--harvest-gap", type=int, default=1)
    for name in ("plan", "run"):
        sub = command.add_parser(name)
        sub.add_argument("--source-dir", type=Path, required=True)
        sub.add_argument("--suite", choices=tuple(campaign.METHODS), required=True)
        sub.add_argument("--limit", type=int, help="bounded check, not full cohort")
        sub.add_argument("--timeout", type=float, default=30)
        sub.add_argument("--workers", type=int, default=1)
        if name == "run":
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--resume", action="store_true")
    ns = parser.parse_args(args)
    if ns.command == "prepare":
        campaign.prepare(ns.output)
        return 0
    if ns.command == "prepare-fourcore10":
        from experiments.same_slot import fourcore10
        config = fourcore10.canonical_config(ns.seed,ns.samples_per_cell,ns.uc,ns.ue,
            ns.period_min,ns.period_max,ns.harvest_rate,ns.harvest_gap)
        print(json.dumps(fourcore10.prepare(ns.output,config),indent=2))
        return 0
    if ns.command == "plan":
        plan, _ = campaign.build_plan(ns.source_dir, ns.suite, ns.limit, ns.timeout, ns.workers)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    terminal = campaign.run(ns.source_dir, ns.output, ns.suite, ns.limit,
                            ns.timeout, ns.workers, ns.resume)
    print(json.dumps(terminal, ensure_ascii=False, indent=2))
    return 0 if terminal["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
