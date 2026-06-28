#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import write_ablations


def main():
    parser = argparse.ArgumentParser(description='Generate scheduler ablations')
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    write_ablations(args.runs, args.output_dir)


if __name__ == '__main__':
    main()
