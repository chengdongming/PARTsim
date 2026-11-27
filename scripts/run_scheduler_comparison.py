#!/usr/bin/env python3
"""Current nine-strategy experiment under harvest-before-debit timing.

Keep the historical runner and its hash/CLI contract unchanged. This entrypoint
selects the current timing explicitly and defaults to an initially empty battery.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_scheduler_load_cross as existing


def current_args(argv):
    # Do not silently turn an explicit old timing or old supplementary campaign
    # into a new experiment.
    for arg in argv:
        if arg == "--uc09-supplement":
            raise ValueError("the UC09 supplement is a historical experiment")
    parser = existing.make_parser()
    requested = parser.parse_args(["--energy-timing", "same-slot",
                                   "--initial-energy-rule", "zero", *argv])
    if requested.energy_timing != "same-slot":
        raise ValueError("current experiments require --energy-timing same-slot")
    if requested.experiment_version == "a-implicit-v1":
        raise ValueError("a-implicit-v1 is historical; select a-implicit")
    if requested.uc09_supplement:
        raise ValueError("the UC09 supplement is a historical experiment")
    return ["--initial-energy-rule", "zero", *argv,
            "--energy-timing", "same-slot"]


def main(argv=None):
    try:
        args = current_args(list(sys.argv[1:] if argv is None else argv))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return existing.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
