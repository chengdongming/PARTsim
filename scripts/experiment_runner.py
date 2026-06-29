#!/usr/bin/env python3
"""Shared utilities for safe, reproducible batch experiment runners."""

import argparse
import csv
import math
import re
import shlex
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_SCRIPT = PROJECT_ROOT / 'acceptance_ratio_test.py'
RESULT_FILES = ('acceptance_ratio_data.csv', 'per_taskset_results.csv')


def safe_run_dir_name(value):
    """Format a numeric parameter as a path-safe stable token."""
    number = float(value)
    token = format(number, '.15g')
    return token.replace('-', 'm').replace('.', 'p')


def safe_experiment_name(value):
    """Reject path traversal while retaining readable experiment names."""
    value = str(value).strip()
    if not value or value in {'.', '..'}:
        raise ValueError('experiment name must not be empty')
    safe = re.sub(r'[^A-Za-z0-9_-]+', '-', value).strip('-')
    if not safe:
        raise ValueError('experiment name has no path-safe characters')
    return safe


def run_dir_complete(run_dir):
    run_dir = Path(run_dir)
    return all((run_dir / filename).is_file() for filename in RESULT_FILES)


def build_command(run_dir, seed_base, num_points, num_tasksets, task_n,
                  battery, initial_energy, solar_time_ms, max_workers,
                  no_group_figures=False, harvesting_scale=1.0,
                  rta_initial_energy=None,
                  rta_horizon_ms=None, rta_timeout=None):
    """Build one acceptance_ratio_test.py invocation without a shell."""
    command = [
        'python3', str(EXPERIMENT_SCRIPT),
        '--run-experiment',
        '--output-dir', str(Path(run_dir)),
        '--seed-base', str(int(seed_base)),
        '--num-points', str(int(num_points)),
        '--num-tasksets', str(int(num_tasksets)),
        '--task-n', str(int(task_n)),
        '--battery', str(float(battery)),
        '--initial-energy', str(float(initial_energy)),
        '--solar-time-ms', str(int(solar_time_ms)),
        '--harvesting-scale', str(float(harvesting_scale)),
        '--max-workers', str(int(max_workers)),
    ]
    if rta_initial_energy is not None:
        command.extend([
            '--enable-rta',
            '--profile-rta',
            '--rta-initial-energy', str(float(rta_initial_energy)),
            '--rta-horizon-ms', str(int(rta_horizon_ms)),
            '--rta-assume-no-overflow',
            '--rta-timeout', str(int(rta_timeout)),
        ])
    if no_group_figures:
        command.append('--no-group-figures')
    return command


def print_command(command):
    rendered = shlex.join([str(part) for part in command])
    print('$ {}'.format(rendered))
    return rendered


def write_manifest(path, fieldnames, rows):
    """Rewrite the small manifest after every run so failures are retained."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def run_command(command):
    """Execute a command in the project root and return its process code."""
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    return int(completed.returncode)


def execute_specs(specs, manifest_path, fieldnames, dry_run=False,
                  skip_existing=False, force=False,
                  stop_on_failure=False):
    """Execute planned runs safely and persist a status row for every spec."""
    rows = []
    for spec in specs:
        row = {field: spec.get(field, '') for field in fieldnames}
        run_dir = Path(spec['run_dir'])
        command = spec['command']
        print_command(command)

        exists_nonempty = run_dir.exists() and (
            not run_dir.is_dir() or any(run_dir.iterdir())
        )
        if skip_existing and run_dir_complete(run_dir):
            row.update(status='skipped_existing', return_code='')
        elif exists_nonempty and not force:
            row.update(status='blocked_existing', return_code='')
            print(
                'Refusing to overwrite non-empty run directory: {}'.format(
                    run_dir
                )
            )
        elif dry_run:
            row.update(status='dry_run', return_code='')
        else:
            if force and run_dir.exists():
                if run_dir.is_dir():
                    shutil.rmtree(run_dir)
                else:
                    run_dir.unlink()
            return_code = run_command(command)
            row.update(
                status='completed' if return_code == 0 else 'failed',
                return_code=return_code,
            )

        rows.append(row)
        write_manifest(manifest_path, fieldnames, rows)
        if stop_on_failure and row['status'] in {
            'blocked_existing', 'failed',
        }:
            break
    return rows


def add_common_arguments(parser, include_battery=True, include_solar_time=True,
                         include_harvesting_scale=True):
    parser.add_argument('--output-root', default='acceptance_ratio_runs')
    parser.add_argument('--experiment-name', required=True)
    parser.add_argument('--num-points', type=int, default=10)
    parser.add_argument('--num-tasksets', type=int, default=50)
    parser.add_argument('--task-n', type=int, default=10)
    if include_battery:
        parser.add_argument('--battery', type=float, default=20.0)
    parser.add_argument('--initial-energy', type=float, default=1.0)
    if include_solar_time:
        parser.add_argument('--solar-time-ms', type=int, default=21975000)
    if include_harvesting_scale:
        parser.add_argument('--harvesting-scale', type=float, default=1.0)
    parser.add_argument('--max-workers', type=int, default=4)
    parser.add_argument('--no-group-figures', action='store_true')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='print commands and write a dry-run manifest without execution',
    )
    safety = parser.add_mutually_exclusive_group()
    safety.add_argument(
        '--skip-existing', action='store_true',
        help='skip directories containing both expected result CSV files',
    )
    safety.add_argument(
        '--force', action='store_true',
        help='delete each existing generated run directory before execution',
    )
    parser.add_argument(
        '--stop-on-failure', action='store_true',
        help='stop after the first failed or blocked run (default: continue)',
    )


def validate_common_args(parser, args):
    for name in ('num_points', 'num_tasksets', 'task_n', 'max_workers'):
        if getattr(args, name) <= 0:
            parser.error('--{} must be positive'.format(name.replace('_', '-')))
    if args.initial_energy < 0:
        parser.error('--initial-energy must be non-negative')
    harvesting_scale = getattr(args, 'harvesting_scale', 1.0)
    if not math.isfinite(harvesting_scale) or harvesting_scale < 0:
        parser.error('--harvesting-scale must be finite and non-negative')
    try:
        safe_experiment_name(args.experiment_name)
    except ValueError as exc:
        parser.error(str(exc))


def output_paths(args):
    output_root = Path(args.output_root).resolve()
    experiment_name = safe_experiment_name(args.experiment_name)
    manifest = output_root / '{}_manifest.csv'.format(experiment_name)
    return output_root, experiment_name, manifest
