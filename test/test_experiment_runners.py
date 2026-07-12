import csv
import os
import subprocess
import sys
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance
from scripts import experiment_runner
from scripts import experiment_analysis
from scripts import run_battery_sensitivity
from scripts import run_multiseed_acceptance
from scripts import run_rta_e0_sensitivity


def read_manifest(path):
    with Path(path).open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def base_args(output_root, name):
    return [
        '--output-root', str(output_root),
        '--experiment-name', name,
        '--num-points', '2',
        '--num-tasksets', '3',
        '--task-n', '4',
        '--initial-energy', '1.0',
        '--solar-time-ms', '1234',
        '--max-workers', '2',
        '--no-group-figures',
    ]


def successful_process():
    return SimpleNamespace(returncode=0)


def fake_successful_subprocess(command, **_kwargs):
    output = Path(command[command.index('--output-dir') + 1])
    rta_path = PROJECT_ROOT / 'asap_block_rta.py' if '--enable-rta' in command else None
    _write_resumable_run(output, command, rta_path=rta_path)
    return successful_process()


def test_multiseed_runner_builds_two_commands_and_manifest(tmp_path):
    output_root = tmp_path / 'runs'
    args = base_args(output_root, 'multi') + [
        '--seeds', '11', '22', '--battery', '20',
    ]
    with mock.patch.object(
        experiment_runner.subprocess, 'run', side_effect=fake_successful_subprocess
    ) as run_mock:
        rows = run_multiseed_acceptance.main(args)

    assert run_mock.call_count == 2
    commands = [call.args[0] for call in run_mock.call_args_list]
    assert [command[command.index('--seed-base') + 1] for command in commands] == [
        '11', '22',
    ]
    assert all('--enable-rta' not in command for command in commands)
    assert all('--require-common-complete' in command for command in commands)
    manifest = output_root / 'multi_manifest.csv'
    manifest_rows = read_manifest(manifest)
    assert len(manifest_rows) == 2
    assert {row['status'] for row in manifest_rows} == {'completed'}
    assert {row['rta_enabled'] for row in manifest_rows} == {'False'}
    assert all(row['run_id'] for row in manifest_rows)
    assert all(len(row['command_sha256']) == 64 for row in manifest_rows)
    assert len({row['run_id'] for row in manifest_rows}) == 2
    assert len(rows) == 2


def test_battery_runner_builds_cartesian_product(tmp_path):
    output_root = tmp_path / 'runs'
    args = base_args(output_root, 'battery') + [
        '--batteries', '5', '20', '--seeds', '11', '22',
    ]
    with mock.patch.object(
        experiment_runner.subprocess, 'run', side_effect=fake_successful_subprocess
    ) as run_mock:
        run_battery_sensitivity.main(args)

    assert run_mock.call_count == 4
    commands = [call.args[0] for call in run_mock.call_args_list]
    batteries = [command[command.index('--battery') + 1] for command in commands]
    assert batteries == ['5.0', '5.0', '20.0', '20.0']
    manifest_rows = read_manifest(output_root / 'battery_manifest.csv')
    assert len(manifest_rows) == 4
    assert {row['battery'] for row in manifest_rows} == {'5.0', '20.0'}


def test_rta_e0_runner_adds_required_rta_options(tmp_path):
    output_root = tmp_path / 'runs'
    args = base_args(output_root, 'rta-e0') + [
        '--e0-values', '0', '0.25',
        '--seed-base', '99',
        '--battery', '20',
        '--rta-horizon-ms', '30000',
        '--rta-timeout', '300',
    ]
    with mock.patch.object(
        experiment_runner.subprocess, 'run', side_effect=fake_successful_subprocess
    ) as run_mock:
        run_rta_e0_sensitivity.main(args)

    assert run_mock.call_count == 2
    commands = [call.args[0] for call in run_mock.call_args_list]
    for command in commands:
        assert '--enable-rta' in command
        assert '--profile-rta' in command
        assert '--rta-assume-no-overflow' in command
    assert [
        command[command.index('--rta-initial-energy') + 1]
        for command in commands
    ] == ['0.0', '0.25']
    assert commands[0][commands[0].index('--initial-energy') + 1] == '1.0'
    manifest_rows = read_manifest(output_root / 'rta-e0_manifest.csv')
    assert {row['E0'] for row in manifest_rows} == {'0.0', '0.25'}
    assert {row['rta_version'] for row in manifest_rows} == {'v20.4'}


def test_dry_run_writes_manifest_without_subprocess(tmp_path):
    output_root = tmp_path / 'runs'
    args = base_args(output_root, 'dry') + [
        '--seeds', '11', '22', '--battery', '20', '--dry-run',
    ]
    with mock.patch.object(experiment_runner.subprocess, 'run') as run_mock:
        run_multiseed_acceptance.main(args)

    run_mock.assert_not_called()
    rows = read_manifest(output_root / 'dry_manifest.csv')
    assert len(rows) == 2
    assert {row['status'] for row in rows} == {'dry_run'}


def test_skip_existing_rejects_legacy_files_without_provenance(tmp_path):
    output_root = tmp_path / 'runs'
    run_dir = output_root / 'resume-seed11'
    run_dir.mkdir(parents=True)
    (run_dir / 'acceptance_ratio_data.csv').write_text('data\n', encoding='utf-8')
    (run_dir / 'per_taskset_results.csv').write_text('data\n', encoding='utf-8')
    args = base_args(output_root, 'resume') + [
        '--seeds', '11', '--battery', '20', '--skip-existing',
    ]
    with mock.patch.object(experiment_runner.subprocess, 'run') as run_mock:
        run_multiseed_acceptance.main(args)

    run_mock.assert_not_called()
    row = read_manifest(output_root / 'resume_manifest.csv')[0]
    assert row['status'] == 'stale_existing_result'
    assert row['official_run_valid'] == 'False'
    assert (run_dir / 'acceptance_ratio_data.csv').is_file()


def _write_resumable_run(run_dir, command, solar_path=None, rta_path=None):
    run_dir.mkdir(parents=True, exist_ok=True)
    tasks = run_dir / 'tasks'
    tasks.mkdir(exist_ok=True)
    task = tasks / 'taskset_u0.50_000.yml'
    task.write_text(
        'resources: []\n'
        'taskset:\n'
        '  - name: task_0\n'
        '    iat: 10\n'
        '    runtime: 1\n'
        '    code:\n'
        '      - fixed(1, bzip2)\n',
        encoding='utf-8',
    )
    semantic_hash = acceptance.taskset_semantic_hash(task)
    raw_hash = experiment_runner._file_sha256(task)
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(run_dir.resolve())))
    solar_hash = 'not_used'
    normalized_solar = ''
    solar_source = ''
    solar_relative = ''
    solar_size = 0
    if solar_path is not None:
        solar_source = str(Path(solar_path).resolve())
        payload = Path(solar_path).read_bytes()
        solar_hash = experiment_runner._file_sha256(solar_path)
        solar_relative = 'inputs/solar/solar_profile_{}.csv'.format(solar_hash)
        snapshot = run_dir / solar_relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(payload)
        normalized_solar = str(snapshot.resolve())
        solar_size = len(payload)
    rta_fingerprint = 'not_used'
    rta_snapshot_path = ''
    rta_snapshot_sha = 'not_used'
    rta_snapshot_size = 0
    rta_source_path = ''
    rta_source_sha = 'not_used'
    if rta_path is not None:
        fingerprint = acceptance.rta_code_fingerprint(True, rta_path)
        rta_fingerprint = fingerprint['combined_sha256']
        rta_source_path = fingerprint['entrypoint']
        rta_source_sha = fingerprint['entrypoint_sha256']
        rta_snapshot = run_dir / 'inputs/rta/rta_snapshot.py'
        rta_snapshot.parent.mkdir(parents=True, exist_ok=True)
        rta_snapshot.write_bytes(Path(rta_path).read_bytes())
        rta_snapshot_path = str(rta_snapshot.resolve())
        rta_snapshot_sha = experiment_runner._file_sha256(rta_snapshot)
        rta_snapshot_size = rta_snapshot.stat().st_size
    fields = [
        'run_id', 'config_id', 'config_group_id', 'taskset_hash',
        'taskset_semantic_hash', 'taskset_raw_file_hash',
        'normalized_utilization', 'task_idx', 'algorithm', 'scheduler',
        'status', 'trace_path', 'result_schema_version',
        'simulation_horizon_ms', 'observed_trace_horizon_ms',
        'simulation_completed', 'simulation_completion_reason',
        'expected_configured_scheduler', 'expected_scheduler_display_name',
        'expected_scheduler_implementation',
        'observed_configured_scheduler', 'observed_scheduler_display_name',
        'observed_scheduler_implementation', 'configured_scheduler',
        'scheduler_display_name', 'scheduler_implementation',
        'solar_profile_sha256', 'solar_profile_path_normalized',
        'solar_profile_size', 'solar_source_path',
        'solar_source_sha256',
        'solar_snapshot_relative_path', 'actual_simulator_solar_path',
        'solar_snapshot_time',
        'rta_code_fingerprint', 'rta_code_snapshot_path',
        'rta_code_snapshot_sha256', 'rta_code_snapshot_size',
        'rta_code_source_path', 'rta_code_source_sha256',
        'rta_enabled', 'rta_status', 'rta_version', 'soundness_violation',
        'rta_attempted', 'rta_runtime_sec', 'rta_timed_out', 'rta_error',
        'rta_response_bound', 'simulated_response_time',
        'rta_schedulable', 'sim_schedulable', 'soundness_valid',
        'soundness_excluded_reason',
    ]
    with (run_dir / 'per_taskset_results.csv').open(
            'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for algorithm in acceptance.ALGORITHMS:
            rta_enabled = rta_path is not None and algorithm == 'gpfp_asap_block'
            writer.writerow({
                'run_id': run_id, 'config_id': 'config-a',
                'config_group_id': 'group-a',
                'taskset_hash': semantic_hash,
                'taskset_semantic_hash': semantic_hash,
                'taskset_raw_file_hash': raw_hash,
                'normalized_utilization': 0.5, 'task_idx': 0,
                'algorithm': algorithm, 'scheduler': algorithm,
                'expected_configured_scheduler': algorithm,
                'expected_scheduler_display_name': (
                    acceptance.ALGO_DISPLAY_NAMES[algorithm]
                ),
                'expected_scheduler_implementation': (
                    acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm]
                ),
                'observed_configured_scheduler': algorithm,
                'observed_scheduler_display_name': (
                    acceptance.ALGO_DISPLAY_NAMES[algorithm]
                ),
                'observed_scheduler_implementation': (
                    acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm]
                ),
                'configured_scheduler': algorithm,
                'scheduler_display_name': acceptance.ALGO_DISPLAY_NAMES[
                    algorithm
                ],
                'scheduler_implementation': (
                    acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm]
                ),
                'status': 'accepted', 'trace_path': '',
                'simulation_horizon_ms': 500,
                'observed_trace_horizon_ms': 500,
                'simulation_completed': True,
                'simulation_completion_reason': 'reached_horizon',
                'result_schema_version': acceptance.RESULT_SCHEMA_VERSION,
                'solar_profile_sha256': solar_hash,
                'solar_profile_path_normalized': normalized_solar,
                'solar_profile_size': solar_size,
                'solar_source_path': solar_source,
                'solar_source_sha256': solar_hash,
                'solar_snapshot_relative_path': solar_relative,
                'actual_simulator_solar_path': normalized_solar,
                'solar_snapshot_time': (
                    '2026-01-01T00:00:00Z' if solar_path else ''
                ),
                'rta_code_fingerprint': (
                    rta_fingerprint if rta_enabled else 'not_used'
                ),
                'rta_code_snapshot_path': (
                    rta_snapshot_path if rta_enabled else ''
                ),
                'rta_code_snapshot_sha256': (
                    rta_snapshot_sha if rta_enabled else 'not_used'
                ),
                'rta_code_snapshot_size': (
                    rta_snapshot_size if rta_enabled else 0
                ),
                'rta_code_source_path': (
                    rta_source_path if rta_enabled else ''
                ),
                'rta_code_source_sha256': (
                    rta_source_sha if rta_enabled else 'not_used'
                ),
                'rta_enabled': rta_enabled,
                'rta_status': (
                    'proven_under_assumptions' if rta_enabled else 'disabled'
                ),
                'rta_version': (
                    acceptance.RTA_VERSION if rta_enabled
                    else acceptance.RTA_INACTIVE_VERSION
                ),
                'soundness_violation': False,
                'rta_attempted': rta_enabled,
                'rta_runtime_sec': 0.1 if rta_enabled else '',
                'rta_timed_out': False, 'rta_error': '',
                'rta_response_bound': 1 if rta_enabled else '',
                'simulated_response_time': 1 if rta_enabled else '',
                'rta_schedulable': rta_enabled,
                'sim_schedulable': True,
                'soundness_valid': rta_enabled,
                'soundness_excluded_reason': '',
            })
    aggregate_fields = [
        'run_id', 'config_id', 'config_group_id', 'algorithm',
        'expected_configured_scheduler', 'expected_scheduler_display_name',
        'expected_scheduler_implementation',
        'normalized_utilization', 'acceptance_ratio',
        'simulation_num_accepted', 'simulation_num_rejected',
        'simulation_num_timeout', 'simulation_num_error',
        'simulation_num_generation_error', 'simulation_num_valid',
        'simulation_num_requested', 'num_samples', 'num_successful',
        'num_valid_samples', 'num_requested_samples',
        'unconditional_success_rate', 'error_rate', 'timeout_rate',
        'no_valid_simulations',
        'official_run_valid', 'result_schema_version',
        'rta_enabled', 'rta_version',
        'rta_num_analyzed', 'rta_num_proven', 'rta_num_unproven',
        'rta_num_errors', 'rta_soundness_violations', 'rta_proven_ratio',
        'sim_success_rta_proven', 'sim_success_rta_unproven',
    ]
    aggregate_by_algorithm = {}
    with (run_dir / 'acceptance_ratio_data.csv').open(
            'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=aggregate_fields)
        writer.writeheader()
        for algorithm in acceptance.ALGORITHMS:
            rta_enabled = rta_path is not None and algorithm == 'gpfp_asap_block'
            aggregate_row = {
                'run_id': run_id, 'config_id': 'config-a',
                'config_group_id': 'group-a', 'algorithm': algorithm,
                'expected_configured_scheduler': algorithm,
                'expected_scheduler_display_name': (
                    acceptance.ALGO_DISPLAY_NAMES[algorithm]
                ),
                'expected_scheduler_implementation': (
                    acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm]
                ),
                'normalized_utilization': 0.5, 'acceptance_ratio': 1.0,
                'simulation_num_accepted': 1,
                'simulation_num_rejected': 0,
                'simulation_num_timeout': 0, 'simulation_num_error': 0,
                'simulation_num_generation_error': 0,
                'simulation_num_valid': 1,
                'simulation_num_requested': 1,
                'num_samples': 1, 'num_successful': 1,
                'num_valid_samples': 1, 'num_requested_samples': 1,
                'unconditional_success_rate': 1.0,
                'error_rate': 0.0, 'timeout_rate': 0.0,
                'no_valid_simulations': False,
                'official_run_valid': True,
                'result_schema_version': acceptance.RESULT_SCHEMA_VERSION,
                'rta_enabled': rta_enabled,
                'rta_version': (
                    acceptance.RTA_VERSION if rta_enabled
                    else acceptance.RTA_INACTIVE_VERSION
                ),
                'rta_num_analyzed': int(rta_enabled),
                'rta_num_proven': int(rta_enabled), 'rta_num_unproven': 0,
                'rta_num_errors': 0, 'rta_soundness_violations': 0,
                'rta_proven_ratio': 1.0 if rta_enabled else '',
                'sim_success_rta_proven': int(rta_enabled),
                'sim_success_rta_unproven': 0,
            }
            aggregate_by_algorithm[algorithm] = aggregate_row
            writer.writerow(aggregate_row)
    common_fields = aggregate_fields + [
        'requested_num_tasksets', 'common_complete_num_tasksets',
        'common_complete_excluded_num', 'common_complete_excluded_error',
        'common_complete_excluded_timeout',
        'common_complete_excluded_generation_error',
        'common_complete_excluded_missing', 'common_complete_accepted',
        'common_complete_rejected', 'common_complete_ratio',
        'common_complete_acceptance_ratio',
        'common_complete_unconditional_success_rate',
        'common_complete_wilson_ci95_low',
        'common_complete_wilson_ci95_high',
        'common_complete_no_valid_simulations', 'official_run_invalid',
    ]
    with (run_dir / 'common_complete_acceptance_data.csv').open(
            'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=common_fields)
        writer.writeheader()
        for algorithm in acceptance.ALGORITHMS:
            common_row = dict(aggregate_by_algorithm[algorithm])
            common_row.update({
                'requested_num_tasksets': 1,
                'common_complete_num_tasksets': 1,
                'common_complete_excluded_num': 0,
                'common_complete_excluded_error': 0,
                'common_complete_excluded_timeout': 0,
                'common_complete_excluded_generation_error': 0,
                'common_complete_excluded_missing': 0,
                'common_complete_accepted': 1,
                'common_complete_rejected': 0,
                'common_complete_ratio': 1.0,
                'common_complete_acceptance_ratio': 1.0,
                'common_complete_unconditional_success_rate': 1.0,
                'common_complete_wilson_ci95_low': (
                    experiment_runner._wilson_interval(1, 1)[0]
                ),
                'common_complete_wilson_ci95_high': (
                    experiment_runner._wilson_interval(1, 1)[1]
                ),
                'common_complete_no_valid_simulations': False,
                'official_run_invalid': False,
            })
            writer.writerow(common_row)
    if rta_path is not None:
        (run_dir / 'rta_results.jsonl').write_text(
            json.dumps({
                'run_id': run_id, 'config_id': 'config-a',
                'config_group_id': 'group-a',
                'result_schema_version': acceptance.RESULT_SCHEMA_VERSION,
                'taskset_semantic_hash': semantic_hash,
                'algorithm': 'gpfp_asap_block',
                'expected_configured_scheduler': 'gpfp_asap_block',
                'expected_scheduler_display_name': 'ASAP-Block',
                'expected_scheduler_implementation': (
                    'GPFPASAPBlockScheduler'
                ),
                'observed_configured_scheduler': 'gpfp_asap_block',
                'observed_scheduler_display_name': 'ASAP-Block',
                'observed_scheduler_implementation': (
                    'GPFPASAPBlockScheduler'
                ),
                'configured_scheduler': 'gpfp_asap_block',
                'scheduler_display_name': 'ASAP-Block',
                'scheduler_implementation': 'GPFPASAPBlockScheduler',
                'rta_status': 'proven_under_assumptions',
                'rta_version': acceptance.RTA_VERSION,
                'simulation_status': 'accepted',
                'soundness_violation': False,
                'rta_enabled': True, 'rta_attempted': True,
                'rta_runtime_sec': 0.1, 'rta_timed_out': False,
                'rta_error': None, 'rta_bound': 1,
                'simulated_response_time': 1,
                'rta_schedulable': True, 'sim_schedulable': True,
                'soundness_valid': True,
                'soundness_excluded_reason': '',
            }) + '\n',
            encoding='utf-8',
        )
    assert experiment_runner.write_run_provenance(run_dir, command)
    return task


def _write_execution_manifest_for_run(run_dir, command):
    sidecar = json.loads(
        (run_dir / experiment_runner.RUN_PROVENANCE_FILE).read_text(
            encoding='utf-8'
        )
    )
    manifest = run_dir.parent / (run_dir.name + '_execution_manifest.csv')
    with manifest.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            'status', 'run_id', 'run_dir', 'command_sha256',
            'rta_enabled', 'official_run_valid',
        ])
        writer.writeheader()
        writer.writerow({
            'status': 'completed',
            'run_id': sidecar['run_id'],
            'run_dir': str(run_dir.resolve()),
            'command_sha256': experiment_runner._command_sha256(command),
            'rta_enabled': '--enable-rta' in command,
            'official_run_valid': True,
        })
    return manifest


def _run_acceptance_csv_cli(source, output):
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['MPLBACKEND'] = 'Agg'
    return subprocess.run(
        [
            sys.executable, str(PROJECT_ROOT / 'acceptance_ratio_test.py'),
            '--csv', str(source), '--output-dir', str(output),
            '--no-group-figures',
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_acceptance_csv_accepts_valid_execution_run_artifact(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'source-run'
    _write_resumable_run(run_dir, command)
    _write_execution_manifest_for_run(run_dir, command)
    output = tmp_path / 'plot'
    completed = _run_acceptance_csv_cli(
        run_dir / 'acceptance_ratio_data.csv', output
    )
    assert completed.returncode == 0, completed.stderr
    assert (output / 'acceptance_ratio_all.png').is_file()


def test_acceptance_csv_accepts_source_bound_derived_artifact(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'source-run'
    _write_resumable_run(run_dir, command)
    _write_execution_manifest_for_run(run_dir, command)
    source = run_dir / 'acceptance_ratio_data.csv'
    run_payload = json.loads(
        (run_dir / experiment_runner.RUN_PROVENANCE_FILE).read_text(
            encoding='utf-8'
        )
    )
    derived = tmp_path / 'derived_acceptance.csv'
    derived.write_bytes(source.read_bytes())
    experiment_runner.write_analysis_artifact_attestation(
        derived,
        producer_id='acceptance_source_equivalent_v1',
        output_role='acceptance_summary',
        producer_config={},
        run_id=run_payload['run_id'],
        config_ids=run_payload['config_id'],
        source_artifacts=[(source, 'source_acceptance_aggregate')],
    )
    output = tmp_path / 'plot'
    completed = _run_acceptance_csv_cli(derived, output)
    assert completed.returncode == 0, completed.stderr
    assert (output / 'acceptance_ratio_all.png').is_file()


def test_acceptance_csv_rejects_source_bound_but_unverified_transformation(
        tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'source-run'
    _write_resumable_run(run_dir, command)
    _write_execution_manifest_for_run(run_dir, command)
    source = run_dir / 'acceptance_ratio_data.csv'
    run_payload = json.loads(
        (run_dir / experiment_runner.RUN_PROVENANCE_FILE).read_text(
            encoding='utf-8'
        )
    )
    derived = tmp_path / 'unverified_transformation.csv'
    transformed = source.read_text(encoding='utf-8').replace(',1.0,', ',0.0,')
    assert transformed != source.read_text(encoding='utf-8')
    derived.write_text(transformed, encoding='utf-8')
    with pytest.raises(ValueError, match='differs from source'):
        experiment_runner.write_analysis_artifact_attestation(
            derived,
            producer_id='acceptance_source_equivalent_v1',
            output_role='acceptance_summary',
            producer_config={},
            run_id=run_payload['run_id'],
            config_ids=run_payload['config_id'],
            source_artifacts=[(source, 'source_acceptance_aggregate')],
        )


@pytest.mark.parametrize('defect', [
    'missing_manifest', 'missing_sidecar', 'official_false',
    'run_id_mismatch', 'config_id_mismatch', 'artifact_changed',
])
def test_acceptance_csv_rejects_broken_execution_attestation(
        tmp_path, defect):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'source-run'
    _write_resumable_run(run_dir, command)
    execution_manifest = _write_execution_manifest_for_run(run_dir, command)
    sidecar = run_dir / experiment_runner.RUN_PROVENANCE_FILE
    source = run_dir / 'acceptance_ratio_data.csv'
    if defect == 'missing_manifest':
        execution_manifest.unlink()
    elif defect == 'missing_sidecar':
        sidecar.unlink()
    elif defect in {'official_false', 'run_id_mismatch',
                    'config_id_mismatch'}:
        payload = json.loads(sidecar.read_text(encoding='utf-8'))
        if defect == 'official_false':
            payload['official_run_valid'] = False
        elif defect == 'run_id_mismatch':
            payload['run_id'] = 'wrong-run'
        else:
            payload['config_id'] = ['wrong-config']
        sidecar.write_text(json.dumps(payload), encoding='utf-8')
    else:
        source.write_text(source.read_text(encoding='utf-8') + '\n',
                          encoding='utf-8')
    output = tmp_path / 'plot'
    completed = _run_acceptance_csv_cli(source, output)
    assert completed.returncode != 0
    assert not (output / 'acceptance_ratio_all.png').exists()


def test_derived_attestation_requires_attested_source_and_revalidates_it(
        tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'source-run'
    _write_resumable_run(run_dir, command)
    _write_execution_manifest_for_run(run_dir, command)
    source = run_dir / 'per_taskset_results.csv'
    derived = tmp_path / 'derived.csv'
    derived.write_bytes(source.read_bytes())
    run_payload = json.loads(
        (run_dir / experiment_runner.RUN_PROVENANCE_FILE).read_text(
            encoding='utf-8'
        )
    )

    sidecar = experiment_runner.write_analysis_artifact_attestation(
        derived,
        producer_id='acceptance_source_equivalent_v1',
        output_role='acceptance_summary',
        producer_config={},
        run_id=run_payload['run_id'],
        config_ids=run_payload['config_id'],
        source_artifacts=[(source, 'source_acceptance_aggregate')],
    )
    assert sidecar.is_file()
    payload = experiment_runner.validate_analysis_artifact_attestation(
        derived
    )
    assert payload['artifact_type'] == 'derived_analysis_artifact'
    assert len(payload['sources']) == 1

    source.write_text(source.read_text(encoding='utf-8') + '\n',
                      encoding='utf-8')
    with pytest.raises(ValueError):
        experiment_runner.validate_analysis_artifact_attestation(derived)


def test_derived_attestation_without_source_removes_old_official_marker(
        tmp_path):
    derived = tmp_path / 'orphan.csv'
    derived.write_text('value\n1\n', encoding='utf-8')
    manifest, sidecar = experiment_runner._analysis_attestation_paths(derived)
    manifest.write_text('{"official_run_valid":true}\n', encoding='utf-8')
    sidecar.write_text('{"official_run_valid":true}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='requires a source'):
        experiment_runner.write_analysis_artifact_attestation(derived)
    assert not manifest.exists()
    assert not sidecar.exists()


@pytest.mark.parametrize('defect', [
    'missing_execution_manifest', 'missing_run_sidecar',
    'changed_source_artifact',
    'source_not_listed', 'forged_source_run_id',
])
def test_derived_writer_rejects_invalid_source_chain(tmp_path, defect):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'source-run'
    _write_resumable_run(run_dir, command)
    execution_manifest = _write_execution_manifest_for_run(run_dir, command)
    run_sidecar = run_dir / experiment_runner.RUN_PROVENANCE_FILE
    source = run_dir / 'per_taskset_results.csv'
    derived = tmp_path / 'derived.csv'
    derived.write_text('value\n1\n', encoding='utf-8')
    if defect == 'missing_execution_manifest':
        execution_manifest.unlink()
    elif defect == 'missing_run_sidecar':
        run_sidecar.unlink()
    elif defect == 'changed_source_artifact':
        source.write_text(source.read_text(encoding='utf-8') + '\n',
                          encoding='utf-8')
    elif defect == 'source_not_listed':
        payload = json.loads(run_sidecar.read_text(encoding='utf-8'))
        payload['result_artifacts'] = [
            record for record in payload['result_artifacts']
            if record['relative_path'] != source.name
        ]
        run_sidecar.write_text(json.dumps(payload), encoding='utf-8')
    elif defect == 'forged_source_run_id':
        rows = read_manifest(execution_manifest)
        rows[0]['run_id'] = 'forged-run-id'
        _rewrite_csv_rows(execution_manifest, rows)

    with pytest.raises((ValueError, FileNotFoundError)):
        experiment_runner.write_analysis_artifact_attestation(
            derived,
            source_artifacts=[(source, 'source_per_taskset_results')],
        )
    manifest, sidecar = experiment_runner._analysis_attestation_paths(derived)
    assert not manifest.exists()
    assert not sidecar.exists()


def test_derived_validator_rejects_source_sidecar_bytes_changed_after_publish(
        tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'source-run'
    _write_resumable_run(run_dir, command)
    _write_execution_manifest_for_run(run_dir, command)
    source = run_dir / 'per_taskset_results.csv'
    derived = tmp_path / 'derived.csv'
    derived.write_bytes(source.read_bytes())
    run_payload = json.loads(
        (run_dir / experiment_runner.RUN_PROVENANCE_FILE).read_text(
            encoding='utf-8'
        )
    )
    experiment_runner.write_analysis_artifact_attestation(
        derived,
        producer_id='acceptance_source_equivalent_v1',
        output_role='acceptance_summary',
        producer_config={},
        run_id=run_payload['run_id'],
        config_ids=run_payload['config_id'],
        source_artifacts=[(source, 'source_acceptance_aggregate')],
    )
    run_sidecar = run_dir / experiment_runner.RUN_PROVENANCE_FILE
    run_sidecar.write_text(
        run_sidecar.read_text(encoding='utf-8') + '\n', encoding='utf-8'
    )
    with pytest.raises(ValueError, match='source_provenance_mismatch'):
        experiment_runner.validate_analysis_artifact_attestation(derived)


def test_derived_writer_rejects_diagnostic_source(tmp_path):
    diagnostic = tmp_path / 'diagnostic_unattested'
    diagnostic.mkdir()
    source = diagnostic / 'diagnostic_source.csv'
    source.write_text('value\n1\n', encoding='utf-8')
    derived = tmp_path / 'derived.csv'
    derived.write_text('value\n1\n', encoding='utf-8')
    with pytest.raises(ValueError, match='diagnostic artifact'):
        experiment_runner.write_analysis_artifact_attestation(
            derived, source_artifacts=[(source, 'diagnostic')]
        )


@pytest.mark.parametrize('raw_mode', ['missing', 'empty'])
def test_rta_attestation_requires_nonempty_raw_artifact(tmp_path, raw_mode):
    command = [
        'python3', 'acceptance_ratio_test.py', '--run-experiment',
        '--enable-rta',
    ]
    run_dir = tmp_path / raw_mode
    _write_resumable_run(
        run_dir, command, rta_path=PROJECT_ROOT / 'asap_block_rta.py'
    )
    raw = run_dir / 'rta_results.jsonl'
    if raw_mode == 'missing':
        raw.unlink()
    else:
        raw.write_text('', encoding='utf-8')
    with pytest.raises(ValueError, match='fingerprint|rta_results.jsonl'):
        experiment_runner.write_run_provenance(run_dir, command)
    assert not (run_dir / experiment_runner.RUN_PROVENANCE_FILE).exists()
    assert experiment_runner.validate_existing_result(
        run_dir, command
    )[0] is False


def test_rta_config_fingerprint_requires_raw_even_if_derived_flags_cleared(
        tmp_path):
    command = [
        'python3', 'acceptance_ratio_test.py', '--run-experiment',
        '--enable-rta',
    ]
    run_dir = tmp_path / 'declared-rta'
    _write_resumable_run(
        run_dir, command, rta_path=PROJECT_ROOT / 'asap_block_rta.py'
    )
    per_path = run_dir / 'per_taskset_results.csv'
    per_rows = read_manifest(per_path)
    for row in per_rows:
        row['rta_enabled'] = 'False'
        row['rta_attempted'] = 'False'
        row['rta_status'] = 'disabled'
    _rewrite_csv_rows(per_path, per_rows)
    aggregate_path = run_dir / 'acceptance_ratio_data.csv'
    aggregate_rows = read_manifest(aggregate_path)
    for row in aggregate_rows:
        for field in (
            'rta_num_analyzed', 'rta_num_proven', 'rta_num_unproven',
            'rta_num_errors', 'rta_soundness_violations',
            'sim_success_rta_proven', 'sim_success_rta_unproven',
        ):
            row[field] = '0'
        row['rta_proven_ratio'] = ''
    _rewrite_csv_rows(aggregate_path, aggregate_rows)
    common_path = run_dir / 'common_complete_acceptance_data.csv'
    common_rows = read_manifest(common_path)
    for row in common_rows:
        for field in (
            'rta_num_analyzed', 'rta_num_proven', 'rta_num_unproven',
            'rta_num_errors', 'rta_soundness_violations',
            'sim_success_rta_proven', 'sim_success_rta_unproven',
        ):
            row[field] = '0'
        row['rta_proven_ratio'] = ''
    _rewrite_csv_rows(common_path, common_rows)
    (run_dir / 'rta_results.jsonl').unlink()

    with pytest.raises(
        ValueError, match='rta_version|fingerprint|rta_results.jsonl'
    ):
        experiment_runner.write_run_provenance(run_dir, command)
    assert not (run_dir / experiment_runner.RUN_PROVENANCE_FILE).exists()


def test_rta_disabled_command_rejects_residual_raw_artifact(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'disabled-with-raw'
    _write_resumable_run(run_dir, command)
    (run_dir / 'rta_results.jsonl').write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='disables RTA'):
        experiment_runner.write_run_provenance(run_dir, command)
    assert not (run_dir / experiment_runner.RUN_PROVENANCE_FILE).exists()


def test_resume_revalidates_taskset_and_solar_hashes(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    solar = tmp_path / 'solar.csv'
    solar.write_text('0,1\n', encoding='utf-8')
    run_dir = tmp_path / 'resume'
    task = _write_resumable_run(run_dir, command, solar)
    original_task = task.read_text(encoding='utf-8')
    assert experiment_runner.validate_existing_result(
        run_dir, command
    ) == (True, '')

    task.write_text('taskset: [changed]\n', encoding='utf-8')
    valid, reason = experiment_runner.validate_existing_result(
        run_dir, command
    )
    assert valid is False
    assert reason == 'taskset_hash_mismatch'

    task.write_text(original_task, encoding='utf-8')
    solar.write_text('0,2\n', encoding='utf-8')
    # The completed run is bound to its immutable snapshot, not the mutable
    # original source path.
    assert experiment_runner.validate_existing_result(
        run_dir, command
    ) == (True, '')

    snapshot = next((run_dir / 'inputs/solar').glob('*.csv'))
    snapshot.write_text('0,3\n', encoding='utf-8')
    valid, reason = experiment_runner.validate_existing_result(
        run_dir, command
    )
    assert valid is False
    assert reason == 'solar_profile_hash_mismatch'


def _rewrite_first_csv_row(path, field, value):
    with Path(path).open(newline='', encoding='utf-8') as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    rows[0][field] = value
    with Path(path).open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize('filename,field,value', [
    ('acceptance_ratio_data.csv', 'acceptance_ratio', '0.0'),
    ('acceptance_ratio_data.csv', 'simulation_num_accepted', '0'),
    ('per_taskset_results.csv', 'status', 'rejected'),
    ('per_taskset_results.csv', 'config_group_id', 'tampered-group'),
    ('per_taskset_results.csv', 'run_id', 'tampered-run'),
])
def test_resume_rejects_result_artifact_tampering(
        tmp_path, filename, field, value):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / (field + '-run')
    _write_resumable_run(run_dir, command)
    _rewrite_first_csv_row(run_dir / filename, field, value)
    valid, _reason = experiment_runner.validate_existing_result(
        run_dir, command
    )
    assert valid is False


@pytest.mark.parametrize('schema', [None, True, 0, -1, 1, 999, '2'])
def test_resume_strictly_rejects_unknown_sidecar_schema(tmp_path, schema):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'schema-run'
    _write_resumable_run(run_dir, command)
    sidecar = run_dir / experiment_runner.RUN_PROVENANCE_FILE
    payload = json.loads(sidecar.read_text(encoding='utf-8'))
    if schema is None:
        payload.pop('schema_version')
    else:
        payload['schema_version'] = schema
    sidecar.write_text(json.dumps(payload), encoding='utf-8')
    assert experiment_runner.validate_existing_result(
        run_dir, command
    ) == (False, 'unsupported_provenance_schema')


def test_resume_rejects_result_file_set_changes(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'file-set-run'
    _write_resumable_run(run_dir, command)
    (run_dir / 'unexpected_results.csv').write_text(
        'run_id\nunexpected\n', encoding='utf-8'
    )
    valid, _reason = experiment_runner.validate_existing_result(
        run_dir, command
    )
    assert valid is False


def test_manifest_run_id_is_bound_to_published_sidecar(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'manifest-run'
    _write_resumable_run(run_dir, command)
    payload = json.loads((
        run_dir / experiment_runner.RUN_PROVENANCE_FILE
    ).read_text(encoding='utf-8'))
    manifest = tmp_path / 'manifest.csv'
    fields = [
        'run_dir', 'status', 'run_id', 'command_sha256',
        'rta_enabled', 'official_run_valid',
    ]
    row = {
        'run_dir': str(run_dir),
        'status': 'completed',
        'run_id': payload['run_id'],
        'command_sha256': payload['command_sha256'],
        'rta_enabled': False,
        'official_run_valid': True,
    }
    experiment_runner.write_manifest(manifest, fields, [row])
    assert experiment_runner.validate_execution_manifest(manifest)

    row['run_id'] = 'tampered-manifest-run-id'
    experiment_runner.write_manifest(manifest, fields, [row])
    with pytest.raises(ValueError, match='manifest_run_id_mismatch'):
        experiment_runner.validate_execution_manifest(manifest)

    row['run_id'] = payload['run_id']
    row['status'] = 'failed'
    experiment_runner.write_manifest(manifest, fields, [row])
    with pytest.raises(ValueError, match='non-formal run status'):
        experiment_runner.validate_execution_manifest(manifest)


def test_formal_loader_requires_manifest_and_sidecar_together(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'attested-run'
    _write_resumable_run(run_dir, command)
    payload = json.loads((
        run_dir / experiment_runner.RUN_PROVENANCE_FILE
    ).read_text(encoding='utf-8'))
    manifest = tmp_path / 'batch_manifest.csv'
    fields = [
        'run_dir', 'status', 'run_id', 'command_sha256',
        'rta_enabled', 'official_run_valid',
    ]
    row = {
        'run_dir': str(run_dir), 'status': 'completed',
        'run_id': payload['run_id'],
        'command_sha256': payload['command_sha256'],
        'rta_enabled': False,
        'official_run_valid': True,
    }
    experiment_runner.write_manifest(manifest, fields, [row])
    assert experiment_analysis.validate_attested_run_directory(run_dir)

    manifest.unlink()
    with pytest.raises(ValueError, match='missing_execution_manifest'):
        experiment_analysis.validate_attested_run_directory(run_dir)

    row['official_run_valid'] = False
    experiment_runner.write_manifest(manifest, fields, [row])
    with pytest.raises(ValueError, match='manifest marks completed run invalid'):
        experiment_analysis.validate_attested_run_directory(run_dir)


@pytest.mark.parametrize(
    'field,value',
    [('common_complete_acceptance_ratio', '0.0'),
     ('common_complete_accepted', '0'),
     ('common_complete_rejected', '1'),
     ('common_complete_num_tasksets', '0'),
     ('requested_num_tasksets', '2'),
     ('common_complete_ratio', '0.0'),
     ('common_complete_excluded_num', '1'),
     ('common_complete_excluded_error', '1'),
     ('common_complete_excluded_timeout', '1'),
     ('common_complete_excluded_generation_error', '1'),
     ('common_complete_excluded_missing', '1'),
     ('common_complete_unconditional_success_rate', '0.0'),
     ('common_complete_wilson_ci95_low', '0.0'),
     ('common_complete_wilson_ci95_high', '0.0'),
     ('common_complete_no_valid_simulations', 'true'),
     ('algorithm', 'gpfp_unknown'),
     ('normalized_utilization', '0.2'),
     ('config_group_id', 'group-tampered')],
)
def test_common_complete_tampering_cannot_receive_new_sidecar(
        tmp_path, field, value):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / ('common-' + field)
    _write_resumable_run(run_dir, command)
    (run_dir / experiment_runner.RUN_PROVENANCE_FILE).unlink()
    _rewrite_first_csv_row(
        run_dir / 'common_complete_acceptance_data.csv', field, value
    )
    with pytest.raises(ValueError):
        experiment_runner.write_run_provenance(run_dir, command)
    assert not (run_dir / experiment_runner.RUN_PROVENANCE_FILE).exists()


@pytest.mark.parametrize('mutation', ['delete', 'duplicate'])
def test_common_complete_row_set_tampering_cannot_receive_new_sidecar(
        tmp_path, mutation):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / ('common-row-' + mutation)
    _write_resumable_run(run_dir, command)
    (run_dir / experiment_runner.RUN_PROVENANCE_FILE).unlink()
    path = run_dir / 'common_complete_acceptance_data.csv'
    with path.open(newline='', encoding='utf-8') as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    if mutation == 'delete':
        rows.pop()
    else:
        rows.append(dict(rows[0]))
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match='common-complete'):
        experiment_runner.write_run_provenance(run_dir, command)
    assert not (run_dir / experiment_runner.RUN_PROVENANCE_FILE).exists()


@pytest.mark.parametrize(
    'field,value',
    [('rta_status', 'rta_unproven'),
     ('simulation_status', 'rejected'),
     ('soundness_violation', True),
     ('taskset_semantic_hash', '0' * 64),
     ('config_id', 'config-tampered'),
     ('rta_timed_out', True),
     ('rta_error', 'tampered'),
     ('rta_runtime_sec', 9.0),
     ('rta_enabled', 'false'),
     ('rta_runtime_sec', 'nan')],
)
def test_rta_semantic_tampering_cannot_receive_new_sidecar(
        tmp_path, field, value):
    command = [
        'python3', 'acceptance_ratio_test.py', '--run-experiment',
        '--enable-rta',
    ]
    tool = tmp_path / 'rta.py'
    tool.write_text('print("rta")\n', encoding='utf-8')
    run_dir = tmp_path / 'rta-semantic-tamper'
    _write_resumable_run(run_dir, command, rta_path=tool)
    (run_dir / experiment_runner.RUN_PROVENANCE_FILE).unlink()
    rta_path = run_dir / 'rta_results.jsonl'
    row = json.loads(rta_path.read_text(encoding='utf-8'))
    row[field] = value
    rta_path.write_text(json.dumps(row) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='RTA|RTA row|orphan'):
        experiment_runner.write_run_provenance(run_dir, command)
    assert not (run_dir / experiment_runner.RUN_PROVENANCE_FILE).exists()


@pytest.mark.parametrize('mutation', ['delete', 'duplicate'])
def test_rta_row_set_tampering_cannot_receive_new_sidecar(
        tmp_path, mutation):
    command = [
        'python3', 'acceptance_ratio_test.py', '--run-experiment',
        '--enable-rta',
    ]
    tool = tmp_path / 'rta-row-set.py'
    tool.write_text('print("rta")\n', encoding='utf-8')
    run_dir = tmp_path / ('rta-row-' + mutation)
    _write_resumable_run(run_dir, command, rta_path=tool)
    (run_dir / experiment_runner.RUN_PROVENANCE_FILE).unlink()
    path = run_dir / 'rta_results.jsonl'
    original = path.read_text(encoding='utf-8')
    path.write_text('' if mutation == 'delete' else original + original,
                    encoding='utf-8')
    with pytest.raises(ValueError, match='RTA|rta'):
        experiment_runner.write_run_provenance(run_dir, command)
    assert not (run_dir / experiment_runner.RUN_PROVENANCE_FILE).exists()


@pytest.mark.parametrize(
    'field,value',
    [('rta_num_proven', '0'),
     ('rta_num_analyzed', '0'),
     ('rta_num_unproven', '1'),
     ('rta_num_errors', '1'),
     ('rta_soundness_violations', '1'),
     ('rta_proven_ratio', '0.0'),
     ('sim_success_rta_proven', '0'),
     ('sim_success_rta_unproven', '1')],
)
def test_rta_summary_tampering_cannot_receive_new_sidecar(
        tmp_path, field, value):
    command = [
        'python3', 'acceptance_ratio_test.py', '--run-experiment',
        '--enable-rta',
    ]
    tool = tmp_path / 'rta-summary.py'
    tool.write_text('print("rta")\n', encoding='utf-8')
    run_dir = tmp_path / 'rta-summary-tamper'
    _write_resumable_run(run_dir, command, rta_path=tool)
    (run_dir / experiment_runner.RUN_PROVENANCE_FILE).unlink()
    _rewrite_first_csv_row(
        run_dir / 'acceptance_ratio_data.csv', field, value
    )
    with pytest.raises(ValueError, match='RTA aggregate mismatch'):
        experiment_runner.write_run_provenance(run_dir, command)
    assert not (run_dir / experiment_runner.RUN_PROVENANCE_FILE).exists()


def test_resume_rejects_changed_rta_source_code(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment',
               '--enable-rta']
    tool = tmp_path / 'rta_tool.py'
    tool.write_text('print("a")\n', encoding='utf-8')
    run_dir = tmp_path / 'rta-source-run'
    _write_resumable_run(run_dir, command, rta_path=tool)
    tool.write_text('print("b")\n', encoding='utf-8')
    valid, reason = experiment_runner.validate_existing_result(
        run_dir, command
    )
    assert valid is False
    assert reason == 'rta_code_fingerprint_mismatch'


def test_completed_child_with_incomplete_provenance_is_failed(tmp_path):
    run_dir = tmp_path / 'run'
    fields = [
        'run_dir', 'status', 'return_code',
        *experiment_runner.EXECUTION_MANIFEST_FIELDS,
    ]
    spec = {
        'run_dir': str(run_dir),
        'command': ['python3', 'acceptance_ratio_test.py'],
    }
    with mock.patch.object(
            experiment_runner, 'run_command', return_value=0), \
            mock.patch.object(
                experiment_runner, 'run_dir_complete', return_value=True
            ), \
            mock.patch.object(
                experiment_runner, 'write_run_provenance', return_value=False
            ):
        rows = experiment_runner.execute_specs(
            [spec], tmp_path / 'manifest.csv', fields
        )

    assert rows[0]['status'] == 'failed'
    assert rows[0]['return_code'] == 1
    assert rows[0]['official_run_valid'] is False
    assert rows[0]['wrapper_exit_code'] == 1
    assert 'provenance_write_failed' in rows[0]['diagnostic_outputs']


def test_wrapper_exit_code_preserves_common_complete_code():
    assert experiment_runner.wrapper_exit_code([
        {'status': 'completed', 'return_code': 0,
         'official_run_valid': True}
    ]) == 0
    assert experiment_runner.wrapper_exit_code([
        {'status': 'failed', 'return_code': 2}
    ]) == 2
    assert experiment_runner.wrapper_exit_code([
        {'status': 'failed', 'return_code': 1}
    ]) == 1


def test_real_wrapper_process_propagates_child_exit_two(tmp_path):
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_python = fake_bin / 'python3'
    fake_python.write_text('#!/bin/sh\nexit 2\n', encoding='utf-8')
    fake_python.chmod(0o755)
    output_root = tmp_path / 'runs'
    command = [
        sys.executable,
        str(PROJECT_ROOT / 'scripts/run_multiseed_acceptance.py'),
        *base_args(output_root, 'exit-two'),
        '--seeds', '11', '--battery', '20',
    ]
    env = os.environ.copy()
    env['PATH'] = str(fake_bin) + os.pathsep + env.get('PATH', '')
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 2
    assert 'Analyze with:' not in completed.stdout
    row = read_manifest(output_root / 'exit-two_manifest.csv')[0]
    assert row['child_exit_code'] == '2'
    assert row['wrapper_exit_code'] == '2'
    assert row['official_run_valid'] == 'False'
    assert row['formal_outputs_generated'] == 'False'


def test_existing_directory_is_blocked_without_safety_flag(tmp_path):
    output_root = tmp_path / 'runs'
    run_dir = output_root / 'blocked-seed11'
    run_dir.mkdir(parents=True)
    marker = run_dir / 'partial.txt'
    marker.write_text('keep', encoding='utf-8')
    args = base_args(output_root, 'blocked') + [
        '--seeds', '11', '--battery', '20',
    ]
    with mock.patch.object(experiment_runner.subprocess, 'run') as run_mock:
        run_multiseed_acceptance.main(args)

    run_mock.assert_not_called()
    assert marker.read_text(encoding='utf-8') == 'keep'
    row = read_manifest(output_root / 'blocked_manifest.csv')[0]
    assert row['status'] == 'blocked_existing'


def test_force_removes_generated_run_directory_before_execution(tmp_path):
    output_root = tmp_path / 'runs'
    run_dir = output_root / 'forced-seed11'
    run_dir.mkdir(parents=True)
    marker = run_dir / 'partial.txt'
    marker.write_text('old', encoding='utf-8')
    args = base_args(output_root, 'forced') + [
        '--seeds', '11', '--battery', '20', '--force',
    ]
    with mock.patch.object(
        experiment_runner.subprocess, 'run', side_effect=fake_successful_subprocess
    ) as run_mock:
        run_multiseed_acceptance.main(args)

    run_mock.assert_called_once()
    assert not marker.exists()
    row = read_manifest(output_root / 'forced_manifest.csv')[0]
    assert row['status'] == 'completed'


def test_failed_run_is_recorded_and_later_runs_continue(tmp_path):
    output_root = tmp_path / 'runs'
    args = base_args(output_root, 'continue') + [
        '--seeds', '11', '22', '--battery', '20',
    ]
    calls = {'count': 0}
    def fail_then_succeed(command, **kwargs):
        calls['count'] += 1
        if calls['count'] == 1:
            return SimpleNamespace(returncode=7)
        return fake_successful_subprocess(command, **kwargs)
    with mock.patch.object(
        experiment_runner.subprocess, 'run', side_effect=fail_then_succeed
    ) as run_mock:
        run_multiseed_acceptance.main(args)

    assert run_mock.call_count == 2
    rows = read_manifest(output_root / 'continue_manifest.csv')
    assert [row['status'] for row in rows] == ['failed', 'completed']
    assert [row['return_code'] for row in rows] == ['7', '0']


def test_safe_run_dir_name_formats_numeric_values():
    assert experiment_runner.safe_run_dir_name(0.25) == '0p25'
    assert experiment_runner.safe_run_dir_name(1.0) == '1'
    assert experiment_runner.safe_run_dir_name(20.0) == '20'


def _rewrite_csv_rows(path, rows):
    with Path(path).open(newline='', encoding='utf-8') as handle:
        fields = csv.DictReader(handle).fieldnames
    with Path(path).open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _formal_trace(path, algorithm, taskset_semantic_hash,
                  run_id='resume-run', horizon=500, rejected=False):
    events = [{
        'run_generation': 1, 'time': 0, 'event_type': 'arrival',
        'task_name': 'task_0',
    }]
    if rejected:
        events.append({
            'run_generation': 1, 'time': 1, 'event_type': 'dline_miss',
            'task_name': 'task_0', 'job_id': 'task_0@0',
            'arrival_time': 0, 'deadline': 1,
            'remaining_execution_ms': 1,
        })
    events.append({
        'run_generation': 1, 'time': horizon, 'event_type': 'idle',
    })
    payload = {
        'trace_schema_version': acceptance.TRACE_SCHEMA_VERSION,
        'taskset_semantic_hash': taskset_semantic_hash,
        'run_id': run_id, 'run_count': 1, 'run_generation': 1,
        'target_run_generation': 1,
        'configured_scheduler': algorithm,
        'scheduler_display_name': acceptance.ALGO_DISPLAY_NAMES[algorithm],
        'scheduler_implementation': (
            acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm]
        ),
        'expected_simulation_horizon_ms': horizon,
        'observed_simulation_end_ms': horizon,
        'simulation_completed': True,
        'simulation_completion_reason': 'reached_horizon',
        'events': events,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload), encoding='utf-8')


@pytest.mark.parametrize('replacement', [
    algorithm for algorithm in acceptance.ALGORITHMS
    if algorithm != acceptance.ASAP_BLOCK_ALGORITHM
])
def test_rta_attestation_rejects_all_non_asap_block_owners(
        tmp_path, replacement):
    command = [
        'python3', 'acceptance_ratio_test.py', '--run-experiment',
        '--enable-rta',
    ]
    run_dir = tmp_path / replacement
    _write_resumable_run(
        run_dir, command, None, rta_path=PROJECT_ROOT / 'asap_block_rta.py'
    )
    (run_dir / experiment_runner.RUN_PROVENANCE_FILE).unlink()

    per_path = run_dir / 'per_taskset_results.csv'
    per_rows = read_manifest(per_path)
    asap = next(row for row in per_rows if row['algorithm'] ==
                acceptance.ASAP_BLOCK_ALGORITHM)
    target = next(row for row in per_rows if row['algorithm'] == replacement)
    for field in (
        'rta_enabled', 'rta_status', 'rta_version', 'soundness_violation',
        'rta_attempted', 'rta_runtime_sec', 'rta_timed_out', 'rta_error',
        'rta_response_bound', 'simulated_response_time', 'rta_schedulable',
        'sim_schedulable', 'soundness_valid', 'soundness_excluded_reason',
        'rta_code_fingerprint', 'rta_code_snapshot_path',
        'rta_code_snapshot_sha256', 'rta_code_snapshot_size',
        'rta_code_source_path', 'rta_code_source_sha256',
    ):
        asap[field], target[field] = target[field], asap[field]
    _rewrite_csv_rows(per_path, per_rows)

    aggregate_path = run_dir / 'acceptance_ratio_data.csv'
    aggregate_rows = read_manifest(aggregate_path)
    asap_aggregate = next(row for row in aggregate_rows if row['algorithm'] ==
                          acceptance.ASAP_BLOCK_ALGORITHM)
    target_aggregate = next(row for row in aggregate_rows
                            if row['algorithm'] == replacement)
    for field in (
        'rta_num_analyzed', 'rta_num_proven', 'rta_num_unproven',
        'rta_num_errors', 'rta_soundness_violations', 'rta_proven_ratio',
        'sim_success_rta_proven', 'sim_success_rta_unproven',
        'rta_enabled', 'rta_version',
    ):
        asap_aggregate[field], target_aggregate[field] = (
            target_aggregate[field], asap_aggregate[field]
        )
    _rewrite_csv_rows(aggregate_path, aggregate_rows)

    rta_path = run_dir / 'rta_results.jsonl'
    raw = json.loads(rta_path.read_text(encoding='utf-8'))
    raw.update({
        'algorithm': replacement,
        'expected_configured_scheduler': replacement,
        'expected_scheduler_display_name': (
            acceptance.ALGO_DISPLAY_NAMES[replacement]
        ),
        'expected_scheduler_implementation': (
            acceptance.SCHEDULER_IMPLEMENTATIONS[replacement]
        ),
        'observed_configured_scheduler': replacement,
        'observed_scheduler_display_name': (
            acceptance.ALGO_DISPLAY_NAMES[replacement]
        ),
        'observed_scheduler_implementation': (
            acceptance.SCHEDULER_IMPLEMENTATIONS[replacement]
        ),
        'configured_scheduler': replacement,
        'scheduler_display_name': acceptance.ALGO_DISPLAY_NAMES[replacement],
        'scheduler_implementation': (
            acceptance.SCHEDULER_IMPLEMENTATIONS[replacement]
        ),
    })
    rta_path.write_text(json.dumps(raw) + '\n', encoding='utf-8')

    with pytest.raises(ValueError, match='must bind RTA'):
        experiment_runner.write_run_provenance(run_dir, command)
    assert not (run_dir / experiment_runner.RUN_PROVENANCE_FILE).exists()


@pytest.mark.parametrize('row_algorithm', [
    'gpfp_asap_nonblock', 'gpfp_alap_block',
])
def test_row_trace_binding_rejects_other_scheduler_trace(
        tmp_path, row_algorithm):
    run_dir = tmp_path / row_algorithm
    _write_resumable_run(
        run_dir, ['python3', 'acceptance_ratio_test.py'], None
    )
    trace = run_dir / 'traces' / 'asap-block.json'
    rows = read_manifest(run_dir / 'per_taskset_results.csv')
    _formal_trace(
        trace, acceptance.ASAP_BLOCK_ALGORITHM,
        rows[0]['taskset_semantic_hash'], run_id=rows[0]['run_id']
    )
    next(row for row in rows if row['algorithm'] == row_algorithm)[
        'trace_path'
    ] = str(trace)
    _rewrite_csv_rows(run_dir / 'per_taskset_results.csv', rows)
    with pytest.raises(ValueError, match='classification mismatch'):
        experiment_runner._validate_result_semantics(run_dir)


def test_row_trace_binding_rejects_duplicate_reference(tmp_path):
    run_dir = tmp_path / 'duplicate-trace'
    _write_resumable_run(
        run_dir, ['python3', 'acceptance_ratio_test.py'], None
    )
    trace = run_dir / 'traces' / 'shared.json'
    rows = read_manifest(run_dir / 'per_taskset_results.csv')
    _formal_trace(
        trace, acceptance.ASAP_BLOCK_ALGORITHM,
        rows[0]['taskset_semantic_hash'], run_id=rows[0]['run_id']
    )
    for algorithm in ('gpfp_asap_block', 'gpfp_asap_nonblock'):
        next(row for row in rows if row['algorithm'] == algorithm)[
            'trace_path'
        ] = str(trace)
    _rewrite_csv_rows(run_dir / 'per_taskset_results.csv', rows)
    with pytest.raises(ValueError, match='multiple result rows'):
        experiment_runner._validate_result_semantics(run_dir)


@pytest.mark.parametrize('defect', [
    'run_id', 'scheduler', 'horizon', 'classification',
    'row_observed_horizon', 'row_completion', 'trace_taskset_hash',
])
def test_row_trace_binding_rejects_trace_metadata_and_status(
        tmp_path, defect):
    run_dir = tmp_path / defect
    _write_resumable_run(
        run_dir, ['python3', 'acceptance_ratio_test.py'], None
    )
    trace = run_dir / 'traces' / 'bound.json'
    algorithm = acceptance.ASAP_BLOCK_ALGORITHM
    rows = read_manifest(run_dir / 'per_taskset_results.csv')
    _formal_trace(
        trace,
        'gpfp_asap_nonblock' if defect == 'scheduler' else algorithm,
        ('b' * 64 if defect == 'trace_taskset_hash'
         else rows[0]['taskset_semantic_hash']),
        run_id='wrong-run' if defect == 'run_id' else rows[0]['run_id'],
        horizon=400 if defect == 'horizon' else 500,
        rejected=defect == 'classification',
    )
    row = next(row for row in rows if row['algorithm'] == algorithm)
    row['trace_path'] = str(trace)
    if defect == 'row_observed_horizon':
        row['observed_trace_horizon_ms'] = 499
    elif defect == 'row_completion':
        row['simulation_completion_reason'] = 'event_queue_exhausted'
    _rewrite_csv_rows(run_dir / 'per_taskset_results.csv', rows)
    with pytest.raises(ValueError):
        experiment_runner._validate_result_semantics(run_dir)
