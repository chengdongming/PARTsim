import csv
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pandas as pd


os.environ.setdefault('MPLBACKEND', 'Agg')
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import analyze_harvesting_sensitivity
from scripts import analyze_mechanism_cases
from scripts import run_harvesting_sensitivity
from scripts import run_mechanism_case_study


def write_fake_run(run_dir, seed=424242, accepted=1):
    tasks = run_dir / 'tasks'
    tasks.mkdir(parents=True)
    taskset = tasks / 'taskset_u0.50_000.yml'
    taskset.write_text(
        'resources: []\n'
        'taskset:\n'
        '  - name: task_0\n'
        '    iat: 10\n'
        '    runtime: 1\n'
        '    code:\n'
        '      - fixed(1, bzip2)\n',
        encoding='utf-8',
    )
    raw = pd.DataFrame([{
        'result_schema_version': 3,
        'source_run_id': run_dir.name,
        'config_id': 'config-{}'.format(seed),
        'config_group_id': 'solar-compatible-group',
        'taskset_id': 'taskset-0',
        'taskset_hash': 'hash-{}'.format(seed),
        'seed_base': seed,
        'taskset_seed': seed + 5000,
        'normalized_utilization': 0.5,
        'task_idx': 0,
        'algorithm': 'gpfp_asap_block',
        'accepted': accepted,
        'status': 'accepted' if accepted else 'rejected',
        'num_tasks': 10,
        'num_cores': 4,
        'battery': 20.0,
        'initial_energy_ratio': 1.0,
        'solar_time_ms': 100,
        'harvesting_profile': 'synthetic_piecewise',
        'simulation_horizon_ms': 30000,
    }])
    raw.to_csv(run_dir / 'per_taskset_results.csv', index=False)
    aggregate = pd.DataFrame([{
        'result_schema_version': 3,
        'source_run_id': run_dir.name,
        'config_id': 'config-{}'.format(seed),
        'config_group_id': 'solar-compatible-group',
        'algorithm': 'gpfp_asap_block',
        'algorithm_display_name': 'ASAP-Block',
        'normalized_utilization': 0.5,
        'acceptance_ratio': float(accepted),
        'num_samples': 1,
        'num_successful': accepted,
        'simulation_num_accepted': accepted,
        'simulation_num_rejected': 1 - accepted,
        'simulation_num_valid': 1,
        'simulation_num_requested': 1,
        'simulation_num_error': 0,
        'simulation_num_timeout': 0,
        'simulation_num_generation_error': 0,
        'seed_base': seed,
    }])
    aggregate.to_csv(run_dir / 'acceptance_ratio_data.csv', index=False)
    return taskset


def test_mechanism_runner_dry_run_locates_taskset_and_writes_summary(tmp_path):
    run_dir = tmp_path / 'run'
    taskset = write_fake_run(run_dir)
    candidates = tmp_path / 'candidates.csv'
    pd.DataFrame([{
        'case_type': 'asap_block_accept__asap_nonblock_reject',
        'seed_base': 424242,
        'normalized_utilization': 0.5,
        'task_idx': 0,
        'comparison_algorithm': 'gpfp_asap_nonblock',
    }]).to_csv(candidates, index=False)
    output = tmp_path / 'cases'

    with mock.patch.object(
        run_mechanism_case_study.subprocess, 'run'
    ) as run_mock:
        frame = run_mechanism_case_study.main([
            '--candidates', str(candidates),
            '--runs', str(run_dir),
            '--output-dir', str(output),
            '--case-types',
            'asap_block_accepts_asap_nonblock_rejects',
            '--dry-run',
            '--allow-unattested-diagnostic-input',
        ])

    run_mock.assert_not_called()
    assert len(frame) == 2
    assert set(frame['scheduler']) == {
        'gpfp_asap_block', 'gpfp_asap_nonblock',
    }
    assert set(frame['simulation_status']) == {'dry_run'}
    assert set(frame['taskset_path']) == {str(taskset.resolve())}
    assert (output / 'diagnostic_unattested' /
            'diagnostic_case_summary.csv').is_file()


def test_trace_metrics_extract_supported_fields_and_leave_unsupported_na(tmp_path):
    trace = tmp_path / 'trace.json'
    trace.write_text(json.dumps({
        'events': [
            {
                'time': '0', 'event_type': 'arrival', 'task_name': 'task_0',
                'current_energy_mJ': 1000,
            },
            {
                'time': '5', 'event_type': 'descheduled',
                'task_name': 'task_0', 'executed_time_ms': 5,
                'current_energy_mJ': 700,
            },
            {
                'time': '10', 'event_type': 'dline_miss',
                'task_name': 'task_0', 'current_energy_mJ': 600,
            },
        ]
    }), encoding='utf-8')

    metrics = run_mechanism_case_study.read_trace_metrics(trace, 10)
    assert metrics['accepted'] == 0
    assert metrics['first_missed_task'] == 'task_0'
    assert metrics['deadline_miss_time'] == 10
    assert metrics['battery_min'] == 0.6
    assert metrics['battery_final'] == 0.6
    assert metrics['executed_ticks'] == 5
    assert metrics['global_blocking_ticks'] == ''
    assert metrics['low_priority_bypass_ticks'] == ''


def test_mechanism_analysis_writes_summary_without_trace(tmp_path):
    case_summary = tmp_path / 'case_summary.csv'
    pd.DataFrame([{
        'case_id': 'case-1',
        'case_type': 'all_accept',
        'scheduler': 'gpfp_asap_block',
        'accepted': 1,
        'simulation_status': 'accepted',
        'battery_min': '',
        'battery_final': '',
        'executed_ticks': '',
        'trace_path': '',
    }]).to_csv(case_summary, index=False)

    output = tmp_path / 'analysis'
    summary = analyze_mechanism_cases.write_mechanism_analysis(
        case_summary, output
    )
    assert len(summary) == 1
    assert summary.iloc[0]['acceptance_ratio'] == 1.0
    assert (output / 'mechanism_case_summary.csv').is_file()


def test_mechanism_analysis_excludes_error_and_timeout_from_acceptance(
        tmp_path):
    case_summary = tmp_path / 'case_summary.csv'
    rows = []
    for index, status in enumerate([
            'accepted', 'rejected', 'error', 'timeout']):
        rows.append({
            'case_id': f'case-{index}',
            'case_type': 'mixed_outcomes',
            'scheduler': 'gpfp_asap_block',
            'accepted': '' if status in {'error', 'timeout'} else int(
                status == 'accepted'
            ),
            'simulation_status': status,
            'battery_min': '',
            'battery_final': '',
            'executed_ticks': '',
            'trace_path': '',
        })
    pd.DataFrame(rows).to_csv(case_summary, index=False)

    summary = analyze_mechanism_cases.write_mechanism_analysis(
        case_summary, tmp_path / 'analysis'
    )

    row = summary.iloc[0]
    assert row['num_cases'] == 4
    assert row['num_accepted'] == 1
    assert row['num_rejected'] == 1
    assert row['num_error'] == 1
    assert row['num_timeout'] == 1
    assert row['acceptance_ratio'] == 0.5


def test_mechanism_analysis_no_valid_outcome_is_nan(tmp_path):
    case_summary = tmp_path / 'case_summary.csv'
    pd.DataFrame([{
        'case_id': 'case-error',
        'case_type': 'no_valid_outcome',
        'scheduler': 'gpfp_asap_block',
        'accepted': '',
        'simulation_status': 'error',
        'battery_min': '',
        'battery_final': '',
        'executed_ticks': '',
        'trace_path': '',
    }]).to_csv(case_summary, index=False)

    summary = analyze_mechanism_cases.write_mechanism_analysis(
        case_summary, tmp_path / 'analysis'
    )

    assert pd.isna(summary.iloc[0]['acceptance_ratio'])


def test_harvesting_runner_dry_run_writes_manifest_and_solar_commands(tmp_path):
    output_root = tmp_path / 'runs'
    with mock.patch(
        'scripts.experiment_runner.subprocess.run'
    ) as run_mock:
        rows = run_harvesting_sensitivity.main([
            '--output-root', str(output_root),
            '--experiment-name', 'solar-test',
            '--solar-times-ms', '0', '7200000',
            '--seeds', '424242',
            '--num-points', '2',
            '--num-tasksets', '2',
            '--task-n', '10',
            '--battery', '20',
            '--initial-energy', '1',
            '--max-workers', '2',
            '--no-group-figures',
            '--dry-run',
        ])

    run_mock.assert_not_called()
    assert len(rows) == 2
    manifest = output_root / 'solar-test_manifest.csv'
    with manifest.open(newline='', encoding='utf-8') as handle:
        manifest_rows = list(csv.DictReader(handle))
    assert {row['solar_time_ms'] for row in manifest_rows} == {
        '0', '7200000',
    }
    parser = run_harvesting_sensitivity.build_parser()
    args = parser.parse_args([
        '--experiment-name', 'solar-test', '--solar-times-ms', '0',
        '7200000', '--seeds', '424242',
    ])
    specs, _ = run_harvesting_sensitivity.build_specs(args)
    assert [
        spec['command'][spec['command'].index('--solar-time-ms') + 1]
        for spec in specs
    ] == ['0', '7200000']


def test_harvesting_analysis_outputs_summary_and_plot(tmp_path):
    run0 = tmp_path / 'run0'
    run1 = tmp_path / 'run1'
    write_fake_run(run0, seed=11, accepted=1)
    write_fake_run(run1, seed=22, accepted=0)
    manifest = tmp_path / 'manifest.csv'
    pd.DataFrame([
        {
            'run_dir': str(run0), 'solar_time_ms': 0, 'seed_base': 11,
            'harvesting_profile': 'synthetic_piecewise',
        },
        {
            'run_dir': str(run1), 'solar_time_ms': 0, 'seed_base': 22,
            'harvesting_profile': 'synthetic_piecewise',
        },
    ]).to_csv(manifest, index=False)

    output = tmp_path / 'harvesting-analysis'
    by_seed, summary = (
            analyze_harvesting_sensitivity.write_harvesting_outputs(
                manifest, output, allow_legacy=True
        )
    )
    assert len(by_seed) == 2
    assert len(summary) == 1
    assert summary.iloc[0]['scheduler'] == 'gpfp_asap_block'
    assert summary.iloc[0]['num_seeds'] == 2
    assert summary.iloc[0]['num_valid_seeds'] == 2
    assert summary.iloc[0]['mean_acceptance_ratio'] == 0.5
    assert (output / 'harvesting_sensitivity_summary.csv').is_file()
    assert (output / 'harvesting_sensitivity_by_seed.csv').is_file()
    assert (output / 'harvesting_sensitivity_plot.png').is_file()


def test_harvesting_analysis_preserves_no_valid_as_nan(tmp_path):
    run = tmp_path / 'no-valid-run'
    write_fake_run(run, seed=33, accepted=0)
    aggregate_path = run / 'acceptance_ratio_data.csv'
    aggregate = pd.read_csv(aggregate_path)
    aggregate.loc[0, 'simulation_num_accepted'] = 0
    aggregate.loc[0, 'simulation_num_rejected'] = 0
    aggregate.loc[0, 'simulation_num_valid'] = 0
    aggregate.loc[0, 'simulation_num_requested'] = 1
    aggregate.loc[0, 'simulation_num_error'] = 1
    aggregate.loc[0, 'simulation_num_timeout'] = 0
    aggregate.to_csv(aggregate_path, index=False)
    raw_path = run / 'per_taskset_results.csv'
    raw = pd.read_csv(raw_path)
    raw.loc[0, 'status'] = 'error'
    raw.loc[0, 'accepted'] = 0
    raw.to_csv(raw_path, index=False)
    manifest = tmp_path / 'manifest.csv'
    pd.DataFrame([{
        'run_dir': str(run), 'solar_time_ms': 0, 'seed_base': 33,
        'harvesting_profile': 'synthetic_piecewise',
    }]).to_csv(manifest, index=False)

    _, summary = analyze_harvesting_sensitivity.summarize_harvesting(
        manifest, allow_legacy=True
    )

    row = summary.iloc[0]
    assert row['num_seeds'] == 1
    assert row['num_valid_seeds'] == 0
    assert pd.isna(row['mean_acceptance_ratio'])
    assert pd.isna(row['ci95_low'])
    assert pd.isna(row['ci95_high'])
