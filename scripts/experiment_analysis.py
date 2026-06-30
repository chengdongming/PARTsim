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


def weighted_tightness(group):
    if 'tightness_num_samples' not in group or 'avg_tightness' not in group:
        return float('nan'), 0
    counts = pd.to_numeric(
        group['tightness_num_samples'], errors='coerce'
    ).fillna(0)
    values = pd.to_numeric(group['avg_tightness'], errors='coerce')
    valid = (counts > 0) & values.notna()
    total = int(counts[valid].sum())
    if not total:
        return float('nan'), 0
    return float((values[valid] * counts[valid]).sum() / total), total


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
    rows = []
    for _, entry in manifest.iterrows():
        run_dir = Path(str(entry['run_dir']))
        if not run_dir.is_absolute():
            run_dir = manifest_path.parent / run_dir
        frame = read_csv(run_dir / 'acceptance_ratio_data.csv')
        frame = frame[frame['algorithm'] == 'gpfp_asap_block']
        if 'rta_version' not in frame.columns:
            raise ValueError(
                '{} is missing rta_version'.format(
                    run_dir / 'acceptance_ratio_data.csv'
                )
            )
        run_versions = set(frame['rta_version'].dropna().astype(str))
        if run_versions != {str(entry['rta_version'])}:
            raise ValueError(
                '{} RTA versions {} do not match manifest {}'.format(
                    run_dir, sorted(run_versions), entry['rta_version']
                )
            )
        for _, row in frame.iterrows():
            rows.append({
                'rta_version': str(entry['rta_version']),
                'E0': float(entry['E0']),
                'normalized_utilization': number(
                    row.get('normalized_utilization')
                ),
                'rta_num_analyzed': integer(row.get('rta_num_analyzed')),
                'rta_num_proven': integer(row.get('rta_num_proven')),
                'rta_num_unproven': integer(row.get('rta_num_unproven')),
                'rta_num_errors': integer(row.get('rta_num_errors')),
                'avg_tightness': number(
                    row.get('avg_tightness'), float('nan')
                ),
                'tightness_num_samples': integer(
                    row.get('tightness_num_samples')
                ),
            })
    by_util = pd.DataFrame(rows)
    if by_util.empty:
        return pd.DataFrame(), by_util
    by_util['rta_proven_ratio'] = by_util.apply(
        lambda row: (
            row['rta_num_proven'] / row['rta_num_analyzed']
            if row['rta_num_analyzed'] else float('nan')
        ), axis=1,
    )
    summaries = []
    for (version, e0), group in by_util.groupby(
        ['rta_version', 'E0'], sort=True
    ):
        analyzed = int(group['rta_num_analyzed'].sum())
        tightness, tight_count = weighted_tightness(group)
        proven = int(group['rta_num_proven'].sum())
        summaries.append({
            'rta_version': version,
            'E0': e0,
            'rta_num_analyzed_total': analyzed,
            'rta_num_proven_total': proven,
            'rta_num_unproven_total': int(group['rta_num_unproven'].sum()),
            'rta_num_errors_total': int(group['rta_num_errors'].sum()),
            'rta_proven_ratio_total': (
                proven / analyzed if analyzed else float('nan')
            ),
            'tightness_num_samples_total': tight_count,
            'avg_tightness_over_reported_rows': tightness,
        })
    return pd.DataFrame(summaries), by_util


def write_rta_e0(manifest_path, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, by_util = summarize_rta_e0(manifest_path)
    summary.to_csv(output_dir / 'rta_e0_sensitivity_summary.csv', index=False)
    by_util.to_csv(
        output_dir / 'rta_e0_sensitivity_by_utilization.csv', index=False
    )
    plots = [
        ('rta_proven_ratio', 'rta_e0_proven_ratio.png', 'RTA Proven Ratio'),
        ('avg_tightness', 'rta_e0_tightness.png', 'Average Tightness'),
    ]
    for field, filename, ylabel in plots:
        fig, ax = plt.subplots(figsize=(8, 6))
        for e0, group in by_util.groupby('E0', sort=True):
            group = group.sort_values('normalized_utilization')
            ax.plot(
                group['normalized_utilization'], group[field],
                marker='o', label='E0={}'.format(e0),
            )
        ax.set_xlabel('Normalized Processor Utilization')
        ax.set_ylabel(ylabel)
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend()
        fig.tight_layout()
        fig.savefig(output_dir / filename, dpi=200)
        plt.close(fig)
    return summary, by_util
