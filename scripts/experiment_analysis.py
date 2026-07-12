#!/usr/bin/env python3
"""Shared helpers for PARTSim experiment post-processing."""

import math
import json
import sys
import warnings
import hashlib
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

FORMAL_RESULT_SCHEMA_VERSION = 4

DISPLAY_NAMES = {
    'gpfp_asap_block': 'ASAP-Block',
    'gpfp_asap_nonblock': 'ASAP-NonBlock',
    'gpfp_asap_sync': 'ASAP-Sync',
    'gpfp_alap_block': 'ALAP-Block',
    'gpfp_alap_nonblock': 'ALAP-NonBlock',
    'gpfp_alap_sync': 'ALAP-Sync',
    'gpfp_st_block': 'ST-Block',
    'gpfp_st_nonblock': 'ST-NonBlock',
    'gpfp_st_sync': 'ST-Sync',
}
TIMING_BLOCK = ['gpfp_asap_block', 'gpfp_alap_block', 'gpfp_st_block']
ASAP_SEMANTICS = [
    'gpfp_asap_block', 'gpfp_asap_nonblock', 'gpfp_asap_sync',
]


def number(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def integer(value, default=0):
    return int(number(value, default))


def accepted(row):
    return outcome_status(row) == 'accepted'


def outcome_status(row):
    status = str(row.get('status', '')).strip().lower()
    if status and status != 'nan':
        if status in {'accepted', 'rejected'}:
            return status
        if status in {'generation_error', 'yaml_generation_failed'}:
            return 'generation_error'
        if status in {'timeout', 'simulation_timeout'}:
            return 'timeout'
        return 'error'
    value = row.get('accepted', '')
    if pd.isna(value) or str(value).strip() == '':
        return 'missing'
    return 'accepted' if integer(value) == 1 else 'rejected'


def read_csv(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def validate_attested_run_directory(run_dir):
    """Require both the per-run commit marker and its execution manifest.

    Batch manifests live beside their run directories.  Discovery keeps the
    public analyzer CLI stable while still making the manifest an
    unskippable part of formal ingestion.
    """
    from scripts.experiment_runner import (
        validate_execution_manifest, validate_result_attestation,
    )

    run_dir = Path(run_dir).resolve()
    payload = validate_result_attestation(run_dir)
    candidates = []
    for parent in (run_dir.parent, run_dir.parent.parent):
        if parent.is_dir():
            # The wrapper uses *_manifest.csv, while public callers may give
            # the execution manifest another name.  Treat only CSVs that
            # actually contain a run_dir column as candidates below.
            candidates.extend(sorted(parent.glob('*.csv')))
    claimed_by = []
    for manifest in dict.fromkeys(path.resolve() for path in candidates):
        try:
            rows = pd.read_csv(manifest, dtype=str, keep_default_na=False)
        except Exception:
            continue
        if 'run_dir' not in rows:
            continue
        manifest_runs = set()
        for value in rows['run_dir']:
            candidate = Path(str(value))
            if not candidate.is_absolute():
                candidate = manifest.parent / candidate
            manifest_runs.add(candidate.resolve())
        if run_dir in manifest_runs:
            claimed_by.append(manifest)
    if not claimed_by:
        raise ValueError('missing_execution_manifest: {}'.format(run_dir))
    if len(claimed_by) != 1:
        raise ValueError(
            'ambiguous_execution_manifest: {} {}'.format(run_dir, claimed_by)
        )
    validate_execution_manifest(claimed_by[0])
    # validate_execution_manifest already cross-checks run_id/command hash;
    # retain the payload return for callers that need the formal identity.
    payload = dict(payload)
    payload['_run_dir'] = str(run_dir)
    payload['_execution_manifest_path'] = str(claimed_by[0])
    payload['_result_sidecar_path'] = str(
        run_dir / 'wrapper_run_provenance.json'
    )
    return payload


def validate_attested_analyzer_input(
        input_path, allow_primary=True,
        require_source_equivalent_derived=False):
    """Require a standard run attestation or a derived-artifact attestation."""
    from scripts.experiment_runner import (
        RUN_PROVENANCE_FILE, validate_analysis_artifact_attestation,
    )

    input_path = Path(input_path).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if 'diagnostic_unattested' in input_path.parts or input_path.name.startswith(
            'diagnostic_'):
        raise ValueError('diagnostic_input_cannot_be_formal: {}'.format(
            input_path
        ))
    run_dir = input_path.parent
    if (run_dir / RUN_PROVENANCE_FILE).is_file():
        payload = validate_attested_run_directory(run_dir)
        attested = {
            str(item.get('relative_path', ''))
            for item in payload.get('result_artifacts', [])
        }
        if input_path.name not in attested:
            raise ValueError(
                'input_not_attested_by_run_sidecar: {}'.format(input_path)
            )
        return payload
    payload = validate_analysis_artifact_attestation(input_path)
    if (not allow_primary
            and payload.get('artifact_type') == 'primary_run_artifact'):
        raise ValueError(
            'primary_analysis_artifact_not_valid_for_formal_csv: {}'.format(
                input_path
            )
        )
    if (require_source_equivalent_derived
            and payload.get('artifact_type') == 'derived_analysis_artifact'):
        artifacts = payload.get('artifacts', [])
        primary_hash = (
            artifacts[0].get('sha256') if artifacts else None
        )
        source_hashes = {
            source.get('source_artifact_sha256')
            for source in payload.get('sources', [])
            if isinstance(source, dict)
        }
        if not primary_hash or primary_hash not in source_hashes:
            raise ValueError(
                'derived_csv_requires_semantic_recomputation: {}'.format(
                    input_path
                )
            )
    return payload


def diagnostic_output_directory(output_dir):
    """Return an isolated namespace that formal analyzers never consume."""
    return Path(output_dir) / 'diagnostic_unattested'


def finalize_diagnostic_outputs(output_dir):
    """Mark and rename every diagnostic artifact away from formal names."""
    output_dir = Path(output_dir)
    for path in sorted(output_dir.rglob('*')):
        if not path.is_file() or path.name.startswith('diagnostic_'):
            continue
        path.rename(path.with_name('diagnostic_' + path.name))
    (output_dir / 'diagnostic_metadata.json').write_text(
        json.dumps({
            'official': False,
            'reason': 'unattested_or_legacy_diagnostic_input',
        }, sort_keys=True, indent=2) + '\n',
        encoding='utf-8',
    )


def validate_horizon_columns(frame):
    """Reject non-finite/negative horizon metadata at analyzer boundaries."""
    expected_columns = [
        name for name in (
            'simulation_horizon_ms', 'expected_simulation_horizon_ms',
        ) if name in frame
    ]
    for column in expected_columns:
        for value in frame[column].dropna():
            parsed = number(value, math.nan)
            if not math.isfinite(parsed) or parsed <= 0:
                raise ValueError('invalid horizon metadata in ' + column)


def wilson_interval(successes, trials, z=1.959963984540054):
    """Return a taskset-level Wilson score interval for accepted/valid."""
    successes = int(successes)
    trials = int(trials)
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError('Wilson counts require 0 <= successes <= trials')
    if trials == 0:
        return math.nan, math.nan
    p_hat = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    center = (p_hat + z2 / (2.0 * trials)) / denominator
    half = (
        z / denominator
        * math.sqrt(
            p_hat * (1.0 - p_hat) / trials
            + z2 / (4.0 * trials * trials)
        )
    )
    return center - half, center + half


def _legacy_id(source, prefix):
    return '{}:{}'.format(
        prefix,
        hashlib.sha256(str(source).encode('utf-8')).hexdigest(),
    )


def _prepare_provenance(frame, source, *, raw, allow_legacy):
    """Require the current semantic-task provenance schema."""
    frame = frame.copy()
    schema = pd.to_numeric(
        frame.get('result_schema_version'), errors='coerce'
    ) if 'result_schema_version' in frame else pd.Series(dtype=float)
    formal_schema = not schema.empty and schema.notna().all() and (
        schema == FORMAL_RESULT_SCHEMA_VERSION
    ).all()
    required = {'config_id'}
    if raw:
        required.update({'taskset_hash', 'algorithm'})
    missing = required - set(frame.columns)
    blank = set()
    for column in required & set(frame.columns):
        empty = frame[column].fillna('').astype(str).str.strip().eq('')
        if raw and column == 'taskset_hash' and empty.any():
            allowed = frame.loc[empty].apply(
                lambda row: (
                    outcome_status(row) == 'generation_error'
                    or 'taskset generation failed'
                    in str(row.get('reason', '')).lower()
                ),
                axis=1,
            )
            if not allowed.all():
                blank.add(column)
        elif empty.any():
            blank.add(column)
    if formal_schema and (missing or blank):
        raise ValueError(
            'missing_formal_provenance: {} missing={} blank={}'.format(
                source, sorted(missing), sorted(blank)
            )
        )
    if not formal_schema or missing or blank:
        if not allow_legacy:
            raise ValueError(
                'legacy_result_requires_explicit_opt_in: {}'.format(source)
            )
        warnings.warn(
            'legacy result provenance synthesized explicitly for {}; '
            'do not use it for formal publication output'.format(source),
            RuntimeWarning,
            stacklevel=3,
        )
        if 'config_id' not in frame:
            frame['config_id'] = _legacy_id(source, 'legacy_config')
        else:
            empty_config = (
                frame['config_id'].fillna('').astype(str).str.strip() == ''
            )
            frame.loc[empty_config, 'config_id'] = _legacy_id(
                source, 'legacy_config'
            )
        if 'config_group_id' not in frame:
            frame['config_group_id'] = _legacy_id(
                source, 'legacy_config_group'
            )
        else:
            empty_group = (
                frame['config_group_id'].fillna('').astype(str).str.strip()
                == ''
            )
            frame.loc[empty_group, 'config_group_id'] = _legacy_id(
                source, 'legacy_config_group'
            )
        if raw and (
            'taskset_hash' not in frame
            or frame['taskset_hash'].fillna('').astype(str).str.strip().eq('').any()
        ):
            display = frame.get(
                'taskset_id', frame.get('task_idx', pd.Series(frame.index))
            )
            frame['taskset_hash'] = [
                _legacy_id(
                    '{}|{}|{}|{}'.format(
                        source,
                        frame.iloc[index].get('seed_base', ''),
                        frame.iloc[index].get('normalized_utilization', ''),
                        value,
                    ),
                    'legacy_taskset',
                )
                for index, value in enumerate(display)
            ]
    if 'config_group_id' not in frame:
        # New formal outputs provide this seed-independent compatibility ID.
        # Older output is not safe for multi-seed pooling.
        if not allow_legacy:
            raise ValueError(
                'missing_formal_provenance: {} requires config_group_id'.format(
                    source
                )
            )
        frame['config_group_id'] = frame['config_id']
    if 'source_run_id' not in frame:
        frame['source_run_id'] = str(source)
    frame['source_run'] = str(source)
    return frame


def _row_status(row):
    if 'taskset generation failed' in str(row.get('reason', '')).lower():
        return 'generation_error'
    return outcome_status(row)


def _validate_raw_identities(frame, context):
    """Reject all duplicate formal keys and display-ID/hash ambiguity."""
    required = {'config_id', 'taskset_hash', 'algorithm'}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            'missing_formal_provenance: {} {}'.format(context, sorted(missing))
        )
    display_column = (
        'taskset_id' if 'taskset_id' in frame else
        'task_idx' if 'task_idx' in frame else None
    )
    if display_column:
        grouped = frame[
            frame['taskset_hash'].fillna('').astype(str).str.strip().ne('')
        ].groupby(['config_id', display_column])
        conflicts = [
            (key, sorted(set(group['taskset_hash'].astype(str))))
            for key, group in grouped
            if group['taskset_hash'].astype(str).nunique(dropna=False) > 1
        ]
        if conflicts:
            raise ValueError(
                'duplicate_conflicting_metadata: {} same config/taskset_id '
                'has different hashes {}'.format(context, conflicts)
            )

    identity_frame = frame.copy()
    empty_hash = (
        identity_frame['taskset_hash'].fillna('').astype(str) == ''
    )
    if empty_hash.any():
        fallback = (
            identity_frame[display_column].astype(str)
            if display_column else identity_frame.index.astype(str)
        )
        identity_frame.loc[empty_hash, 'taskset_hash'] = (
            'generation_error:' + fallback[empty_hash]
        )
    keys = ['config_id', 'taskset_hash', 'algorithm']
    duplicates = identity_frame[identity_frame.duplicated(keys, keep=False)]
    if duplicates.empty:
        return
    for key, group in duplicates.groupby(keys, sort=True):
        statuses = {_row_status(row) for row in group.to_dict('records')}
        if len(statuses) > 1:
            kind = 'duplicate_conflicting_status'
        else:
            comparable = group.drop(
                columns=['source_run'], errors='ignore'
            ).fillna('<NA>').astype(str)
            kind = (
                'duplicate_identical_result'
                if len(comparable.drop_duplicates()) == 1
                else 'duplicate_conflicting_metadata'
            )
        sources = group['source_run'].astype(str).tolist()
        raise ValueError(
            '{}: {} key={} sources={}'.format(kind, context, key, sources)
        )

    observed_columns = [
        name for name in (
            'observed_trace_horizon_ms', 'observed_simulation_end_ms',
        ) if name in frame
    ]
    for column in observed_columns:
        for value in frame[column].dropna():
            parsed = number(value, math.nan)
            if not math.isfinite(parsed) or parsed < 0:
                raise ValueError('invalid horizon metadata in ' + column)


def load_acceptance_runs(run_dirs, allow_legacy=False):
    frames = []
    raw_frames = []
    for run_dir in map(Path, run_dirs):
        if not allow_legacy:
            validate_attested_run_directory(run_dir)
        frame = read_csv(run_dir / 'acceptance_ratio_data.csv')
        raw = read_csv(run_dir / 'per_taskset_results.csv')
        frame = _prepare_provenance(
            frame, run_dir, raw=False, allow_legacy=allow_legacy
        )
        raw = _prepare_provenance(
            raw, run_dir, raw=True, allow_legacy=allow_legacy
        )
        validate_horizon_columns(frame)
        validate_horizon_columns(raw)
        for _, aggregate in frame.iterrows():
            utilization = number(aggregate['normalized_utilization'])
            raw_utilization = pd.to_numeric(
                raw['normalized_utilization'], errors='coerce'
            )
            selected = raw[
                (raw['algorithm'] == aggregate['algorithm'])
                & (raw['config_id'].astype(str) == str(aggregate['config_id']))
                & ((raw_utilization - utilization).abs() < 1e-9)
            ]
            expected_samples = integer(aggregate['num_samples'])
            expected_accepted = integer(aggregate.get('num_successful'))
            observed_accepted = sum(
                accepted(row) for row in selected.to_dict('records')
            )
            if (
                len(selected) != expected_samples
                or observed_accepted != expected_accepted
            ):
                raise ValueError(
                    'aggregate/raw mismatch in {} for {} at U={}'.format(
                        run_dir, aggregate['algorithm'], utilization
                    )
                )
        frames.append(frame)
        raw_frames.append(raw)
    if not frames:
        raise ValueError('at least one run directory is required')
    combined_raw = pd.concat(raw_frames, ignore_index=True)
    _validate_raw_identities(combined_raw, 'acceptance run ingestion')
    combined = pd.concat(frames, ignore_index=True)
    aggregate_keys = [
        'config_id', 'algorithm', 'normalized_utilization'
    ]
    duplicate = combined[combined.duplicated(aggregate_keys, keep=False)]
    if not duplicate.empty:
        sources = duplicate['source_run'].astype(str).tolist()
        raise ValueError(
            'duplicate_aggregate_result: keys={} sources={}'.format(
                aggregate_keys, sources
            )
        )
    return combined


def acceptance_by_seed(run_dirs, allow_legacy=False):
    """Recompute conditional observations from accepted/rejected counts."""
    frame = load_acceptance_runs(run_dirs, allow_legacy=allow_legacy)
    if 'simulation_num_accepted' not in frame and 'num_successful' in frame:
        frame['simulation_num_accepted'] = frame['num_successful']
    if 'simulation_num_requested' not in frame and 'num_samples' in frame:
        frame['simulation_num_requested'] = frame['num_samples']
    if (
        'simulation_num_rejected' not in frame
        and 'simulation_num_valid' in frame
        and 'simulation_num_accepted' in frame
    ):
        frame['simulation_num_rejected'] = (
            pd.to_numeric(frame['simulation_num_valid'], errors='raise')
            - pd.to_numeric(
                frame['simulation_num_accepted'], errors='raise'
            )
        )
    if (
        'simulation_num_rejected' not in frame
        and 'simulation_num_requested' in frame
        and 'simulation_num_accepted' in frame
    ):
        requested_minus_accepted = (
            pd.to_numeric(
                frame['simulation_num_requested'], errors='raise'
            )
            - pd.to_numeric(
                frame['simulation_num_accepted'], errors='raise'
            )
        )
        infrastructure_columns = [
            column for column in (
                'simulation_num_error', 'simulation_num_timeout',
                'simulation_num_generation_error',
            ) if column in frame
        ]
        if infrastructure_columns:
            infrastructure_counts = [
                pd.to_numeric(frame[column], errors='raise')
                for column in infrastructure_columns
            ]
            if any(counts.isna().any() for counts in infrastructure_counts):
                raise ValueError('missing infrastructure outcome counts')
            infrastructure_failures = sum(infrastructure_counts)
            frame['simulation_num_rejected'] = (
                requested_minus_accepted - infrastructure_failures
            )
            if (frame['simulation_num_rejected'] < 0).any():
                raise ValueError(
                    'aggregate outcome counts exceed requested simulations'
                )
        else:
            warnings.warn(
                'legacy aggregate has no valid/rejected counts; treating '
                'requested minus accepted as rejected because no '
                'infrastructure counters are available',
                RuntimeWarning,
                stacklevel=2,
            )
            frame['simulation_num_rejected'] = requested_minus_accepted
    required = {
        'algorithm', 'normalized_utilization', 'seed_base',
        'simulation_num_accepted', 'simulation_num_rejected',
        'simulation_num_requested',
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError('missing acceptance columns: {}'.format(
            ', '.join(sorted(missing))
        ))
    if 'algorithm_display_name' not in frame:
        frame['algorithm_display_name'] = frame['algorithm'].map(
            DISPLAY_NAMES
        ).fillna(frame['algorithm'])

    rows = []
    columns = [
        'config_group_id', 'config_id', 'seed_base', 'algorithm',
        'algorithm_display_name', 'normalized_utilization',
    ]
    for keys, group in frame.groupby(columns, sort=True):
        total_accepted = int(pd.to_numeric(
            group['simulation_num_accepted'], errors='raise'
        ).sum())
        total_rejected = int(pd.to_numeric(
            group['simulation_num_rejected'], errors='raise'
        ).sum())
        total_valid = total_accepted + total_rejected
        total_requested = int(pd.to_numeric(
            group['simulation_num_requested'], errors='raise'
        ).sum())
        total_error = (
            int(pd.to_numeric(
                group['simulation_num_error'], errors='raise'
            ).sum())
            if 'simulation_num_error' in group else 0
        )
        total_timeout = (
            int(pd.to_numeric(
                group['simulation_num_timeout'], errors='raise'
            ).sum())
            if 'simulation_num_timeout' in group else 0
        )
        total_generation_error = (
            int(pd.to_numeric(
                group['simulation_num_generation_error'], errors='raise'
            ).sum())
            if 'simulation_num_generation_error' in group else 0
        )
        conditional_acceptance = (
            total_accepted / total_valid if total_valid else math.nan
        )
        has_common = {
            'common_complete_accepted', 'common_complete_num_tasksets',
        } <= set(group.columns)
        common_accepted = (
            int(pd.to_numeric(
                group['common_complete_accepted'], errors='raise'
            ).sum())
            if has_common else 0
        )
        common_total = (
            int(pd.to_numeric(
                group['common_complete_num_tasksets'], errors='raise'
            ).sum())
            if has_common else 0
        )
        common_acceptance = (
            common_accepted / common_total if common_total else math.nan
        )
        acceptance_accepted = (
            common_accepted if has_common else total_accepted
        )
        acceptance_valid = common_total if has_common else total_valid
        acceptance_rejected = acceptance_valid - acceptance_accepted
        seed_conditional_acceptance = (
            acceptance_accepted / acceptance_valid
            if acceptance_valid else math.nan
        )
        rows.append({
            'config_group_id': keys[0],
            'config_id': keys[1],
            'seed_base': keys[2],
            'algorithm': keys[3],
            'algorithm_display_name': keys[4],
            'normalized_utilization': float(keys[5]),
            'source_run_id': ','.join(sorted(set(
                group['source_run_id'].astype(str)
            ))),
            'num_tasksets': total_valid,
            'num_valid_samples': total_valid,
            'num_requested_samples': total_requested,
            'num_accepted': total_accepted,
            'num_rejected': total_rejected,
            'num_error': total_error,
            'num_timeout': total_timeout,
            'num_generation_error': total_generation_error,
            'no_valid_simulations': total_valid == 0,
            'per_scheduler_conditional_acceptance_ratio': (
                conditional_acceptance
            ),
            'common_complete_accepted': (
                common_accepted if has_common else math.nan
            ),
            'common_complete_num_tasksets': (
                common_total if has_common else math.nan
            ),
            'common_complete_acceptance_ratio': common_acceptance,
            'acceptance_num_accepted': acceptance_accepted,
            'acceptance_num_rejected': acceptance_rejected,
            'acceptance_num_valid': acceptance_valid,
            'acceptance_basis': (
                'common_complete' if has_common else 'per_scheduler_valid'
            ),
            'seed_conditional_acceptance': seed_conditional_acceptance,
            # Compatibility alias. Formal pooling uses the counts above.
            'acceptance_ratio': seed_conditional_acceptance,
            'unconditional_success_rate': (
                total_accepted / total_requested
                if total_requested else math.nan
            ),
        })
    return pd.DataFrame(rows)


def summarize_multiseed(run_dirs, allow_legacy=False):
    by_seed = acceptance_by_seed(run_dirs, allow_legacy=allow_legacy)
    rows = []
    columns = [
        'config_group_id', 'algorithm', 'algorithm_display_name',
        'normalized_utilization',
    ]
    for keys, group in by_seed.groupby(columns, sort=True):
        total_accepted = int(pd.to_numeric(
            group['acceptance_num_accepted'], errors='raise'
        ).sum())
        total_rejected = int(pd.to_numeric(
            group['acceptance_num_rejected'], errors='raise'
        ).sum())
        total_valid = total_accepted + total_rejected
        total_requested = int(pd.to_numeric(
            group['num_requested_samples'], errors='raise'
        ).sum())
        simulation_total_accepted = int(pd.to_numeric(
            group['num_accepted'], errors='raise'
        ).sum())
        total_error = int(pd.to_numeric(
            group['num_error'], errors='raise'
        ).sum())
        total_timeout = int(pd.to_numeric(
            group['num_timeout'], errors='raise'
        ).sum())
        total_generation_error = int(pd.to_numeric(
            group['num_generation_error'], errors='raise'
        ).sum())
        pooled = (
            total_accepted / total_valid if total_valid else math.nan
        )
        unconditional = (
            simulation_total_accepted / total_requested
            if total_requested else math.nan
        )
        low, high = wilson_interval(total_accepted, total_valid)
        seed_values = pd.to_numeric(
            group['seed_conditional_acceptance'], errors='coerce'
        ).dropna()
        rows.append({
            'config_group_id': keys[0],
            'algorithm': keys[1],
            'algorithm_display_name': keys[2],
            'normalized_utilization': float(keys[3]),
            'num_seeds': len(group),
            'num_seeds_requested': len(group),
            'num_valid_seeds': len(seed_values),
            'num_seeds_with_valid_simulations': len(seed_values),
            'num_seeds_without_valid_simulations': (
                len(group) - len(seed_values)
            ),
            'total_tasksets': total_valid,
            'total_accepted': total_accepted,
            'total_rejected': total_rejected,
            'total_valid': total_valid,
            'total_requested': total_requested,
            'simulation_total_accepted': simulation_total_accepted,
            'total_error': total_error,
            'total_timeout': total_timeout,
            'total_generation_error': total_generation_error,
            'no_valid_simulations': total_valid == 0,
            'pooled_conditional_acceptance': pooled,
            'unconditional_success_rate': unconditional,
            # Compatibility field used by existing plotting scripts.
            'mean_acceptance_ratio': pooled,
            'seed_ratio_std': (
                float(seed_values.std(ddof=1))
                if len(seed_values) > 1
                else 0.0 if len(seed_values) == 1 else math.nan
            ),
            'ci95_method': 'wilson_accepted_over_valid',
            'ci95_low': low,
            'ci95_high': high,
        })
    return by_seed, pd.DataFrame(rows)


def plot_acceptance(summary, output_path, title):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 6))
    for algorithm, group in summary.groupby('algorithm', sort=True):
        group = group.sort_values('normalized_utilization')
        x = group['normalized_utilization'].astype(float).to_numpy()
        mean = group['mean_acceptance_ratio'].astype(float).to_numpy()
        low = group['ci95_low'].astype(float).to_numpy()
        high = group['ci95_high'].astype(float).to_numpy()
        label = group.iloc[0].get(
            'algorithm_display_name', DISPLAY_NAMES.get(algorithm, algorithm)
        )
        line = ax.plot(x, mean, marker='o', label=label)[0]
        ax.fill_between(x, low, high, color=line.get_color(), alpha=0.18)
    ax.set_xlabel('Normalized Processor Utilization')
    ax.set_ylabel('Acceptance Ratio')
    ax.set_ylim(-0.02, 1.02)
    ax.set_title(title)
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def write_multiseed(run_dirs, output_dir, allow_legacy=False):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    by_seed, summary = summarize_multiseed(
        run_dirs, allow_legacy=allow_legacy
    )
    by_seed.to_csv(output_dir / 'multiseed_acceptance_by_seed.csv', index=False)
    summary.to_csv(
        output_dir / 'multiseed_acceptance_summary.csv', index=False
    )
    plot_acceptance(
        summary, output_dir / 'multiseed_acceptance_plot.png',
        'Multi-seed Scheduler Acceptance',
    )
    return by_seed, summary


def load_raw_runs(run_dirs, allow_legacy=False):
    frames = []
    for run_dir in map(Path, run_dirs):
        if not allow_legacy:
            validate_attested_run_directory(run_dir)
        frame = read_csv(run_dir / 'per_taskset_results.csv')
        frame = _prepare_provenance(
            frame, run_dir, raw=True, allow_legacy=allow_legacy
        )
        validate_horizon_columns(frame)
        frames.append(frame)
    if not frames:
        raise ValueError('at least one run directory is required')
    combined = pd.concat(frames, ignore_index=True)
    _validate_raw_identities(combined, 'per-taskset result ingestion')
    return combined


def paired_comparison(run_dirs, baseline='gpfp_asap_block',
                      allow_legacy=False):
    frame = load_raw_runs(run_dirs, allow_legacy=allow_legacy)
    keys = ['config_id', 'taskset_hash']
    rows = []
    algorithms = sorted(set(frame['algorithm']) - {baseline})
    grouping = ['config_group_id', 'normalized_utilization']
    for group_key, selected in frame.groupby(grouping, sort=True):
        config_group_id, utilization = group_key
        baseline_map = {
            tuple(row[key] for key in keys): row
            for _, row in selected[selected['algorithm'] == baseline].iterrows()
        }
        for other in algorithms:
            other_map = {
                tuple(row[key] for key in keys): row
                for _, row in selected[selected['algorithm'] == other].iterrows()
            }
            all_keys = sorted(set(baseline_map) | set(other_map))
            counts = defaultdict(int)
            for key in all_keys:
                base_row = baseline_map.get(key)
                other_row = other_map.get(key)
                if base_row is None or other_row is None:
                    counts['excluded_missing'] += 1
                    continue
                base_status = outcome_status(base_row)
                other_status = outcome_status(other_row)
                statuses = {base_status, other_status}
                if not statuses <= {'accepted', 'rejected'}:
                    if 'generation_error' in statuses:
                        counts['excluded_generation_error'] += 1
                    elif 'timeout' in statuses:
                        counts['excluded_timeout'] += 1
                    else:
                        counts['excluded_error'] += 1
                    continue
                base_ok = base_status == 'accepted'
                other_ok = other_status == 'accepted'
                bucket = (
                    'both_accepted' if base_ok and other_ok else
                    'baseline_only_accepted' if base_ok else
                    'other_only_accepted' if other_ok else 'both_rejected'
                )
                counts[bucket] += 1
            paired = sum(
                counts[name] for name in (
                    'both_accepted', 'baseline_only_accepted',
                    'other_only_accepted', 'both_rejected',
                )
            )
            base_only = counts['baseline_only_accepted']
            other_only = counts['other_only_accepted']
            rows.append({
                'config_group_id': config_group_id,
                'normalized_utilization': utilization,
                'baseline_algorithm': baseline,
                'other_algorithm': other,
                'both_accepted': counts['both_accepted'],
                'baseline_only_accepted': base_only,
                'other_only_accepted': other_only,
                'both_rejected': counts['both_rejected'],
                'both_accept': counts['both_accepted'],
                'both_reject': counts['both_rejected'],
                'A_only_accepts': base_only,
                'B_only_accepts': other_only,
                'baseline_win_rate': base_only / paired if paired else 0.0,
                'other_win_rate': other_only / paired if paired else 0.0,
                'net_win': base_only - other_only,
                'num_paired_tasksets': paired,
                'common_valid_tasksets': paired,
                'excluded_generation_error': (
                    counts['excluded_generation_error']
                ),
                'excluded_error': counts['excluded_error'],
                'excluded_timeout': counts['excluded_timeout'],
                'excluded_missing': counts['excluded_missing'],
            })
    return pd.DataFrame(rows)


def write_paired(run_dirs, baseline, output_dir, allow_legacy=False):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = paired_comparison(
        run_dirs, baseline, allow_legacy=allow_legacy
    )
    result.to_csv(output_dir / 'paired_comparison.csv', index=False)
    return result


def write_ablations(run_dirs, output_dir, allow_legacy=False):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _, summary = summarize_multiseed(
        run_dirs, allow_legacy=allow_legacy
    )
    outputs = {}
    for name, algorithms in {
        'ablation_timing_block': TIMING_BLOCK,
        'ablation_asap_semantics': ASAP_SEMANTICS,
    }.items():
        selected = summary[summary['algorithm'].isin(algorithms)].copy()
        selected.to_csv(output_dir / '{}.csv'.format(name), index=False)
        plot_acceptance(
            selected, output_dir / '{}.png'.format(name),
            name.replace('_', ' '),
        )
        outputs[name] = selected
    return outputs


def summarize_battery(run_battery_pairs, allow_legacy=False):
    observations = []
    for run_dir, battery in run_battery_pairs:
        by_seed = acceptance_by_seed(
            [run_dir], allow_legacy=allow_legacy
        )
        by_seed['battery'] = float(battery)
        observations.append(by_seed)
    if not observations:
        raise ValueError('at least one --run/--battery pair is required')
    frame = pd.concat(observations, ignore_index=True)
    rows = []
    columns = [
        'config_group_id', 'battery', 'algorithm', 'algorithm_display_name',
        'normalized_utilization',
    ]
    for keys, group in frame.groupby(columns, sort=True):
        total_accepted = int(pd.to_numeric(
            group['acceptance_num_accepted'], errors='raise'
        ).sum())
        total_rejected = int(pd.to_numeric(
            group['acceptance_num_rejected'], errors='raise'
        ).sum())
        total_valid = total_accepted + total_rejected
        total_requested = int(pd.to_numeric(
            group['num_requested_samples'], errors='raise'
        ).sum())
        simulation_total_accepted = int(pd.to_numeric(
            group['num_accepted'], errors='raise'
        ).sum())
        total_error = int(pd.to_numeric(
            group['num_error'], errors='raise'
        ).sum())
        total_timeout = int(pd.to_numeric(
            group['num_timeout'], errors='raise'
        ).sum())
        total_generation_error = int(pd.to_numeric(
            group['num_generation_error'], errors='raise'
        ).sum())
        pooled = (
            total_accepted / total_valid if total_valid else math.nan
        )
        low, high = wilson_interval(total_accepted, total_valid)
        valid_seeds = int((group['acceptance_num_valid'] > 0).sum())
        rows.append({
            'config_group_id': keys[0],
            'battery': keys[1],
            'algorithm': keys[2],
            'algorithm_display_name': keys[3],
            'normalized_utilization': keys[4],
            'num_seeds': len(group),
            'num_seeds_requested': len(group),
            'num_valid_seeds': valid_seeds,
            'num_seeds_with_valid_simulations': valid_seeds,
            'num_seeds_without_valid_simulations': len(group) - valid_seeds,
            'total_accepted': total_accepted,
            'total_rejected': total_rejected,
            'total_valid': total_valid,
            'total_requested': total_requested,
            'simulation_total_accepted': simulation_total_accepted,
            'total_error': total_error,
            'total_timeout': total_timeout,
            'total_generation_error': total_generation_error,
            'no_valid_simulations': total_valid == 0,
            'pooled_conditional_acceptance': pooled,
            'unconditional_success_rate': (
                simulation_total_accepted / total_requested
                if total_requested else math.nan
            ),
            'mean_acceptance_ratio': pooled,
            'ci95_method': 'wilson_accepted_over_valid',
            'ci95_low': low,
            'ci95_high': high,
        })
    return pd.DataFrame(rows)


def write_battery(run_battery_pairs, output_dir, allow_legacy=False):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_battery(
        run_battery_pairs, allow_legacy=allow_legacy
    )
    summary.to_csv(output_dir / 'battery_sensitivity_summary.csv', index=False)
    fig, ax = plt.subplots(figsize=(8, 6))
    asap = summary[summary['algorithm'] == 'gpfp_asap_block']
    for battery, group in asap.groupby('battery'):
        group = group.sort_values('normalized_utilization')
        ax.plot(
            group['normalized_utilization'], group['mean_acceptance_ratio'],
            marker='o', label='battery={}'.format(battery),
        )
    ax.set_xlabel('Normalized Processor Utilization')
    ax.set_ylabel('ASAP-Block Acceptance Ratio')
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_dir / 'battery_sensitivity_plot.png', dpi=200)
    plt.close(fig)
    return summary


def select_cases(run_dirs, allow_legacy=False):
    frame = load_raw_runs(run_dirs, allow_legacy=allow_legacy)
    keys = ['config_id', 'taskset_hash']
    rows = []
    comparisons = {
        'gpfp_asap_nonblock': 'asap_block_accept__asap_nonblock_reject',
        'gpfp_asap_sync': 'asap_block_accept__asap_sync_reject',
        'gpfp_alap_block': 'asap_block_accept__alap_block_reject',
    }
    for key, group in frame.groupby(keys, sort=True):
        by_algorithm = {
            row['algorithm']: row for _, row in group.iterrows()
        }
        baseline = by_algorithm.get('gpfp_asap_block')
        if baseline is None:
            print('warning: missing ASAP-BLOCK for {}'.format(key), file=sys.stderr)
            continue
        base_ok = accepted(baseline)

        def add(case_type, comparison_algorithm, comparison):
            rows.append({
                'case_type': case_type,
                'config_id': key[0],
                'taskset_hash': key[1],
                'seed_base': baseline.get('seed_base', ''),
                'taskset_seed': baseline.get('taskset_seed', ''),
                'normalized_utilization': baseline.get(
                    'normalized_utilization', ''
                ),
                'task_idx': baseline.get(
                    'task_index', baseline.get('task_idx', '')
                ),
                'baseline_algorithm': 'gpfp_asap_block',
                'comparison_algorithm': comparison_algorithm,
                'baseline_status': baseline.get('status', ''),
                'comparison_status': comparison.get('status', ''),
                'output_dir': baseline.get(
                    'output_dir', baseline.get('source_run', '')
                ),
                'trace_path': baseline.get('trace_path', ''),
            })

        for algorithm, case_type in comparisons.items():
            comparison = by_algorithm.get(algorithm)
            if (
                comparison is not None
                and base_ok
                and outcome_status(comparison) == 'rejected'
            ):
                add(case_type, algorithm, comparison)
        accepted_others = [
            row for algorithm, row in by_algorithm.items()
            if algorithm != 'gpfp_asap_block' and accepted(row)
        ]
        if not base_ok and accepted_others:
            other = accepted_others[0]
            add('asap_block_reject__other_accept', other['algorithm'], other)
        statuses = [outcome_status(row) for row in by_algorithm.values()]
        if (
            len(by_algorithm) >= len(DISPLAY_NAMES)
            and all(status == 'accepted' for status in statuses)
        ):
            add('all_accept', '', baseline)
        if (
            len(by_algorithm) >= len(DISPLAY_NAMES)
            and all(status == 'rejected' for status in statuses)
        ):
            add('all_reject', '', baseline)
    return pd.DataFrame(rows)


def write_cases(run_dirs, output_dir, allow_legacy=False):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = select_cases(run_dirs, allow_legacy=allow_legacy)
    result.to_csv(output_dir / 'mechanism_case_candidates.csv', index=False)
    return result


def truthy(value):
    """Interpret bool-like CSV values without relying on Python truthiness."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value)) and float(value) == 1.0
    return str(value).strip().lower() in {'true', '1', 'yes', 'y', 't'}


def nonempty(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return math.isfinite(float(value)) and float(value) != 0.0
    return str(value).strip().lower() not in {'', 'none', 'nan', 'null'}


def finite_values(series):
    values = pd.to_numeric(series, errors='coerce')
    valid = values.map(
        lambda value: pd.notna(value) and math.isfinite(value)
    )
    return values[valid]


def _resolve_manifest_run_dir(manifest_path, value):
    run_dir = Path(str(value))
    if run_dir.is_absolute():
        return run_dir
    candidates = [run_dir, manifest_path.parent / run_dir]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[-1]


def _tightness_statistics(values):
    if values.empty:
        return {
            'tightness_num_samples': 0,
            'avg_tightness': float('nan'),
            'median_tightness': float('nan'),
            'max_tightness': float('nan'),
        }
    return {
        'tightness_num_samples': int(len(values)),
        'avg_tightness': float(values.mean()),
        'median_tightness': float(values.median()),
        'max_tightness': float(values.max()),
    }


def _summarize_rta_raw_group(group, total_suffix=False):
    analyzed = int(len(group))
    proven = int(group['_rta_proven'].sum())
    errors = int(group['_rta_error'].sum())
    timeouts = int(group['_rta_timeout'].sum())
    unproven = analyzed - proven - errors
    tightness = finite_values(
        group.loc[group['_rta_proven'], '_tightness']
    )
    stats = _tightness_statistics(tightness)
    suffix = '_total' if total_suffix else ''
    result = {
        'rta_num_analyzed{}'.format(suffix): analyzed,
        'rta_num_proven{}'.format(suffix): proven,
        'rta_num_unproven{}'.format(suffix): unproven,
        'rta_num_errors{}'.format(suffix): errors,
        'rta_num_timeouts{}'.format(suffix): timeouts,
        'rta_proven_ratio{}'.format(suffix): (
            proven / analyzed if analyzed else float('nan')
        ),
        'soundness_proven_but_rejected': int(
            group['_soundness_rejected'].sum()
        ),
        'soundness_observed_exceeds_bound': int(
            group['_soundness_bound'].sum()
        ),
        'e1_rta_pass_sim_pass': int(
            (group['_rta_proven'] & group['_accepted']).sum()
        ),
        'e1_rta_fail_sim_pass': int(
            (~group['_rta_proven'] & group['_accepted']).sum()
        ),
        'e1_rta_fail_sim_fail': int(
            (~group['_rta_proven'] & ~group['_accepted']).sum()
        ),
        'e1_rta_pass_sim_fail': int(
            (group['_rta_proven'] & ~group['_accepted']).sum()
        ),
        'e1_soundness_violation_count': int(
            group['_e1_soundness_violation'].sum()
        ),
    }
    if total_suffix:
        result.update({
            'tightness_num_samples_total': stats['tightness_num_samples'],
            'avg_tightness': stats['avg_tightness'],
            'median_tightness': stats['median_tightness'],
            'max_tightness': stats['max_tightness'],
            # Explicit names for downstream paper scripts.
            'tightness_num_proven_samples': stats['tightness_num_samples'],
            'avg_tightness_proven': stats['avg_tightness'],
            'median_tightness_proven': stats['median_tightness'],
            'max_tightness_proven': stats['max_tightness'],
            # Compatibility alias; it now has the correct raw-row value.
            'avg_tightness_over_reported_rows': stats['avg_tightness'],
        })
    else:
        result.update(stats)
    return result


def summarize_rta_e0(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = read_csv(manifest_path)
    required = {'run_dir', 'E0', 'rta_version'}
    if not required.issubset(manifest.columns):
        raise ValueError('manifest must contain run_dir,E0,rta_version')
    versions = set(manifest['rta_version'].dropna().astype(str))
    if versions != {'v20.4'}:
        raise ValueError(
            'RTA E0 analysis requires only v20.4 runs, got {}'.format(
                sorted(versions)
            )
        )
    raw_frames = []
    for _, entry in manifest.iterrows():
        run_dir = _resolve_manifest_run_dir(manifest_path, entry['run_dir'])
        raw_path = run_dir / 'per_taskset_results.csv'
        frame = read_csv(raw_path)
        required_raw = {
            'normalized_utilization', 'rta_enabled', 'rta_version',
            'rta_status', 'rta_proven', 'rta_error', 'tightness',
            'accepted', 'rta_response_time_bound',
            'simulated_response_time',
        }
        missing = required_raw - set(frame.columns)
        if missing:
            raise ValueError(
                '{} is missing raw RTA columns: {}'.format(
                    raw_path, ', '.join(sorted(missing))
                )
            )
        run_versions = set(frame['rta_version'].astype(str).str.strip())
        if run_versions != {str(entry['rta_version'])}:
            raise ValueError(
                '{} RTA versions {} do not match manifest {}'.format(
                    run_dir, sorted(run_versions), entry['rta_version']
                )
            )
        enabled = frame['rta_enabled'].map(truthy)
        selected = frame.loc[enabled].copy()
        selected['rta_version'] = str(entry['rta_version'])
        selected['E0'] = float(entry['E0'])
        selected['normalized_utilization'] = pd.to_numeric(
            selected['normalized_utilization'], errors='coerce'
        )
        if selected['normalized_utilization'].isna().any():
            raise ValueError('{} contains invalid utilization'.format(raw_path))
        selected['_rta_proven'] = selected['rta_proven'].map(truthy)
        status = selected['rta_status'].astype(str).str.strip().str.lower()
        error_text = selected['rta_error'].astype(str)
        selected['_rta_error'] = (
            error_text.map(nonempty)
            | status.isin({
                'rta_error', 'error', 'failed', 'rta_timeout', 'timeout',
            })
        )
        if (selected['_rta_proven'] & selected['_rta_error']).any():
            raise ValueError(
                '{} has rows that are both proven and errors'.format(
                    raw_path
                )
            )
        selected['_rta_timeout'] = (
            error_text.str.contains('timed out', case=False, na=False)
            | status.isin({'rta_timeout', 'timeout'})
        )
        selected['_tightness'] = pd.to_numeric(
            selected['tightness'], errors='coerce'
        )
        selected['_accepted'] = selected['accepted'].map(truthy)
        if 'timeout' in selected.columns:
            selected['_sim_timeout'] = selected['timeout'].map(truthy)
        elif 'status' in selected.columns:
            selected['_sim_timeout'] = (
                selected['status'].astype(str).str.strip().str.lower()
                .isin({'timeout', 'simulation_timeout'})
            )
        else:
            selected['_sim_timeout'] = False
        bound = pd.to_numeric(
            selected['rta_response_time_bound'], errors='coerce'
        )
        observed = pd.to_numeric(
            selected['simulated_response_time'], errors='coerce'
        )
        finite_pair = bound.map(
            lambda value: pd.notna(value) and math.isfinite(value)
        ) & observed.map(
            lambda value: pd.notna(value) and math.isfinite(value)
        )
        if 'soundness_violation' in selected.columns:
            selected['_e1_soundness_violation'] = (
                selected['soundness_violation'].map(truthy)
            )
        else:
            selected['_e1_soundness_violation'] = (
                selected['_rta_proven']
                & ~selected['_accepted']
                & ~selected['_sim_timeout']
            )
        selected['_soundness_rejected'] = selected[
            '_e1_soundness_violation'
        ]
        selected['_soundness_bound'] = (
            selected['_rta_proven'] & finite_pair & (observed > bound)
        )
        raw_frames.append(selected)

    if not raw_frames:
        return pd.DataFrame(), pd.DataFrame()
    raw = pd.concat(raw_frames, ignore_index=True)
    rejected_count = int(raw['_soundness_rejected'].sum())
    bound_count = int(raw['_soundness_bound'].sum())
    if rejected_count or bound_count:
        raise ValueError(
            'RTA soundness violation: proven_but_rejected={}, '
            'observed_exceeds_bound={}'.format(rejected_count, bound_count)
        )

    by_util_rows = []
    for keys, group in raw.groupby(
        ['rta_version', 'E0', 'normalized_utilization'], sort=True
    ):
        row = {
            'rta_version': keys[0],
            'E0': float(keys[1]),
            'normalized_utilization': float(keys[2]),
        }
        row.update(_summarize_rta_raw_group(group))
        by_util_rows.append(row)
    by_util = pd.DataFrame(by_util_rows)

    summaries = []
    for (version, e0), group in raw.groupby(
        ['rta_version', 'E0'], sort=True
    ):
        row = {
            'rta_version': version,
            'E0': float(e0),
        }
        row.update(_summarize_rta_raw_group(group, total_suffix=True))
        summaries.append(row)
    return pd.DataFrame(summaries), by_util


def write_rta_e0(manifest_path, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, by_util = summarize_rta_e0(manifest_path)
    summary.to_csv(output_dir / 'rta_e0_sensitivity_summary.csv', index=False)
    by_util.to_csv(
        output_dir / 'rta_e0_sensitivity_by_utilization.csv', index=False
    )
    fig, ax = plt.subplots(figsize=(8, 6))
    for e0, group in by_util.groupby('E0', sort=True):
        group = group.sort_values('normalized_utilization')
        ax.plot(
            group['normalized_utilization'], group['rta_proven_ratio'],
            marker='o', label='E0={}'.format(e0),
        )
    ax.set_xlabel('Normalized Processor Utilization')
    ax.set_ylabel('RTA Proven Ratio')
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_dir / 'rta_e0_proven_ratio.png', dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 6))
    for e0, group in by_util.groupby('E0', sort=True):
        group = group.sort_values('normalized_utilization')
        for field, statistic, linestyle in (
            ('avg_tightness', 'mean', '-'),
            ('median_tightness', 'median', '--'),
        ):
            values = pd.to_numeric(group[field], errors='coerce')
            if values.notna().any():
                ax.plot(
                    group['normalized_utilization'], values,
                    marker='o', linestyle=linestyle,
                    label='E0={} {}'.format(e0, statistic),
                )
    ax.set_xlabel('Normalized Processor Utilization')
    ax.set_ylabel('Tightness for RTA-Proven Raw Rows')
    ax.grid(True, linestyle='--', alpha=0.4)
    if ax.lines:
        ax.legend()
    fig.tight_layout()
    fig.savefig(output_dir / 'rta_e0_tightness.png', dpi=200)
    plt.close(fig)
    return summary, by_util
