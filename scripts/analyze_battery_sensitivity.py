#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import write_battery


def main():
    parser = argparse.ArgumentParser(description='Aggregate battery sensitivity runs')
    parser.add_argument('--run', action='append', required=True)
    parser.add_argument('--battery', action='append', required=True, type=float)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    if len(args.run) != len(args.battery):
        parser.error('each --run must have one corresponding --battery')
    write_battery(list(zip(args.run, args.battery)), args.output_dir)


if __name__ == '__main__':
    main()
