#!/usr/bin/env python3
"""Extract deterministic case/task analysis rows from audited B4-PE results."""

from __future__ import annotations

import argparse
import json
import sys


sys.dont_write_bytecode = True

import analysis_common as analysis


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Extract deterministic B4-PE case/task data from a strict audit"
        )
    )
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--expected-records", required=True)
    parser.add_argument("--audit-report", required=True)
    parser.add_argument("--analysis-root", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--analysis-contract-version",
        type=int,
        choices=(1, 2),
        default=1,
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        analysis.validate_analysis_root(args.analysis_root)
        outputs, manifest, audit = analysis.build_outputs(
            args.output_root,
            args.expected_records,
            args.audit_report,
            args.strict,
            args.analysis_contract_version,
        )
        analysis.publish_outputs(args.analysis_root, outputs)
    except (analysis.AnalysisError, OSError, ValueError) as exc:
        analysis.write_failure_audit(args.analysis_root)
        print(f"ANALYSIS EXTRACTION ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "analysis_schema_version": manifest[
                    "analysis_schema_version"
                ],
                "case_row_count": manifest["case_row_count"],
                "task_row_count": manifest["task_row_count"],
                "pairing_group_count": manifest["pairing_group_count"],
                "overall_pass": audit["overall_pass"],
                "no_paper_data_generated": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
