#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import (
    diagnostic_output_directory, finalize_diagnostic_outputs, write_battery,
)
from scripts.experiment_runner import validate_execution_manifest


def main():
    parser = argparse.ArgumentParser(description='Aggregate battery sensitivity runs')
    parser.add_argument('--run', action='append', required=True)
    parser.add_argument('--battery', action='append', required=True, type=float)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--allow-legacy', action='store_true')
    args = parser.parse_args()
    if len(args.run) != len(args.battery):
        parser.error('each --run must have one corresponding --battery')
    if not args.allow_legacy:
        validate_execution_manifest(args.manifest)
    output = (diagnostic_output_directory(args.output_dir)
              if args.allow_legacy else args.output_dir)
    write_battery(list(zip(args.run, args.battery)), output,
                  allow_legacy=args.allow_legacy)
    if args.allow_legacy:
        finalize_diagnostic_outputs(output)


if __name__ == '__main__':
    main()
