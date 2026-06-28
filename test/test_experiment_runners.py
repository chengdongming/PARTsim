import csv
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import experiment_runner
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


def test_multiseed_runner_builds_two_commands_and_manifest(tmp_path):
    output_root = tmp_path / 'runs'
    args = base_args(output_root, 'multi') + [
        '--seeds', '11', '22', '--battery', '20',
    ]
    with mock.patch.object(
        experiment_runner.subprocess, 'run', return_value=successful_process()
    ) as run_mock:
        rows = run_multiseed_acceptance.main(args)

    assert run_mock.call_count == 2
    commands = [call.args[0] for call in run_mock.call_args_list]
    assert [command[command.index('--seed-base') + 1] for command in commands] == [
        '11', '22',
    ]
    assert all('--enable-rta' not in command for command in commands)
    manifest = output_root / 'multi_manifest.csv'
    manifest_rows = read_manifest(manifest)
    assert len(manifest_rows) == 2
    assert {row['status'] for row in manifest_rows} == {'completed'}
    assert {row['rta_enabled'] for row in manifest_rows} == {'False'}
    assert len(rows) == 2


def test_battery_runner_builds_cartesian_product(tmp_path):
    output_root = tmp_path / 'runs'
    args = base_args(output_root, 'battery') + [
        '--batteries', '5', '20', '--seeds', '11', '22',
    ]
    with mock.patch.object(
        experiment_runner.subprocess, 'run', return_value=successful_process()
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
        experiment_runner.subprocess, 'run', return_value=successful_process()
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


def test_skip_existing_requires_both_result_files(tmp_path):
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
    assert row['status'] == 'skipped_existing'
    assert (run_dir / 'acceptance_ratio_data.csv').is_file()


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
        experiment_runner.subprocess, 'run', return_value=successful_process()
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
    with mock.patch.object(
        experiment_runner.subprocess,
        'run',
        side_effect=[SimpleNamespace(returncode=7), successful_process()],
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
