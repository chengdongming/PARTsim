#!/usr/bin/env python3
"""Shared helpers for PARTSim experiment post-processing."""

import math
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


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
    if str(row.get('accepted', '')).strip() != '':
        return integer(row.get('accepted')) == 1
    return str(row.get('status', '')).strip() == 'accepted'


def read_csv(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_csv(path, keep_default_na=False)


def load_acceptance_runs(run_dirs):
    frames = []
    for run_dir in map(Path, run_dirs):
        frame = read_csv(run_dir / 'acceptance_ratio_data.csv')
        raw = read_csv(run_dir / 'per_taskset_results.csv')
        for _, aggregate in frame.iterrows():
            utilization = number(aggregate['normalized_utilization'])
            raw_utilization = pd.to_numeric(
                raw['normalized_utilization'], errors='coerce'
            )
            selected = raw[
                (raw['algorithm'] == aggregate['algorithm'])
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
        frame['source_run'] = str(run_dir)
        frames.append(frame)
    if not frames:
        raise ValueError('at least one run directory is required')
    return pd.concat(frames, ignore_index=True)


def acceptance_by_seed(run_dirs):
    """Return one weighted observation per seed/algorithm/utilization."""
    frame = load_acceptance_runs(run_dirs)
    required = {
        'algorithm', 'normalized_utilization', 'acceptance_ratio',
        'num_samples', 'seed_base',
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
        'seed_base', 'algorithm', 'algorithm_display_name',
        'normalized_utilization',
    ]
    for keys, group in frame.groupby(columns, sort=True):
        samples = pd.to_numeric(group['num_samples'], errors='coerce').fillna(0)
        ratios = pd.to_numeric(
            group['acceptance_ratio'], errors='coerce'
        ).fillna(0)
        total = int(samples.sum())
        rows.append({
            'seed_base': keys[0],
            'algorithm': keys[1],
            'algorithm_display_name': keys[2],
            'normalized_utilization': float(keys[3]),
            'num_tasksets': total,
            'acceptance_ratio': (
                float((ratios * samples).sum() / total) if total else 0.0
            ),
        })
    return pd.DataFrame(rows)


def summarize_multiseed(run_dirs):
    by_seed = acceptance_by_seed(run_dirs)
    rows = []
    columns = [
        'algorithm', 'algorithm_display_name', 'normalized_utilization',
    ]
    for keys, group in by_seed.groupby(columns, sort=True):
        values = group['acceptance_ratio'].astype(float)
        count = len(values)
        mean = float(values.mean())
        std = float(values.std(ddof=1)) if count > 1 else 0.0
        margin = 1.96 * std / math.sqrt(count) if count > 1 else 0.0
        rows.append({
            'algorithm': keys[0],
            'algorithm_display_name': keys[1],
            'normalized_utilization': float(keys[2]),
            'num_seeds': count,
            'total_tasksets': int(group['num_tasksets'].sum()),
            'mean_acceptance_ratio': mean,
            'std_acceptance_ratio': std,
            'ci95_low': max(0.0, mean - margin),
            'ci95_high': min(1.0, mean + margin),
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


def write_multiseed(run_dirs, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    by_seed, summary = summarize_multiseed(run_dirs)
    by_seed.to_csv(output_dir / 'multiseed_acceptance_by_seed.csv', index=False)
    summary.to_csv(
        output_dir / 'multiseed_acceptance_summary.csv', index=False
    )
    plot_acceptance(
        summary, output_dir / 'multiseed_acceptance_plot.png',
        'Multi-seed Scheduler Acceptance',
    )
    return by_seed, summary


def load_raw_runs(run_dirs):
    frames = []
    for run_dir in map(Path, run_dirs):
        frame = read_csv(run_dir / 'per_taskset_results.csv')
        frame['source_run'] = str(run_dir)
        frames.append(frame)
    if not frames:
        raise ValueError('at least one run directory is required')
    return pd.concat(frames, ignore_index=True)


def paired_comparison(run_dirs, baseline='gpfp_asap_block'):
    frame = load_raw_runs(run_dirs)
    keys = ['seed_base', 'normalized_utilization', 'task_idx']
    duplicate = frame.duplicated(keys + ['algorithm'], keep=False)
    if duplicate.any():
        print('warning: duplicate scheduler rows; keeping first', file=sys.stderr)
        frame = frame.drop_duplicates(keys + ['algorithm'], keep='first')
    rows = []
    algorithms = sorted(set(frame['algorithm']) - {baseline})
    for utilization in sorted(frame['normalized_utilization'].unique()):
        selected = frame[frame['normalized_utilization'] == utilization]
        baseline_map = {
            tuple(row[key] for key in keys): row
            for _, row in selected[selected['algorithm'] == baseline].iterrows()
        }
        for other in algorithms:
            other_map = {
                tuple(row[key] for key in keys): row
                for _, row in selected[selected['algorithm'] == other].iterrows()
            }
            common = sorted(set(baseline_map) & set(other_map))
            missing = set(baseline_map) ^ set(other_map)
            if missing:
                print(
                    'warning: {} unpaired keys for {} vs {}'.format(
                        len(missing), baseline, other
                    ), file=sys.stderr,
                )
            counts = defaultdict(int)
            for key in common:
                base_ok = accepted(baseline_map[key])
                other_ok = accepted(other_map[key])
                bucket = (
                    'both_accepted' if base_ok and other_ok else
                    'baseline_only_accepted' if base_ok else
                    'other_only_accepted' if other_ok else 'both_rejected'
                )
                counts[bucket] += 1
            paired = len(common)
            base_only = counts['baseline_only_accepted']
            other_only = counts['other_only_accepted']
            rows.append({
                'normalized_utilization': utilization,
                'baseline_algorithm': baseline,
                'other_algorithm': other,
                'both_accepted': counts['both_accepted'],
                'baseline_only_accepted': base_only,
                'other_only_accepted': other_only,
                'both_rejected': counts['both_rejected'],
                'baseline_win_rate': base_only / paired if paired else 0.0,
                'other_win_rate': other_only / paired if paired else 0.0,
                'net_win': base_only - other_only,
                'num_paired_tasksets': paired,
            })
    return pd.DataFrame(rows)


def write_paired(run_dirs, baseline, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = paired_comparison(run_dirs, baseline)
    result.to_csv(output_dir / 'paired_comparison.csv', index=False)
    return result


def write_ablations(run_dirs, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _, summary = summarize_multiseed(run_dirs)
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


def summarize_battery(run_battery_pairs):
    observations = []
    for run_dir, battery in run_battery_pairs:
        by_seed = acceptance_by_seed([run_dir])
        by_seed['battery'] = float(battery)
        observations.append(by_seed)
    if not observations:
        raise ValueError('at least one --run/--battery pair is required')
    frame = pd.concat(observations, ignore_index=True)
    rows = []
    columns = [
        'battery', 'algorithm', 'algorithm_display_name',
        'normalized_utilization',
    ]
    for keys, group in frame.groupby(columns, sort=True):
        values = group['acceptance_ratio'].astype(float)
        count = len(values)
        mean = float(values.mean())
        std = float(values.std(ddof=1)) if count > 1 else 0.0
        margin = 1.96 * std / math.sqrt(count) if count > 1 else 0.0
        rows.append({
            'battery': keys[0],
            'algorithm': keys[1],
            'algorithm_display_name': keys[2],
            'normalized_utilization': keys[3],
            'num_seeds': count,
            'mean_acceptance_ratio': mean,
            'ci95_low': max(0.0, mean - margin),
            'ci95_high': min(1.0, mean + margin),
        })
    return pd.DataFrame(rows)


def write_battery(run_battery_pairs, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_battery(run_battery_pairs)
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


def select_cases(run_dirs):
    frame = load_raw_runs(run_dirs)
    keys = ['seed_base', 'normalized_utilization', 'task_idx']
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
                'seed_base': key[0],
                'taskset_seed': baseline.get('taskset_seed', ''),
                'normalized_utilization': key[1],
                'task_idx': key[2],
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
            if comparison is not None and base_ok and not accepted(comparison):
                add(case_type, algorithm, comparison)
        accepted_others = [
            row for algorithm, row in by_algorithm.items()
            if algorithm != 'gpfp_asap_block' and accepted(row)
        ]
        if not base_ok and accepted_others:
            other = accepted_others[0]
            add('asap_block_reject__other_accept', other['algorithm'], other)
        statuses = [accepted(row) for row in by_algorithm.values()]
        if len(by_algorithm) >= len(DISPLAY_NAMES) and all(statuses):
            add('all_accept', '', baseline)
        if len(by_algorithm) >= len(DISPLAY_NAMES) and not any(statuses):
            add('all_reject', '', baseline)
    return pd.DataFrame(rows)


def write_cases(run_dirs, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = select_cases(run_dirs)
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
