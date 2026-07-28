#!/usr/bin/env python3
"""Run deterministic B4-PE I5D statistics without executing simulations."""

from __future__ import annotations

import argparse
import json
import sys


sys.dont_write_bytecode = True

import statistics_common as statistics


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Compute deterministic B4-PE I5D statistics and figures"
    )
    parser.add_argument("--analysis-root", required=True)
    parser.add_argument("--statistics-root", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("validation", "pilot", "formal-main", "negative-control"),
    )
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        statistics.validate_statistics_root(args.statistics_root)
        outputs, manifest, audit = statistics.build_outputs(
            args.analysis_root, args.mode, args.strict
        )
        statistics.publish_outputs(args.statistics_root, outputs)
    except (statistics.StatisticsError, OSError, ValueError) as exc:
        statistics.write_failure_audit(args.statistics_root, args.mode)
        print(f"STATISTICS ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "mode": manifest["mode"],
                "case_count": manifest["counts"]["case_count"],
                "task_count": manifest["counts"]["task_count"],
                "cluster_count": manifest["counts"]["cluster_count"],
                "overall_pass": audit["overall_pass"],
                "paper_results_authorized": manifest[
                    "paper_results_authorized"
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
