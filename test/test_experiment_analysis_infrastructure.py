import os
import json
import subprocess
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
    config_id = 'config-{}'.format(seed)
    config_group_id = 'compatible-default-config'
    rows = []
    raw_rows = []
    for algorithm, ratio in ratios.items():
        successful = int(ratio * samples)
        rows.append({
            'result_schema_version': 3,
            'source_run_id': path.name,
            'config_id': config_id,
            'config_group_id': config_group_id,
            'algorithm': algorithm,
            'algorithm_display_name': analysis.DISPLAY_NAMES[algorithm],
            'normalized_utilization': 0.5,
            'acceptance_ratio': ratio,
            'num_samples': samples,
            'num_successful': successful,
            'simulation_num_accepted': successful,
            'simulation_num_rejected': samples - successful,
            'simulation_num_valid': samples,
            'simulation_num_requested': samples,
            'simulation_num_error': 0,
            'simulation_num_timeout': 0,
            'simulation_num_generation_error': 0,
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
                'result_schema_version': 3,
                'source_run_id': path.name,
                'config_id': config_id,
                'config_group_id': config_group_id,
                'taskset_id': 'taskset-{}'.format(task_idx),
                'taskset_hash': 'hash-{}-{}'.format(seed, task_idx),
                'seed_base': seed,
                'taskset_seed': seed + task_idx,
                'normalized_utilization': 0.5,
                'task_idx': task_idx,
                'task_index': task_idx,
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
            'taskset_hash': 'shared-taskset-hash',
            'config_id': runner.config_id(0.5),
            'config_group_id': runner.config_group_id(0.5),
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
    assert set(raw['expected_configured_scheduler']) == set(
        acceptance.ALGORITHMS
    )
    assert raw['observed_configured_scheduler'].isna().all()
    assert raw['configured_scheduler'].isna().all()


def test_multiseed_summary_supports_one_and_multiple_seeds(tmp_path):
    run1 = tmp_path / 'run1'
    run2 = tmp_path / 'run2'
    ratios1 = {'gpfp_asap_block': 1.0}
    ratios2 = {'gpfp_asap_block': 0.0}
    write_acceptance_run(run1, 101, ratios1)
    write_acceptance_run(run2, 202, ratios2)

    _, single = analysis.summarize_multiseed([run1], allow_legacy=True)
    assert single.iloc[0]['num_seeds'] == 1
    assert 0.0 < single.iloc[0]['ci95_low'] < 1.0
    assert single.iloc[0]['ci95_high'] == pytest.approx(1.0)

    by_seed, combined = analysis.summarize_multiseed(
        [run1, run2], allow_legacy=True
    )
    assert len(by_seed) == 2
    assert combined.iloc[0]['num_seeds'] == 2
    assert combined.iloc[0]['total_tasksets'] == 20
    assert combined.iloc[0]['mean_acceptance_ratio'] == 0.5
    assert 0.0 <= combined.iloc[0]['ci95_low'] <= 0.5
    assert 0.5 <= combined.iloc[0]['ci95_high'] <= 1.0


def test_multiseed_uses_pooled_counts_not_mean_of_seed_ratios(tmp_path):
    run1 = tmp_path / 'one-of-one'
    run2 = tmp_path / 'zero-of-nine'
    write_acceptance_run(
        run1, 101, {'gpfp_asap_block': 1.0}, samples=1
    )
    write_acceptance_run(
        run2, 202, {'gpfp_asap_block': 0.0}, samples=9
    )

    by_seed, summary = analysis.summarize_multiseed(
        [run1, run2], allow_legacy=True
    )
    assert set(by_seed['seed_conditional_acceptance']) == {0.0, 1.0}
    row = summary.iloc[0]
    assert row['total_accepted'] == 1
    assert row['total_valid'] == 10
    assert row['pooled_conditional_acceptance'] == pytest.approx(0.1)
    expected = analysis.wilson_interval(1, 10)
    assert row['ci95_low'] == pytest.approx(expected[0])
    assert row['ci95_high'] == pytest.approx(expected[1])


def test_multiseed_reports_no_valid_seed_without_turning_it_into_zero(
        tmp_path):
    valid_run = tmp_path / 'valid-seed'
    error_run = tmp_path / 'error-seed'
    write_acceptance_run(
        valid_run, 101, {'gpfp_asap_block': 1.0}, samples=1
    )
    write_acceptance_run(
        error_run, 202, {'gpfp_asap_block': 0.0}, samples=1
    )
    aggregate = pd.read_csv(error_run / 'acceptance_ratio_data.csv')
    aggregate.loc[0, 'simulation_num_accepted'] = 0
    aggregate.loc[0, 'simulation_num_rejected'] = 0
    aggregate.loc[0, 'simulation_num_valid'] = 0
    aggregate.loc[0, 'simulation_num_error'] = 1
    aggregate.to_csv(
        error_run / 'acceptance_ratio_data.csv', index=False
    )
    raw = pd.read_csv(error_run / 'per_taskset_results.csv')
    raw.loc[0, 'status'] = 'error'
    raw.loc[0, 'accepted'] = 0
    raw.to_csv(error_run / 'per_taskset_results.csv', index=False)

    by_seed, summary = analysis.summarize_multiseed(
        [valid_run, error_run], allow_legacy=True
    )
    assert by_seed['seed_conditional_acceptance'].isna().sum() == 1
    row = summary.iloc[0]
    assert row['num_seeds_requested'] == 2
    assert row['num_seeds_with_valid_simulations'] == 1
    assert row['num_seeds_without_valid_simulations'] == 1
    assert row['pooled_conditional_acceptance'] == 1.0
    assert row['unconditional_success_rate'] == 0.5


def test_config_and_taskset_hashes_are_stable_and_config_sensitive(tmp_path):
    runner = make_runner(tmp_path)
    config = runner.canonical_experiment_config(0.1)
    assert acceptance.stable_config_id(config) == acceptance.stable_config_id(
        runner.canonical_experiment_config(0.10000000000000001)
    )
    original_id = runner.config_id(0.1)
    runner.battery_capacity = 21.0
    assert runner.config_id(0.1) != original_id

    taskset = tmp_path / 'taskset.yml'
    taskset.write_text('taskset: []\n', encoding='utf-8')
    first = acceptance.taskset_file_hash(taskset)
    assert acceptance.taskset_file_hash(taskset) == first
    taskset.write_text('taskset:\n  - name: changed\n', encoding='utf-8')
    assert acceptance.taskset_file_hash(taskset) != first


def test_solar_bytes_and_rta_execution_parameters_change_config_identity(
        tmp_path, monkeypatch):
    solar = tmp_path / 'solar.csv'
    solar.write_bytes(b'time,irradiance\n0,1\n')
    template = tmp_path / 'system.yml'
    template.write_text(
        'energy_management:\n'
        '  use_real_solar_data: true\n'
        '  solar_data_file: "{}"\n'
        '  pv_efficiency: 0.18\n'
        '  pv_area_m2: 1.0\n'
        '  periodic_collection_interval_ms: 1\n'.format(solar),
        encoding='utf-8',
    )
    monkeypatch.setattr(acceptance, 'CONFIG_TEMPLATE', str(template))
    def real_runner(output):
        return acceptance.ExperimentRunner(
            output_dir=output, utilization_points=[0.5],
            num_tasksets=1, task_n=3, task_p_min=10, task_p_max=20,
            simulation_time=100, battery_capacity=20.0,
            initial_energy_ratio=0.5, solar_start_time_ms=123,
            use_real_solar_data=True, system_cores=2, max_workers=1,
            enable_rta=False, seed_base=424242,
        )
    runner = real_runner(tmp_path / 'run-a')
    first_id = runner.config_id(0.5)
    first_group = runner.config_group_id(0.5)
    solar.write_bytes(b'time,irradiance\n0,2\n')
    # This run remains bound to snapshot A.  A newly created run snapshots B
    # and therefore receives a different formal identity.
    assert runner.config_id(0.5) == first_id
    assert runner.config_group_id(0.5) == first_group
    runner = real_runner(tmp_path / 'run-b')
    assert runner.config_id(0.5) != first_id
    assert runner.config_group_id(0.5) != first_group

    timeout_id = runner.config_id(0.5)
    runner.rta_timeout = 999
    assert runner.config_id(0.5) != timeout_id
    profile_id = runner.config_id(0.5)
    runner.profile_rta = True
    assert runner.config_id(0.5) != profile_id

    provenance = acceptance.solar_profile_provenance(template, True)
    assert provenance['present'] is True
    assert provenance['sha256'] == acceptance.taskset_file_hash(solar)
    solar.unlink()
    missing = acceptance.solar_profile_provenance(template, True)
    assert missing['present'] is False
    assert missing['sha256'] == 'missing'


def test_wilson_interval_known_one_of_ten_value():
    low, high = analysis.wilson_interval(1, 10)
    assert low == pytest.approx(0.017876213095072924)
    assert high == pytest.approx(0.4041500267952385)
    assert all(math.isnan(value) for value in analysis.wilson_interval(0, 0))


@pytest.mark.parametrize(
    'statuses,expected',
    [
        (['accepted', 'accepted'], 'duplicate_identical_result'),
        (['accepted', 'error'], 'duplicate_conflicting_status'),
        (['error', 'accepted'], 'duplicate_conflicting_status'),
    ],
)
def test_formal_duplicate_rows_fail_independent_of_order(
        tmp_path, statuses, expected):
    run = tmp_path / ('duplicate-' + '-'.join(statuses))
    run.mkdir()
    rows = []
    for status in statuses:
        rows.append({
            'result_schema_version': 3,
            'source_run_id': run.name,
            'config_id': 'duplicate-config',
            'config_group_id': 'duplicate-group',
            'taskset_id': 'taskset-0',
            'taskset_hash': 'duplicate-hash',
            'algorithm': 'gpfp_asap_block',
            'normalized_utilization': 0.5,
            'status': status,
            'accepted': int(status == 'accepted'),
        })
    pd.DataFrame(rows).to_csv(run / 'per_taskset_results.csv', index=False)
    with pytest.raises(ValueError, match=expected):
        analysis.load_raw_runs([run], allow_legacy=True)


def test_same_config_taskset_id_with_different_hash_fails(tmp_path):
    run = tmp_path / 'hash-conflict'
    run.mkdir()
    rows = []
    for algorithm, task_hash in [
        ('gpfp_asap_block', 'hash-a'),
        ('gpfp_asap_nonblock', 'hash-b'),
    ]:
        rows.append({
            'result_schema_version': 3,
            'source_run_id': run.name,
            'config_id': 'same-config',
            'config_group_id': 'same-group',
            'taskset_id': 'same-display-id',
            'taskset_hash': task_hash,
            'algorithm': algorithm,
            'normalized_utilization': 0.5,
            'status': 'accepted',
            'accepted': 1,
        })
    pd.DataFrame(rows).to_csv(run / 'per_taskset_results.csv', index=False)
    with pytest.raises(ValueError, match='duplicate_conflicting_metadata'):
        analysis.load_raw_runs([run], allow_legacy=True)


def test_schema_v2_provenance_is_strict_and_legacy_is_explicit(tmp_path):
    run = tmp_path / 'legacy'
    run.mkdir()
    pd.DataFrame([{
        'algorithm': 'gpfp_asap_block',
        'algorithm_display_name': 'ASAP-Block',
        'normalized_utilization': 0.5,
        'acceptance_ratio': 1.0,
        'num_samples': 1,
        'num_successful': 1,
        'seed_base': 1,
    }]).to_csv(run / 'acceptance_ratio_data.csv', index=False)
    pd.DataFrame([{
        'algorithm': 'gpfp_asap_block',
        'normalized_utilization': 0.5,
        'task_idx': 0,
        'accepted': 1,
        'status': 'accepted',
    }]).to_csv(run / 'per_taskset_results.csv', index=False)

    with pytest.raises(ValueError, match='missing_result_files'):
        analysis.acceptance_by_seed([run])
    with pytest.warns(RuntimeWarning, match='legacy result provenance'):
        result = analysis.acceptance_by_seed(
            [run], allow_legacy=True
        )
    assert result.iloc[0]['acceptance_ratio'] == 1.0


@pytest.mark.parametrize('script', [
    'scripts/ablation_analysis.py',
    'scripts/paired_scheduler_comparison.py',
    'scripts/select_mechanism_cases.py',
    'scripts/run_scheduler_diversity_audit.py',
])
def test_formal_analyzer_clis_reject_orphan_csv_without_outputs(
        tmp_path, script):
    run = tmp_path / 'orphan-run'
    write_acceptance_run(
        run, 1, {'gpfp_asap_block': 1.0}, samples=1
    )
    # Complete the filename set while deliberately omitting both the sidecar
    # and execution manifest.
    pd.DataFrame([{
        'result_schema_version': 3, 'run_id': 'orphan',
        'config_id': 'config-1',
        'config_group_id': 'compatible-default-config',
        'algorithm': 'gpfp_asap_block',
    }]).to_csv(
        run / 'common_complete_acceptance_data.csv', index=False
    )
    output = tmp_path / ('output-' + Path(script).stem)
    command = [
        sys.executable, str(PROJECT_ROOT / script),
        '--runs', str(run), '--output-dir', str(output),
    ]
    if script.endswith('run_scheduler_diversity_audit.py'):
        command.append('--dry-run')
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, capture_output=True, text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert 'missing_resume_provenance' in completed.stderr
    if output.exists():
        assert not [
            path for path in output.rglob('*')
            if path.is_file() and path.suffix in {'.csv', '.png', '.pdf'}
        ]


def test_unattested_analyzer_opt_in_is_diagnostic_only(tmp_path):
    run = tmp_path / 'diagnostic-run'
    write_acceptance_run(
        run, 1, {'gpfp_asap_block': 1.0}, samples=1
    )
    output = tmp_path / 'diagnostic-output'
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / 'scripts/ablation_analysis.py'),
         '--runs', str(run), '--output-dir', str(output), '--allow-legacy'],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    diagnostic = output / 'diagnostic_unattested'
    files = [path for path in diagnostic.iterdir() if path.is_file()]
    assert files
    assert all(path.name.startswith('diagnostic_') for path in files)
    metadata = json.loads((
        diagnostic / 'diagnostic_metadata.json'
    ).read_text(encoding='utf-8'))
    assert metadata['official'] is False
    assert not [path for path in output.iterdir() if path.is_file()]


def test_incomplete_common_sample_suppresses_all_formal_figures(tmp_path):
    output = tmp_path / 'formal-output'
    figures = output / 'figures'
    figures.mkdir(parents=True)
    formal_paths = [
        output / 'acceptance_ratio_all.png',
        output / 'acceptance_ratio_all.pdf',
        output / 'acceptance_ratio_figure.png',
        figures / 'acceptance_ratio_asap.png',
    ]
    for path in formal_paths:
        path.write_text('stale', encoding='utf-8')
    frame = pd.DataFrame([{
        'official_run_valid': False,
        'official_run_invalid': True,
    }])
    assert acceptance.suppress_formal_figures_for_incomplete_common_sample(
        frame, True, output
    )
    assert not any(path.exists() for path in formal_paths)


def test_require_common_complete_exits_two_before_any_formal_plot(
        tmp_path, monkeypatch):
    output = tmp_path / 'invalid-common-run'

    def fake_run_experiments(self):
        results = defaultdict(lambda: defaultdict(list))
        utilization = 0.5
        for algorithm in acceptance.ALGORITHMS:
            status = 'error' if algorithm == 'gpfp_st_sync' else 'accepted'
            results[algorithm][utilization].append({
                'result_schema_version': 3,
                'algorithm': algorithm,
                'config_id': self.config_id(utilization),
                'config_group_id': self.config_group_id(utilization),
                'taskset_id': 'taskset-0',
                'taskset_hash': 'shared-taskset-hash',
                'task_idx': 0,
                'simulation_status': status,
                'acceptance_ratio': 1.0 if status == 'accepted' else math.nan,
                'rta_enabled': False,
                'rta_status': 'disabled',
            })
        return results

    monkeypatch.setattr(
        acceptance.ExperimentRunner,
        'run_experiments',
        fake_run_experiments,
    )
    monkeypatch.setattr(
        acceptance.FigureGenerator,
        'plot_all_groups',
        lambda *args, **kwargs: pytest.fail('formal group plot was called'),
    )
    monkeypatch.setattr(
        acceptance.FigureGenerator,
        'plot_acceptance_ratio',
        lambda *args, **kwargs: pytest.fail('formal main plot was called'),
    )
    monkeypatch.setattr(sys, 'argv', [
        'acceptance_ratio_test.py',
        '--run-experiment',
        '--output-dir', str(output),
        '--fixed-utilization', '0.5',
        '--num-tasksets', '1',
        '--M', '1',
        '--require-common-complete',
    ])

    with pytest.raises(SystemExit) as exit_info:
        acceptance.main()
    assert exit_info.value.code == 2
    diagnostic = pd.read_csv(
        output / 'common_complete_acceptance_data.csv'
    )
    assert not diagnostic['official_run_valid'].astype(bool).any()
    assert not (output / 'acceptance_ratio_all.png').exists()
    assert not (output / 'acceptance_ratio_figure.png').exists()
    assert not (output / 'figures').exists()


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
                'result_schema_version': 3,
                'source_run_id': run.name,
                'config_id': 'paired-config',
                'config_group_id': 'paired-group',
                'taskset_id': 'taskset-{}'.format(task_idx),
                'taskset_hash': 'paired-hash-{}'.format(task_idx),
                'seed_base': 1,
                'taskset_seed': 100 + task_idx,
                'normalized_utilization': 0.5,
                'task_idx': task_idx,
                'algorithm': algorithm,
                'accepted': is_ok,
                'status': 'accepted' if is_ok else 'rejected',
            })
    pd.DataFrame(rows).to_csv(run / 'per_taskset_results.csv', index=False)

    result = analysis.paired_comparison([run], allow_legacy=True).iloc[0]
    assert result['both_accepted'] == 1
    assert result['baseline_only_accepted'] == 1
    assert result['other_only_accepted'] == 1
    assert result['both_rejected'] == 1
    assert result['num_paired_tasksets'] == 4
    assert result['net_win'] == 0


def test_conditional_multirun_uses_valid_counts_not_requested_weights(tmp_path):
    run_a = tmp_path / 'run_a'
    run_b = tmp_path / 'run_b'
    write_acceptance_run(run_a, 7, {'gpfp_asap_block': 1.0}, samples=1)
    write_acceptance_run(run_b, 8, {'gpfp_asap_block': 0.0}, samples=1)

    aggregate_a = pd.read_csv(run_a / 'acceptance_ratio_data.csv')
    aggregate_a.loc[0, 'num_samples'] = 2
    aggregate_a.loc[0, 'simulation_num_requested'] = 2
    aggregate_a.loc[0, 'simulation_num_error'] = 1
    aggregate_a.to_csv(run_a / 'acceptance_ratio_data.csv', index=False)
    raw_a = pd.read_csv(run_a / 'per_taskset_results.csv')
    raw_a = pd.concat([raw_a, pd.DataFrame([{
        'result_schema_version': 3,
        'source_run_id': run_a.name,
        'config_id': 'config-7',
        'config_group_id': 'compatible-default-config',
        'taskset_id': 'taskset-99',
        'taskset_hash': 'hash-7-99',
        'seed_base': 7,
        'normalized_utilization': 0.5,
        'task_idx': 99,
        'algorithm': 'gpfp_asap_block',
        'accepted': 0,
        'status': 'error',
    }])], ignore_index=True, sort=False)
    raw_a.to_csv(run_a / 'per_taskset_results.csv', index=False)

    _, summary = analysis.summarize_multiseed(
        [run_a, run_b], allow_legacy=True
    )
    result = summary.iloc[0]
    assert result['pooled_conditional_acceptance'] == 0.5
    assert result['unconditional_success_rate'] == pytest.approx(1 / 3)
    assert result['total_valid'] == 2
    assert result['total_requested'] == 3


def test_legacy_counts_subtract_explicit_infrastructure_failures(tmp_path):
    run = tmp_path / 'legacy_with_error_counts'
    write_acceptance_run(
        run, 17, {'gpfp_asap_block': 0.5}, samples=2
    )
    aggregate = pd.read_csv(run / 'acceptance_ratio_data.csv')
    aggregate = aggregate.drop(columns=[
        'simulation_num_rejected', 'simulation_num_valid',
    ])
    aggregate.loc[0, 'simulation_num_error'] = 1
    aggregate.loc[0, 'simulation_num_timeout'] = 0
    aggregate.to_csv(run / 'acceptance_ratio_data.csv', index=False)

    raw = pd.read_csv(run / 'per_taskset_results.csv')
    raw.loc[raw.index[-1], 'status'] = 'error'
    raw.loc[raw.index[-1], 'accepted'] = 0
    raw.to_csv(run / 'per_taskset_results.csv', index=False)

    result = analysis.acceptance_by_seed([run], allow_legacy=True).iloc[0]
    assert result['num_accepted'] == 1
    assert result['num_rejected'] == 0
    assert result['num_error'] == 1
    assert result['per_scheduler_conditional_acceptance_ratio'] == 1.0
    assert result['unconditional_success_rate'] == 0.5


def test_no_valid_multirun_keeps_nan(tmp_path):
    run = tmp_path / 'no_valid'
    run.mkdir()
    pd.DataFrame([{
        'result_schema_version': 3,
        'source_run_id': run.name,
        'config_id': 'no-valid-config',
        'config_group_id': 'no-valid-group',
        'algorithm': 'gpfp_asap_block',
        'algorithm_display_name': 'ASAP-Block',
        'normalized_utilization': 0.5,
        'seed_base': 8,
        'acceptance_ratio': math.nan,
        'num_samples': 2,
        'num_successful': 0,
        'simulation_num_accepted': 0,
        'simulation_num_rejected': 0,
        'simulation_num_valid': 0,
        'simulation_num_requested': 2,
        'simulation_num_error': 2,
        'simulation_num_timeout': 0,
    }]).to_csv(run / 'acceptance_ratio_data.csv', index=False)
    pd.DataFrame([
        {'result_schema_version': 3, 'source_run_id': run.name,
         'config_id': 'no-valid-config', 'config_group_id': 'no-valid-group',
         'taskset_id': 'taskset-{}'.format(index),
         'taskset_hash': 'no-valid-hash-{}'.format(index),
         'algorithm': 'gpfp_asap_block', 'normalized_utilization': 0.5,
         'seed_base': 8, 'task_idx': index, 'accepted': 0, 'status': 'error'}
        for index in range(2)
    ]).to_csv(run / 'per_taskset_results.csv', index=False)

    result = analysis.acceptance_by_seed([run], allow_legacy=True).iloc[0]
    assert math.isnan(result['acceptance_ratio'])
    assert result['no_valid_simulations']

    battery = analysis.summarize_battery(
        [(run, 20.0)], allow_legacy=True
    ).iloc[0]
    assert battery['num_seeds'] == 1
    assert battery['num_valid_seeds'] == 0
    assert math.isnan(battery['mean_acceptance_ratio'])
    assert math.isnan(battery['ci95_low'])
    assert math.isnan(battery['ci95_high'])


def test_paired_comparison_excludes_error_instead_of_counting_win(tmp_path):
    run = tmp_path / 'paired_error'
    run.mkdir()
    pd.DataFrame([
        {'result_schema_version': 3, 'source_run_id': run.name,
         'config_id': 'paired-error-config',
         'config_group_id': 'paired-error-group',
         'taskset_id': 'taskset-0', 'taskset_hash': 'paired-error-hash',
         'seed_base': 1, 'normalized_utilization': 0.5, 'task_idx': 0,
         'algorithm': 'gpfp_asap_block', 'accepted': 1,
         'status': 'accepted'},
        {'result_schema_version': 3, 'source_run_id': run.name,
         'config_id': 'paired-error-config',
         'config_group_id': 'paired-error-group',
         'taskset_id': 'taskset-0', 'taskset_hash': 'paired-error-hash',
         'seed_base': 1, 'normalized_utilization': 0.5, 'task_idx': 0,
         'algorithm': 'gpfp_asap_nonblock', 'accepted': 0,
         'status': 'error'},
    ]).to_csv(run / 'per_taskset_results.csv', index=False)

    result = analysis.paired_comparison([run], allow_legacy=True).iloc[0]
    assert result['common_valid_tasksets'] == 0
    assert result['A_only_accepts'] == 0
    assert result['excluded_error'] == 1


def test_common_complete_excludes_any_scheduler_error(tmp_path):
    runner = make_runner(tmp_path)
    runner.num_tasksets = 3
    results = defaultdict(lambda: defaultdict(list))
    for algorithm in acceptance.ALGORITHMS:
        statuses = ['accepted', 'accepted', 'rejected']
        if algorithm == 'gpfp_st_sync':
            statuses[1] = 'error'
        for task_idx, status in enumerate(statuses):
            results[algorithm][0.5].append({
                'algorithm': algorithm,
                'config_id': runner.config_id(0.5),
                'config_group_id': runner.config_group_id(0.5),
                'taskset_hash': 'common-hash-{}'.format(task_idx),
                'taskset_id': 'taskset-{}'.format(task_idx),
                'task_idx': task_idx,
                'simulation_status': status,
                'acceptance_ratio': (
                    1.0 if status == 'accepted'
                    else 0.0 if status == 'rejected' else math.nan
                ),
            })

    aggregate = runner.aggregate_results(results)
    assert set(aggregate['common_complete_num_tasksets']) == {2}
    assert set(aggregate['common_complete_excluded_num']) == {1}
    assert set(aggregate['common_complete_excluded_error']) == {1}
    assert set(aggregate['common_complete_acceptance_ratio']) == {0.5}
    assert aggregate['official_run_invalid'].all()


def test_common_complete_equals_conditional_when_all_nine_are_valid(tmp_path):
    runner = make_runner(tmp_path)
    runner.num_tasksets = 2
    results = defaultdict(lambda: defaultdict(list))
    for algorithm in acceptance.ALGORITHMS:
        for task_idx, status in enumerate(['accepted', 'rejected']):
            results[algorithm][0.5].append({
                'algorithm': algorithm,
                'config_id': runner.config_id(0.5),
                'config_group_id': runner.config_group_id(0.5),
                'taskset_hash': 'complete-hash-{}'.format(task_idx),
                'taskset_id': 'taskset-{}'.format(task_idx),
                'task_idx': task_idx,
                'simulation_status': status,
                'acceptance_ratio': float(status == 'accepted'),
            })

    aggregate = runner.aggregate_results(results)
    assert (aggregate['common_complete_acceptance_ratio'] ==
            aggregate['acceptance_ratio']).all()
    assert set(aggregate['common_complete_num_tasksets']) == {2}
    assert not aggregate['official_run_invalid'].any()


def test_common_complete_duplicate_fails_instead_of_last_wins(tmp_path):
    runner = make_runner(tmp_path)
    results = defaultdict(lambda: defaultdict(list))
    for algorithm in acceptance.ALGORITHMS:
        row = {
            'algorithm': algorithm,
            'config_id': runner.config_id(0.5),
            'config_group_id': runner.config_group_id(0.5),
            'taskset_id': 'taskset-0',
            'taskset_hash': 'same-hash',
            'task_idx': 0,
            'simulation_status': 'accepted',
            'acceptance_ratio': 1.0,
        }
        results[algorithm][0.5].append(row)
    results['gpfp_asap_block'][0.5].append(dict(
        results['gpfp_asap_block'][0.5][0]
    ))

    with pytest.raises(
        acceptance.DuplicateResultError,
        match='duplicate_identical_result',
    ):
        runner.common_complete_summaries(results)


def test_ablation_outputs_select_expected_algorithms(tmp_path):
    run = tmp_path / 'run'
    algorithms = set(analysis.TIMING_BLOCK + analysis.ASAP_SEMANTICS)
    write_acceptance_run(run, 1, {algorithm: 0.5 for algorithm in algorithms})
    outputs = analysis.write_ablations(
        [run], tmp_path / 'out', allow_legacy=True
    )

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

    summary = analysis.summarize_battery(
        [(run5, 5), (run20, 20)], allow_legacy=True
    )
    assert set(summary['battery']) == {5.0, 20.0}
    assert set(summary['num_seeds']) == {1}


def test_mechanism_case_selection_finds_baseline_win(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    rows = []
    for algorithm in analysis.DISPLAY_NAMES:
        is_ok = algorithm != 'gpfp_asap_nonblock'
        rows.append({
            'result_schema_version': 3,
            'source_run_id': run.name,
            'config_id': 'mechanism-config',
            'config_group_id': 'mechanism-group',
            'taskset_id': 'taskset-0',
            'taskset_hash': 'mechanism-hash',
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

    cases = analysis.select_cases([run], allow_legacy=True)
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
