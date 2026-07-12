#!/usr/bin/env python3
"""Aggregate manifest-driven harvesting-strength sensitivity runs."""

import argparse
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import (
    acceptance_by_seed, diagnostic_output_directory,
    finalize_diagnostic_outputs, read_csv, wilson_interval,
)
from scripts.experiment_runner import validate_execution_manifest
from scripts.run_harvesting_strength_sensitivity import LIMITATION


BY_SEED_FIELDS = [
    'config_group_id', 'config_id', 'scheduler', 'algorithm_display_name',
    'normalized_utilization',
    'harvesting_scale', 'harvesting_profile', 'seed_base', 'num_tasksets',
    'num_accepted', 'num_rejected', 'num_valid', 'num_requested',
    'simulation_num_accepted',
    'num_error', 'num_timeout', 'num_generation_error',
    'seed_conditional_acceptance', 'acceptance_ratio', 'run_dir',
]
SUMMARY_FIELDS = [
    'config_group_id', 'scheduler', 'algorithm_display_name',
    'normalized_utilization',
    'harvesting_scale', 'harvesting_profile', 'mean_acceptance_ratio',
    'ci95_low', 'ci95_high', 'num_seeds', 'num_valid_seeds',
    'num_seeds_without_valid_simulations', 'total_tasksets',
    'total_accepted', 'total_rejected', 'total_valid', 'total_requested',
    'simulation_total_accepted',
    'total_error', 'total_timeout', 'total_generation_error',
    'unconditional_success_rate', 'no_valid_simulations', 'ci95_method',
]


def summarize_harvesting_strength(manifest_path, allow_legacy=False):
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
        observations = acceptance_by_seed(
            [run_dir], allow_legacy=allow_legacy
        )
        for _, observation in observations.iterrows():
            rows.append({
                'config_group_id': observation['config_group_id'],
                'config_id': observation['config_id'],
                'scheduler': observation['algorithm'],
                'algorithm_display_name': observation['algorithm_display_name'],
                'normalized_utilization': observation['normalized_utilization'],
                'harvesting_scale': float(entry['harvesting_scale']),
                'harvesting_profile': entry.get(
                    'harvesting_profile', 'synthetic_piecewise'
                ),
                'seed_base': observation['seed_base'],
                'num_tasksets': observation['num_tasksets'],
                'num_accepted': observation['acceptance_num_accepted'],
                'num_rejected': observation['acceptance_num_rejected'],
                'num_valid': observation['acceptance_num_valid'],
                'num_requested': observation['num_requested_samples'],
                'simulation_num_accepted': observation['num_accepted'],
                'num_error': observation['num_error'],
                'num_timeout': observation['num_timeout'],
                'num_generation_error': observation[
                    'num_generation_error'
                ],
                'seed_conditional_acceptance': observation[
                    'seed_conditional_acceptance'
                ],
                'acceptance_ratio': observation['acceptance_ratio'],
                'run_dir': str(run_dir),
            })
    by_seed = pd.DataFrame(rows, columns=BY_SEED_FIELDS)
    duplicate = by_seed[by_seed.duplicated(
        ['config_id', 'scheduler'], keep=False
    )]
    if not duplicate.empty:
        raise ValueError(
            'duplicate_aggregate_result: harvesting-strength inputs '
            'contain repeated config_id/scheduler rows'
        )
    summaries = []
    group_fields = [
        'config_group_id', 'scheduler', 'algorithm_display_name',
        'normalized_utilization',
        'harvesting_scale', 'harvesting_profile',
    ]
    for keys, group in by_seed.groupby(group_fields, sort=True):
        total_accepted = int(group['num_accepted'].sum())
        total_rejected = int(group['num_rejected'].sum())
        total_valid = total_accepted + total_rejected
        total_requested = int(group['num_requested'].sum())
        simulation_total_accepted = int(
            group['simulation_num_accepted'].sum()
        )
        mean = total_accepted / total_valid if total_valid else math.nan
        low, high = wilson_interval(total_accepted, total_valid)
        count = int((group['num_valid'] > 0).sum())
        summaries.append({
            'config_group_id': keys[0],
            'scheduler': keys[1],
            'algorithm_display_name': keys[2],
            'normalized_utilization': keys[3],
            'harvesting_scale': keys[4],
            'harvesting_profile': keys[5],
            'mean_acceptance_ratio': mean,
            'ci95_low': low,
            'ci95_high': high,
            'num_seeds': len(group),
            'num_valid_seeds': count,
            'num_seeds_without_valid_simulations': len(group) - count,
            'total_tasksets': total_valid,
            'total_accepted': total_accepted,
            'total_rejected': total_rejected,
            'total_valid': total_valid,
            'total_requested': total_requested,
            'simulation_total_accepted': simulation_total_accepted,
            'total_error': int(group['num_error'].sum()),
            'total_timeout': int(group['num_timeout'].sum()),
            'total_generation_error': int(
                group['num_generation_error'].sum()
            ),
            'unconditional_success_rate': (
                simulation_total_accepted / total_requested
                if total_requested else math.nan
            ),
            'no_valid_simulations': total_valid == 0,
            'ci95_method': 'wilson_accepted_over_valid',
        })
    return by_seed, pd.DataFrame(summaries, columns=SUMMARY_FIELDS)


def write_harvesting_strength_outputs(
        manifest_path, output_dir, allow_legacy=False):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    by_seed, summary = summarize_harvesting_strength(
        manifest_path, allow_legacy=allow_legacy
    )
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
    parser.add_argument('--allow-legacy', action='store_true')
    args = parser.parse_args(argv)
    if not args.allow_legacy:
        validate_execution_manifest(args.manifest)
    output = (diagnostic_output_directory(args.output_dir)
              if args.allow_legacy else args.output_dir)
    write_harvesting_strength_outputs(
        args.manifest, output, allow_legacy=args.allow_legacy
    )
    if args.allow_legacy:
        finalize_diagnostic_outputs(output)
    print(LIMITATION)


if __name__ == '__main__':
    main()
