#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import write_rta_e0
from scripts.experiment_runner import validate_execution_manifest


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Aggregate RTA release-time energy lower-bound E0 sensitivity runs'
        )
    )
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    validate_execution_manifest(args.manifest)
    write_rta_e0(args.manifest, args.output_dir)


if __name__ == '__main__':
    main()
