#!/usr/bin/env python3
"""Run simulation-only acceptance experiments across independent seeds."""

import argparse
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import experiment_runner as runner


MANIFEST_FIELDS = [
    'experiment_name', 'run_dir', 'seed_base', 'num_points',
    'num_tasksets', 'task_n', 'battery', 'initial_energy',
    'solar_time_ms', 'harvesting_scale', 'max_workers', 'rta_enabled', 'status',
    'return_code',
]


def build_parser():
    parser = argparse.ArgumentParser(
        description='Run multi-seed, simulation-only acceptance experiments'
    )
    runner.add_common_arguments(parser)
    parser.add_argument('--seeds', nargs='+', required=True, type=int)
    return parser


def build_specs(args):
    output_root, name, manifest = runner.output_paths(args)
    specs = []
    for seed in args.seeds:
        run_dir = output_root / '{}-seed{}'.format(name, seed)
        specs.append({
            'experiment_name': args.experiment_name,
            'run_dir': str(run_dir),
            'seed_base': seed,
            'num_points': args.num_points,
            'num_tasksets': args.num_tasksets,
            'task_n': args.task_n,
            'battery': args.battery,
            'initial_energy': args.initial_energy,
            'solar_time_ms': args.solar_time_ms,
            'harvesting_scale': args.harvesting_scale,
            'max_workers': args.max_workers,
            'rta_enabled': False,
            'command': runner.build_command(
                run_dir, seed, args.num_points, args.num_tasksets,
                args.task_n, args.battery, args.initial_energy,
                args.solar_time_ms, args.max_workers,
                args.no_group_figures,
                harvesting_scale=args.harvesting_scale,
            ),
        })
    return specs, manifest


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    runner.validate_common_args(parser, args)
    specs, manifest = build_specs(args)
    rows = runner.execute_specs(
        specs, manifest, MANIFEST_FIELDS,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing,
        force=args.force,
        stop_on_failure=args.stop_on_failure,
    )
    run_dirs = [spec['run_dir'] for spec in specs]
    command = [
        'python3', 'scripts/analyze_multiseed_acceptance.py',
        '--runs', *run_dirs,
        '--output-dir', 'analysis_outputs/multiseed',
    ]
    print('\nAnalyze with:\n$ {}'.format(shlex.join(command)))
    print('Manifest: {}'.format(manifest))
    return rows


if __name__ == '__main__':
    main()
