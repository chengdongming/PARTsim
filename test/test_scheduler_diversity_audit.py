import json
import os
import sys
from pathlib import Path
from unittest import mock

import pandas as pd


os.environ.setdefault('MPLBACKEND', 'Agg')
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance
from scripts import analyze_scheduler_diversity_audit as analyzer
from scripts import run_scheduler_diversity_audit as audit


def write_raw_run(run_dir, outcomes):
    tasks = run_dir / 'tasks'
    tasks.mkdir(parents=True)
    taskset = tasks / 'taskset_u0.50_000.yml'
    taskset.write_text('tasks: []\n', encoding='utf-8')
    rows = []
    for scheduler in acceptance.ALGORITHMS:
        is_accepted = outcomes[scheduler]
        rows.append({
            'seed_base': 424242,
            'taskset_seed': 429242,
            'normalized_utilization': 0.5,
            'task_idx': 0,
            'algorithm': scheduler,
            'accepted': int(is_accepted),
            'status': 'accepted' if is_accepted else 'rejected',
            'num_tasks': 10,
            'num_cores': 4,
            'battery': 20.0,
            'initial_energy_ratio': 1.0,
            'solar_time_ms': 21975000,
            'harvesting_profile': 'synthetic_piecewise',
            'simulation_horizon_ms': 30000,
        })
    pd.DataFrame(rows).to_csv(
        run_dir / 'per_taskset_results.csv', index=False
    )
    return taskset


def test_category_selection_detects_asap_block_only_acceptance(tmp_path):
    run_dir = tmp_path / 'run'
    outcomes = {scheduler: False for scheduler in acceptance.ALGORITHMS}
    outcomes[acceptance.ASAP_BLOCK_ALGORITHM] = True
    write_raw_run(run_dir, outcomes)

    selected = audit.select_tasksets(
        [run_dir], ['asap_block_only_accepted'], 30
    )
    assert len(selected) == 1
    assert selected[0]['category'] == 'asap_block_only_accepted'


def test_audit_runner_dry_run_writes_nine_scheduler_rows(tmp_path):
    run_dir = tmp_path / 'run'
    taskset = write_raw_run(
        run_dir, {scheduler: True for scheduler in acceptance.ALGORITHMS}
    )
    output = tmp_path / 'audit'
    with mock.patch.object(audit.subprocess, 'run') as run_mock:
        frame = audit.main([
            '--runs', str(run_dir),
            '--output-dir', str(output),
            '--max-tasksets', '1',
            '--categories', 'all_accepted',
            '--dry-run',
        ])

    run_mock.assert_not_called()
    assert len(frame) == 9
    assert set(frame['scheduler']) == set(acceptance.ALGORITHMS)
    assert set(frame['simulation_status']) == {'dry_run'}
    assert set(frame['taskset_path']) == {str(taskset.resolve())}
    assert (output / 'audit_runs.csv').is_file()


def write_trace(path, task_name='task_0', energy=1000):
    path.write_text(json.dumps({
        'events': [
            {
                'time': '0', 'event_type': 'arrival',
                'task_name': task_name, 'arrival_time': '0',
                'current_energy_mJ': energy,
            },
            {
                'time': '1', 'event_type': 'scheduled',
                'task_name': task_name, 'arrival_time': '0',
                'current_energy_mJ': energy - 10,
            },
            {
                'time': '2', 'event_type': 'end_instance',
                'task_name': task_name, 'arrival_time': '0',
                'execution_time_ms': 1,
                'current_energy_mJ': energy - 20,
            },
        ]
    }), encoding='utf-8')


def test_analyzer_detects_same_outcome_but_different_timeline(tmp_path):
    traces = tmp_path / 'traces'
    traces.mkdir()
    rows = []
    for scheduler in acceptance.ALGORITHMS:
        trace = traces / '{}.json'.format(scheduler)
        task_name = 'task_changed' if scheduler == 'gpfp_asap_sync' else 'task_0'
        write_trace(trace, task_name=task_name)
        rows.append({
            'audit_case_id': 'case-1',
            'category': 'all_accepted',
            'scheduler': scheduler,
            'accepted': 1,
            'simulation_status': 'accepted',
            'first_missed_task': '',
            'deadline_miss_time': '',
            'trace_path': str(trace),
        })
    audit_csv = tmp_path / 'audit_runs.csv'
    pd.DataFrame(rows).to_csv(audit_csv, index=False)
    output = tmp_path / 'analysis'

    summary, pairwise = analyzer.write_audit_analysis(audit_csv, output)
    assert len(summary) == 1
    assert summary.iloc[0]['accepted_same']
    assert not summary.iloc[0]['execution_timeline_hash_same']
    assert summary.iloc[0]['conclusion'] == 'outcome_same_trace_different'
    pair = pairwise[
        (pairwise['scheduler_a'] == 'gpfp_asap_nonblock')
        & (pairwise['scheduler_b'] == 'gpfp_asap_sync')
    ].iloc[0]
    assert pair['classification'] == 'outcome_same_trace_different'
    assert (output / 'scheduler_diversity_summary.csv').is_file()
    assert (output / 'pairwise_trace_comparison.csv').is_file()


def test_missing_trace_events_produce_na_hashes(tmp_path):
    trace = tmp_path / 'trace.json'
    trace.write_text(json.dumps({
        'events': [{'time': '0', 'event_type': 'arrival'}]
    }), encoding='utf-8')
    signatures = analyzer.trace_signatures(trace)
    assert signatures['execution_timeline_hash'] == ''
    assert signatures['battery_curve_hash'] == ''
    assert signatures['trace_file_size'] > 0


def test_all_rejected_without_miss_data_is_not_labeled_different():
    frame = pd.DataFrame([
        {
            'scheduler': scheduler,
            'accepted': 0,
            'first_missed_task': '',
            'deadline_miss_time': '',
            'execution_timeline_hash': '',
            'battery_curve_hash': '',
            'trace_file_size': '',
        }
        for scheduler in acceptance.ALGORITHMS
    ])
    assert analyzer.case_conclusion(frame) == ''
