#!/usr/bin/env python3
"""Run simulation-only acceptance experiments across battery capacities."""

import argparse
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import experiment_runner as runner


MANIFEST_FIELDS = [
    'experiment_name', 'run_dir', 'battery', 'seed_base', 'num_points',
    'num_tasksets', 'task_n', 'initial_energy', 'solar_time_ms',
    'harvesting_scale', 'max_workers', 'status', 'return_code',
]


def build_parser():
    parser = argparse.ArgumentParser(
        description='Run simulation-only battery sensitivity experiments'
    )
    runner.add_common_arguments(parser, include_battery=False)
    parser.add_argument('--batteries', nargs='+', required=True, type=float)
    parser.add_argument('--seeds', nargs='+', required=True, type=int)
    return parser


def build_specs(args):
    output_root, name, manifest = runner.output_paths(args)
    specs = []
    for battery in args.batteries:
        battery_token = runner.safe_run_dir_name(battery)
        for seed in args.seeds:
            run_dir = output_root / '{}-B{}-seed{}'.format(
                name, battery_token, seed
            )
            specs.append({
                'experiment_name': args.experiment_name,
                'run_dir': str(run_dir),
                'battery': battery,
                'seed_base': seed,
                'num_points': args.num_points,
                'num_tasksets': args.num_tasksets,
                'task_n': args.task_n,
                'initial_energy': args.initial_energy,
                'solar_time_ms': args.solar_time_ms,
                'harvesting_scale': args.harvesting_scale,
                'max_workers': args.max_workers,
                'command': runner.build_command(
                    run_dir, seed, args.num_points, args.num_tasksets,
                    args.task_n, battery, args.initial_energy,
                    args.solar_time_ms, args.max_workers,
                    args.no_group_figures,
                    harvesting_scale=args.harvesting_scale,
                    min_task_util=args.min_task_util,
                    max_task_util=args.max_task_util,
                    wcet_rounding=args.wcet_rounding,
                    actual_utilization_tolerance_total=(
                        args.actual_utilization_tolerance_total
                    ),
                    constrained_deadlines=args.constrained_deadlines,
                ),
            })
    return specs, manifest


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    runner.validate_common_args(parser, args)
    if any(battery <= 0 for battery in args.batteries):
        parser.error('--batteries values must be positive')
    specs, manifest = build_specs(args)
    rows = runner.execute_specs(
        specs, manifest, MANIFEST_FIELDS,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing,
        force=args.force,
        stop_on_failure=args.stop_on_failure,
    )
    command = ['python3', 'scripts/analyze_battery_sensitivity.py']
    for spec in specs:
        command.extend([
            '--run', spec['run_dir'], '--battery', str(spec['battery'])
        ])
    command.extend(['--output-dir', 'analysis_outputs/battery'])
    print('\nAnalyze with:\n$ {}'.format(shlex.join(command)))
    print('Manifest: {}'.format(manifest))
    return rows


if __name__ == '__main__':
    main()
