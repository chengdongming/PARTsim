import os
import sys
import math
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pytest
from unittest import mock


os.environ.setdefault('MPLBACKEND', 'Agg')
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance
from scripts import experiment_analysis as analysis


def make_runner(tmp_path):
    return acceptance.ExperimentRunner(
        output_dir=tmp_path / 'run',
        utilization_points=[0.5],
        num_tasksets=1,
        task_n=3,
        task_p_min=10,
        task_p_max=20,
        simulation_time=100,
        battery_capacity=20.0,
        initial_energy_ratio=0.5,
        solar_start_time_ms=123,
        use_real_solar_data=False,
        system_cores=2,
        max_workers=1,
        enable_rta=False,
        seed_base=424242,
    )


def write_acceptance_run(path, seed, ratios, samples=10):
    path.mkdir(parents=True)
    rows = []
    raw_rows = []
    for algorithm, ratio in ratios.items():
        successful = int(ratio * samples)
        rows.append({
            'algorithm': algorithm,
            'algorithm_display_name': analysis.DISPLAY_NAMES[algorithm],
            'normalized_utilization': 0.5,
            'acceptance_ratio': ratio,
            'num_samples': samples,
            'num_successful': successful,
            'seed_base': seed,
            'rta_version': 'v20.4',
            'rta_num_analyzed': 0,
            'rta_num_proven': 0,
            'rta_num_unproven': 0,
            'rta_num_errors': 0,
            'avg_tightness': '',
            'tightness_num_samples': 0,
        })
        for task_idx in range(samples):
            is_accepted = task_idx < successful
            raw_rows.append({
                'seed_base': seed,
                'taskset_seed': seed + task_idx,
                'normalized_utilization': 0.5,
                'task_idx': task_idx,
                'algorithm': algorithm,
                'accepted': int(is_accepted),
                'status': 'accepted' if is_accepted else 'rejected',
            })
    pd.DataFrame(rows).to_csv(path / 'acceptance_ratio_data.csv', index=False)
    pd.DataFrame(raw_rows).to_csv(path / 'per_taskset_results.csv', index=False)


def test_per_taskset_results_written_and_match_aggregate(tmp_path):
    runner = make_runner(tmp_path)
    results = defaultdict(lambda: defaultdict(list))
    statuses = ['accepted', 'rejected', 'simulation_timeout', 'simulation_error']
    for index, algorithm in enumerate(acceptance.ALGORITHMS):
        status = statuses[index % len(statuses)]
        results[algorithm][0.5].append({
            'algorithm': algorithm,
            'acceptance_ratio': float(status == 'accepted'),
            'simulation_status': status,
            'simulation_error': 'failure' if status == 'simulation_error' else None,
            'task_idx': 0,
            'taskset_id': 'u0.50-000',
            'seed_base': 424242,
            'taskset_seed': 429242,
            'rta_enabled': False,
            'rta_status': 'disabled',
            'rta_error': None,
        })

    raw = runner.write_per_taskset_results(results)
    aggregate = runner.aggregate_results(results)

    assert runner.per_taskset_results_file.is_file()
    assert len(raw) == 9
    assert set(raw['taskset_seed']) == {429242}
    assert set(raw['algorithm']) == set(acceptance.ALGORITHMS)
    assert raw['accepted'].sum() == aggregate['simulation_num_accepted'].sum()
    assert raw['rejected'].sum() == aggregate['simulation_num_rejected'].sum()
    assert raw['timeout'].sum() == aggregate['simulation_num_timeout'].sum()
    assert raw['error'].sum() == aggregate['simulation_num_error'].sum()
    assert list(raw.columns) == acceptance.PER_TASKSET_RESULT_FIELDS


def test_generation_failure_still_writes_nine_raw_rows(tmp_path):
    runner = make_runner(tmp_path)
    with mock.patch.object(
        runner, 'modify_config',
        side_effect=lambda algorithm: str(tmp_path / '{}.yml'.format(algorithm)),
    ), mock.patch.object(runner, 'generate_taskset', return_value=None):
        results = runner.run_experiments()

    raw = pd.read_csv(runner.per_taskset_results_file)
    aggregate = runner.aggregate_results(results)
    assert len(raw) == 9
    assert set(raw['status']) == {'error'}
    assert set(raw['reason']) == {'taskset generation failed'}
    assert aggregate['simulation_num_error'].sum() == 9


def test_multiseed_summary_supports_one_and_multiple_seeds(tmp_path):
    run1 = tmp_path / 'run1'
    run2 = tmp_path / 'run2'
    ratios1 = {'gpfp_asap_block': 1.0}
    ratios2 = {'gpfp_asap_block': 0.0}
    write_acceptance_run(run1, 101, ratios1)
    write_acceptance_run(run2, 202, ratios2)

    _, single = analysis.summarize_multiseed([run1])
    assert single.iloc[0]['num_seeds'] == 1
    assert single.iloc[0]['ci95_low'] == 1.0
    assert single.iloc[0]['ci95_high'] == 1.0

    by_seed, combined = analysis.summarize_multiseed([run1, run2])
    assert len(by_seed) == 2
    assert combined.iloc[0]['num_seeds'] == 2
    assert combined.iloc[0]['total_tasksets'] == 20
    assert combined.iloc[0]['mean_acceptance_ratio'] == 0.5
    assert 0.0 <= combined.iloc[0]['ci95_low'] <= 0.5
    assert 0.5 <= combined.iloc[0]['ci95_high'] <= 1.0


def test_paired_comparison_counts_all_outcomes(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    rows = []
    states = [(1, 1), (1, 0), (0, 1), (0, 0)]
    for task_idx, (baseline_ok, other_ok) in enumerate(states):
        for algorithm, is_ok in [
            ('gpfp_asap_block', baseline_ok),
            ('gpfp_asap_nonblock', other_ok),
        ]:
            rows.append({
                'seed_base': 1,
                'taskset_seed': 100 + task_idx,
                'normalized_utilization': 0.5,
                'task_idx': task_idx,
                'algorithm': algorithm,
                'accepted': is_ok,
                'status': 'accepted' if is_ok else 'rejected',
            })
    pd.DataFrame(rows).to_csv(run / 'per_taskset_results.csv', index=False)

    result = analysis.paired_comparison([run]).iloc[0]
    assert result['both_accepted'] == 1
    assert result['baseline_only_accepted'] == 1
    assert result['other_only_accepted'] == 1
    assert result['both_rejected'] == 1
    assert result['num_paired_tasksets'] == 4
    assert result['net_win'] == 0


def test_ablation_outputs_select_expected_algorithms(tmp_path):
    run = tmp_path / 'run'
    algorithms = set(analysis.TIMING_BLOCK + analysis.ASAP_SEMANTICS)
    write_acceptance_run(run, 1, {algorithm: 0.5 for algorithm in algorithms})
    outputs = analysis.write_ablations([run], tmp_path / 'out')

    assert set(outputs['ablation_timing_block']['algorithm']) == set(
        analysis.TIMING_BLOCK
    )
    assert set(outputs['ablation_asap_semantics']['algorithm']) == set(
        analysis.ASAP_SEMANTICS
    )
    assert (tmp_path / 'out' / 'ablation_timing_block.png').is_file()


def test_battery_summary_keeps_capacity_dimension(tmp_path):
    run5 = tmp_path / 'battery5'
    run20 = tmp_path / 'battery20'
    ratios = {'gpfp_asap_block': 0.5}
    write_acceptance_run(run5, 1, ratios)
    write_acceptance_run(run20, 1, ratios)

    summary = analysis.summarize_battery([(run5, 5), (run20, 20)])
    assert set(summary['battery']) == {5.0, 20.0}
    assert set(summary['num_seeds']) == {1}


def test_mechanism_case_selection_finds_baseline_win(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    rows = []
    for algorithm in analysis.DISPLAY_NAMES:
        is_ok = algorithm != 'gpfp_asap_nonblock'
        rows.append({
            'seed_base': 1,
            'taskset_seed': 10,
            'normalized_utilization': 0.5,
            'task_idx': 0,
            'algorithm': algorithm,
            'accepted': int(is_ok),
            'status': 'accepted' if is_ok else 'rejected',
            'output_dir': str(run),
            'trace_path': '',
        })
    pd.DataFrame(rows).to_csv(run / 'per_taskset_results.csv', index=False)

    cases = analysis.select_cases([run])
    assert 'asap_block_accept__asap_nonblock_reject' in set(cases['case_type'])


def write_rta_raw_run(
    path, total=500, proven=50, timeouts=9, version='v20.4'
):
    path.mkdir(parents=True)
    tightness = [1.0 + index / 10.0 for index in range(proven)]
    true_values = [True, 'True', 'true', '1', 1]
    rows = []
    for index in range(total):
        is_proven = index < proven
        is_timeout = index >= total - timeouts
        rows.append({
            'normalized_utilization': 0.1 if index < total // 2 else 0.2,
            'rta_enabled': true_values[index % len(true_values)],
            'rta_version': version,
            'rta_status': (
                'proven_under_assumptions' if is_proven
                else 'rta_error' if is_timeout else 'rta_unproven'
            ),
            'rta_proven': (
                true_values[index % len(true_values)] if is_proven else False
            ),
            'rta_error': (
                'RTA timed out after 300 seconds' if is_timeout else ''
            ),
            'tightness': tightness[index] if is_proven else '',
            'accepted': 1,
            'rta_response_time_bound': 20 if is_proven else '',
            'simulated_response_time': 10 if is_proven else '',
        })
    pd.DataFrame(rows).to_csv(
        path / 'per_taskset_results.csv', index=False
    )
    # Deliberately wrong aggregate values: the raw analyzer must ignore them.
    pd.DataFrame([{
        'algorithm': 'gpfp_asap_block',
        'normalized_utilization': 0.1,
        'rta_version': version,
        'avg_tightness': 999,
        'tightness_num_samples': total,
    }]).to_csv(path / 'acceptance_ratio_data.csv', index=False)
    return tightness


def test_rta_e0_manifest_aggregates_proven_raw_rows(tmp_path):
    run = tmp_path / 'e025'
    tightness = write_rta_raw_run(run)
    manifest = tmp_path / 'manifest.csv'
    pd.DataFrame([
        {'run_dir': str(run), 'E0': 0.25, 'rta_version': 'v20.4'},
    ]).to_csv(manifest, index=False)

    summary, by_util = analysis.summarize_rta_e0(manifest)
    assert len(by_util) == 2
    row = summary.iloc[0]
    assert row['rta_num_analyzed_total'] == 500
    assert row['rta_num_proven_total'] == 50
    assert row['rta_num_unproven_total'] == 441
    assert row['rta_num_errors_total'] == 9
    assert row['rta_num_timeouts_total'] == 9
    assert row['tightness_num_samples_total'] == 50
    assert row['tightness_num_proven_samples'] == 50
    assert row['avg_tightness'] == pytest.approx(pd.Series(tightness).mean())
    assert row['median_tightness'] == pytest.approx(
        pd.Series(tightness).median()
    )
    assert row['max_tightness'] == max(tightness)
    assert row['avg_tightness_over_reported_rows'] != 999
    assert by_util['rta_num_analyzed'].sum() == 500
    assert by_util['tightness_num_samples'].sum() == 50

    output = tmp_path / 'analysis'
    analysis.write_rta_e0(manifest, output)
    assert (output / 'rta_e0_sensitivity_summary.csv').is_file()
    assert (output / 'rta_e0_sensitivity_by_utilization.csv').is_file()
    assert (output / 'rta_e0_tightness.png').is_file()


def test_rta_e0_without_proven_rows_keeps_tightness_nan(tmp_path):
    run = tmp_path / 'e0'
    write_rta_raw_run(run, total=10, proven=0, timeouts=0)
    manifest = tmp_path / 'manifest.csv'
    pd.DataFrame([{
        'run_dir': str(run), 'E0': 0.0, 'rta_version': 'v20.4',
    }]).to_csv(manifest, index=False)

    summary, by_util = analysis.summarize_rta_e0(manifest)
    row = summary.iloc[0]
    assert row['tightness_num_samples_total'] == 0
    assert math.isnan(row['avg_tightness'])
    assert math.isnan(row['median_tightness'])
    assert math.isnan(row['max_tightness'])
    assert by_util['tightness_num_samples'].sum() == 0


@pytest.mark.parametrize('violation', ['rejected', 'bound'])
def test_rta_e0_rejects_soundness_violations(tmp_path, violation):
    run = tmp_path / violation
    write_rta_raw_run(run, total=1, proven=1, timeouts=0)
    raw_path = run / 'per_taskset_results.csv'
    raw = pd.read_csv(raw_path)
    if violation == 'rejected':
        raw.loc[0, 'accepted'] = 0
    else:
        raw.loc[0, 'simulated_response_time'] = 21
    raw.to_csv(raw_path, index=False)
    manifest = tmp_path / 'manifest.csv'
    pd.DataFrame([{
        'run_dir': str(run), 'E0': 1.0, 'rta_version': 'v20.4',
    }]).to_csv(manifest, index=False)

    with pytest.raises(ValueError, match='RTA soundness violation'):
        analysis.summarize_rta_e0(manifest)


def test_rta_e0_manifest_rejects_mixed_rta_versions(tmp_path):
    manifest = tmp_path / 'manifest.csv'
    pd.DataFrame([
        {'run_dir': 'old', 'E0': 0.0, 'rta_version': 'v20.1'},
        {'run_dir': 'new', 'E0': 0.0, 'rta_version': 'v20.4'},
    ]).to_csv(manifest, index=False)

    with pytest.raises(ValueError, match='only v20.4'):
        analysis.summarize_rta_e0(manifest)


def test_rta_e0_rejects_non_v20_4_raw_rows(tmp_path):
    run = tmp_path / 'mixed'
    write_rta_raw_run(run, total=2, proven=1, timeouts=0)
    raw_path = run / 'per_taskset_results.csv'
    raw = pd.read_csv(raw_path)
    raw.loc[1, 'rta_version'] = 'v20.1'
    raw.to_csv(raw_path, index=False)
    manifest = tmp_path / 'manifest.csv'
    pd.DataFrame([{
        'run_dir': str(run), 'E0': 1.0, 'rta_version': 'v20.4',
    }]).to_csv(manifest, index=False)

    with pytest.raises(ValueError, match='RTA versions'):
        analysis.summarize_rta_e0(manifest)
