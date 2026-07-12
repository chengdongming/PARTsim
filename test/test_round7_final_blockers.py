import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import experiment_runner
import acceptance_ratio_test as acceptance


ANALYZERS = [
    ('analyze_mechanism_cases.py', '--case-summary'),
    ('analyze_scheduler_diversity_audit.py', '--audit-runs'),
    ('analyze_rta_e1_soundness.py', '--input'),
    ('analyze_rta_scalability.py', '--input'),
    ('analyze_rta_parameter_sensitivity.py', '--input'),
    ('analyze_rta_ablation.py', '--input'),
    ('analyze_rta_v21_comparison.py', '--input'),
]


def _run_analyzer(script, input_option, source, output):
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['MPLBACKEND'] = 'Agg'
    return subprocess.run(
        [
            sys.executable, str(PROJECT_ROOT / 'scripts' / script),
            input_option, str(source), '--output-dir', str(output),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize('script,input_option', ANALYZERS)
@pytest.mark.parametrize('defect', [
    'missing_manifest', 'missing_sidecar', 'official_false',
    'run_id_mismatch', 'config_id_mismatch', 'artifact_mismatch',
])
def test_every_formal_analyzer_rejects_unattested_or_inconsistent_input(
        tmp_path, script, input_option, defect):
    case = tmp_path / '{}-{}'.format(Path(script).stem, defect)
    case.mkdir()
    source = case / 'input.csv'
    source.write_text('config_id,value\ncfg,1\n', encoding='utf-8')
    experiment_runner.write_primary_analysis_artifact_attestation(
        source, run_id='round7-run', config_ids=['cfg']
    )
    manifest, sidecar = experiment_runner._analysis_attestation_paths(source)
    if defect == 'missing_manifest':
        manifest.unlink()
    elif defect == 'missing_sidecar':
        sidecar.unlink()
    elif defect == 'official_false':
        payload = json.loads(manifest.read_text(encoding='utf-8'))
        payload['official_run_valid'] = False
        manifest.write_text(json.dumps(payload), encoding='utf-8')
    elif defect == 'run_id_mismatch':
        payload = json.loads(sidecar.read_text(encoding='utf-8'))
        payload['run_id'] = 'different-run'
        sidecar.write_text(json.dumps(payload), encoding='utf-8')
    elif defect == 'config_id_mismatch':
        payload = json.loads(sidecar.read_text(encoding='utf-8'))
        payload['config_id'] = ['different-config']
        sidecar.write_text(json.dumps(payload), encoding='utf-8')
    elif defect == 'artifact_mismatch':
        source.write_text('config_id,value\ncfg,2\n', encoding='utf-8')

    output = case / 'formal-output'
    completed = _run_analyzer(
        script, input_option, source, output
    )
    assert completed.returncode != 0
    assert not output.exists() or not any(
        path.suffix.lower() in {'.csv', '.png', '.pdf'}
        for path in output.rglob('*') if path.is_file()
    )


def test_unattested_diagnostic_mode_is_isolated_and_marked(tmp_path):
    source = tmp_path / 'case_summary.csv'
    source.write_text(
        'case_id,case_type,scheduler,simulation_status,battery_min,'
        'battery_final,executed_ticks,trace_path\n'
        'c1,energy,gpfp_asap_block,accepted,1,1,1,\n',
        encoding='utf-8',
    )
    output = tmp_path / 'output'
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / 'scripts' / 'analyze_mechanism_cases.py'),
            '--case-summary', str(source), '--output-dir', str(output),
            '--allow-unattested-diagnostic-input',
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    diagnostic = output / 'diagnostic_unattested'
    assert (diagnostic / 'diagnostic_metadata.json').is_file()
    metadata = json.loads(
        (diagnostic / 'diagnostic_metadata.json').read_text(encoding='utf-8')
    )
    assert metadata['official'] is False
    assert all(
        path.name.startswith('diagnostic_')
        for path in diagnostic.rglob('*') if path.is_file()
    )


def _acceptance_csv_fixture(path):
    path.write_text(
        'algorithm,normalized_utilization,acceptance_ratio\n'
        'gpfp_asap_block,0.5,1.0\n',
        encoding='utf-8',
    )


def _run_acceptance_csv(source, output, *extra):
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['MPLBACKEND'] = 'Agg'
    return subprocess.run(
        [
            sys.executable, str(PROJECT_ROOT / 'acceptance_ratio_test.py'),
            '--csv', str(source), '--output-dir', str(output),
            '--no-group-figures', *extra,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_acceptance_csv_formal_mode_rejects_orphan_without_overwrite(tmp_path):
    source = tmp_path / 'orphan.csv'
    _acceptance_csv_fixture(source)
    output = tmp_path / 'formal-output'
    output.mkdir()
    old_figure = output / 'acceptance_ratio_all.png'
    old_figure.write_bytes(b'preserve-old-formal-figure')
    completed = _run_acceptance_csv(source, output)
    assert completed.returncode != 0
    assert old_figure.read_bytes() == b'preserve-old-formal-figure'


def test_acceptance_csv_formal_mode_rejects_self_attested_primary(tmp_path):
    source = tmp_path / 'primary.csv'
    _acceptance_csv_fixture(source)
    experiment_runner.write_primary_analysis_artifact_attestation(
        source, run_id='primary-run', config_ids=['primary-config']
    )
    output = tmp_path / 'formal-output'
    completed = _run_acceptance_csv(source, output)
    assert completed.returncode != 0
    assert 'primary_analysis_artifact_not_valid_for_formal_csv' in (
        completed.stdout + completed.stderr
    )
    assert not (output / 'acceptance_ratio_all.png').exists()


def test_acceptance_csv_diagnostic_mode_isolated_and_cannot_be_refed(
        tmp_path):
    source = tmp_path / 'unattested.csv'
    _acceptance_csv_fixture(source)
    output = tmp_path / 'diagnostic-output'
    completed = _run_acceptance_csv(
        source, output, '--allow-unattested-diagnostic-input'
    )
    assert completed.returncode == 0, completed.stderr
    diagnostic = output / 'diagnostic_unattested'
    assert (diagnostic / 'diagnostic_acceptance_ratio_all.png').is_file()
    assert json.loads(
        (diagnostic / 'diagnostic_metadata.json').read_text(encoding='utf-8')
    )['official'] is False

    diagnostic_source = diagnostic / 'diagnostic_input.csv'
    _acceptance_csv_fixture(diagnostic_source)
    formal_output = tmp_path / 'refed-output'
    refused = _run_acceptance_csv(diagnostic_source, formal_output)
    assert refused.returncode != 0
    assert not (formal_output / 'acceptance_ratio_all.png').exists()


def _valid_trace(rejected=False):
    algorithm = acceptance.ASAP_BLOCK_ALGORITHM
    events = [{
        'run_generation': 1, 'time': 0, 'event_type': 'arrival',
        'task_name': 'task_0',
    }]
    if rejected:
        events.append({
            'run_generation': 1, 'time': 5, 'event_type': 'dline_miss',
            'task_name': 'task_0', 'job_id': 'task_0@0',
            'arrival_time': 0, 'deadline': 5,
            'remaining_execution_ms': 1,
        })
    events.append({
        'run_generation': 1, 'time': 10, 'event_type': 'idle',
    })
    return {
        'trace_schema_version': acceptance.TRACE_SCHEMA_VERSION,
        'taskset_semantic_hash': 'a' * 64,
        'run_id': 'fixture-run', 'run_count': 1, 'run_generation': 1,
        'target_run_generation': 1,
        'configured_scheduler': algorithm,
        'scheduler_display_name': acceptance.ALGO_DISPLAY_NAMES[algorithm],
        'scheduler_implementation': (
            acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm]
        ),
        'expected_simulation_horizon_ms': 10,
        'observed_simulation_end_ms': 10,
        'simulation_completed': True,
        'simulation_completion_reason': 'reached_horizon',
        'events': events,
    }


def _publication_validator_script():
    source = (PROJECT_ROOT / 'rtsim' / 'main.cpp').read_text(encoding='utf-8')
    prefix = 'static const char validator[] = R"PY(\n'
    start = source.index(prefix) + len(prefix)
    end = source.index('\n)PY";', start)
    return source[start:end]


@pytest.mark.parametrize('fixture', [
    'valid_accepted', 'valid_rejected', 'scheduler_mismatch',
    'horizon_mismatch', 'missing_run_id', 'run_count_error',
    'generation_missing', 'generation_conflict', 'generation_zero',
    'duplicate_key', 'completion_contradiction', 'malformed_miss',
    'duplicate_miss', 'nan_horizon', 'infinite_horizon',
    'bool_generation', 'trailing_data', 'empty_events', 'empty_file',
    'missing_taskset_hash', 'wrong_taskset_hash', 'bad_taskset_hash_type',
    'bad_taskset_hash_format',
])
def test_cpp_publication_and_python_classifier_have_accept_reject_parity(
        tmp_path, fixture):
    payload = _valid_trace(rejected=fixture == 'valid_rejected')
    if fixture == 'scheduler_mismatch':
        algorithm = 'gpfp_asap_nonblock'
        payload.update({
            'configured_scheduler': algorithm,
            'scheduler_display_name': acceptance.ALGO_DISPLAY_NAMES[algorithm],
            'scheduler_implementation': (
                acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm]
            ),
        })
    elif fixture == 'horizon_mismatch':
        payload['expected_simulation_horizon_ms'] = 11
        payload['observed_simulation_end_ms'] = 11
    elif fixture == 'missing_run_id':
        payload.pop('run_id')
    elif fixture == 'run_count_error':
        payload['run_count'] = 2
    elif fixture == 'generation_missing':
        payload.pop('target_run_generation')
    elif fixture == 'generation_conflict':
        payload['run_generation'] = 2
    elif fixture == 'generation_zero':
        payload['run_generation'] = 0
        payload['target_run_generation'] = 0
        for event in payload['events']:
            event['run_generation'] = 0
    elif fixture == 'completion_contradiction':
        payload['simulation_completed'] = False
    elif fixture == 'malformed_miss':
        payload = _valid_trace(rejected=True)
        payload['events'][1]['remaining_execution_ms'] = 0
    elif fixture == 'duplicate_miss':
        payload = _valid_trace(rejected=True)
        payload['events'].insert(2, dict(payload['events'][1]))
    elif fixture == 'nan_horizon':
        payload['observed_simulation_end_ms'] = float('nan')
    elif fixture == 'infinite_horizon':
        payload['observed_simulation_end_ms'] = float('inf')
    elif fixture == 'bool_generation':
        payload['target_run_generation'] = True
    elif fixture == 'empty_events':
        payload['events'] = []
    elif fixture == 'missing_taskset_hash':
        payload.pop('taskset_semantic_hash')
    elif fixture == 'wrong_taskset_hash':
        payload['taskset_semantic_hash'] = 'b' * 64
    elif fixture == 'bad_taskset_hash_type':
        payload['taskset_semantic_hash'] = True
    elif fixture == 'bad_taskset_hash_format':
        payload['taskset_semantic_hash'] = 'not-a-sha256'

    trace = tmp_path / (fixture + '.json')
    if fixture == 'empty_file':
        trace.write_text('', encoding='utf-8')
    elif fixture == 'duplicate_key':
        encoded = json.dumps(payload)
        trace.write_text(
            '{"run_id":"duplicate",' + encoded[1:], encoding='utf-8'
        )
    elif fixture == 'trailing_data':
        trace.write_text(json.dumps(payload) + '\n{}', encoding='utf-8')
    else:
        trace.write_text(json.dumps(payload), encoding='utf-8')

    algorithm = acceptance.ASAP_BLOCK_ALGORITHM
    cpp = subprocess.run(
        [
            sys.executable, '-c', _publication_validator_script(),
            str(trace), 'fixture-run',
            str(acceptance.TRACE_SCHEMA_VERSION), algorithm,
            acceptance.ALGO_DISPLAY_NAMES[algorithm],
            acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm], '10', 'a' * 64,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    python_result = acceptance.TraceParser(str(trace)).evaluate(
        10, expected_algorithm=algorithm,
        expected_taskset_semantic_hash='a' * 64,
    )
    python_accepts = python_result.status in {'accepted', 'rejected'}
    assert (cpp.returncode == 0) == python_accepts, (
        fixture, cpp.stderr, python_result
    )
