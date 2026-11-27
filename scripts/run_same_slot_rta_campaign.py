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
    if len(args) == 2 and args[0] == "--worker":
        print(json.dumps(campaign.evaluate(json.load(sys.stdin), args[1]),
                         ensure_ascii=False, allow_nan=False))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    prepare = command.add_parser("prepare", help="regenerate verified synthetic inputs")
    prepare.add_argument("--output", type=Path, required=True)
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
