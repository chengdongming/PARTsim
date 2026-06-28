#!/usr/bin/env python3
"""Rerun a small, paired taskset sample across schedulers with traces."""

import argparse
import os
import shlex
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import acceptance_ratio_test as acceptance
from scripts.experiment_analysis import accepted, load_raw_runs
from scripts.run_mechanism_case_study import (
    build_case_config,
    find_run_and_taskset,
    read_trace_metrics,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = [
    'all_rejected',
    'all_accepted',
    'asap_block_only_accepted',
    'non_asap_identical_group',
]
AUDIT_FIELDS = [
    'audit_case_id', 'category', 'seed_base', 'normalized_utilization',
    'task_idx', 'scheduler', 'accepted', 'simulation_status',
    'first_missed_task', 'deadline_miss_time', 'taskset_path',
    'trace_path', 'error',
]


def category_matches(category, by_scheduler):
    if set(by_scheduler) != set(acceptance.ALGORITHMS):
        return False
    outcomes = {
        scheduler: accepted(row)
        for scheduler, row in by_scheduler.items()
    }
    if category == 'all_rejected':
        return not any(outcomes.values())
    if category == 'all_accepted':
        return all(outcomes.values())
    if category == 'asap_block_only_accepted':
        return (
            outcomes[acceptance.ASAP_BLOCK_ALGORITHM]
            and not any(
                value for scheduler, value in outcomes.items()
                if scheduler != acceptance.ASAP_BLOCK_ALGORITHM
            )
        )
    if category == 'non_asap_identical_group':
        non_asap = [
            value for scheduler, value in outcomes.items()
            if scheduler != acceptance.ASAP_BLOCK_ALGORITHM
        ]
        return len(set(non_asap)) == 1
    raise ValueError('unknown category {}'.format(category))


def select_tasksets(run_dirs, categories=None, max_tasksets=30):
    """Select deterministic, category-balanced tasksets from raw outcomes."""
    categories = list(categories or CATEGORIES)
    frame = load_raw_runs(run_dirs)
    keys = ['seed_base', 'normalized_utilization', 'task_idx']
    buckets = defaultdict(list)
    for key, group in frame.groupby(keys, sort=True):
        group = group.drop_duplicates('algorithm', keep='first')
        by_scheduler = {
            row['algorithm']: row for _, row in group.iterrows()
        }
        for category in categories:
            if category_matches(category, by_scheduler):
                buckets[category].append({
                    'seed_base': key[0],
                    'normalized_utilization': key[1],
                    'task_idx': key[2],
                    'category': category,
                })

    selected = []
    used = set()
    offsets = defaultdict(int)
    while len(selected) < max_tasksets:
        added = False
        for category in categories:
            candidates = buckets[category]
            while offsets[category] < len(candidates):
                candidate = candidates[offsets[category]]
                offsets[category] += 1
                key = (
                    candidate['seed_base'],
                    candidate['normalized_utilization'],
                    candidate['task_idx'],
                )
                if key in used:
                    continue
                used.add(key)
                selected.append(candidate)
                added = True
                break
            if len(selected) >= max_tasksets:
                break
        if not added:
            break
    return selected


def write_audit_rows(path, rows):
    pd.DataFrame(rows, columns=AUDIT_FIELDS).to_csv(path, index=False)


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            'Rerun a small sample of existing tasksets across schedulers and '
            'preserve traces for behavioral diversity analysis.'
        )
    )
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--max-tasksets', type=int, default=30)
    parser.add_argument('--categories', nargs='+', choices=CATEGORIES)
    parser.add_argument(
        '--schedulers', nargs='+', choices=acceptance.ALGORITHMS,
        default=list(acceptance.ALGORITHMS),
    )
    parser.add_argument('--simulation-timeout', type=int, default=120)
    parser.add_argument('--dry-run', action='store_true')
    return parser


def missing_taskset_rows(candidate, case_id, schedulers, error):
    rows = []
    for scheduler in schedulers:
        rows.append({
            'audit_case_id': case_id,
            'category': candidate['category'],
            'seed_base': candidate['seed_base'],
            'normalized_utilization': candidate['normalized_utilization'],
            'task_idx': candidate['task_idx'],
            'scheduler': scheduler,
            'accepted': '',
            'simulation_status': 'taskset_not_found',
            'first_missed_task': '',
            'deadline_miss_time': '',
            'taskset_path': '',
            'trace_path': '',
            'error': str(error),
        })
    return rows


def run_scheduler(args, output_dir, trace_dir, candidate, case_id,
                  scheduler, taskset_path, metadata):
    simulation_time = int(float(
        metadata.get('simulation_horizon_ms')
        or acceptance.DEFAULT_SIMULATION_TIME
    ))
    trace_path = trace_dir / '{}-{}.json'.format(case_id, scheduler)
    config_path = (
        output_dir / 'configs' / case_id
        / 'config_{}.yml'.format(scheduler)
    ).resolve()
    command = [
        acceptance.SIMULATOR, str(config_path), str(taskset_path),
        str(simulation_time), '-t', str(trace_path),
    ]
    print('$ {}'.format(shlex.join(command)))
    row = {
        'audit_case_id': case_id,
        'category': candidate['category'],
        'seed_base': candidate['seed_base'],
        'normalized_utilization': candidate['normalized_utilization'],
        'task_idx': candidate['task_idx'],
        'scheduler': scheduler,
        'accepted': '',
        'simulation_status': 'dry_run' if args.dry_run else 'error',
        'first_missed_task': '',
        'deadline_miss_time': '',
        'taskset_path': str(taskset_path),
        'trace_path': '' if args.dry_run else str(trace_path),
        'error': '',
    }
    if args.dry_run:
        return row

    command[1] = str(build_case_config(
        output_dir, case_id, scheduler, metadata
    ))
    env = os.environ.copy()
    library = str(PROJECT_ROOT / 'build' / 'librtsim')
    env['LD_LIBRARY_PATH'] = library + ':' + env.get('LD_LIBRARY_PATH', '')
    try:
        completed = subprocess.run(
            command, cwd=str(PROJECT_ROOT), env=env,
            check=False, capture_output=True, text=True,
            timeout=args.simulation_timeout,
        )
        if completed.returncode != 0:
            row['error'] = (
                completed.stderr or completed.stdout or
                'simulator exited {}'.format(completed.returncode)
            ).strip()
        elif trace_path.is_file():
            metrics = read_trace_metrics(trace_path, simulation_time)
            row.update({
                'accepted': metrics['accepted'],
                'simulation_status': metrics['simulation_status'],
                'first_missed_task': metrics['first_missed_task'],
                'deadline_miss_time': metrics['deadline_miss_time'],
            })
        else:
            row['error'] = 'simulator produced no trace file'
    except subprocess.TimeoutExpired:
        row['simulation_status'] = 'timeout'
        row['error'] = 'simulation timed out'
    return row


def run_audit(args):
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('output directory already exists and is not empty')
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = output_dir / 'traces'
    trace_dir.mkdir()
    audit_path = output_dir / 'audit_runs.csv'
    selected = select_tasksets(
        args.runs, args.categories, args.max_tasksets
    )
    rows = []
    for index, candidate in enumerate(selected, start=1):
        case_id = 'audit-{:03d}-seed{}-u{}-i{}'.format(
            index,
            int(float(candidate['seed_base'])),
            str(candidate['normalized_utilization']).replace('.', 'p'),
            int(float(candidate['task_idx'])),
        )
        try:
            _, taskset_path, metadata = find_run_and_taskset(
                candidate, args.runs
            )
        except FileNotFoundError as exc:
            print('error: {}'.format(exc), file=sys.stderr)
            rows.extend(missing_taskset_rows(
                candidate, case_id, args.schedulers, exc
            ))
            write_audit_rows(audit_path, rows)
            continue
        metadata['normalized_utilization'] = candidate[
            'normalized_utilization'
        ]
        for scheduler in args.schedulers:
            rows.append(run_scheduler(
                args, output_dir, trace_dir, candidate, case_id,
                scheduler, taskset_path, metadata,
            ))
            write_audit_rows(audit_path, rows)
    write_audit_rows(audit_path, rows)
    print('Audit rows: {}'.format(audit_path))
    return pd.DataFrame(rows, columns=AUDIT_FIELDS)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_tasksets <= 0 or args.simulation_timeout <= 0:
        parser.error('taskset limit and simulation timeout must be positive')
    try:
        return run_audit(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
