#!/usr/bin/env python3
"""Rerun selected paired scheduler cases while preserving JSON traces."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import acceptance_ratio_test as acceptance
from scripts.experiment_analysis import validate_attested_run_directory
from scripts.experiment_analysis import validate_attested_analyzer_input
from scripts.experiment_runner import write_analysis_artifact_attestation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRACE_LIMITATION = (
    'The current trace schema has no dedicated idle-interval, '
    'global-blocking, low-priority-bypass, sync-batch-reject, or '
    'ALAP-slack-wait events; those metrics are reported as NA pending '
    'trace instrumentation.'
)
CASE_TYPE_ALIASES = {
    'asap_block_accepts_asap_nonblock_rejects': (
        'asap_block_accept__asap_nonblock_reject'
    ),
    'asap_block_accepts_asap_sync_rejects': (
        'asap_block_accept__asap_sync_reject'
    ),
    'asap_block_accepts_alap_block_rejects': (
        'asap_block_accept__alap_block_reject'
    ),
    'all_accepted': 'all_accept',
    'all_rejected': 'all_reject',
}
DEFAULT_SCHEDULERS = {
    'asap_block_accept__asap_nonblock_reject': [
        'gpfp_asap_block', 'gpfp_asap_nonblock',
    ],
    'asap_block_accept__asap_sync_reject': [
        'gpfp_asap_block', 'gpfp_asap_sync',
    ],
    'asap_block_accept__alap_block_reject': [
        'gpfp_asap_block', 'gpfp_alap_block',
    ],
    'asap_block_reject__other_accept': ['gpfp_asap_block'],
    'all_accept': list(acceptance.ALGORITHMS),
    'all_reject': list(acceptance.ALGORITHMS),
}
SUMMARY_FIELDS = [
    'case_id', 'case_type', 'seed_base', 'normalized_utilization',
    'task_idx', 'scheduler', 'accepted', 'simulation_status',
    'deadline_miss_time', 'first_missed_task', 'trace_path',
    'taskset_path', 'battery_min', 'battery_final', 'executed_ticks',
    'idle_ticks', 'deadline_miss_tick', 'global_blocking_ticks',
    'low_priority_bypass_ticks', 'sync_batch_reject_ticks',
    'alap_slack_wait_ticks', 'error',
]


def canonical_case_type(value):
    value = str(value)
    return CASE_TYPE_ALIASES.get(value, value)


def select_candidates(frame, requested_types=None, max_per_type=1):
    selected = frame.copy()
    selected['_canonical_type'] = selected['case_type'].map(canonical_case_type)
    if requested_types:
        allowed = {canonical_case_type(value) for value in requested_types}
        selected = selected[selected['_canonical_type'].isin(allowed)]
    return selected.groupby('_canonical_type', sort=False).head(max_per_type)


def find_run_and_taskset(candidate, run_dirs):
    seed = int(float(candidate['seed_base']))
    utilization = float(candidate['normalized_utilization'])
    task_idx = int(float(candidate['task_idx']))
    for run_dir in map(Path, run_dirs):
        raw_path = run_dir / 'per_taskset_results.csv'
        if not raw_path.is_file():
            continue
        raw = pd.read_csv(raw_path, keep_default_na=False)
        raw_util = pd.to_numeric(raw['normalized_utilization'], errors='coerce')
        matched = raw[
            (pd.to_numeric(raw['seed_base'], errors='coerce') == seed)
            & ((raw_util - utilization).abs() < 1e-9)
            & (pd.to_numeric(raw['task_idx'], errors='coerce') == task_idx)
        ]
        if matched.empty:
            continue
        metadata = matched.iloc[0].to_dict()
        for field in ('taskset_path', 'task_file'):
            value = metadata.get(field)
            if value:
                path = Path(str(value))
                if not path.is_absolute():
                    path = run_dir / path
                if path.is_file():
                    return run_dir, path.resolve(), metadata
        exact = run_dir / 'tasks' / 'taskset_u{:.2f}_{:03d}.yml'.format(
            utilization, task_idx
        )
        if exact.is_file():
            return run_dir, exact.resolve(), metadata
        patterns = [
            '*u{:.2f}*{:03d}*.yml'.format(utilization, task_idx),
            '*u{}*{}*.yml'.format(utilization, task_idx),
        ]
        for pattern in patterns:
            matches = sorted((run_dir / 'tasks').glob(pattern))
            if matches:
                return run_dir, matches[0].resolve(), metadata
        raise FileNotFoundError(
            'matched run {} but could not locate taskset for seed={}, U={}, '
            'task_idx={}'.format(run_dir, seed, utilization, task_idx)
        )
    raise FileNotFoundError(
        'no --runs directory contains seed={}, U={}, task_idx={}'.format(
            seed, utilization, task_idx
        )
    )


def read_trace_metrics(trace_path, expected_simulation_time=30000):
    trace_path = Path(trace_path)
    with trace_path.open(encoding='utf-8') as handle:
        events = json.load(handle).get('events', [])
    parser = acceptance.TraceParser(str(trace_path))
    accepted = parser.get_acceptance_ratio(expected_simulation_time) == 1.0
    misses = [
        event for event in events
        if isinstance(event, dict) and event.get('event_type') == 'dline_miss'
    ]
    first_miss = min(
        misses, key=lambda event: float(event.get('time', 'inf'))
    ) if misses else {}
    energy = []
    executed = 0.0
    for event in events:
        if not isinstance(event, dict):
            continue
        value = acceptance._extract_number(event.get('current_energy_mJ'))
        if value is not None:
            energy.append(value / 1000.0)
        for field in ('execution_time_ms', 'executed_time_ms'):
            value = acceptance._extract_number(event.get(field))
            if value is not None and value >= 0:
                executed += value
                break
    miss_time = acceptance._extract_number(first_miss.get('time'))
    return {
        'accepted': int(accepted),
        'simulation_status': 'accepted' if accepted else 'rejected',
        'deadline_miss_time': '' if miss_time is None else miss_time,
        'first_missed_task': first_miss.get('task_name', ''),
        'battery_min': min(energy) if energy else '',
        'battery_final': energy[-1] if energy else '',
        'executed_ticks': executed if executed else '',
        'idle_ticks': '',
        'deadline_miss_tick': '' if miss_time is None else miss_time,
        'global_blocking_ticks': '',
        'low_priority_bypass_ticks': '',
        'sync_batch_reject_ticks': '',
        'alap_slack_wait_ticks': '',
    }


def build_case_config(output_dir, case_id, scheduler, metadata):
    config_dir = Path(output_dir) / 'configs' / case_id
    initial_energy_ratio = metadata.get('initial_energy_ratio')
    if initial_energy_ratio in ('', None):
        initial_energy_ratio = 1.0
    harvesting_scale = metadata.get('harvesting_scale')
    if harvesting_scale in ('', None):
        harvesting_scale = 1.0
    experiment = acceptance.ExperimentRunner(
        output_dir=config_dir,
        utilization_points=[float(metadata['normalized_utilization'])],
        num_tasksets=1,
        task_n=int(float(metadata.get('num_tasks') or 10)),
        task_p_min=acceptance.DEFAULT_TASK_P_MIN,
        task_p_max=acceptance.DEFAULT_TASK_P_MAX,
        simulation_time=int(float(
            metadata.get('simulation_horizon_ms')
            or acceptance.DEFAULT_SIMULATION_TIME
        )),
        battery_capacity=float(metadata.get('battery') or 20.0),
        initial_energy_ratio=float(initial_energy_ratio),
        solar_start_time_ms=int(float(metadata.get('solar_time_ms') or 0)),
        use_real_solar_data=(
            metadata.get('harvesting_profile') == 'real_solar'
        ),
        system_cores=int(float(metadata.get('num_cores') or 4)),
        max_workers=1,
        harvesting_scale=float(harvesting_scale),
    )
    return Path(experiment.modify_config(scheduler)).resolve()


def scheduler_list(candidate, override=None):
    if override:
        return list(override)
    case_type = canonical_case_type(candidate['case_type'])
    schedulers = list(DEFAULT_SCHEDULERS.get(case_type, []))
    comparison = candidate.get('comparison_algorithm')
    if comparison and comparison not in schedulers:
        schedulers.append(str(comparison))
    if not schedulers:
        schedulers = ['gpfp_asap_block']
    return schedulers


def run_cases(args):
    source_attestations = []
    if not args.allow_unattested_diagnostic_input:
        validate_attested_analyzer_input(args.candidates)
        for run_dir in args.runs:
            source_attestations.append(
                validate_attested_run_directory(run_dir)
            )
    output_dir = Path(args.output_dir).resolve()
    if args.allow_unattested_diagnostic_input:
        output_dir = output_dir / 'diagnostic_unattested'
    summary_path = output_dir / (
        'diagnostic_case_summary.csv'
        if args.allow_unattested_diagnostic_input else 'case_summary.csv'
    )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('output directory already exists and is not empty')
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = output_dir / 'traces'
    trace_dir.mkdir()
    candidates = pd.read_csv(args.candidates, keep_default_na=False)
    required = {
        'case_type', 'seed_base', 'normalized_utilization', 'task_idx',
    }
    missing = required - set(candidates.columns)
    if missing:
        raise ValueError('candidate CSV is missing columns: {}'.format(
            ', '.join(sorted(missing))
        ))
    selected = select_candidates(
        candidates, args.case_types, args.max_cases_per_type
    )
    rows = []
    for ordinal, (_, candidate) in enumerate(selected.iterrows(), start=1):
        case_type = canonical_case_type(candidate['case_type'])
        case_id = '{}-{:03d}'.format(case_type, ordinal)
        try:
            _, taskset_path, metadata = find_run_and_taskset(
                candidate, args.runs
            )
        except FileNotFoundError as exc:
            print('error: {}'.format(exc), file=sys.stderr)
            rows.append({
                'case_id': case_id,
                'case_type': case_type,
                'seed_base': candidate.get('seed_base', ''),
                'normalized_utilization': candidate.get(
                    'normalized_utilization', ''
                ),
                'task_idx': candidate.get('task_idx', ''),
                'scheduler': '',
                'accepted': '',
                'simulation_status': 'taskset_not_found',
                'error': str(exc),
            })
            continue
        metadata['normalized_utilization'] = candidate[
            'normalized_utilization'
        ]
        simulation_time = int(float(
            metadata.get('simulation_horizon_ms')
            or acceptance.DEFAULT_SIMULATION_TIME
        ))
        for scheduler in scheduler_list(candidate, args.schedulers):
            trace_path = trace_dir / '{}-{}.json'.format(case_id, scheduler)
            config_path = (
                output_dir / 'configs' / case_id
                / 'config_{}.yml'.format(scheduler)
            ).resolve()
            command = [
                acceptance.SIMULATOR, str(config_path), str(taskset_path),
                str(simulation_time), '-t', str(trace_path),
                '--run-id', '{}-{}'.format(case_id, scheduler),
                '--taskset-semantic-hash',
                acceptance.taskset_semantic_hash(taskset_path),
            ]
            print('$ {}'.format(shlex.join(command)))
            base = {
                'case_id': case_id,
                'case_type': case_type,
                'seed_base': candidate['seed_base'],
                'normalized_utilization': candidate['normalized_utilization'],
                'task_idx': candidate['task_idx'],
                'scheduler': scheduler,
                'accepted': '',
                'simulation_status': 'dry_run' if args.dry_run else 'error',
                'deadline_miss_time': '',
                'first_missed_task': '',
                'trace_path': str(trace_path) if not args.dry_run else '',
                'taskset_path': str(taskset_path),
                'battery_min': '',
                'battery_final': '',
                'executed_ticks': '',
                'idle_ticks': '',
                'deadline_miss_tick': '',
                'global_blocking_ticks': '',
                'low_priority_bypass_ticks': '',
                'sync_batch_reject_ticks': '',
                'alap_slack_wait_ticks': '',
                'error': '',
            }
            if not args.dry_run:
                config_path = build_case_config(
                    output_dir, case_id, scheduler, metadata
                )
                command[1] = str(config_path)
                env = os.environ.copy()
                library = str(PROJECT_ROOT / 'build' / 'librtsim')
                env['LD_LIBRARY_PATH'] = library + ':' + env.get(
                    'LD_LIBRARY_PATH', ''
                )
                try:
                    completed = subprocess.run(
                        command, cwd=str(PROJECT_ROOT),
                        env=env, check=False, capture_output=True, text=True,
                        timeout=args.simulation_timeout,
                    )
                    if completed.returncode != 0:
                        base['simulation_status'] = 'error'
                        base['error'] = (
                            completed.stderr or completed.stdout or
                            'simulator exited {}'.format(completed.returncode)
                        ).strip()
                    elif trace_path.is_file():
                        base.update(read_trace_metrics(
                            trace_path, simulation_time
                        ))
                    else:
                        base['error'] = 'simulator produced no trace file'
                except subprocess.TimeoutExpired:
                    base['simulation_status'] = 'timeout'
                    base['error'] = 'simulation timed out'
            rows.append(base)
    frame = pd.DataFrame(rows, columns=SUMMARY_FIELDS)
    frame.to_csv(summary_path, index=False)
    if not args.allow_unattested_diagnostic_input:
        candidate_snapshot = output_dir / 'candidate_snapshot.csv'
        shutil.copyfile(Path(args.candidates).resolve(), candidate_snapshot)
        write_analysis_artifact_attestation(
            summary_path,
            companion_paths=[candidate_snapshot] + [
                Path(str(row['trace_path'])).resolve()
                for row in rows if str(row.get('trace_path', '')).strip()
            ],
            producer_id='mechanism_case_study_v1',
            output_role='mechanism_case_summary',
            producer_config={
                'case_types': list(args.case_types or []),
                'max_cases_per_type': int(args.max_cases_per_type),
                'schedulers': list(args.schedulers or []),
            },
            config_ids=[
                value for payload in source_attestations
                for value in payload.get('config_id', [])
            ],
            source_artifacts=[
                (Path(args.candidates).resolve(), 'mechanism_candidates')
            ] + [
                (Path(run_dir).resolve() / 'per_taskset_results.csv',
                 'source_per_taskset_results')
                for run_dir in args.runs
            ],
        )
    print(TRACE_LIMITATION)
    print('Case summary: {}'.format(summary_path))
    return frame


def build_parser():
    parser = argparse.ArgumentParser(
        description='Rerun selected mechanism cases with traces. ' +
        TRACE_LIMITATION
    )
    parser.add_argument('--candidates', required=True)
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--case-types', nargs='+')
    parser.add_argument('--max-cases-per-type', type=int, default=1)
    parser.add_argument('--schedulers', nargs='+')
    parser.add_argument('--simulation-timeout', type=int, default=120)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument(
        '--allow-unattested-diagnostic-input', action='store_true',
        help='read unattested fixtures into a diagnostic-only subdirectory',
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_cases_per_type <= 0 or args.simulation_timeout <= 0:
        parser.error('case limit and simulation timeout must be positive')
    unknown = set(args.schedulers or []) - set(acceptance.ALGORITHMS)
    if unknown:
        parser.error('unknown scheduler(s): {}'.format(
            ', '.join(sorted(unknown))
        ))
    try:
        return run_cases(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
