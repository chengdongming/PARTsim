#!/usr/bin/env python3
"""Shared utilities for safe, reproducible batch experiment runners."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_SCRIPT = PROJECT_ROOT / 'acceptance_ratio_test.py'
PROVENANCE_SCHEMA_VERSION = 3
RESULT_FILES = (
    'acceptance_ratio_data.csv',
    'per_taskset_results.csv',
    'common_complete_acceptance_data.csv',
)
RUN_PROVENANCE_FILE = 'wrapper_run_provenance.json'
EXECUTION_MANIFEST_FIELDS = [
    'run_id', 'command_sha256',
    'rta_enabled', 'require_common_complete', 'official_run_valid',
    'child_exit_code',
    'wrapper_exit_code', 'failed_specs', 'blocked_specs',
    'diagnostic_outputs', 'formal_outputs_generated',
]


def safe_run_dir_name(value):
    """Format a numeric parameter as a path-safe stable token."""
    number = float(value)
    token = format(number, '.15g')
    return token.replace('-', 'm').replace('.', 'p')


def safe_experiment_name(value):
    """Reject path traversal while retaining readable experiment names."""
    value = str(value).strip()
    if not value or value in {'.', '..'}:
        raise ValueError('experiment name must not be empty')
    safe = re.sub(r'[^A-Za-z0-9_-]+', '-', value).strip('-')
    if not safe:
        raise ValueError('experiment name has no path-safe characters')
    return safe


def run_dir_complete(run_dir):
    run_dir = Path(run_dir)
    return all((run_dir / filename).is_file() for filename in RESULT_FILES)


def _command_sha256(command):
    payload = json.dumps(
        [str(part) for part in command],
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _wilson_interval(successes, trials, z=1.959963984540054):
    """Return the two-sided 95% Wilson interval used by the producer."""
    if trials <= 0:
        return math.nan, math.nan
    proportion = successes / trials
    z_squared = z * z
    denominator = 1.0 + z_squared / trials
    centre = (proportion + z_squared / (2.0 * trials)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / trials
            + z_squared / (4.0 * trials * trials)
        )
        / denominator
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _read_result_rows(run_dir):
    path = Path(run_dir) / 'per_taskset_results.csv'
    with path.open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def _load_json_no_duplicates(text):
    def pairs_hook(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key: {}'.format(key))
            value[key] = item
        return value
    return json.loads(text, object_pairs_hook=pairs_hook)


def _formal_input_hashes():
    inputs = {}
    for relative in (
        'acceptance_ratio_test.py',
        'system_config_unified_template.yml',
        'global_task_generator.py',
    ):
        path = PROJECT_ROOT / relative
        inputs[relative] = {
            'path': relative,
            'size_bytes': path.stat().st_size if path.is_file() else 0,
            'sha256': _file_sha256(path) if path.is_file() else 'missing',
        }
    return inputs


def _atomic_write_json(path, payload):
    path = Path(path)
    temporary = path.with_name(path.name + '.partial')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    try:
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass


def _read_csv_rows(path):
    with Path(path).open(newline='', encoding='utf-8') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError('missing CSV header: {}'.format(path))
        return reader.fieldnames, list(reader)


def _unique_nonempty(rows, field, path):
    values = sorted({str(row.get(field, '')).strip() for row in rows})
    if not values or '' in values:
        raise ValueError('{} missing {}'.format(path, field))
    return values


ANALYSIS_ATTESTATION_SCHEMA_VERSION = 3
ANALYSIS_PRODUCER_PROFILE_VERSION = 1
MAX_ANALYSIS_PROVENANCE_DEPTH = 16


# Formal derived artifacts are issued only by these repository-owned
# producers.  Validators are selected by immutable ids below; callers cannot
# inject callbacks or turn an arbitrary transform into an official result.
ANALYSIS_PRODUCER_PROFILES = {
    'acceptance_source_equivalent_v1': {
        'profile_version': 1,
        'output_role': 'acceptance_summary',
        'allowed_source_roles': {
            'source_acceptance_aggregate', 'acceptance_summary',
        },
        'required_source_roles': {'source_acceptance_aggregate'},
        'transformation_mode': 'source_equivalent',
        'validator_id': 'source_equivalent_bytes_v1',
        'validator_version': 1,
        'code_files': (
            'scripts/experiment_runner.py',
            'scripts/experiment_analysis.py',
            'acceptance_ratio_test.py',
        ),
    },
    'mechanism_case_selection_v1': {
        'profile_version': 1,
        'output_role': 'mechanism_candidates',
        'allowed_source_roles': {'source_per_taskset_results'},
        'required_source_roles': {'source_per_taskset_results'},
        'transformation_mode': 'producer_validated',
        'validator_id': 'mechanism_case_selection_v1',
        'validator_version': 1,
        'code_files': (
            'scripts/select_mechanism_cases.py',
            'scripts/experiment_analysis.py',
            'scripts/experiment_runner.py',
        ),
    },
    'scheduler_diversity_audit_v1': {
        'profile_version': 1,
        'output_role': 'scheduler_diversity_audit',
        'allowed_source_roles': {'source_per_taskset_results'},
        'required_source_roles': {'source_per_taskset_results'},
        'transformation_mode': 'producer_validated',
        'validator_id': 'scheduler_diversity_audit_v1',
        'validator_version': 1,
        'code_files': (
            'scripts/run_scheduler_diversity_audit.py',
            'scripts/run_mechanism_case_study.py',
            'scripts/experiment_runner.py',
            'acceptance_ratio_test.py',
        ),
    },
    'mechanism_case_study_v1': {
        'profile_version': 1,
        'output_role': 'mechanism_case_summary',
        'allowed_source_roles': {
            'mechanism_candidates', 'source_per_taskset_results',
        },
        'required_source_roles': {
            'mechanism_candidates', 'source_per_taskset_results',
        },
        'transformation_mode': 'producer_validated',
        'validator_id': 'mechanism_case_study_v1',
        'validator_version': 1,
        'code_files': (
            'scripts/run_mechanism_case_study.py',
            'scripts/experiment_runner.py',
            'acceptance_ratio_test.py',
        ),
    },
}


def _producer_profile(producer_id):
    producer_id = str(producer_id or '').strip()
    if not producer_id:
        raise ValueError('derived analysis artifact requires producer_id')
    profile = ANALYSIS_PRODUCER_PROFILES.get(producer_id)
    if not isinstance(profile, dict):
        raise ValueError('unregistered analysis producer: ' + producer_id)
    required = {
        'profile_version', 'output_role', 'allowed_source_roles',
        'required_source_roles', 'transformation_mode', 'validator_id',
        'validator_version', 'code_files',
    }
    if not required.issubset(profile):
        raise ValueError('incomplete analysis producer profile: ' + producer_id)
    if profile['transformation_mode'] not in {
            'source_equivalent', 'producer_validated'}:
        raise ValueError('invalid analysis transformation mode')
    if not str(profile['validator_id']).strip():
        raise ValueError('analysis producer profile has no validator')
    return profile


def _producer_code_identity(producer_id, profile=None):
    profile = profile or _producer_profile(producer_id)
    records = []
    for relative in profile['code_files']:
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid producer code path')
        path = (PROJECT_ROOT / relative).resolve()
        path.relative_to(PROJECT_ROOT.resolve())
        if not path.is_file():
            raise ValueError('producer code file is missing: ' + str(relative))
        records.append({
            'relative_path': relative.as_posix(),
            'size_bytes': path.stat().st_size,
            'sha256': _file_sha256(path),
        })
    combined = hashlib.sha256(json.dumps(
        records, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()
    return {'files': records, 'combined_sha256': combined}


def _canonical_producer_config(config):
    config = {} if config is None else config
    if not isinstance(config, dict):
        raise ValueError('producer_config must be an object')
    try:
        encoded = json.dumps(
            config, sort_keys=True, separators=(',', ':'), allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError('producer_config is not canonical JSON') from error
    return json.loads(encoded)


def _read_frame(path):
    import pandas as pd
    return pd.read_csv(path, keep_default_na=False)


def _assert_frames_equal(actual, expected, context):
    import pandas as pd
    actual = actual.fillna('')
    expected = expected.fillna('')
    try:
        pd.testing.assert_frame_equal(
            actual.reset_index(drop=True), expected.reset_index(drop=True),
            check_dtype=False, check_like=False,
        )
    except AssertionError as error:
        raise ValueError(context + ' semantic validation failed') from error


def _validate_source_equivalent(output_path, source_inputs,
                                _companion_paths, _config):
    output_hash = _file_sha256(output_path)
    if not any(_file_sha256(path) == output_hash for path, _role in source_inputs):
        raise ValueError('source-equivalent producer output differs from source')


def _validate_mechanism_case_selection(output_path, source_inputs,
                                       _companion_paths, config):
    if config:
        raise ValueError('mechanism case selection has no producer config')
    from scripts.experiment_analysis import select_cases
    run_dirs = sorted({
        str(Path(path).resolve().parent)
        for path, role in source_inputs
        if role == 'source_per_taskset_results'
    })
    expected = select_cases(run_dirs, allow_legacy=False)
    _assert_frames_equal(
        _read_frame(output_path), expected,
        'mechanism case selection',
    )


def _source_result_rows(source_inputs):
    import pandas as pd
    frames = [
        _read_frame(path) for path, role in source_inputs
        if role == 'source_per_taskset_results'
    ]
    if not frames:
        raise ValueError('producer has no per-taskset source')
    return pd.concat(frames, ignore_index=True)


def _matched_source_rows(source, seed, utilization, task_idx):
    import pandas as pd
    seed_values = pd.to_numeric(source['seed_base'], errors='coerce')
    utilization_values = pd.to_numeric(
        source['normalized_utilization'], errors='coerce'
    )
    task_values = pd.to_numeric(
        source.get('task_idx', source.get('task_index')), errors='coerce'
    )
    return source[
        (seed_values == float(seed))
        & ((utilization_values - float(utilization)).abs() < 1e-9)
        & (task_values == float(task_idx))
    ]


def _candidate_source_rows(source, candidate):
    matched = source[
        (source['config_id'].astype(str) == str(candidate['config_id']))
        & (source['taskset_hash'].astype(str)
           == str(candidate['taskset_hash']))
    ]
    if matched.empty:
        raise ValueError('derived case has no exact source taskset identity')
    return matched


def _case_key(case_type, seed, utilization, task_idx, scheduler):
    return (
        str(case_type), int(float(seed)), float(utilization),
        int(float(task_idx)), str(scheduler),
    )


def _validate_trace_backed_row(row, matched, scheduler):
    import acceptance_ratio_test as acceptance
    status = str(row.get('simulation_status', '')).strip()
    if status not in {'accepted', 'rejected'}:
        raise ValueError('formal derived row lacks a valid simulation result')
    source = matched[matched['algorithm'] == scheduler]
    if source.empty:
        raise ValueError('derived scheduler row has no source taskset identity')
    source_row = source.iloc[0]
    trace = Path(str(row.get('trace_path', ''))).resolve()
    if not trace.is_file():
        raise ValueError('formal derived row has no trace')
    horizon = float(source_row['simulation_horizon_ms'])
    semantic_hash = str(source_row['taskset_semantic_hash'])
    evaluation = acceptance.TraceParser(str(trace)).evaluate(
        horizon,
        expected_algorithm=scheduler,
        expected_taskset_semantic_hash=semantic_hash,
    )
    if evaluation.status != status:
        raise ValueError('derived row/trace classification mismatch')


def _validate_scheduler_diversity(output_path, source_inputs,
                                  companion_paths, config):
    from scripts.run_scheduler_diversity_audit import (
        AUDIT_FIELDS, CATEGORIES, category_matches, select_tasksets,
    )
    actual = _read_frame(output_path)
    if list(actual.columns) != AUDIT_FIELDS or actual.empty:
        raise ValueError('invalid scheduler diversity output schema')
    source = _source_result_rows(source_inputs)
    if set(config) != {'categories', 'max_tasksets', 'schedulers'}:
        raise ValueError('scheduler diversity producer config is incomplete')
    categories = list(config['categories'])
    schedulers = list(config['schedulers'])
    max_tasksets = int(config['max_tasksets'])
    if (not categories or not schedulers or max_tasksets <= 0
            or len(categories) != len(set(categories))
            or len(schedulers) != len(set(schedulers))
            or not set(categories).issubset(CATEGORIES)):
        raise ValueError('invalid scheduler diversity producer config')
    run_dirs = sorted({
        str(Path(path).resolve().parent)
        for path, role in source_inputs
        if role == 'source_per_taskset_results'
    })
    selected = select_tasksets(
        run_dirs, categories, max_tasksets, allow_legacy=False,
    )
    expected = {}
    for index, candidate in enumerate(selected, start=1):
        case_id = 'audit-{:03d}-seed{}-u{}-i{}'.format(
            index, int(float(candidate['seed_base'])),
            str(candidate['normalized_utilization']).replace('.', 'p'),
            int(float(candidate['task_idx'])),
        )
        for scheduler in schedulers:
            key = _case_key(
                candidate['category'], candidate['seed_base'],
                candidate['normalized_utilization'], candidate['task_idx'],
                scheduler,
            )
            if key in expected:
                raise ValueError('ambiguous scheduler diversity source identity')
            expected[key] = (candidate, case_id)
    observed = {}
    trace_paths = set()
    for _, row in actual.iterrows():
        key = _case_key(
            row['category'], row['seed_base'], row['normalized_utilization'],
            row['task_idx'], row['scheduler'],
        )
        if key in observed:
            raise ValueError('duplicate scheduler diversity output row')
        observed[key] = row
        if key not in expected:
            raise ValueError('unexpected scheduler diversity output row')
        candidate, case_id = expected[key]
        if str(row['audit_case_id']) != case_id:
            raise ValueError('scheduler diversity case id mismatch')
        scheduler = str(row['scheduler'])
        matched = _candidate_source_rows(source, candidate)
        by_scheduler = {
            item['algorithm']: item for _, item in matched.iterrows()
        }
        if not category_matches(str(row['category']), by_scheduler):
            raise ValueError('scheduler diversity category mismatch')
        _validate_trace_backed_row(row, matched, scheduler)
        trace_paths.add(str(Path(str(row['trace_path'])).resolve()))
    if set(observed) != set(expected):
        raise ValueError('scheduler diversity output is incomplete')
    if trace_paths != {str(Path(path).resolve()) for path in companion_paths}:
        raise ValueError('scheduler diversity trace companion mismatch')


def _validate_mechanism_case_study(output_path, source_inputs,
                                   companion_paths, config):
    from scripts.run_mechanism_case_study import (
        SUMMARY_FIELDS, canonical_case_type, read_trace_metrics,
        scheduler_list, select_candidates,
    )
    actual = _read_frame(output_path)
    if list(actual.columns) != SUMMARY_FIELDS or actual.empty:
        raise ValueError('invalid mechanism case output schema')
    candidate_paths = [
        path for path, role in source_inputs if role == 'mechanism_candidates'
    ]
    if len(candidate_paths) != 1:
        raise ValueError('mechanism case producer needs one candidate source')
    candidates = _read_frame(candidate_paths[0])
    snapshots = [
        path for path in companion_paths if Path(path).name == 'candidate_snapshot.csv'
    ]
    if (len(snapshots) != 1
            or _file_sha256(snapshots[0]) != _file_sha256(candidate_paths[0])):
        raise ValueError('mechanism candidate snapshot mismatch')
    source = _source_result_rows(source_inputs)
    if set(config) != {'case_types', 'max_cases_per_type', 'schedulers'}:
        raise ValueError('mechanism case producer config is incomplete')
    case_types = list(config['case_types'])
    max_cases = int(config['max_cases_per_type'])
    configured_schedulers = list(config['schedulers'])
    if (max_cases <= 0
            or len(case_types) != len(set(case_types))
            or len(configured_schedulers) != len(set(configured_schedulers))):
        raise ValueError('invalid mechanism case producer config')
    selected = select_candidates(
        candidates, case_types or None, max_cases,
    )
    override = configured_schedulers or None
    expected = {}
    for ordinal, (_, candidate) in enumerate(selected.iterrows(), start=1):
        case_type = canonical_case_type(candidate['case_type'])
        case_id = '{}-{:03d}'.format(case_type, ordinal)
        for scheduler in scheduler_list(candidate, override):
            key = _case_key(
                case_type, candidate['seed_base'],
                candidate['normalized_utilization'], candidate['task_idx'],
                scheduler,
            )
            if key in expected:
                raise ValueError('ambiguous mechanism case source identity')
            expected[key] = (candidate, case_id)
    observed = {}
    trace_paths = set()
    for _, row in actual.iterrows():
        key = _case_key(
            canonical_case_type(row['case_type']), row['seed_base'],
            row['normalized_utilization'], row['task_idx'], row['scheduler'],
        )
        if key in observed:
            raise ValueError('duplicate mechanism case output row')
        observed[key] = row
        if key not in expected:
            raise ValueError('unexpected mechanism case output row')
        candidate, case_id = expected[key]
        if str(row['case_id']) != case_id:
            raise ValueError('mechanism case id mismatch')
        scheduler = str(row['scheduler'])
        matched = _candidate_source_rows(source, candidate)
        _validate_trace_backed_row(row, matched, scheduler)
        horizon = float(matched.iloc[0]['simulation_horizon_ms'])
        metrics = read_trace_metrics(row['trace_path'], horizon)
        for field in (
            'accepted', 'simulation_status', 'deadline_miss_time',
            'first_missed_task', 'battery_min', 'battery_final',
            'executed_ticks', 'idle_ticks', 'deadline_miss_tick',
            'global_blocking_ticks', 'low_priority_bypass_ticks',
            'sync_batch_reject_ticks', 'alap_slack_wait_ticks',
        ):
            if str(row.get(field, '')) != str(metrics.get(field, '')):
                raise ValueError('mechanism trace metric mismatch: ' + field)
        trace_paths.add(str(Path(str(row['trace_path'])).resolve()))
    if set(observed) != set(expected):
        raise ValueError('mechanism case output is incomplete')
    expected_companions = {
        str(Path(path).resolve()) for path in companion_paths
        if Path(path).name != 'candidate_snapshot.csv'
    }
    if trace_paths != expected_companions:
        raise ValueError('mechanism case trace companion mismatch')


_ANALYSIS_VALIDATORS = {
    'source_equivalent_bytes_v1': _validate_source_equivalent,
    'mechanism_case_selection_v1': _validate_mechanism_case_selection,
    'scheduler_diversity_audit_v1': _validate_scheduler_diversity,
    'mechanism_case_study_v1': _validate_mechanism_case_study,
}


def _run_registered_producer_validator(profile, output_path, source_inputs,
                                       companion_paths, config):
    validator = _ANALYSIS_VALIDATORS.get(profile['validator_id'])
    if validator is None:
        raise ValueError('registered analysis producer has no validator')
    validator(
        Path(output_path).resolve(),
        [(Path(path).resolve(), role) for path, role in source_inputs],
        [Path(path).resolve() for path in companion_paths],
        _canonical_producer_config(config),
    )


def _analysis_attestation_paths(primary_path):
    primary_path = Path(primary_path).resolve()
    return (
        primary_path.with_name(primary_path.name + '.formal_manifest.json'),
        primary_path.with_name(primary_path.name + '.formal_sidecar.json'),
    )


def _analysis_artifact_record(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()
    relative = path.relative_to(root).as_posix()
    if not path.is_file():
        raise ValueError('missing analysis artifact: ' + relative)
    record = {
        'relative_path': relative,
        'size_bytes': path.stat().st_size,
        'sha256': _file_sha256(path),
        'row_count': None,
        'run_id': [],
        'config_id': [],
    }
    if path.suffix == '.csv':
        fields, rows = _read_csv_rows(path)
        record['row_count'] = len(rows)
        if 'run_id' in fields and rows:
            record['run_id'] = _unique_nonempty(rows, 'run_id', relative)
        if 'config_id' in fields and rows:
            record['config_id'] = _unique_nonempty(
                rows, 'config_id', relative
            )
    return record


def _remove_analysis_artifact_attestation(primary_path):
    for path in _analysis_attestation_paths(primary_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _build_analysis_source_record(source_path, role, validation_context=None,
                                  current_depth=0):
    from scripts.experiment_analysis import validate_attested_run_directory

    source_path = Path(source_path).resolve()
    if ('diagnostic_unattested' in source_path.parts
            or source_path.name.startswith('diagnostic_')):
        raise ValueError('diagnostic artifact cannot be a formal source')
    source_dir = source_path.parent
    standard_sidecar = source_dir / RUN_PROVENANCE_FILE
    if standard_sidecar.is_file():
        payload = validate_attested_run_directory(source_dir)
        artifacts = payload.get('result_artifacts', [])
        relative = source_path.relative_to(source_dir).as_posix()
        artifact = next((
            item for item in artifacts
            if item.get('relative_path') == relative
        ), None)
        if artifact is None:
            raise ValueError('source artifact is not listed in run sidecar')
        manifest_path = Path(payload['_execution_manifest_path']).resolve()
        sidecar_path = Path(payload['_result_sidecar_path']).resolve()
        source_type = 'primary_run_artifact'
        run_id = payload['run_id']
        config_ids = payload['config_id']
    else:
        payload = validate_analysis_artifact_attestation(
            source_path,
            _validation_context=validation_context,
            _current_depth=current_depth + 1,
        )
        manifest_path, sidecar_path = _analysis_attestation_paths(source_path)
        source_dir = source_path.parent
        relative = source_path.name
        artifact = next((
            item for item in payload['artifacts']
            if item.get('relative_path') == relative
        ), None)
        if artifact is None:
            raise ValueError('source artifact is not listed in analysis sidecar')
        source_type = payload['artifact_type']
        if (source_type == 'derived_analysis_artifact'
                and payload.get('output_role') != str(role)):
            raise ValueError('derived source artifact role mismatch')
        run_id = payload['run_id']
        config_ids = payload['config_id']
    return {
        'source_type': source_type,
        'source_directory': str(source_dir),
        'source_run_id': run_id,
        'source_config_id': config_ids,
        'source_manifest_relative_path': os.path.relpath(
            manifest_path, source_dir
        ),
        'source_manifest_sha256': _file_sha256(manifest_path),
        'source_sidecar_relative_path': os.path.relpath(
            sidecar_path, source_dir
        ),
        'source_sidecar_sha256': _file_sha256(sidecar_path),
        'source_artifact_relative_path': relative,
        'source_artifact_sha256': _file_sha256(source_path),
        'source_artifact_role': str(role),
    }


def _write_analysis_artifact_attestation(
        primary_path, artifact_type, companion_paths=(), run_id=None,
        config_ids=(), source_artifacts=(), producer_id=None,
        output_role=None, producer_config=None):
    primary_path = Path(primary_path).resolve()
    _remove_analysis_artifact_attestation(primary_path)
    root = primary_path.parent
    paths = [primary_path] + [Path(path).resolve() for path in companion_paths]
    records = [_analysis_artifact_record(path, root) for path in paths]
    sources = [
        _build_analysis_source_record(path, role)
        for path, role in source_artifacts
    ]
    producer = None
    if artifact_type == 'derived_analysis_artifact' and not sources:
        raise ValueError('derived analysis artifact requires a source')
    if artifact_type == 'primary_run_artifact' and sources:
        raise ValueError('primary run artifact cannot declare derived sources')
    if artifact_type == 'derived_analysis_artifact':
        profile = _producer_profile(producer_id)
        output_role = str(output_role or '').strip()
        if output_role != profile['output_role']:
            raise ValueError('analysis producer output role mismatch')
        source_roles = {str(role) for _path, role in source_artifacts}
        if (not source_roles.issubset(profile['allowed_source_roles'])
                or not profile['required_source_roles'].issubset(source_roles)):
            raise ValueError('analysis producer source role mismatch')
        producer_config = _canonical_producer_config(producer_config)
        code_identity = _producer_code_identity(producer_id, profile)
        _run_registered_producer_validator(
            profile, primary_path, source_artifacts, companion_paths,
            producer_config,
        )
        producer = {
            'producer_id': str(producer_id),
            'profile_version': profile['profile_version'],
            'output_role': output_role,
            'transformation_mode': profile['transformation_mode'],
            'validator_id': profile['validator_id'],
            'validator_version': profile['validator_version'],
            'code_files': code_identity['files'],
            'code_sha256': code_identity['combined_sha256'],
            'config': producer_config,
        }
    elif any(value is not None for value in (
            producer_id, output_role, producer_config)):
        raise ValueError('primary artifacts cannot declare a derived producer')
    content_identity = hashlib.sha256(json.dumps(
        [(record['relative_path'], record['sha256']) for record in records],
        separators=(',', ':'), sort_keys=True,
    ).encode('utf-8')).hexdigest()
    run_id = str(run_id or 'derived-' + content_identity).strip()
    if not run_id:
        raise ValueError('analysis attestation requires run_id')
    config_ids = sorted({str(value).strip() for value in config_ids if str(value).strip()})
    if not config_ids:
        discovered = {
            value for record in records for value in record['config_id']
        }
        config_ids = sorted(discovered) or ['bundle-' + content_identity]
    manifest_path, sidecar_path = _analysis_attestation_paths(primary_path)
    manifest = {
        'schema_version': ANALYSIS_ATTESTATION_SCHEMA_VERSION,
        'artifact_type': artifact_type,
        'location_binding': 'content_based',
        'output_role': output_role,
        'official_run_valid': True,
        'run_id': run_id,
        'config_id': config_ids,
        'sources': sources,
        'primary_artifact': primary_path.name,
        'artifacts': records,
        'producer': producer,
    }
    _atomic_write_json(manifest_path, manifest)
    sidecar = {
        'schema_version': ANALYSIS_ATTESTATION_SCHEMA_VERSION,
        'artifact_type': artifact_type,
        'location_binding': 'content_based',
        'output_role': output_role,
        'official_run_valid': True,
        'run_id': run_id,
        'config_id': config_ids,
        'manifest': {
            'relative_path': manifest_path.name,
            'size_bytes': manifest_path.stat().st_size,
            'sha256': _file_sha256(manifest_path),
        },
        'primary_artifact': records[0],
        'producer': producer,
    }
    _atomic_write_json(sidecar_path, sidecar)
    return sidecar_path


def write_analysis_artifact_attestation(
        primary_path, companion_paths=(), run_id=None, config_ids=(),
        source_artifacts=(), producer_id=None, output_role=None,
        producer_config=None):
    """Publish a derived artifact only after validating every source."""
    return _write_analysis_artifact_attestation(
        primary_path, 'derived_analysis_artifact', companion_paths,
        run_id, config_ids, source_artifacts, producer_id, output_role,
        producer_config,
    )


def write_primary_analysis_artifact_attestation(
        primary_path, companion_paths=(), run_id=None, config_ids=()):
    """Publish a primary producer artifact under a distinct schema role."""
    return _write_analysis_artifact_attestation(
        primary_path, 'primary_run_artifact', companion_paths,
        run_id, config_ids, (), None, None, None,
    )


def validate_analysis_artifact_attestation(
        primary_path, _validation_context=None, _current_depth=0):
    """Validate one content-bound analysis bundle with cycle protection."""
    primary_path = Path(primary_path).resolve()
    manifest_path, sidecar_path = _analysis_attestation_paths(primary_path)
    node = (
        str(primary_path), str(manifest_path.resolve()),
        str(sidecar_path.resolve()),
    )
    context = _validation_context
    if context is None:
        context = {'active': set(), 'visited': {}}
    if _current_depth > MAX_ANALYSIS_PROVENANCE_DEPTH:
        raise ValueError('analysis provenance maximum depth exceeded')
    if node in context['active']:
        raise ValueError('analysis provenance cycle detected')
    if node in context['visited']:
        return context['visited'][node]
    context['active'].add(node)
    try:
        payload = _validate_analysis_artifact_attestation_impl(
            primary_path, context, _current_depth
        )
    except RecursionError as error:
        raise ValueError('analysis provenance recursion rejected') from error
    finally:
        context['active'].discard(node)
    context['visited'][node] = payload
    return payload


def _validate_analysis_artifact_attestation_impl(
        primary_path, validation_context, current_depth):
    """Validate a derived artifact's manifest, sidecar and all companions."""
    primary_path = Path(primary_path).resolve()
    root = primary_path.parent
    manifest_path, sidecar_path = _analysis_attestation_paths(primary_path)
    if not manifest_path.is_file():
        raise ValueError('missing_analysis_manifest: ' + str(manifest_path))
    if not sidecar_path.is_file():
        raise ValueError('missing_analysis_sidecar: ' + str(sidecar_path))
    manifest = _load_json_no_duplicates(
        manifest_path.read_text(encoding='utf-8')
    )
    sidecar = _load_json_no_duplicates(
        sidecar_path.read_text(encoding='utf-8')
    )
    for name, payload in (('manifest', manifest), ('sidecar', sidecar)):
        if (not isinstance(payload, dict)
                or payload.get('schema_version') !=
                ANALYSIS_ATTESTATION_SCHEMA_VERSION
                or payload.get('official_run_valid') is not True
                or not isinstance(payload.get('run_id'), str)
                or not payload['run_id']
                or not isinstance(payload.get('config_id'), list)
                or not payload['config_id']):
            raise ValueError('invalid_analysis_' + name)
    artifact_type = manifest.get('artifact_type')
    if artifact_type not in {
            'primary_run_artifact', 'derived_analysis_artifact'}:
        raise ValueError('invalid_analysis_artifact_type')
    if (manifest.get('location_binding') != 'content_based'
            or sidecar.get('location_binding') != 'content_based'):
        raise ValueError('analysis_location_binding_mismatch')
    if (manifest['run_id'] != sidecar['run_id']
            or manifest['config_id'] != sidecar['config_id']
            or artifact_type != sidecar.get('artifact_type')
            or manifest.get('output_role') != sidecar.get('output_role')
            or manifest.get('producer') != sidecar.get('producer')):
        raise ValueError('analysis_manifest_sidecar_identity_mismatch')
    manifest_record = sidecar.get('manifest')
    expected_manifest = {
        'relative_path': manifest_path.name,
        'size_bytes': manifest_path.stat().st_size,
        'sha256': _file_sha256(manifest_path),
    }
    if manifest_record != expected_manifest:
        raise ValueError('analysis_manifest_artifact_mismatch')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError('analysis_manifest_has_no_artifacts')
    observed = []
    for record in artifacts:
        if not isinstance(record, dict):
            raise ValueError('invalid_analysis_artifact_record')
        relative = Path(str(record.get('relative_path', '')))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid_analysis_artifact_path')
        observed.append(_analysis_artifact_record(root / relative, root))
    if observed != artifacts:
        raise ValueError('analysis_artifact_mismatch')
    if (manifest.get('primary_artifact') != primary_path.name
            or observed[0]['relative_path'] != primary_path.name
            or sidecar.get('primary_artifact') != observed[0]):
        raise ValueError('analysis_primary_artifact_mismatch')
    row_run_ids = observed[0]['run_id']
    if row_run_ids and row_run_ids != [manifest['run_id']]:
        raise ValueError('analysis_row_run_id_mismatch')
    row_config_ids = observed[0]['config_id']
    if row_config_ids and row_config_ids != manifest['config_id']:
        raise ValueError('analysis_row_config_id_mismatch')
    sources = manifest.get('sources')
    if not isinstance(sources, list):
        raise ValueError('analysis_sources_must_be_a_list')
    if artifact_type == 'derived_analysis_artifact' and not sources:
        raise ValueError('derived_analysis_source_missing')
    if artifact_type == 'primary_run_artifact' and sources:
        raise ValueError('primary_analysis_has_sources')
    producer = manifest.get('producer')
    profile = None
    if artifact_type == 'derived_analysis_artifact':
        if not isinstance(producer, dict):
            raise ValueError('derived_analysis_producer_missing')
        producer_id = producer.get('producer_id')
        profile = _producer_profile(producer_id)
        expected_code = _producer_code_identity(producer_id, profile)
        expected_producer = {
            'producer_id': str(producer_id),
            'profile_version': profile['profile_version'],
            'output_role': profile['output_role'],
            'transformation_mode': profile['transformation_mode'],
            'validator_id': profile['validator_id'],
            'validator_version': profile['validator_version'],
            'code_files': expected_code['files'],
            'code_sha256': expected_code['combined_sha256'],
            'config': _canonical_producer_config(producer.get('config')),
        }
        if (producer != expected_producer
                or manifest.get('output_role') != profile['output_role']):
            raise ValueError('analysis_producer_identity_mismatch')
        source_roles = {
            str(source.get('source_artifact_role')) for source in sources
            if isinstance(source, dict)
        }
        if (not source_roles.issubset(profile['allowed_source_roles'])
                or not profile['required_source_roles'].issubset(source_roles)):
            raise ValueError('analysis_producer_source_role_mismatch')
    elif producer is not None or manifest.get('output_role') is not None:
        raise ValueError('primary_analysis_has_derived_producer')
    source_inputs = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('invalid_analysis_source_record')
        source_path = (
            Path(source['source_directory']) /
            source['source_artifact_relative_path']
        ).resolve()
        observed_source = _build_analysis_source_record(
            source_path,
            source['source_artifact_role'],
            validation_context=validation_context,
            current_depth=current_depth,
        )
        if observed_source != source:
            raise ValueError('analysis_source_provenance_mismatch')
        source_inputs.append((source_path, source['source_artifact_role']))
    if artifact_type == 'derived_analysis_artifact':
        companion_paths = [
            root / record['relative_path'] for record in artifacts[1:]
        ]
        _run_registered_producer_validator(
            profile, primary_path, source_inputs, companion_paths,
            producer['config'],
        )
    return manifest


def _artifact_record(run_dir, relative_path):
    run_dir = Path(run_dir).resolve()
    path = (run_dir / relative_path).resolve()
    path.relative_to(run_dir)
    if not path.is_file():
        raise ValueError('missing result artifact: {}'.format(relative_path))
    record = {
        'relative_path': Path(relative_path).as_posix(),
        'size_bytes': path.stat().st_size,
        'sha256': _file_sha256(path),
        'schema_version': None,
        'row_count': None,
        'run_id': [],
        'config_id': [],
        'config_group_id': [],
    }
    if path.suffix == '.csv':
        _fields, rows = _read_csv_rows(path)
        record['row_count'] = len(rows)
        if not rows:
            raise ValueError('empty result artifact: {}'.format(relative_path))
        record['run_id'] = _unique_nonempty(rows, 'run_id', relative_path)
        record['config_id'] = _unique_nonempty(rows, 'config_id', relative_path)
        record['config_group_id'] = _unique_nonempty(
            rows, 'config_group_id', relative_path
        )
        record['schema_version'] = _unique_nonempty(
            rows, 'result_schema_version', relative_path
        )
    elif path.suffix == '.jsonl':
        rows = []
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError('JSONL row must be an object')
                    rows.append(value)
        if not rows:
            raise ValueError('empty result artifact: {}'.format(relative_path))
        record['row_count'] = len(rows)
        record['run_id'] = _unique_nonempty(rows, 'run_id', relative_path)
        record['config_id'] = _unique_nonempty(rows, 'config_id', relative_path)
        record['config_group_id'] = _unique_nonempty(
            rows, 'config_group_id', relative_path
        )
        record['schema_version'] = _unique_nonempty(
            rows, 'result_schema_version', relative_path
        )
    elif path.suffix == '.json':
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('trace must be a JSON object')
        record['row_count'] = len(data.get('events', []))
        record['schema_version'] = data.get('trace_schema_version')
        run_id = data.get('run_id')
        if not isinstance(run_id, str) or not run_id:
            raise ValueError('trace missing run_id')
        record['run_id'] = [run_id]
    return record


def _validate_result_semantics(run_dir, validate_mutable_sources=True,
                               expected_rta_enabled=None):
    import acceptance_ratio_test as acceptance

    run_dir = Path(run_dir).resolve()
    _fields, per_rows = _read_csv_rows(run_dir / 'per_taskset_results.csv')
    _aggregate_fields, aggregate_rows = _read_csv_rows(
        run_dir / 'acceptance_ratio_data.csv'
    )
    _common_fields, common_rows = _read_csv_rows(
        run_dir / 'common_complete_acceptance_data.csv'
    )
    for name, rows in (
        ('per_taskset_results.csv', per_rows),
        ('acceptance_ratio_data.csv', aggregate_rows),
        ('common_complete_acceptance_data.csv', common_rows),
    ):
        versions = _unique_nonempty(rows, 'result_schema_version', name)
        if versions != [str(acceptance.RESULT_SCHEMA_VERSION)]:
            raise ValueError('unsupported formal result schema in ' + name)
    acceptance.validate_formal_result_identities(
        per_rows, 'resume provenance validation'
    )
    canonical_schedulers = set(acceptance.ALGORITHMS)
    if {row.get('algorithm') for row in per_rows} - canonical_schedulers:
        raise ValueError('invalid scheduler in per-taskset results')
    if {row.get('algorithm') for row in aggregate_rows} - canonical_schedulers:
        raise ValueError('invalid scheduler in aggregate results')

    def require_asap_block_identity(row, source, require_observed=False):
        algorithm = acceptance.ASAP_BLOCK_ALGORITHM
        display = acceptance.ALGO_DISPLAY_NAMES[algorithm]
        implementation = acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm]
        required = {
            'algorithm': algorithm,
            'expected_configured_scheduler': algorithm,
            'expected_scheduler_display_name': display,
            'expected_scheduler_implementation': implementation,
        }
        if require_observed:
            required.update({
                'observed_configured_scheduler': algorithm,
                'observed_scheduler_display_name': display,
                'observed_scheduler_implementation': implementation,
                'configured_scheduler': algorithm,
                'scheduler_display_name': display,
                'scheduler_implementation': implementation,
            })
        for field, expected in required.items():
            if str(row.get(field, '')).strip() != expected:
                raise ValueError(
                    '{} must bind RTA {} to {}'.format(
                        source, field, expected
                    )
                )

    run_ids = _unique_nonempty(per_rows, 'run_id', 'per_taskset_results.csv')
    if len(run_ids) != 1:
        raise ValueError('result artifacts must contain exactly one run_id')
    config_ids = _unique_nonempty(
        per_rows, 'config_id', 'per_taskset_results.csv'
    )
    group_ids = _unique_nonempty(
        per_rows, 'config_group_id', 'per_taskset_results.csv'
    )
    for name, rows in (
        ('acceptance_ratio_data.csv', aggregate_rows),
        ('common_complete_acceptance_data.csv', common_rows),
    ):
        if _unique_nonempty(rows, 'run_id', name) != run_ids:
            raise ValueError('run_id mismatch in ' + name)
        if _unique_nonempty(rows, 'config_id', name) != config_ids:
            raise ValueError('config_id mismatch in ' + name)
        if _unique_nonempty(rows, 'config_group_id', name) != group_ids:
            raise ValueError('config_group_id mismatch in ' + name)

    tasksets = {}
    for row in per_rows:
        semantic_hash = str(row.get('taskset_semantic_hash', '')).strip()
        raw_hash = str(row.get('taskset_raw_file_hash', '')).strip()
        if not semantic_hash or row.get('taskset_hash') != semantic_hash:
            raise ValueError('taskset semantic hash mismatch in results')
        if not raw_hash:
            raise ValueError('missing taskset raw file hash')
        utilization = float(row['normalized_utilization'])
        task_index = int(row.get('task_idx', row.get('task_index')))
        relative = Path('tasks') / 'taskset_u{:.2f}_{:03d}.yml'.format(
            utilization, task_index
        )
        task_path = run_dir / relative
        current_raw = _file_sha256(task_path)
        current_semantic = acceptance.taskset_semantic_hash(task_path)
        if current_raw != raw_hash or current_semantic != semantic_hash:
            raise ValueError('taskset hash mismatch: {}'.format(relative))
        key = relative.as_posix()
        descriptor = {
            'relative_path': key,
            'semantic_sha256': semantic_hash,
            'raw_file_sha256': raw_hash,
            'size_bytes': task_path.stat().st_size,
        }
        if key in tasksets and tasksets[key] != descriptor:
            raise ValueError('conflicting taskset provenance: ' + key)
        tasksets[key] = descriptor

    def same_number(actual, expected):
        if str(actual).strip() == '':
            return math.isnan(expected)
        observed = float(actual)
        if math.isnan(expected):
            return math.isnan(observed)
        return math.isclose(observed, expected, rel_tol=0, abs_tol=1e-12)

    grouped = {}
    for row in per_rows:
        key = (
            row['config_id'], row['algorithm'],
            format(float(row['normalized_utilization']), '.15g'),
        )
        counts = grouped.setdefault(key, {
            'accepted': 0, 'rejected': 0, 'timeout': 0, 'error': 0,
            'generation_error': 0,
        })
        raw_status = str(row.get('status', '')).strip()
        status = 'error' if raw_status == 'generation_error' else raw_status
        if status not in counts:
            status = 'error'
        counts[status] += 1
        if ('generation' in str(row.get('reason', '')).lower()
                or raw_status == 'generation_error'):
            counts['generation_error'] += 1
    for row in aggregate_rows:
        key = (
            row['config_id'], row['algorithm'],
            format(float(row['normalized_utilization']), '.15g'),
        )
        if key not in grouped:
            raise ValueError('aggregate row has no per-taskset source')
        counts = grouped.pop(key)
        expected = {
            'simulation_num_accepted': counts['accepted'],
            'simulation_num_rejected': counts['rejected'],
            'simulation_num_timeout': counts['timeout'],
            'simulation_num_error': counts['error'],
            'simulation_num_generation_error': counts['generation_error'],
        }
        for field, value in expected.items():
            if int(row[field]) != value:
                raise ValueError('aggregate count mismatch: ' + field)
        valid = counts['accepted'] + counts['rejected']
        requested = sum(counts[field] for field in (
            'accepted', 'rejected', 'timeout', 'error'
        ))
        integer_expected = {
            'num_samples': requested,
            'num_successful': counts['accepted'],
            'num_valid_samples': valid,
            'num_requested_samples': requested,
            'simulation_num_valid': valid,
            'simulation_num_requested': requested,
        }
        for field, value in integer_expected.items():
            if int(row[field]) != value:
                raise ValueError('aggregate count mismatch: ' + field)
        numeric_expected = {
            'acceptance_ratio': (
                counts['accepted'] / valid if valid else math.nan
            ),
            'unconditional_success_rate': (
                counts['accepted'] / requested if requested else math.nan
            ),
            'error_rate': counts['error'] / requested if requested else math.nan,
            'timeout_rate': (
                counts['timeout'] / requested if requested else math.nan
            ),
        }
        for field, value in numeric_expected.items():
            if not same_number(row[field], value):
                raise ValueError('aggregate ratio mismatch: ' + field)
        if str(row['no_valid_simulations']).lower() != str(valid == 0).lower():
            raise ValueError('aggregate no-valid mismatch')
    if grouped:
        raise ValueError('per-taskset groups missing from aggregate')

    # common-complete is a derived artifact: rebuild the nine-scheduler
    # intersection from per-taskset rows instead of trusting its ratios.
    common_expected = {}
    common_groups = {}
    for row in per_rows:
        group_key = (
            row['config_group_id'],
            format(float(row['normalized_utilization']), '.15g'),
        )
        identity = (row['config_id'], row['taskset_semantic_hash'])
        by_task = common_groups.setdefault(group_key, {})
        by_scheduler = by_task.setdefault(identity, {})
        scheduler = row['algorithm']
        if scheduler in by_scheduler:
            raise ValueError('duplicate common-complete source row')
        by_scheduler[scheduler] = row
    for group_key, by_task in common_groups.items():
        complete = []
        excluded = {'error': 0, 'timeout': 0, 'generation_error': 0,
                    'missing': 0}
        for identity, schedulers in by_task.items():
            if set(schedulers) != canonical_schedulers:
                excluded['missing'] += 1
                continue
            statuses = [str(item.get('status', '')).strip()
                        for item in schedulers.values()]
            if all(status in {'accepted', 'rejected'} for status in statuses):
                complete.append(identity)
            elif any(status == 'timeout' for status in statuses):
                excluded['timeout'] += 1
            elif any(status == 'generation_error' or
                     'generation' in str(item.get('reason', '')).lower()
                     for status, item in zip(statuses, schedulers.values())):
                excluded['generation_error'] += 1
            else:
                excluded['error'] += 1
        requested = len(by_task)
        for scheduler in canonical_schedulers:
            statuses = [by_task[key][scheduler]['status'] for key in complete]
            accepted = statuses.count('accepted')
            rejected = statuses.count('rejected')
            wilson_low, wilson_high = _wilson_interval(
                accepted, len(complete)
            )
            common_expected[(group_key, scheduler)] = {
                'requested_num_tasksets': requested,
                'common_complete_num_tasksets': len(complete),
                'common_complete_excluded_num': requested - len(complete),
                'common_complete_excluded_error': excluded['error'],
                'common_complete_excluded_timeout': excluded['timeout'],
                'common_complete_excluded_generation_error': excluded['generation_error'],
                'common_complete_excluded_missing': excluded['missing'],
                'common_complete_accepted': accepted,
                'common_complete_rejected': rejected,
                'common_complete_ratio': len(complete) / requested if requested else math.nan,
                'common_complete_acceptance_ratio': accepted / len(complete) if complete else math.nan,
                'common_complete_unconditional_success_rate': accepted / requested if requested else math.nan,
                'common_complete_wilson_ci95_low': wilson_low,
                'common_complete_wilson_ci95_high': wilson_high,
                'common_complete_no_valid_simulations': not complete,
                'official_run_valid': len(complete) == requested,
                'official_run_invalid': len(complete) != requested,
            }
    observed_common = set()
    for row in common_rows:
        key = ((row['config_group_id'],
                format(float(row['normalized_utilization']), '.15g')),
               row['algorithm'])
        if key in observed_common or key not in common_expected:
            raise ValueError('unexpected/duplicate common-complete row')
        observed_common.add(key)
        expected = common_expected[key]
        for field in (
            'requested_num_tasksets', 'common_complete_num_tasksets',
            'common_complete_excluded_num', 'common_complete_excluded_error',
            'common_complete_excluded_timeout',
            'common_complete_excluded_generation_error',
            'common_complete_excluded_missing', 'common_complete_accepted',
            'common_complete_rejected',
        ):
            if int(row[field]) != expected[field]:
                raise ValueError('common-complete mismatch: ' + field)
        for field in ('common_complete_ratio',
                      'common_complete_acceptance_ratio',
                      'common_complete_unconditional_success_rate',
                      'common_complete_wilson_ci95_low',
                      'common_complete_wilson_ci95_high'):
            if not same_number(row[field], expected[field]):
                raise ValueError('common-complete mismatch: ' + field)
        for field in ('common_complete_no_valid_simulations',
                      'official_run_valid', 'official_run_invalid'):
            if str(row[field]).lower() != str(expected[field]).lower():
                raise ValueError('common-complete mismatch: ' + field)
    if observed_common != set(common_expected):
        raise ValueError('common-complete rows missing')

    # Raw RTA JSONL rows must align one-to-one with their simulation rows.
    def strict_csv_bool(value, field):
        normalized = str(value).strip().lower()
        if normalized not in {'true', 'false'}:
            raise ValueError(field + ' must be an explicit boolean')
        return normalized == 'true'

    per_rta_enabled = []
    for row in per_rows:
        enabled = strict_csv_bool(row.get('rta_enabled', ''), 'rta_enabled')
        per_rta_enabled.append(enabled)
        version = str(row.get('rta_version', '')).strip()
        fingerprint = str(row.get('rta_code_fingerprint', '')).strip()
        if enabled:
            if version != acceptance.RTA_VERSION:
                raise ValueError('enabled RTA row has invalid rta_version')
            require_asap_block_identity(
                row, 'RTA-enabled simulation row', require_observed=True
            )
            if fingerprint in {'', 'not_used'}:
                raise ValueError('enabled RTA row has no code fingerprint')
        else:
            if version != acceptance.RTA_INACTIVE_VERSION:
                raise ValueError('disabled RTA row has active rta_version')
            if fingerprint != 'not_used':
                raise ValueError('disabled RTA row has active fingerprint')
            if strict_csv_bool(
                    row.get('rta_attempted', 'false'), 'rta_attempted'):
                raise ValueError('disabled RTA row was attempted')

    aggregate_rta_enabled = []
    for row in aggregate_rows:
        enabled = strict_csv_bool(
            row.get('rta_enabled', ''), 'aggregate rta_enabled'
        )
        aggregate_rta_enabled.append(enabled)
        version = str(row.get('rta_version', '')).strip()
        if enabled:
            if version != acceptance.RTA_VERSION:
                raise ValueError('enabled RTA aggregate has invalid version')
            require_asap_block_identity(row, 'RTA-enabled aggregate row')
        elif version != acceptance.RTA_INACTIVE_VERSION:
            raise ValueError('disabled RTA aggregate has active version')

    run_rta_enabled = any(per_rta_enabled) or any(aggregate_rta_enabled)
    enabled_per_algorithms = {
        row['algorithm'] for row, enabled in zip(per_rows, per_rta_enabled)
        if enabled
    }
    enabled_aggregate_algorithms = {
        row['algorithm']
        for row, enabled in zip(aggregate_rows, aggregate_rta_enabled)
        if enabled
    }
    expected_enabled_algorithms = (
        {acceptance.ASAP_BLOCK_ALGORITHM} if run_rta_enabled else set()
    )
    if (enabled_per_algorithms != expected_enabled_algorithms
            or enabled_aggregate_algorithms != expected_enabled_algorithms):
        raise ValueError('RTA enabled contract is inconsistent across rows')

    def row_rta_active(row):
        truth = lambda value: str(value).strip().lower() in {'true', '1'}
        status = str(row.get('rta_status', '')).strip().lower()
        return (
            truth(row.get('rta_enabled', False))
            or truth(row.get('rta_attempted', False))
            or status not in {'', 'disabled', 'not_applicable'}
        )

    active_rta_rows = [row for row in per_rows if row_rta_active(row)]
    for row, enabled in zip(per_rows, per_rta_enabled):
        if row_rta_active(row) != enabled:
            raise ValueError('explicit rta_enabled contradicts per-taskset fields')
    declared_rta_fingerprints = {
        str(row.get('rta_code_fingerprint', '')).strip()
        for row in per_rows
    }
    rta_declared_by_config = bool(
        declared_rta_fingerprints - {'', 'not_used'}
    )
    active_rta_aggregates = []
    for row, enabled in zip(aggregate_rows, aggregate_rta_enabled):
        counters = [
            int(float(row.get(field, 0) or 0))
            for field in (
                'rta_num_analyzed', 'rta_num_proven', 'rta_num_unproven',
                'rta_num_errors', 'rta_soundness_violations',
                'sim_success_rta_proven', 'sim_success_rta_unproven',
            )
        ]
        if any(counters):
            if not enabled:
                raise ValueError(
                    'disabled RTA aggregate has nonzero RTA counters'
                )
            active_rta_aggregates.append(row)
        elif enabled:
            # An enabled run may still contain an error record, but it must
            # have one raw row and therefore rta_num_analyzed is nonzero.
            raise ValueError('enabled RTA aggregate has no analyzed rows')
    for row in active_rta_rows:
        require_asap_block_identity(
            row, 'RTA-enabled simulation row', require_observed=True
        )
    for row in active_rta_aggregates:
        require_asap_block_identity(row, 'RTA-enabled aggregate row')

    rta_file = run_dir / 'rta_results.jsonl'
    inferred_rta_enabled = bool(
        active_rta_rows or active_rta_aggregates or rta_declared_by_config
        or rta_file.is_file() or run_rta_enabled
    )
    if expected_rta_enabled is True:
        inferred_rta_enabled = True
    if (expected_rta_enabled is not None
            and bool(expected_rta_enabled) != run_rta_enabled):
        raise ValueError('command and explicit rta_enabled disagree')
    if expected_rta_enabled is False and inferred_rta_enabled:
        raise ValueError('RTA artifacts present while command disables RTA')
    if inferred_rta_enabled and not rta_file.is_file():
        raise ValueError('RTA enabled but rta_results.jsonl is missing')
    if not inferred_rta_enabled and rta_file.is_file():
        raise ValueError('stale rta_results.jsonl while RTA is disabled')
    if rta_file.is_file():
        with rta_file.open(encoding='utf-8') as handle:
            rta_rows = [json.loads(line) for line in handle if line.strip()]
        if not rta_rows:
            raise ValueError('rta_results.jsonl is empty')
        per_map = {(row['config_id'], row['taskset_semantic_hash'],
                    row['algorithm']): row for row in per_rows}
        seen_rta = set()
        for row in rta_rows:
            required_rta = {
                'run_id', 'config_id', 'config_group_id',
                'taskset_semantic_hash', 'algorithm', 'rta_status',
                'rta_version', 'simulation_status', 'rta_enabled',
                'rta_attempted', 'rta_runtime_sec', 'rta_timed_out',
                'rta_error', 'rta_bound', 'simulated_response_time',
                'rta_schedulable', 'sim_schedulable',
                'soundness_violation', 'soundness_valid',
                'soundness_excluded_reason',
                'expected_configured_scheduler',
                'expected_scheduler_display_name',
                'expected_scheduler_implementation',
                'observed_configured_scheduler',
                'observed_scheduler_display_name',
                'observed_scheduler_implementation',
                'configured_scheduler', 'scheduler_display_name',
                'scheduler_implementation',
            }
            if not required_rta.issubset(row):
                raise ValueError('RTA row missing required semantic fields')
            for field in (
                'run_id', 'config_id', 'config_group_id',
                'taskset_semantic_hash', 'algorithm', 'rta_status',
                'rta_version', 'simulation_status',
            ):
                if not isinstance(row[field], str) or not row[field]:
                    raise ValueError('RTA row invalid string field: ' + field)
            key = (row.get('config_id'), row.get('taskset_semantic_hash'),
                   row.get('algorithm'))
            if key in seen_rta or key not in per_map:
                raise ValueError('duplicate/orphan RTA row')
            seen_rta.add(key)
            source = per_map[key]
            require_asap_block_identity(
                row, 'raw RTA row', require_observed=True
            )
            require_asap_block_identity(
                source, 'RTA simulation row', require_observed=True
            )
            for field in ('run_id', 'config_group_id', 'rta_status',
                          'rta_version'):
                if str(row.get(field, '')) != str(source.get(field, '')):
                    raise ValueError('RTA row mismatch: ' + field)
            if str(row.get('simulation_status', '')) != str(source['status']):
                raise ValueError('RTA simulation status mismatch')
            for field in (
                'rta_enabled', 'rta_attempted', 'rta_timed_out',
                'rta_schedulable', 'sim_schedulable',
                'soundness_violation', 'soundness_valid',
            ):
                if type(row[field]) is not bool:
                    raise ValueError('RTA row invalid boolean field: ' + field)
                if row[field] != (
                        str(source.get(field, '')).lower() == 'true'):
                    raise ValueError('RTA row mismatch: ' + field)
            for json_field, csv_field in (
                ('rta_runtime_sec', 'rta_runtime_sec'),
                ('rta_bound', 'rta_response_bound'),
                ('simulated_response_time', 'simulated_response_time'),
            ):
                observed = row[json_field]
                expected = source.get(csv_field, '')
                if observed is None and str(expected).strip() == '':
                    continue
                if (isinstance(observed, bool)
                        or not isinstance(observed, (int, float))
                        or not math.isfinite(observed)
                        or observed < 0):
                    raise ValueError(
                        'RTA row invalid numeric field: ' + json_field
                    )
                if not same_number(expected, observed):
                    raise ValueError('RTA row mismatch: ' + csv_field)
            for field in ('rta_error', 'soundness_excluded_reason'):
                if row[field] is not None and not isinstance(row[field], str):
                    raise ValueError('RTA row invalid text field: ' + field)
                observed = '' if row[field] is None else row[field]
                if observed != str(source.get(field, '')):
                    raise ValueError('RTA row mismatch: ' + field)
            expected_violation = (
                bool(row['rta_schedulable'])
                and str(source['status']) == 'rejected'
            )
            if bool(row['soundness_violation']) != expected_violation:
                raise ValueError('RTA soundness definition mismatch')
        expected_rta = {key for key, row in per_map.items()
                        if str(row.get('rta_enabled', '')).lower() == 'true'}
        if seen_rta != expected_rta:
            raise ValueError('RTA rows missing')

        # Aggregate RTA counters are a second derived artifact.  Recompute
        # them from the attested raw rows; byte hashes alone cannot detect a
        # self-consistent replacement of both files.
        rta_by_aggregate = {}
        for row in rta_rows:
            source = per_map[(row['config_id'], row['taskset_semantic_hash'],
                              row['algorithm'])]
            key = (source['config_id'], source['algorithm'],
                   format(float(source['normalized_utilization']), '.15g'))
            bucket = rta_by_aggregate.setdefault(key, [])
            bucket.append((row, source))
        for aggregate in aggregate_rows:
            key = (aggregate['config_id'], aggregate['algorithm'],
                   format(float(aggregate['normalized_utilization']), '.15g'))
            records = rta_by_aggregate.pop(key, [])
            if records:
                require_asap_block_identity(
                    aggregate, 'RTA aggregate row'
                )
            statuses = [str(row.get('rta_status', ''))
                        for row, _source in records]
            expected = {
                'rta_num_analyzed': len(records),
                'rta_num_proven': statuses.count('proven_under_assumptions'),
                'rta_num_unproven': statuses.count('rta_unproven'),
                'rta_num_errors': sum(status in {
                    'rta_error', 'rta_timeout', 'timeout', 'failed'
                } for status in statuses),
                'rta_soundness_violations': sum(
                    bool(row.get('soundness_violation', False))
                    for row, _source in records
                ),
                'sim_success_rta_proven': sum(
                    source['status'] == 'accepted'
                    and row.get('rta_status') == 'proven_under_assumptions'
                    for row, source in records
                ),
                'sim_success_rta_unproven': sum(
                    source['status'] == 'accepted'
                    and row.get('rta_status') == 'rta_unproven'
                    for row, source in records
                ),
            }
            for field, value in expected.items():
                if int(aggregate[field]) != value:
                    raise ValueError('RTA aggregate mismatch: ' + field)
            ratio = (expected['rta_num_proven'] / expected['rta_num_analyzed']
                     if expected['rta_num_analyzed'] else math.nan)
            if not same_number(aggregate['rta_proven_ratio'], ratio):
                raise ValueError('RTA aggregate mismatch: rta_proven_ratio')
        if rta_by_aggregate:
            raise ValueError('RTA aggregate row missing')

    if any(str(row.get('official_run_valid', '')).lower() != 'true'
           for row in aggregate_rows):
        raise ValueError('official result is not valid')

    trace_paths = set()
    for row in per_rows:
        raw_path = str(row.get('trace_path', '')).strip()
        if not raw_path:
            continue
        trace = Path(raw_path).resolve()
        relative = trace.relative_to(run_dir).as_posix()
        if relative in trace_paths:
            raise ValueError(
                'trace referenced by multiple result rows: ' + relative
            )
        if not trace.is_file():
            raise ValueError('result row trace is missing: ' + relative)
        parser = acceptance.TraceParser(str(trace))
        expected_horizon = float(row['simulation_horizon_ms'])
        evaluation = parser.evaluate(
            expected_horizon, expected_algorithm=row['algorithm'],
            expected_taskset_semantic_hash=row['taskset_semantic_hash'],
        )
        if parser.metadata.get('run_id') != row['run_id']:
            raise ValueError('result row trace run_id mismatch: ' + relative)
        status = str(row.get('status', '')).strip().lower()
        if status not in {'accepted', 'rejected'} or evaluation.status != status:
            raise ValueError(
                'result row trace classification mismatch: ' + relative
            )
        if not math.isclose(
                float(row['observed_trace_horizon_ms']),
                float(evaluation.observed_horizon_ms),
                rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(
                'result row trace observed horizon mismatch: ' + relative
            )
        if (str(row['simulation_completed']).strip().lower()
                != str(evaluation.simulation_completed).lower()
                or str(row['simulation_completion_reason']).strip()
                != str(evaluation.completion_reason)):
            raise ValueError(
                'result row trace completion mismatch: ' + relative
            )
        semantic_hash = parser.data.get('taskset_semantic_hash')
        if semantic_hash != row['taskset_semantic_hash']:
            raise ValueError(
                'result row trace taskset identity mismatch: ' + relative
            )
        trace_paths.add(relative)

    solar_rows = {
        (
            row.get('solar_source_path', ''),
            row.get('solar_source_sha256', ''),
            row.get('solar_snapshot_relative_path', ''),
            row.get('solar_snapshot_time', ''),
            row.get('solar_profile_sha256', ''),
            row.get('solar_profile_size', ''),
            row.get('actual_simulator_solar_path', ''),
        ) for row in per_rows
    }
    if len(solar_rows) != 1:
        raise ValueError('inconsistent solar snapshot provenance')
    solar = next(iter(solar_rows))
    solar_snapshot = {
        'source_original_path': solar[0],
        'source_sha256': solar[1],
        'snapshot_relative_path': solar[2],
        'source_snapshot_time': solar[3],
        'snapshot_sha256': solar[4],
        'snapshot_size': int(solar[5] or 0),
        'actual_simulator_solar_path': solar[6],
    }
    if solar_snapshot['snapshot_sha256'] != 'not_used':
        if solar_snapshot['source_sha256'] != solar_snapshot['snapshot_sha256']:
            raise ValueError('solar source/snapshot hash mismatch')
        snapshot = (run_dir / solar_snapshot['snapshot_relative_path']).resolve()
        snapshot.relative_to(run_dir)
        if str(snapshot) != str(Path(solar_snapshot[
                'actual_simulator_solar_path']).resolve()):
            raise ValueError('simulator solar path is not the snapshot')
        if (not snapshot.is_file()
                or snapshot.stat().st_size != solar_snapshot['snapshot_size']
                or _file_sha256(snapshot) != solar_snapshot['snapshot_sha256']):
            raise ValueError('solar snapshot hash mismatch')

    rta_identity_rows = [
        row for row, enabled in zip(per_rows, per_rta_enabled) if enabled
    ]
    rta_fingerprints = sorted({
        str(row.get('rta_code_fingerprint', '')).strip()
        for row in rta_identity_rows
    }) or ['not_used']
    if not rta_fingerprints or '' in rta_fingerprints:
        raise ValueError('missing RTA code fingerprint')
    rta_paths = sorted({
        str(row.get('rta_code_snapshot_path', '')).strip()
        for row in rta_identity_rows
        if str(row.get('rta_code_snapshot_path', '')).strip()
    })
    rta_hashes = sorted({
        str(row.get('rta_code_snapshot_sha256', '')).strip()
        for row in rta_identity_rows
    }) or ['not_used']
    rta_sizes = sorted({
        int(row.get('rta_code_snapshot_size', 0) or 0)
        for row in rta_identity_rows
    }) or [0]
    rta_sources = sorted({
        str(row.get('rta_code_source_path', '')).strip()
        for row in rta_identity_rows
        if str(row.get('rta_code_source_path', '')).strip()
    })
    rta_source_hashes = sorted({
        str(row.get('rta_code_source_sha256', '')).strip()
        for row in rta_identity_rows
    }) or ['not_used']
    rta_snapshot = {
        'combined_sha256': rta_fingerprints[0],
        'snapshot_paths': rta_paths,
        'snapshot_sha256': rta_hashes[0] if len(rta_hashes) == 1 else '',
        'snapshot_size': rta_sizes[0] if len(rta_sizes) == 1 else -1,
        'source_paths': rta_sources,
        'source_sha256': (
            rta_source_hashes[0] if len(rta_source_hashes) == 1 else ''
        ),
    }
    if rta_fingerprints != ['not_used']:
        if len(rta_fingerprints) != 1 or len(rta_paths) != 1:
            raise ValueError('inconsistent RTA code snapshot provenance')
        rta_path = Path(rta_paths[0]).resolve()
        rta_path.relative_to(run_dir)
        if (not rta_path.is_file()
                or len(rta_hashes) != 1 or len(rta_sizes) != 1
                or rta_path.stat().st_size != rta_sizes[0]
                or _file_sha256(rta_path) != rta_hashes[0]):
            raise ValueError('RTA code snapshot mismatch')
        if len(rta_sources) != 1 or len(rta_source_hashes) != 1:
            raise ValueError('inconsistent RTA source fingerprint')
        if validate_mutable_sources:
            source_path = Path(rta_sources[0]).resolve()
            if (not source_path.is_file()
                    or _file_sha256(source_path) != rta_source_hashes[0]):
                raise ValueError('RTA code source changed')

    return {
        'run_id': run_ids[0],
        'config_ids': config_ids,
        'config_group_ids': group_ids,
        'tasksets': sorted(tasksets.values(), key=lambda item: item['relative_path']),
        'trace_paths': sorted(trace_paths),
        'solar_snapshot': solar_snapshot,
        'rta_enabled': run_rta_enabled,
        'rta_code_fingerprint': rta_snapshot,
    }


def _formal_result_paths(run_dir, semantic):
    run_dir = Path(run_dir)
    paths = set(RESULT_FILES)
    if (run_dir / 'rta_results.jsonl').is_file():
        paths.add('rta_results.jsonl')
    paths.update(semantic['trace_paths'])
    discovered = {
        path.name for path in run_dir.iterdir()
        if path.is_file() and path.suffix in {'.csv', '.jsonl'}
    }
    expected_root = {Path(path).name for path in paths if '/' not in path}
    if discovered != expected_root:
        raise ValueError(
            'unexpected formal result file set: expected={} observed={}'.format(
                sorted(expected_root), sorted(discovered)
            )
        )
    traces_dir = run_dir / 'traces'
    discovered_traces = set()
    if traces_dir.is_dir():
        discovered_traces = {
            path.resolve().relative_to(run_dir.resolve()).as_posix()
            for path in traces_dir.glob('*.json') if path.is_file()
        }
    expected_traces = {
        path for path in paths if path.startswith('traces/')
    }
    if discovered_traces != expected_traces:
        raise ValueError(
            'unexpected formal trace set: expected={} observed={}'.format(
                sorted(expected_traces), sorted(discovered_traces)
            )
        )
    return sorted(paths)


def write_run_provenance(run_dir, command):
    """Atomically attest every formal input and result of one run."""
    run_dir = Path(run_dir)
    target = run_dir / RUN_PROVENANCE_FILE
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    if not run_dir_complete(run_dir):
        return False
    semantic = _validate_result_semantics(
        run_dir, expected_rta_enabled='--enable-rta' in command
    )
    if ('--enable-rta' in command) != (
            semantic['rta_code_fingerprint']['combined_sha256'] != 'not_used'):
        raise ValueError('RTA command/fingerprint mismatch')
    result_paths = _formal_result_paths(run_dir, semantic)
    result_artifacts = [
        _artifact_record(run_dir, relative) for relative in result_paths
    ]
    if any(artifact['run_id'] != [semantic['run_id']]
           for artifact in result_artifacts):
        raise ValueError('result artifact run_id mismatch')

    payload = {
        'schema_version': PROVENANCE_SCHEMA_VERSION,
        'run_id': semantic['run_id'],
        'created_at': datetime.now(timezone.utc).isoformat(),
        'command_sha256': _command_sha256(command),
        'config_id': semantic['config_ids'],
        'config_group_id': semantic['config_group_ids'],
        'official_run_valid': True,
        'rta_enabled': semantic['rta_enabled'],
        'input_artifacts': _formal_input_hashes(),
        'result_artifacts': result_artifacts,
        'tasksets': semantic['tasksets'],
        'solar_snapshot': semantic['solar_snapshot'],
        'rta_code_fingerprint': semantic['rta_code_fingerprint'],
    }
    _atomic_write_json(target, payload)
    return True


def validate_result_attestation(run_dir, expected_run_id=None,
                                expected_command_sha256=None):
    """Validate the published bytes/semantics without reconstructing a CLI."""
    run_dir = Path(run_dir)
    provenance_path = run_dir / RUN_PROVENANCE_FILE
    if not run_dir_complete(run_dir):
        raise ValueError('missing_result_files')
    if not provenance_path.is_file():
        raise ValueError('missing_resume_provenance')
    payload = _load_json_no_duplicates(
        provenance_path.read_text(encoding='utf-8')
    )
    schema = payload.get('schema_version')
    if type(schema) is not int or schema != PROVENANCE_SCHEMA_VERSION:
        raise ValueError('unsupported_provenance_schema')
    required = {
        'run_id', 'created_at', 'command_sha256', 'config_id',
        'config_group_id', 'official_run_valid', 'input_artifacts',
        'result_artifacts', 'tasksets', 'solar_snapshot',
        'rta_enabled', 'rta_code_fingerprint',
    }
    if not required.issubset(payload) or payload['official_run_valid'] is not True:
        raise ValueError('invalid_provenance_payload')
    if (not isinstance(payload['run_id'], str) or not payload['run_id']
            or not isinstance(payload['created_at'], str)
            or not payload['created_at']
            or not isinstance(payload['command_sha256'], str)
            or len(payload['command_sha256']) != 64
            or not isinstance(payload['config_id'], list)
            or not isinstance(payload['config_group_id'], list)
            or not isinstance(payload['input_artifacts'], dict)
            or not isinstance(payload['result_artifacts'], list)
            or not payload['result_artifacts']
            or not isinstance(payload['tasksets'], list)
            or not isinstance(payload['solar_snapshot'], dict)
            or type(payload['rta_enabled']) is not bool
            or not isinstance(payload['rta_code_fingerprint'], dict)):
        raise ValueError('invalid_provenance_payload')
    if expected_run_id is not None and payload['run_id'] != expected_run_id:
        raise ValueError('manifest_run_id_mismatch')
    if (expected_command_sha256 is not None
            and payload['command_sha256'] != expected_command_sha256):
        raise ValueError('manifest_command_mismatch')

    semantic = _validate_result_semantics(
        run_dir, validate_mutable_sources=False
    )
    if payload['run_id'] != semantic['run_id']:
        raise ValueError('run_id_mismatch')
    if payload['config_id'] != semantic['config_ids']:
        raise ValueError('config_id_mismatch')
    if payload['config_group_id'] != semantic['config_group_ids']:
        raise ValueError('config_group_id_mismatch')
    if payload['tasksets'] != semantic['tasksets']:
        raise ValueError('taskset_hash_mismatch')
    if payload['solar_snapshot'] != semantic['solar_snapshot']:
        raise ValueError('solar_profile_hash_mismatch')
    if payload['rta_enabled'] != semantic['rta_enabled']:
        raise ValueError('rta_enabled_mismatch')
    if payload['rta_code_fingerprint'] != semantic['rta_code_fingerprint']:
        raise ValueError('rta_code_fingerprint_mismatch')
    paths = _formal_result_paths(run_dir, semantic)
    current_artifacts = [
        _artifact_record(run_dir, relative) for relative in paths
    ]
    if payload['result_artifacts'] != current_artifacts:
        raise ValueError('result_artifact_mismatch')
    return payload


def validate_execution_manifest(manifest_path):
    """Bind every successful wrapper row to its per-run attestation."""
    manifest_path = Path(manifest_path)
    _fields, rows = _read_csv_rows(manifest_path)
    if not rows:
        raise ValueError('empty execution manifest')
    seen_run_ids = set()
    seen_run_dirs = set()
    for row in rows:
        status = str(row.get('status', '')).strip()
        if status not in {'completed', 'skipped_existing'}:
            raise ValueError(
                'manifest contains non-formal run status: {}'.format(status)
            )
        run_id = str(row.get('run_id', '')).strip()
        command_sha256 = str(row.get('command_sha256', '')).strip()
        raw_run_dir = str(row.get('run_dir', '')).strip()
        if not run_id or not command_sha256 or not raw_run_dir:
            raise ValueError('manifest missing formal run identity')
        run_dir = Path(raw_run_dir)
        if not run_dir.is_absolute():
            run_dir = manifest_path.parent / run_dir
        canonical_run_dir = str(run_dir.resolve())
        if run_id in seen_run_ids or canonical_run_dir in seen_run_dirs:
            raise ValueError('duplicate formal run in manifest')
        if str(row.get('official_run_valid', '')).strip().lower() != 'true':
            raise ValueError('manifest marks completed run invalid')
        raw_rta_enabled = str(row.get('rta_enabled', '')).strip().lower()
        if raw_rta_enabled not in {'true', 'false'}:
            raise ValueError('manifest rta_enabled must be explicit')
        payload = validate_result_attestation(
            run_dir,
            expected_run_id=run_id,
            expected_command_sha256=command_sha256,
        )
        if payload['rta_enabled'] != (raw_rta_enabled == 'true'):
            raise ValueError('manifest rta_enabled mismatch')
        seen_run_ids.add(run_id)
        seen_run_dirs.add(canonical_run_dir)
    return True


def validate_existing_result(run_dir, command, allow_legacy=False):
    """Reject stale/tampered results instead of silently resuming them."""
    run_dir = Path(run_dir)
    if not run_dir_complete(run_dir):
        return False, 'missing_result_files'
    provenance_path = run_dir / RUN_PROVENANCE_FILE
    if not provenance_path.is_file():
        return False, 'missing_resume_provenance'
    try:
        payload = _load_json_no_duplicates(
            provenance_path.read_text(encoding='utf-8')
        )
        schema = payload.get('schema_version')
        if type(schema) is not int or schema != PROVENANCE_SCHEMA_VERSION:
            return False, 'unsupported_provenance_schema'
        required = {
            'run_id', 'created_at', 'command_sha256', 'config_id',
            'config_group_id', 'official_run_valid', 'input_artifacts',
            'result_artifacts', 'tasksets', 'solar_snapshot',
            'rta_enabled', 'rta_code_fingerprint',
        }
        if not required.issubset(payload) or payload['official_run_valid'] is not True:
            return False, 'invalid_provenance_payload'
        if (not isinstance(payload['run_id'], str) or not payload['run_id']
                or not isinstance(payload['created_at'], str)
                or not payload['created_at']
                or not isinstance(payload['config_id'], list)
                or not isinstance(payload['config_group_id'], list)
                or not isinstance(payload['input_artifacts'], dict)
                or not isinstance(payload['result_artifacts'], list)
                or not payload['result_artifacts']
                or not isinstance(payload['tasksets'], list)
                or not isinstance(payload['solar_snapshot'], dict)
                or type(payload['rta_enabled']) is not bool
                or not isinstance(payload['rta_code_fingerprint'], dict)):
            return False, 'invalid_provenance_payload'
        if payload.get('command_sha256') != _command_sha256(command):
            return False, 'config_id_mismatch'
        if payload.get('input_artifacts') != _formal_input_hashes():
            return False, 'config_id_mismatch'
        semantic = _validate_result_semantics(
            run_dir, expected_rta_enabled='--enable-rta' in command
        )
        if ('--enable-rta' in command) != (
                semantic['rta_code_fingerprint']['combined_sha256']
                != 'not_used'):
            return False, 'rta_code_fingerprint_mismatch'
        if payload['run_id'] != semantic['run_id']:
            return False, 'run_id_mismatch'
        if payload['config_id'] != semantic['config_ids']:
            return False, 'config_id_mismatch'
        if payload['config_group_id'] != semantic['config_group_ids']:
            return False, 'config_group_id_mismatch'
        if payload['tasksets'] != semantic['tasksets']:
            return False, 'taskset_hash_mismatch'
        if payload['solar_snapshot'] != semantic['solar_snapshot']:
            return False, 'solar_profile_hash_mismatch'
        if payload['rta_enabled'] != semantic['rta_enabled']:
            return False, 'rta_enabled_mismatch'
        if payload['rta_code_fingerprint'] != semantic['rta_code_fingerprint']:
            return False, 'rta_code_fingerprint_mismatch'
        paths = _formal_result_paths(run_dir, semantic)
        current_artifacts = [
            _artifact_record(run_dir, relative) for relative in paths
        ]
        if payload['result_artifacts'] != current_artifacts:
            return False, 'result_artifact_mismatch'
    except ValueError as error:
        message = str(error).lower()
        if 'taskset' in message:
            return False, 'taskset_hash_mismatch'
        if 'solar' in message:
            return False, 'solar_profile_hash_mismatch'
        if 'rta code' in message:
            return False, 'rta_code_fingerprint_mismatch'
        return False, 'stale_existing_result'
    except (OSError, TypeError, KeyError, json.JSONDecodeError):
        return False, 'stale_existing_result'
    return True, ''


def build_command(run_dir, seed_base, num_points, num_tasksets, task_n,
                  battery, initial_energy, solar_time_ms, max_workers,
                  no_group_figures=False, harvesting_scale=1.0,
                  rta_initial_energy=None,
                  rta_horizon_ms=None, rta_timeout=None,
                  min_task_util=0.01, max_task_util=0.8,
                  wcet_rounding='floor',
                  actual_utilization_tolerance_total=None,
                  constrained_deadlines=False,
                  require_common_complete=False):
    """Build one acceptance_ratio_test.py invocation without a shell."""
    command = [
        'python3', str(EXPERIMENT_SCRIPT),
        '--run-experiment',
        '--output-dir', str(Path(run_dir)),
        '--seed-base', str(int(seed_base)),
        '--num-points', str(int(num_points)),
        '--num-tasksets', str(int(num_tasksets)),
        '--task-n', str(int(task_n)),
        '--battery', str(float(battery)),
        '--initial-energy', str(float(initial_energy)),
        '--solar-time-ms', str(int(solar_time_ms)),
        '--harvesting-scale', str(float(harvesting_scale)),
        '--max-workers', str(int(max_workers)),
        '--min-task-util', str(float(min_task_util)),
        '--max-task-util', str(float(max_task_util)),
        '--wcet-rounding', str(wcet_rounding),
    ]
    if actual_utilization_tolerance_total is not None:
        command.extend([
            '--actual-utilization-tolerance-total',
            str(float(actual_utilization_tolerance_total)),
        ])
    if constrained_deadlines:
        command.append('--constrained-deadlines')
    if require_common_complete:
        command.append('--require-common-complete')
    if rta_initial_energy is not None:
        command.extend([
            '--enable-rta',
            '--profile-rta',
            '--rta-initial-energy', str(float(rta_initial_energy)),
            '--rta-horizon-ms', str(int(rta_horizon_ms)),
            '--rta-assume-no-overflow',
            '--rta-timeout', str(int(rta_timeout)),
        ])
    if no_group_figures:
        command.append('--no-group-figures')
    return command


def print_command(command):
    rendered = shlex.join([str(part) for part in command])
    print('$ {}'.format(rendered))
    return rendered


def write_manifest(path, fieldnames, rows):
    """Rewrite the small manifest after every run so failures are retained."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def run_command(command):
    """Execute a command in the project root and return its process code."""
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    return int(completed.returncode)


def execute_specs(specs, manifest_path, fieldnames, dry_run=False,
                  skip_existing=False, force=False,
                  stop_on_failure=False):
    """Execute planned runs safely and persist a status row for every spec."""
    rows = []
    for spec in specs:
        row = {field: spec.get(field, '') for field in fieldnames}
        run_dir = Path(spec['run_dir'])
        command = spec['command']
        row['command_sha256'] = _command_sha256(command)
        print_command(command)

        exists_nonempty = run_dir.exists() and (
            not run_dir.is_dir() or any(run_dir.iterdir())
        )
        if skip_existing and exists_nonempty:
            reusable, resume_error = validate_existing_result(
                run_dir, command
            )
            if reusable:
                row.update(status='skipped_existing', return_code='')
                payload = json.loads(
                    (run_dir / RUN_PROVENANCE_FILE).read_text(encoding='utf-8')
                )
                row['run_id'] = payload['run_id']
            else:
                row.update(
                    status='stale_existing_result',
                    return_code='',
                    diagnostic_outputs=resume_error,
                )
        elif exists_nonempty and not force:
            row.update(status='blocked_existing', return_code='')
            print(
                'Refusing to overwrite non-empty run directory: {}'.format(
                    run_dir
                )
            )
        elif dry_run:
            row.update(status='dry_run', return_code='')
        else:
            if force and run_dir.exists():
                if run_dir.is_dir():
                    shutil.rmtree(run_dir)
                else:
                    run_dir.unlink()
            return_code = run_command(command)
            row.update(
                status='completed' if return_code == 0 else 'failed',
                return_code=return_code,
            )
            if return_code == 0:
                try:
                    if not run_dir_complete(run_dir):
                        raise ValueError('missing formal result files')
                    provenance_written = write_run_provenance(
                        run_dir, command
                    )
                    if not provenance_written:
                        raise ValueError(
                            'result files lack complete formal provenance'
                        )
                    payload = json.loads(
                        (run_dir / RUN_PROVENANCE_FILE).read_text(
                            encoding='utf-8'
                        )
                    )
                    row['run_id'] = payload['run_id']
                except (OSError, ValueError, TypeError, KeyError,
                        json.JSONDecodeError) as error:
                    row.update(
                        status='failed',
                        return_code=1,
                        diagnostic_outputs=(
                            'provenance_write_failed: {}'.format(error)
                        ),
                    )

        require_common = '--require-common-complete' in command
        child_code = row.get('return_code', '')
        official_valid = (
            row.get('status') in {'completed', 'skipped_existing'}
            and run_dir_complete(run_dir)
            and bool(row.get('run_id'))
        )
        formal_outputs = bool(
            official_valid and run_dir_complete(run_dir)
        )
        diagnostics = [
            str(run_dir / name) for name in RESULT_FILES
            if (run_dir / name).is_file()
        ]
        execution_values = {
            'rta_enabled': '--enable-rta' in command,
            'require_common_complete': require_common,
            'official_run_valid': official_valid,
            'child_exit_code': child_code,
            'diagnostic_outputs': ';'.join(diagnostics),
            'formal_outputs_generated': formal_outputs,
        }
        for key, value in execution_values.items():
            if key in fieldnames and not (
                    key == 'diagnostic_outputs'
                    and row.get('diagnostic_outputs')):
                row[key] = value

        rows.append(row)
        write_manifest(manifest_path, fieldnames, rows)
        if stop_on_failure and row['status'] in {
            'blocked_existing', 'failed', 'stale_existing_result',
        }:
            break
    exit_code = wrapper_exit_code(rows)
    failed_specs = sum(row.get('status') == 'failed' for row in rows)
    blocked_specs = sum(row.get('status') in {
        'blocked_existing', 'stale_existing_result'
    } for row in rows)
    for row in rows:
        for key, value in {
            'wrapper_exit_code': exit_code,
            'failed_specs': failed_specs,
            'blocked_specs': blocked_specs,
        }.items():
            if key in fieldnames:
                row[key] = value
    write_manifest(manifest_path, fieldnames, rows)
    return rows


def wrapper_exit_code(rows):
    """Map child/common-complete results to the wrapper process status."""
    ordinary_failure = False
    common_complete_failure = False
    for row in rows:
        if row.get('status') in {'blocked_existing', 'stale_existing_result'}:
            ordinary_failure = True
        if (row.get('status') == 'completed'
                and not row.get('official_run_valid', False)):
            ordinary_failure = True
        if row.get('status') != 'failed':
            continue
        try:
            return_code = int(row.get('return_code'))
        except (TypeError, ValueError):
            ordinary_failure = True
            continue
        if return_code == 2:
            common_complete_failure = True
        elif return_code != 0:
            ordinary_failure = True
    if ordinary_failure:
        return 1
    if common_complete_failure:
        return 2
    return 0


def add_common_arguments(parser, include_battery=True, include_solar_time=True,
                         include_harvesting_scale=True):
    parser.add_argument('--output-root', default='acceptance_ratio_runs')
    parser.add_argument('--experiment-name', required=True)
    parser.add_argument('--num-points', type=int, default=10)
    parser.add_argument('--num-tasksets', type=int, default=50)
    parser.add_argument('--task-n', type=int, default=10)
    if include_battery:
        parser.add_argument('--battery', type=float, default=20.0)
    parser.add_argument('--initial-energy', type=float, default=1.0)
    if include_solar_time:
        parser.add_argument('--solar-time-ms', type=int, default=21975000)
    if include_harvesting_scale:
        parser.add_argument('--harvesting-scale', type=float, default=1.0)
    parser.add_argument('--max-workers', type=int, default=4)
    parser.add_argument('--min-task-util', type=float, default=0.01)
    parser.add_argument('--max-task-util', type=float, default=0.8)
    parser.add_argument(
        '--wcet-rounding',
        choices=('floor', 'round', 'ceil', 'compensated'),
        default='floor',
    )
    parser.add_argument(
        '--actual-utilization-tolerance-total',
        type=float,
        default=None,
        help=(
            'absolute total-utilization error tolerance after integer WCET '
            'rounding; when set, tasksets outside the tolerance are discarded '
            'and regenerated'
        ),
    )
    parser.add_argument(
        '--constrained-deadlines',
        action='store_true',
        help='generate constrained deadlines C_i<=D_i<=T_i',
    )
    parser.add_argument('--no-group-figures', action='store_true')
    common_complete = parser.add_mutually_exclusive_group()
    common_complete.add_argument(
        '--require-common-complete',
        dest='require_common_complete',
        action='store_true',
        default=True,
        help='require the formal nine-scheduler common-complete sample',
    )
    common_complete.add_argument(
        '--no-require-common-complete',
        dest='require_common_complete',
        action='store_false',
        help='allow incomplete samples for diagnostic runs',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='print commands and write a dry-run manifest without execution',
    )
    safety = parser.add_mutually_exclusive_group()
    safety.add_argument(
        '--skip-existing', action='store_true',
        help='skip directories containing both expected result CSV files',
    )
    safety.add_argument(
        '--force', action='store_true',
        help='delete each existing generated run directory before execution',
    )
    parser.add_argument(
        '--stop-on-failure', action='store_true',
        help='stop after the first failed or blocked run (default: continue)',
    )


def validate_common_args(parser, args):
    for name in ('num_points', 'num_tasksets', 'task_n', 'max_workers'):
        if getattr(args, name) <= 0:
            parser.error('--{} must be positive'.format(name.replace('_', '-')))
    if args.initial_energy < 0:
        parser.error('--initial-energy must be non-negative')
    harvesting_scale = getattr(args, 'harvesting_scale', 1.0)
    if not math.isfinite(harvesting_scale) or harvesting_scale < 0:
        parser.error('--harvesting-scale must be finite and non-negative')
    min_task_util = getattr(args, 'min_task_util', 0.01)
    max_task_util = getattr(args, 'max_task_util', 0.8)
    if min_task_util < 0 or max_task_util <= 0:
        parser.error('--min-task-util/--max-task-util must be positive bounds')
    if min_task_util > max_task_util:
        parser.error('--min-task-util must be <= --max-task-util')
    if max_task_util > 1.0:
        parser.error('--max-task-util must be <= 1.0 for sequential tasks')
    actual_tolerance = getattr(
        args, 'actual_utilization_tolerance_total', None
    )
    if actual_tolerance is not None and (
        not math.isfinite(actual_tolerance) or actual_tolerance < 0
    ):
        parser.error(
            '--actual-utilization-tolerance-total must be finite and non-negative'
        )
    try:
        safe_experiment_name(args.experiment_name)
    except ValueError as exc:
        parser.error(str(exc))


def output_paths(args):
    output_root = Path(args.output_root).resolve()
    experiment_name = safe_experiment_name(args.experiment_name)
    manifest = output_root / '{}_manifest.csv'.format(experiment_name)
    return output_root, experiment_name, manifest
