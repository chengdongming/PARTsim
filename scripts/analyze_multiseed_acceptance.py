#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import write_multiseed


def main():
    parser = argparse.ArgumentParser(description='Aggregate multi-seed acceptance results')
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    write_multiseed(args.runs, args.output_dir)


if __name__ == '__main__':
    main()
