import json
import shutil
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance
from scripts import experiment_analysis, experiment_runner
from scripts.build_identity import (
    BUILD_IDENTITY_FILENAME, generate_build_identity,
    validate_build_identity,
)
from test_experiment_runners import (
    _write_execution_manifest_for_run, _write_resumable_run,
    read_manifest, _rewrite_csv_rows,
)


PRODUCER_ARGS = {
    'producer_id': 'acceptance_source_equivalent_v1',
    'output_role': 'acceptance_summary',
    'producer_config': {},
}


def primary_source(tmp_path):
    source = tmp_path / 'source.csv'
    source.write_text('value\n1\n', encoding='utf-8')
    experiment_runner.write_primary_analysis_artifact_attestation(
        source, run_id='source-run', config_ids=['source-config']
    )
    return source


def write_equivalent_derived(path, source, nested=()):
    path.write_bytes(source.read_bytes())
    return experiment_runner.write_analysis_artifact_attestation(
        path,
        source_artifacts=[
            (source, 'source_acceptance_aggregate'),
            *[(item, 'acceptance_summary') for item in nested],
        ],
        **PRODUCER_ARGS,
    )


def refresh_analysis_sidecar(path):
    manifest_path, sidecar_path = (
        experiment_runner._analysis_attestation_paths(path)
    )
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    sidecar = json.loads(sidecar_path.read_text(encoding='utf-8'))
    sidecar['producer'] = manifest.get('producer')
    sidecar['output_role'] = manifest.get('output_role')
    sidecar['manifest'] = {
        'relative_path': manifest_path.name,
        'size_bytes': manifest_path.stat().st_size,
        'sha256': experiment_runner._file_sha256(manifest_path),
    }
    experiment_runner._atomic_write_json(sidecar_path, sidecar)


def test_derived_writer_requires_registered_producer(tmp_path):
    source = primary_source(tmp_path)
    output = tmp_path / 'derived.csv'
    output.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match='requires producer_id'):
        experiment_runner.write_analysis_artifact_attestation(
            output,
            source_artifacts=[(source, 'source_acceptance_aggregate')],
        )
    with pytest.raises(ValueError, match='unregistered'):
        experiment_runner.write_analysis_artifact_attestation(
            output, producer_id='not_registered',
            output_role='acceptance_summary', producer_config={},
            source_artifacts=[(source, 'source_acceptance_aggregate')],
        )


def test_registered_producer_rejects_roles_and_arbitrary_transform(tmp_path):
    source = primary_source(tmp_path)
    output = tmp_path / 'derived.csv'
    output.write_text('value\n999\n', encoding='utf-8')
    with pytest.raises(ValueError, match='output role'):
        experiment_runner.write_analysis_artifact_attestation(
            output, producer_id='acceptance_source_equivalent_v1',
            output_role='wrong-role', producer_config={},
            source_artifacts=[(source, 'source_acceptance_aggregate')],
        )
    with pytest.raises(ValueError, match='source role'):
        experiment_runner.write_analysis_artifact_attestation(
            output, **PRODUCER_ARGS,
            source_artifacts=[(source, 'wrong-source-role')],
        )
    with pytest.raises(ValueError, match='differs from source'):
        experiment_runner.write_analysis_artifact_attestation(
            output, **PRODUCER_ARGS,
            source_artifacts=[(source, 'source_acceptance_aggregate')],
        )


def test_producer_cannot_publish_under_another_profile(tmp_path):
    source = primary_source(tmp_path)
    output = tmp_path / 'derived.csv'
    output.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match='output role'):
        experiment_runner.write_analysis_artifact_attestation(
            output,
            producer_id='mechanism_case_selection_v1',
            output_role='acceptance_summary',
            producer_config={},
            source_artifacts=[(source, 'source_acceptance_aggregate')],
        )


def test_profile_without_validator_and_callback_injection_fail_closed(
        tmp_path, monkeypatch):
    source = primary_source(tmp_path)
    output = tmp_path / 'derived.csv'
    output.write_bytes(source.read_bytes())
    profile = dict(
        experiment_runner.ANALYSIS_PRODUCER_PROFILES[
            'acceptance_source_equivalent_v1'
        ]
    )
    profile['validator_id'] = 'missing_validator'
    monkeypatch.setitem(
        experiment_runner.ANALYSIS_PRODUCER_PROFILES,
        'missing_validator_profile', profile,
    )
    with pytest.raises(ValueError, match='no validator'):
        experiment_runner.write_analysis_artifact_attestation(
            output, producer_id='missing_validator_profile',
            output_role='acceptance_summary', producer_config={},
            source_artifacts=[(source, 'source_acceptance_aggregate')],
        )
    with pytest.raises(TypeError):
        experiment_runner.write_analysis_artifact_attestation(
            output, validator=lambda *_args: True, **PRODUCER_ARGS,
            source_artifacts=[(source, 'source_acceptance_aggregate')],
        )


def test_registered_mechanism_selection_recomputes_output(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'run'
    _write_resumable_run(run_dir, command)
    _write_execution_manifest_for_run(run_dir, command)
    source = run_dir / 'per_taskset_results.csv'
    correct = experiment_analysis.select_cases([run_dir], allow_legacy=False)
    output = tmp_path / 'mechanism_case_candidates.csv'
    correct.to_csv(output, index=False)
    experiment_runner.write_analysis_artifact_attestation(
        output,
        producer_id='mechanism_case_selection_v1',
        output_role='mechanism_candidates',
        producer_config={},
        source_artifacts=[(source, 'source_per_taskset_results')],
    )
    assert experiment_runner.validate_analysis_artifact_attestation(output)
    output.write_text(output.read_text(encoding='utf-8') + 'wrong,row\n',
                      encoding='utf-8')
    with pytest.raises(ValueError):
        experiment_runner.write_analysis_artifact_attestation(
            output,
            producer_id='mechanism_case_selection_v1',
            output_role='mechanism_candidates',
            producer_config={},
            source_artifacts=[(source, 'source_per_taskset_results')],
        )


def test_scheduler_diversity_producer_rejects_incomplete_output(
        tmp_path, monkeypatch):
    from scripts import run_scheduler_diversity_audit as diversity
    from scripts.run_scheduler_diversity_audit import AUDIT_FIELDS

    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'run'
    _write_resumable_run(run_dir, command)
    _write_execution_manifest_for_run(run_dir, command)
    source_row = read_manifest(run_dir / 'per_taskset_results.csv')[0]
    candidate = {
        'config_id': source_row['config_id'],
        'taskset_hash': source_row['taskset_hash'],
        'seed_base': 1,
        'normalized_utilization': 0.5,
        'task_idx': 0,
        'category': 'all_accepted',
    }
    monkeypatch.setattr(
        diversity, 'select_tasksets',
        lambda *_args, **_kwargs: [candidate],
    )
    trace = tmp_path / 'trace.json'
    trace.write_text('{}\n', encoding='utf-8')
    output = tmp_path / 'audit_runs.csv'
    row = {
        'audit_case_id': 'audit-001-seed{}-u{}-i{}'.format(
            int(float(candidate['seed_base'])),
            str(candidate['normalized_utilization']).replace('.', 'p'),
            int(float(candidate['task_idx'])),
        ),
        'category': candidate['category'],
        'seed_base': candidate['seed_base'],
        'normalized_utilization': candidate['normalized_utilization'],
        'task_idx': candidate['task_idx'],
        'scheduler': acceptance.ALGORITHMS[0],
        'accepted': True,
        'simulation_status': 'accepted',
        'first_missed_task': '',
        'deadline_miss_time': '',
        'taskset_path': '',
        'trace_path': str(trace),
        'error': '',
    }
    import pandas as pd
    pd.DataFrame([row], columns=AUDIT_FIELDS).to_csv(output, index=False)
    monkeypatch.setattr(
        experiment_runner, '_validate_trace_backed_row',
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(ValueError, match='incomplete'):
        experiment_runner.write_analysis_artifact_attestation(
            output,
            companion_paths=[trace],
            producer_id='scheduler_diversity_audit_v1',
            output_role='scheduler_diversity_audit',
            producer_config={
                'categories': ['all_accepted'],
                'max_tasksets': 1,
                'schedulers': list(acceptance.ALGORITHMS[:2]),
            },
            source_artifacts=[
                (run_dir / 'per_taskset_results.csv',
                 'source_per_taskset_results'),
            ],
        )


def test_producer_code_identity_is_revalidated(tmp_path):
    source = primary_source(tmp_path)
    output = tmp_path / 'derived.csv'
    write_equivalent_derived(output, source)
    manifest_path, sidecar_path = (
        experiment_runner._analysis_attestation_paths(output)
    )
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    sidecar = json.loads(sidecar_path.read_text(encoding='utf-8'))
    manifest['producer']['code_sha256'] = '0' * 64
    sidecar['producer']['code_sha256'] = '0' * 64
    experiment_runner._atomic_write_json(manifest_path, manifest)
    experiment_runner._atomic_write_json(sidecar_path, sidecar)
    refresh_analysis_sidecar(output)
    with pytest.raises(ValueError, match='producer_identity_mismatch'):
        experiment_runner.validate_analysis_artifact_attestation(output)


def test_provenance_self_and_two_node_cycles_are_controlled(tmp_path):
    source = primary_source(tmp_path)
    a = tmp_path / 'a.csv'
    b = tmp_path / 'b.csv'
    write_equivalent_derived(a, source)
    write_equivalent_derived(b, source)
    raw_record = experiment_runner._build_analysis_source_record(
        source, 'source_acceptance_aggregate'
    )
    a_record = experiment_runner._build_analysis_source_record(
        a, 'acceptance_summary'
    )
    b_record = experiment_runner._build_analysis_source_record(
        b, 'acceptance_summary'
    )

    manifest_a, _ = experiment_runner._analysis_attestation_paths(a)
    payload_a = json.loads(manifest_a.read_text(encoding='utf-8'))
    payload_a['sources'] = [raw_record, a_record]
    experiment_runner._atomic_write_json(manifest_a, payload_a)
    refresh_analysis_sidecar(a)
    with pytest.raises(ValueError, match='cycle detected'):
        experiment_runner.validate_analysis_artifact_attestation(a)

    write_equivalent_derived(a, source)
    manifest_a, _ = experiment_runner._analysis_attestation_paths(a)
    manifest_b, _ = experiment_runner._analysis_attestation_paths(b)
    payload_a = json.loads(manifest_a.read_text(encoding='utf-8'))
    payload_b = json.loads(manifest_b.read_text(encoding='utf-8'))
    payload_a['sources'] = [raw_record, b_record]
    payload_b['sources'] = [raw_record, a_record]
    experiment_runner._atomic_write_json(manifest_a, payload_a)
    experiment_runner._atomic_write_json(manifest_b, payload_b)
    refresh_analysis_sidecar(a)
    refresh_analysis_sidecar(b)
    with pytest.raises(ValueError, match='cycle detected'):
        experiment_runner.validate_analysis_artifact_attestation(a)


def test_provenance_three_node_cycle_is_controlled(tmp_path):
    source = primary_source(tmp_path)
    artifacts = [tmp_path / '{}.csv'.format(name) for name in ('a', 'b', 'c')]
    for artifact in artifacts:
        write_equivalent_derived(artifact, source)
    raw_record = experiment_runner._build_analysis_source_record(
        source, 'source_acceptance_aggregate'
    )
    records = [
        experiment_runner._build_analysis_source_record(
            artifact, 'acceptance_summary'
        )
        for artifact in artifacts
    ]
    for artifact, nested_record in zip(artifacts, records[1:] + records[:1]):
        manifest, _sidecar = (
            experiment_runner._analysis_attestation_paths(artifact)
        )
        payload = json.loads(manifest.read_text(encoding='utf-8'))
        payload['sources'] = [raw_record, nested_record]
        experiment_runner._atomic_write_json(manifest, payload)
        refresh_analysis_sidecar(artifact)
    with pytest.raises(ValueError, match='cycle detected'):
        experiment_runner.validate_analysis_artifact_attestation(artifacts[0])


def test_provenance_depth_shared_dag_and_relocation(tmp_path):
    source = primary_source(tmp_path)
    previous = None
    depth_rejected = False
    for index in range(experiment_runner.MAX_ANALYSIS_PROVENANCE_DEPTH + 3):
        current = tmp_path / 'depth-{}.csv'.format(index)
        try:
            write_equivalent_derived(
                current, source, () if previous is None else (previous,)
            )
            previous = current
        except ValueError as error:
            assert 'maximum depth' in str(error)
            depth_rejected = True
            break
    assert depth_rejected

    left = tmp_path / 'left.csv'
    right = tmp_path / 'right.csv'
    shared = tmp_path / 'shared.csv'
    write_equivalent_derived(left, source)
    write_equivalent_derived(right, source)
    write_equivalent_derived(shared, source, (left, right))
    assert experiment_runner.validate_analysis_artifact_attestation(shared)
    assert experiment_runner.validate_analysis_artifact_attestation(shared)

    moved = tmp_path / 'moved'
    moved.mkdir()
    for path in (shared,) + experiment_runner._analysis_attestation_paths(shared):
        shutil.copy2(path, moved / path.name)
    moved_artifact = moved / shared.name
    assert experiment_runner.validate_analysis_artifact_attestation(
        moved_artifact
    )['location_binding'] == 'content_based'
    moved_artifact.write_text('value\nchanged\n', encoding='utf-8')
    with pytest.raises(ValueError, match='artifact_mismatch'):
        experiment_runner.validate_analysis_artifact_attestation(moved_artifact)

    relocation_source = tmp_path / 'relocation-source'
    relocation_source.mkdir()
    separate_source = primary_source(relocation_source)
    relocatable = tmp_path / 'relocatable.csv'
    write_equivalent_derived(relocatable, separate_source)
    broken = tmp_path / 'broken-relocation'
    broken.mkdir()
    for path in ((relocatable,)
                 + experiment_runner._analysis_attestation_paths(relocatable)):
        shutil.copy2(path, broken / path.name)
    separate_source.unlink()
    with pytest.raises(ValueError, match='missing analysis artifact'):
        experiment_runner.validate_analysis_artifact_attestation(
            broken / relocatable.name
        )


def test_explicit_rta_enabled_schema_for_simulation_and_rta_runs(tmp_path):
    plain_command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    plain = tmp_path / 'plain'
    _write_resumable_run(plain, plain_command)
    plain_rows = read_manifest(plain / 'per_taskset_results.csv')
    plain_aggregate = read_manifest(plain / 'acceptance_ratio_data.csv')
    plain_sidecar = json.loads(
        (plain / experiment_runner.RUN_PROVENANCE_FILE).read_text(
            encoding='utf-8'
        )
    )
    assert {row['rta_enabled'] for row in plain_rows} == {'False'}
    assert {row['rta_version'] for row in plain_rows} == {'not_used'}
    assert {row['rta_enabled'] for row in plain_aggregate} == {'False'}
    assert {row['rta_version'] for row in plain_aggregate} == {'not_used'}
    assert plain_sidecar['rta_enabled'] is False

    rta_command = [*plain_command, '--enable-rta']
    rta = tmp_path / 'rta'
    _write_resumable_run(
        rta, rta_command, rta_path=PROJECT_ROOT / 'asap_block_rta.py'
    )
    rta_rows = read_manifest(rta / 'per_taskset_results.csv')
    enabled = [row for row in rta_rows if row['rta_enabled'] == 'True']
    assert {row['algorithm'] for row in enabled} == {'gpfp_asap_block'}
    assert {row['rta_version'] for row in enabled} == {acceptance.RTA_VERSION}
    assert (rta / 'rta_results.jsonl').is_file()
    assert json.loads(
        (rta / experiment_runner.RUN_PROVENANCE_FILE).read_text(
            encoding='utf-8'
        )
    )['rta_enabled'] is True


@pytest.mark.parametrize('mutation', [
    'active_version_while_disabled', 'invalid_enabled_version',
    'manifest_conflict',
])
def test_explicit_rta_enabled_contradictions_are_rejected(tmp_path, mutation):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / mutation
    _write_resumable_run(run_dir, command)
    if mutation == 'manifest_conflict':
        sidecar = run_dir / experiment_runner.RUN_PROVENANCE_FILE
        payload = json.loads(sidecar.read_text(encoding='utf-8'))
        payload['rta_enabled'] = True
        sidecar.write_text(json.dumps(payload), encoding='utf-8')
        with pytest.raises(ValueError, match='rta_enabled_mismatch'):
            experiment_runner.validate_result_attestation(run_dir)
        return
    path = run_dir / 'per_taskset_results.csv'
    rows = read_manifest(path)
    rows[0]['rta_version'] = (
        acceptance.RTA_VERSION
        if mutation == 'active_version_while_disabled' else 'invalid-version'
    )
    if mutation == 'invalid_enabled_version':
        rows[0]['rta_enabled'] = 'True'
    _rewrite_csv_rows(path, rows)
    with pytest.raises(ValueError, match='rta_version|invalid'):
        experiment_runner.write_run_provenance(run_dir, command)


@pytest.mark.parametrize('mutation', [
    'raw_while_disabled', 'aggregate_while_disabled', 'command_conflict',
])
def test_disabled_rta_evidence_and_command_conflicts_are_rejected(
        tmp_path, mutation):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / mutation
    _write_resumable_run(run_dir, command)
    if mutation == 'raw_while_disabled':
        (run_dir / 'rta_results.jsonl').write_text('{}\n', encoding='utf-8')
    elif mutation == 'aggregate_while_disabled':
        path = run_dir / 'acceptance_ratio_data.csv'
        rows = read_manifest(path)
        rows[0]['rta_num_analyzed'] = '1'
        _rewrite_csv_rows(path, rows)
    else:
        command = [*command, '--enable-rta']
    with pytest.raises(ValueError, match='RTA|rta_enabled|command'):
        experiment_runner.write_run_provenance(run_dir, command)


def test_execution_manifest_rta_enabled_must_match_result_sidecar(tmp_path):
    command = ['python3', 'acceptance_ratio_test.py', '--run-experiment']
    run_dir = tmp_path / 'execution-manifest'
    _write_resumable_run(run_dir, command)
    manifest = _write_execution_manifest_for_run(run_dir, command)
    rows = read_manifest(manifest)
    rows[0]['rta_enabled'] = 'True'
    _rewrite_csv_rows(manifest, rows)
    with pytest.raises(ValueError, match='rta_enabled mismatch'):
        experiment_runner.validate_execution_manifest(manifest)


def synthetic_build(tmp_path):
    build = tmp_path / 'build'
    binary = build / 'rtsim' / 'rtsim'
    library = build / 'librtsim' / 'librtsim.so.3'
    binary.parent.mkdir(parents=True)
    library.parent.mkdir(parents=True)
    binary.write_bytes(b'local-rtsim')
    binary.chmod(0o755)
    library.write_bytes(b'local-librtsim')
    identity = build / BUILD_IDENTITY_FILENAME
    generate_build_identity(
        PROJECT_ROOT, build, 'Release', 'GNU', '9.4.0',
        binary, library, identity,
    )
    return build, binary, library, identity


def test_build_identity_detects_artifact_and_source_mismatch(tmp_path):
    build, binary, library, identity = synthetic_build(tmp_path)
    assert validate_build_identity(binary, PROJECT_ROOT)
    binary.write_bytes(b'changed-rtsim')
    with pytest.raises(ValueError, match='artifact hash mismatch'):
        validate_build_identity(binary, PROJECT_ROOT)

    build, binary, library, identity = synthetic_build(tmp_path / 'library')
    library.write_bytes(b'changed-library')
    with pytest.raises(ValueError, match='artifact hash mismatch'):
        validate_build_identity(binary, PROJECT_ROOT)

    build, binary, library, identity = synthetic_build(tmp_path / 'source')
    payload = json.loads(identity.read_text(encoding='utf-8'))
    payload['source_tree']['combined_sha256'] = '0' * 64
    identity.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='source fingerprint mismatch'):
        validate_build_identity(binary, PROJECT_ROOT)


def test_build_identity_rejects_missing_wrong_root_and_wrong_build(tmp_path):
    build, binary, _library, identity = synthetic_build(tmp_path)
    identity.unlink()
    with pytest.raises(ValueError, match='identity is missing'):
        validate_build_identity(binary, PROJECT_ROOT)

    build, binary, _library, identity = synthetic_build(tmp_path / 'root')
    payload = json.loads(identity.read_text(encoding='utf-8'))
    payload['source_root'] = str(tmp_path)
    identity.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='source root mismatch'):
        validate_build_identity(binary, PROJECT_ROOT)

    build, binary, _library, identity = synthetic_build(tmp_path / 'builddir')
    payload = json.loads(identity.read_text(encoding='utf-8'))
    payload['build_directory'] = str(tmp_path / 'different-build')
    identity.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='build directory mismatch'):
        validate_build_identity(binary, PROJECT_ROOT)
