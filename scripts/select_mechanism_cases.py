#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import write_cases


def main():
    parser = argparse.ArgumentParser(description='Select paired mechanism cases')
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    write_cases(args.runs, args.output_dir)


if __name__ == '__main__':
    main()
