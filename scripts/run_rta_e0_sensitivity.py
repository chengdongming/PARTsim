#!/usr/bin/env python3
"""Run RTA-enabled experiments across conservative initial-energy bounds."""

import argparse
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import experiment_runner as runner


MANIFEST_FIELDS = [
    'experiment_name', 'run_dir', 'E0', 'seed_base', 'num_points',
    'num_tasksets', 'task_n', 'battery', 'initial_energy',
    'solar_time_ms', 'harvesting_scale', 'rta_horizon_ms', 'rta_timeout', 'max_workers',
    'status', 'return_code',
]


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            'Run RTA E0 sensitivity experiments. E0 is an absolute energy '
            'lower bound in joules, not the simulation initial-energy ratio.'
        )
    )
    runner.add_common_arguments(parser)
    parser.add_argument('--e0-values', nargs='+', required=True, type=float)
    parser.add_argument('--seed-base', type=int, default=424242)
    parser.add_argument('--rta-horizon-ms', type=int, required=True)
    parser.add_argument('--rta-timeout', type=int, default=300)
    return parser


def build_specs(args):
    output_root, name, manifest = runner.output_paths(args)
    specs = []
    for e0 in args.e0_values:
        run_dir = output_root / '{}-E0_{}'.format(
            name, runner.safe_run_dir_name(e0)
        )
        specs.append({
            'experiment_name': args.experiment_name,
            'run_dir': str(run_dir),
            'E0': e0,
            'seed_base': args.seed_base,
            'num_points': args.num_points,
            'num_tasksets': args.num_tasksets,
            'task_n': args.task_n,
            'battery': args.battery,
            'initial_energy': args.initial_energy,
            'solar_time_ms': args.solar_time_ms,
            'harvesting_scale': args.harvesting_scale,
            'rta_horizon_ms': args.rta_horizon_ms,
            'rta_timeout': args.rta_timeout,
            'max_workers': args.max_workers,
            'command': runner.build_command(
                run_dir, args.seed_base, args.num_points,
                args.num_tasksets, args.task_n, args.battery,
                args.initial_energy, args.solar_time_ms, args.max_workers,
                args.no_group_figures,
                harvesting_scale=args.harvesting_scale,
                rta_initial_energy=e0,
                rta_horizon_ms=args.rta_horizon_ms,
                rta_timeout=args.rta_timeout,
            ),
        })
    return specs, manifest


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    runner.validate_common_args(parser, args)
    if any(e0 < 0 for e0 in args.e0_values):
        parser.error('--e0-values must be non-negative')
    if args.battery <= 0:
        parser.error('--battery must be positive')
    if args.rta_horizon_ms <= 0 or args.rta_timeout <= 0:
        parser.error('RTA horizon and timeout must be positive')
    specs, manifest = build_specs(args)
    rows = runner.execute_specs(
        specs, manifest, MANIFEST_FIELDS,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing,
        force=args.force,
        stop_on_failure=args.stop_on_failure,
    )
    command = [
        'python3', 'scripts/analyze_rta_e0_sensitivity.py',
        '--manifest', str(manifest),
        '--output-dir', 'analysis_outputs/rta_e0_sensitivity',
    ]
    print('\nAnalyze with:\n$ {}'.format(shlex.join(command)))
    print('Manifest: {}'.format(manifest))
    return rows


if __name__ == '__main__':
    main()
