#!/usr/bin/env python3
"""Run solar-time sensitivity under the existing synthetic profile."""

import argparse
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import experiment_runner as runner


LIMITATION = (
    'This is a harvesting time/intensity sensitivity experiment under the '
    'existing synthetic_piecewise profile. It is not yet a full '
    'profile-shape comparison.'
)
MANIFEST_FIELDS = [
    'experiment_name', 'run_dir', 'solar_time_ms', 'seed_base',
    'num_points', 'num_tasksets', 'task_n', 'battery', 'initial_energy',
    'harvesting_scale', 'max_workers', 'harvesting_profile', 'status', 'return_code',
] + runner.EXECUTION_MANIFEST_FIELDS


def build_parser():
    parser = argparse.ArgumentParser(
        description='Run harvesting solar-time sensitivity. ' + LIMITATION
    )
    runner.add_common_arguments(parser, include_solar_time=False)
    parser.add_argument(
        '--solar-times-ms', nargs='+', required=True, type=int,
        help='synthetic_piecewise time-of-day values in milliseconds',
    )
    parser.add_argument('--seeds', nargs='+', required=True, type=int)
    return parser


def build_specs(args):
    output_root, name, manifest = runner.output_paths(args)
    specs = []
    for solar_time in args.solar_times_ms:
        for seed in args.seeds:
            run_dir = output_root / '{}-solar{}-seed{}'.format(
                name, solar_time, seed
            )
            specs.append({
                'experiment_name': args.experiment_name,
                'run_dir': str(run_dir),
                'solar_time_ms': solar_time,
                'seed_base': seed,
                'num_points': args.num_points,
                'num_tasksets': args.num_tasksets,
                'task_n': args.task_n,
                'battery': args.battery,
                'initial_energy': args.initial_energy,
                'harvesting_scale': args.harvesting_scale,
                'max_workers': args.max_workers,
                'harvesting_profile': 'synthetic_piecewise',
                'command': runner.build_command(
                    run_dir, seed, args.num_points, args.num_tasksets,
                    args.task_n, args.battery, args.initial_energy,
                    solar_time, args.max_workers, args.no_group_figures,
                    harvesting_scale=args.harvesting_scale,
                    min_task_util=args.min_task_util,
                    max_task_util=args.max_task_util,
                    wcet_rounding=args.wcet_rounding,
                    actual_utilization_tolerance_total=(
                        args.actual_utilization_tolerance_total
                    ),
                    constrained_deadlines=args.constrained_deadlines,
                    require_common_complete=args.require_common_complete,
                ),
            })
    return specs, manifest


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    runner.validate_common_args(parser, args)
    if args.battery <= 0:
        parser.error('--battery must be positive')
    if any(value < 0 for value in args.solar_times_ms):
        parser.error('--solar-times-ms values must be non-negative')
    specs, manifest = build_specs(args)
    rows = runner.execute_specs(
        specs, manifest, MANIFEST_FIELDS,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing,
        force=args.force,
        stop_on_failure=args.stop_on_failure,
    )
    if runner.wrapper_exit_code(rows) == 0:
        command = [
            'python3', 'scripts/analyze_harvesting_sensitivity.py',
            '--manifest', str(manifest),
            '--output-dir', 'analysis_outputs/harvesting_sensitivity',
        ]
        print('\n{}\nAnalyze with:\n$ {}'.format(
            LIMITATION, shlex.join(command)
        ))
    else:
        print('\nFormal analysis blocked because at least one child failed.')
    print('Manifest: {}'.format(manifest))
    return rows


if __name__ == '__main__':
    raise SystemExit(runner.wrapper_exit_code(main()))
