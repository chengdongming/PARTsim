#!/usr/bin/env python3
"""Run synthetic harvesting-strength sensitivity experiments."""

import argparse
import math
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import experiment_runner as runner


LIMITATION = (
    'This experiment scales only the existing synthetic_piecewise harvesting '
    'supply. It does not change battery capacity, initial energy, task costs, '
    'task timing, or scheduler semantics.'
)
MANIFEST_FIELDS = [
    'experiment_name', 'run_dir', 'harvesting_scale', 'seed_base',
    'num_points', 'num_tasksets', 'task_n', 'battery', 'initial_energy',
    'solar_time_ms', 'max_workers', 'harvesting_profile', 'status', 'return_code',
] + runner.EXECUTION_MANIFEST_FIELDS


def build_parser():
    parser = argparse.ArgumentParser(
        description='Run harvesting-strength sensitivity. ' + LIMITATION
    )
    runner.add_common_arguments(parser, include_harvesting_scale=False)
    parser.add_argument(
        '--harvesting-scales', nargs='+', required=True, type=float,
        help='non-negative synthetic_piecewise supply multipliers',
    )
    parser.add_argument('--seeds', nargs='+', required=True, type=int)
    return parser


def build_specs(args):
    output_root, name, manifest = runner.output_paths(args)
    specs = []
    for scale in args.harvesting_scales:
        for seed in args.seeds:
            run_dir = output_root / '{}-hscale{}-seed{}'.format(
                name, runner.safe_run_dir_name(scale), seed
            )
            specs.append({
                'experiment_name': args.experiment_name,
                'run_dir': str(run_dir),
                'harvesting_scale': scale,
                'seed_base': seed,
                'num_points': args.num_points,
                'num_tasksets': args.num_tasksets,
                'task_n': args.task_n,
                'battery': args.battery,
                'initial_energy': args.initial_energy,
                'solar_time_ms': args.solar_time_ms,
                'max_workers': args.max_workers,
                'harvesting_profile': 'synthetic_piecewise',
                'command': runner.build_command(
                    run_dir, seed, args.num_points, args.num_tasksets,
                    args.task_n, args.battery, args.initial_energy,
                    args.solar_time_ms, args.max_workers,
                    args.no_group_figures, harvesting_scale=scale,
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
    if any(not math.isfinite(value) or value < 0
           for value in args.harvesting_scales):
        parser.error('--harvesting-scales values must be finite and non-negative')
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
            'python3', 'scripts/analyze_harvesting_strength_sensitivity.py',
            '--manifest', str(manifest),
            '--output-dir', 'analysis_outputs/harvesting_strength_sensitivity',
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
