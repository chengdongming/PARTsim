#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import (
    diagnostic_output_directory, finalize_diagnostic_outputs, write_cases,
)
from scripts.experiment_runner import write_analysis_artifact_attestation


def main():
    parser = argparse.ArgumentParser(description='Select paired mechanism cases')
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--allow-legacy', action='store_true')
    args = parser.parse_args()
    output = (diagnostic_output_directory(args.output_dir)
              if args.allow_legacy else args.output_dir)
    write_cases(args.runs, output, allow_legacy=args.allow_legacy)
    if args.allow_legacy:
        finalize_diagnostic_outputs(output)
    else:
        write_analysis_artifact_attestation(
            Path(output) / 'mechanism_case_candidates.csv',
            producer_id='mechanism_case_selection_v1',
            output_role='mechanism_candidates',
            producer_config={},
            source_artifacts=[
                (Path(run_dir).resolve() / 'per_taskset_results.csv',
                 'source_per_taskset_results')
                for run_dir in args.runs
            ],
        )


if __name__ == '__main__':
    main()
