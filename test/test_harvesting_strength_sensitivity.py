import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest
import yaml


os.environ.setdefault('MPLBACKEND', 'Agg')
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance
from energy_manager import EnergyConfig, EnergyHarvester
from scripts import analyze_harvesting_strength_sensitivity as analyzer
from scripts import experiment_runner
from scripts import experiment_analysis as analysis
from scripts import run_harvesting_strength_sensitivity as runner


def write_fake_run(run_dir, seed, accepted, scale=1.0):
    run_dir.mkdir(parents=True)
    pd.DataFrame([{
        'result_schema_version': 3,
        'source_run_id': run_dir.name,
        'config_id': 'config-{}-{}'.format(scale, seed),
        'config_group_id': 'harvesting-scale-{}'.format(scale),
        'taskset_id': 'taskset-0',
        'taskset_hash': 'hash-{}'.format(seed),
        'seed_base': seed,
        'normalized_utilization': 0.5,
        'algorithm': 'gpfp_asap_block',
        'accepted': accepted,
        'status': 'accepted' if accepted else 'rejected',
    }]).to_csv(run_dir / 'per_taskset_results.csv', index=False)
    pd.DataFrame([{
        'result_schema_version': 3,
        'source_run_id': run_dir.name,
        'config_id': 'config-{}-{}'.format(scale, seed),
        'config_group_id': 'harvesting-scale-{}'.format(scale),
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
    }]).to_csv(run_dir / 'acceptance_ratio_data.csv', index=False)


def test_acceptance_cli_defaults_to_unscaled_harvesting():
    parser = argparse.ArgumentParser()
    acceptance.add_experiment_cli_args(parser)
    args = parser.parse_args([])
    assert args.harvesting_scale == 1.0
    assert '--harvesting-scale' in parser.format_help()


def test_default_and_explicit_scale_one_preserve_synthetic_rate():
    default_config = EnergyConfig()
    explicit_config = EnergyConfig()
    explicit_config.harvesting_scale = 1.0
    half_config = EnergyConfig()
    half_config.harvesting_scale = 0.5

    default_rate = EnergyHarvester(default_config).get_harvesting_rate(43200000)
    explicit_rate = EnergyHarvester(explicit_config).get_harvesting_rate(43200000)
    half_rate = EnergyHarvester(half_config).get_harvesting_rate(43200000)

    assert default_config.harvesting_scale == 1.0
    assert explicit_rate == default_rate
    assert half_rate == default_rate * 0.5


def test_scale_does_not_change_real_harvesting_loader_rate():
    class FakeSolarLoader:
        def get_harvesting_rate(self, _time, _efficiency, _area):
            return 0.0125

    config = EnergyConfig()
    config.harvesting_scale = 2.0
    harvester = EnergyHarvester(config)
    harvester.solar_loader = FakeSolarLoader()
    assert harvester.get_harvesting_rate(0) == 0.0125


def test_shared_command_default_passes_scale_one():
    command = experiment_runner.build_command(
        'out', 1, 1, 1, 2, 20, 1, 0, 1
    )
    index = command.index('--harvesting-scale')
    assert command[index + 1] == '1.0'


def test_runner_writes_scale_to_config_and_result_metadata(tmp_path):
    experiment = acceptance.ExperimentRunner(
        output_dir=tmp_path / 'run',
        utilization_points=[0.5],
        num_tasksets=1,
        task_n=2,
        task_p_min=10,
        task_p_max=20,
        simulation_time=100,
        battery_capacity=20.0,
        initial_energy_ratio=1.0,
        solar_start_time_ms=1234,
        use_real_solar_data=False,
        system_cores=2,
        max_workers=1,
        harvesting_scale=0.25,
    )
    config_path = experiment.modify_config('gpfp_asap_block')
    config_text = Path(config_path).read_text(encoding='utf-8')
    assert 'harvesting_scale: 0.25' in config_text
    config = yaml.safe_load(config_text)
    assert config['energy_management']['base_harvesting_rate'] == 0.0135

    result = {
        'algorithm': 'gpfp_asap_block',
        'acceptance_ratio': 1.0,
        'simulation_status': 'accepted',
        'config_id': experiment.config_id(0.5),
        'config_group_id': experiment.config_group_id(0.5),
        'taskset_id': 'taskset-0',
        'taskset_hash': 'taskset-hash-0',
        'task_idx': 0,
    }
    raw_row = experiment._per_taskset_result_row(
        'gpfp_asap_block', 0.5, result
    )
    assert raw_row['harvesting_scale'] == 0.25

    results = defaultdict(lambda: defaultdict(list))
    results['gpfp_asap_block'][0.5].append(result)
    aggregate = experiment.aggregate_results(results)
    assert aggregate.iloc[0]['harvesting_scale'] == 0.25


def test_generated_cpp_synthetic_rate_changes_with_scale(tmp_path):
    def generated_energy_config(scale, use_real_solar_data=False):
        experiment = acceptance.ExperimentRunner(
            output_dir=tmp_path / 'scale-{}'.format(scale),
            utilization_points=[0.5], num_tasksets=1, task_n=2,
            task_p_min=10, task_p_max=20, simulation_time=100,
            battery_capacity=10.0, initial_energy_ratio=0.25,
            solar_start_time_ms=21975000,
            use_real_solar_data=use_real_solar_data,
            system_cores=2, max_workers=1, harvesting_scale=scale,
        )
        config_path = experiment.modify_config('gpfp_asap_block')
        return yaml.safe_load(Path(config_path).read_text(encoding='utf-8'))[
            'energy_management'
        ]

    scale_zero = generated_energy_config(0.0)
    scale_four = generated_energy_config(4.0)
    real_scale_four = generated_energy_config(4.0, use_real_solar_data=True)

    assert scale_zero['base_harvesting_rate'] == 0.0
    assert scale_four['base_harvesting_rate'] == 0.216
    assert scale_zero['base_harvesting_rate'] != scale_four[
        'base_harvesting_rate'
    ]
    assert real_scale_four['base_harvesting_rate'] == 0.054
    for field in ('initial_energy', 'max_energy', 'time_of_day_ms'):
        assert scale_zero[field] == scale_four[field]


def test_strength_runner_dry_run_writes_scale_seed_product(tmp_path):
    output_root = tmp_path / 'runs'
    with mock.patch.object(experiment_runner.subprocess, 'run') as run_mock:
        rows = runner.main([
            '--output-root', str(output_root),
            '--experiment-name', 'strength',
            '--harvesting-scales', '0.25', '1.0',
            '--seeds', '11', '22',
            '--num-points', '1', '--num-tasksets', '1', '--task-n', '2',
            '--battery', '20', '--initial-energy', '1',
            '--solar-time-ms', '1234', '--max-workers', '1', '--dry-run',
        ])

    run_mock.assert_not_called()
    assert len(rows) == 4
    with (output_root / 'strength_manifest.csv').open(newline='') as handle:
        manifest = list(csv.DictReader(handle))
    assert {(row['harvesting_scale'], row['seed_base']) for row in manifest} == {
        ('0.25', '11'), ('0.25', '22'), ('1.0', '11'), ('1.0', '22'),
    }
    assert {Path(row['run_dir']).name for row in manifest} == {
        'strength-hscale0p25-seed11', 'strength-hscale0p25-seed22',
        'strength-hscale1-seed11', 'strength-hscale1-seed22',
    }
    assert {row['status'] for row in manifest} == {'dry_run'}


def test_strength_analyzer_groups_by_harvesting_scale(tmp_path):
    runs = []
    manifest_rows = []
    for scale, seed, accepted in [
        (0.5, 11, 0), (0.5, 22, 1), (1.0, 11, 1), (1.0, 22, 1),
    ]:
        run_dir = tmp_path / 'run-{}-{}'.format(scale, seed)
        write_fake_run(run_dir, seed, accepted, scale=scale)
        runs.append(run_dir)
        manifest_rows.append({
            'run_dir': str(run_dir),
            'harvesting_scale': scale,
            'seed_base': seed,
            'harvesting_profile': 'synthetic_piecewise',
            'status': 'completed',
        })
    manifest = tmp_path / 'manifest.csv'
    pd.DataFrame(manifest_rows).to_csv(manifest, index=False)

    output = tmp_path / 'analysis'
    by_seed, summary = analyzer.write_harvesting_strength_outputs(
        manifest, output, allow_legacy=True
    )

    assert len(by_seed) == 4
    assert len(summary) == 2
    assert set(summary['num_valid_seeds']) == {2}
    means = {
        row.harvesting_scale: row.mean_acceptance_ratio
        for row in summary.itertuples()
    }
    assert means == {0.5: 0.5, 1.0: 1.0}
    assert (output / 'harvesting_strength_sensitivity_by_seed.csv').is_file()
    assert (output / 'harvesting_strength_sensitivity_summary.csv').is_file()
    assert (output / 'harvesting_strength_sensitivity_plot.png').is_file()


def test_strength_analyzer_pools_accepted_and_valid_counts(tmp_path):
    accepted_run = tmp_path / 'strength-accepted'
    rejected_run = tmp_path / 'strength-rejected'
    write_fake_run(accepted_run, seed=11, accepted=1, scale=0.5)
    write_fake_run(rejected_run, seed=22, accepted=0, scale=0.5)

    raw = pd.read_csv(rejected_run / 'per_taskset_results.csv')
    rows = []
    for index in range(9):
        row = raw.iloc[0].to_dict()
        row['taskset_id'] = 'taskset-{}'.format(index)
        row['taskset_hash'] = 'hash-22-{}'.format(index)
        row['task_idx'] = index
        rows.append(row)
    pd.DataFrame(rows).to_csv(
        rejected_run / 'per_taskset_results.csv', index=False
    )
    aggregate = pd.read_csv(
        rejected_run / 'acceptance_ratio_data.csv'
    )
    aggregate.loc[0, 'num_samples'] = 9
    aggregate.loc[0, 'num_successful'] = 0
    aggregate.loc[0, 'simulation_num_rejected'] = 9
    aggregate.loc[0, 'simulation_num_valid'] = 9
    aggregate.loc[0, 'simulation_num_requested'] = 9
    aggregate.to_csv(
        rejected_run / 'acceptance_ratio_data.csv', index=False
    )
    manifest = tmp_path / 'pooled-manifest.csv'
    pd.DataFrame([
        {'run_dir': str(accepted_run), 'harvesting_scale': 0.5,
         'seed_base': 11, 'harvesting_profile': 'synthetic_piecewise'},
        {'run_dir': str(rejected_run), 'harvesting_scale': 0.5,
         'seed_base': 22, 'harvesting_profile': 'synthetic_piecewise'},
    ]).to_csv(manifest, index=False)

    _, summary = analyzer.summarize_harvesting_strength(
        manifest, allow_legacy=True
    )
    row = summary.iloc[0]
    assert row['total_accepted'] == 1
    assert row['total_valid'] == 10
    assert row['mean_acceptance_ratio'] == 0.1
    assert row['ci95_low'] == pytest.approx(
        analysis.wilson_interval(1, 10)[0]
    )


def test_strength_analyzer_preserves_no_valid_as_nan(tmp_path):
    run_dir = tmp_path / 'no-valid-run'
    write_fake_run(run_dir, seed=33, accepted=0)
    aggregate_path = run_dir / 'acceptance_ratio_data.csv'
    aggregate = pd.read_csv(aggregate_path)
    aggregate.loc[0, 'simulation_num_accepted'] = 0
    aggregate.loc[0, 'simulation_num_rejected'] = 0
    aggregate.loc[0, 'simulation_num_valid'] = 0
    aggregate.loc[0, 'simulation_num_requested'] = 1
    aggregate.loc[0, 'simulation_num_error'] = 1
    aggregate.loc[0, 'simulation_num_timeout'] = 0
    aggregate.to_csv(aggregate_path, index=False)
    raw_path = run_dir / 'per_taskset_results.csv'
    raw = pd.read_csv(raw_path)
    raw.loc[0, 'status'] = 'error'
    raw.loc[0, 'accepted'] = 0
    raw.to_csv(raw_path, index=False)
    manifest = tmp_path / 'manifest.csv'
    pd.DataFrame([{
        'run_dir': str(run_dir),
        'harvesting_scale': 1.0,
        'seed_base': 33,
        'harvesting_profile': 'synthetic_piecewise',
        'status': 'completed',
    }]).to_csv(manifest, index=False)

    _, summary = analyzer.summarize_harvesting_strength(
        manifest, allow_legacy=True
    )

    row = summary.iloc[0]
    assert row['num_seeds'] == 1
    assert row['num_valid_seeds'] == 0
    assert pd.isna(row['mean_acceptance_ratio'])
    assert pd.isna(row['ci95_low'])
    assert pd.isna(row['ci95_high'])
