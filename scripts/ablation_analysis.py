#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import (
    diagnostic_output_directory, finalize_diagnostic_outputs,
    write_ablations,
)


def main():
    parser = argparse.ArgumentParser(description='Generate scheduler ablations')
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--allow-legacy', action='store_true')
    args = parser.parse_args()
    output = (diagnostic_output_directory(args.output_dir)
              if args.allow_legacy else args.output_dir)
    write_ablations(args.runs, output, allow_legacy=args.allow_legacy)
    if args.allow_legacy:
        finalize_diagnostic_outputs(output)


if __name__ == '__main__':
    main()
