import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path
from unittest import mock

import pandas as pd
import yaml


os.environ.setdefault('MPLBACKEND', 'Agg')
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance
from energy_manager import EnergyConfig, EnergyHarvester
from scripts import analyze_harvesting_strength_sensitivity as analyzer
from scripts import experiment_runner
from scripts import run_harvesting_strength_sensitivity as runner


def write_fake_run(run_dir, seed, accepted):
    run_dir.mkdir(parents=True)
    pd.DataFrame([{
        'seed_base': seed,
        'normalized_utilization': 0.5,
        'algorithm': 'gpfp_asap_block',
        'accepted': accepted,
        'status': 'accepted' if accepted else 'rejected',
    }]).to_csv(run_dir / 'per_taskset_results.csv', index=False)
    pd.DataFrame([{
        'algorithm': 'gpfp_asap_block',
        'algorithm_display_name': 'ASAP-Block',
        'normalized_utilization': 0.5,
        'acceptance_ratio': float(accepted),
        'num_samples': 1,
        'num_successful': accepted,
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

    result = {'acceptance_ratio': 1.0, 'simulation_status': 'accepted'}
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
        write_fake_run(run_dir, seed, accepted)
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
        manifest, output
    )

    assert len(by_seed) == 4
    assert len(summary) == 2
    means = {
        row.harvesting_scale: row.mean_acceptance_ratio
        for row in summary.itertuples()
    }
    assert means == {0.5: 0.5, 1.0: 1.0}
    assert (output / 'harvesting_strength_sensitivity_by_seed.csv').is_file()
    assert (output / 'harvesting_strength_sensitivity_summary.csv').is_file()
    assert (output / 'harvesting_strength_sensitivity_plot.png').is_file()
