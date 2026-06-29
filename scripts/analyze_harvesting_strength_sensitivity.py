#!/usr/bin/env python3
"""Aggregate manifest-driven harvesting-strength sensitivity runs."""

import argparse
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import acceptance_by_seed, read_csv
from scripts.run_harvesting_strength_sensitivity import LIMITATION


BY_SEED_FIELDS = [
    'scheduler', 'algorithm_display_name', 'normalized_utilization',
    'harvesting_scale', 'harvesting_profile', 'seed_base', 'num_tasksets',
    'acceptance_ratio', 'run_dir',
]
SUMMARY_FIELDS = [
    'scheduler', 'algorithm_display_name', 'normalized_utilization',
    'harvesting_scale', 'harvesting_profile', 'mean_acceptance_ratio',
    'ci95_low', 'ci95_high', 'num_seeds', 'total_tasksets',
]


def summarize_harvesting_strength(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = read_csv(manifest_path)
    required = {'run_dir', 'harvesting_scale', 'seed_base'}
    missing = required - set(manifest.columns)
    if missing:
        raise ValueError('missing manifest columns: {}'.format(
            ', '.join(sorted(missing))
        ))
    rows = []
    for _, entry in manifest.iterrows():
        run_dir = Path(str(entry['run_dir']))
        if not run_dir.is_absolute():
            run_dir = manifest_path.parent / run_dir
        if not (run_dir / 'acceptance_ratio_data.csv').is_file():
            print('warning: missing completed run {}'.format(run_dir), file=sys.stderr)
            continue
        observations = acceptance_by_seed([run_dir])
        for _, observation in observations.iterrows():
            rows.append({
                'scheduler': observation['algorithm'],
                'algorithm_display_name': observation['algorithm_display_name'],
                'normalized_utilization': observation['normalized_utilization'],
                'harvesting_scale': float(entry['harvesting_scale']),
                'harvesting_profile': entry.get(
                    'harvesting_profile', 'synthetic_piecewise'
                ),
                'seed_base': observation['seed_base'],
                'num_tasksets': observation['num_tasksets'],
                'acceptance_ratio': observation['acceptance_ratio'],
                'run_dir': str(run_dir),
            })
    by_seed = pd.DataFrame(rows, columns=BY_SEED_FIELDS)
    summaries = []
    group_fields = [
        'scheduler', 'algorithm_display_name', 'normalized_utilization',
        'harvesting_scale', 'harvesting_profile',
    ]
    for keys, group in by_seed.groupby(group_fields, sort=True):
        values = group['acceptance_ratio'].astype(float)
        count = len(values)
        mean = float(values.mean())
        std = float(values.std(ddof=1)) if count > 1 else 0.0
        margin = 1.96 * std / math.sqrt(count) if count > 1 else 0.0
        summaries.append({
            'scheduler': keys[0],
            'algorithm_display_name': keys[1],
            'normalized_utilization': keys[2],
            'harvesting_scale': keys[3],
            'harvesting_profile': keys[4],
            'mean_acceptance_ratio': mean,
            'ci95_low': max(0.0, mean - margin),
            'ci95_high': min(1.0, mean + margin),
            'num_seeds': count,
            'total_tasksets': int(group['num_tasksets'].sum()),
        })
    return by_seed, pd.DataFrame(summaries, columns=SUMMARY_FIELDS)


def write_harvesting_strength_outputs(manifest_path, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    by_seed, summary = summarize_harvesting_strength(manifest_path)
    by_seed.to_csv(
        output_dir / 'harvesting_strength_sensitivity_by_seed.csv', index=False
    )
    summary.to_csv(
        output_dir / 'harvesting_strength_sensitivity_summary.csv', index=False
    )
    fig, ax = plt.subplots(figsize=(9, 6))
    for (scheduler, scale), group in summary.groupby(
        ['scheduler', 'harvesting_scale'], sort=True
    ):
        group = group.sort_values('normalized_utilization')
        ax.plot(
            group['normalized_utilization'],
            group['mean_acceptance_ratio'],
            marker='o', label='{} scale={:g}'.format(scheduler, scale),
        )
    ax.set_xlabel('Normalized Processor Utilization')
    ax.set_ylabel('Acceptance Ratio')
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, linestyle='--', alpha=0.4)
    if not summary.empty:
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(
        output_dir / 'harvesting_strength_sensitivity_plot.png', dpi=200
    )
    plt.close(fig)
    return by_seed, summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Analyze harvesting-strength sensitivity. ' + LIMITATION
    )
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(argv)
    write_harvesting_strength_outputs(args.manifest, args.output_dir)
    print(LIMITATION)


if __name__ == '__main__':
    main()
