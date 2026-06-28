#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import write_paired


def main():
    parser = argparse.ArgumentParser(description='Compare paired scheduler outcomes')
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--baseline', default='gpfp_asap_block')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    write_paired(args.runs, args.baseline, args.output_dir)


if __name__ == '__main__':
    main()
