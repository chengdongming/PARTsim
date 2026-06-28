#!/usr/bin/env python3
"""Summarize mechanism cases and render plots supported by trace data."""

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_mechanism_case_study import TRACE_LIMITATION


SUMMARY_FIELDS = [
    'case_type', 'scheduler', 'num_cases', 'num_accepted', 'num_rejected',
    'num_timeout', 'num_error', 'acceptance_ratio', 'battery_min_min',
    'battery_final_mean', 'executed_ticks_total',
]


def summarize_cases(frame):
    rows = []
    valid = frame[frame['scheduler'].astype(str) != '']
    for keys, group in valid.groupby(['case_type', 'scheduler'], sort=True):
        statuses = group['simulation_status'].astype(str)
        accepted = pd.to_numeric(group['accepted'], errors='coerce').fillna(0)
        battery_min = pd.to_numeric(group['battery_min'], errors='coerce')
        battery_final = pd.to_numeric(group['battery_final'], errors='coerce')
        executed = pd.to_numeric(group['executed_ticks'], errors='coerce')
        rows.append({
            'case_type': keys[0],
            'scheduler': keys[1],
            'num_cases': len(group),
            'num_accepted': int(accepted.sum()),
            'num_rejected': int((statuses == 'rejected').sum()),
            'num_timeout': int((statuses == 'timeout').sum()),
            'num_error': int(statuses.isin([
                'error', 'taskset_not_found'
            ]).sum()),
            'acceptance_ratio': float(accepted.mean()),
            'battery_min_min': (
                float(battery_min.min()) if battery_min.notna().any() else ''
            ),
            'battery_final_mean': (
                float(battery_final.mean())
                if battery_final.notna().any() else ''
            ),
            'executed_ticks_total': (
                float(executed.sum()) if executed.notna().any() else ''
            ),
        })
    return pd.DataFrame(rows, columns=SUMMARY_FIELDS)


def load_events(trace_path):
    try:
        with Path(trace_path).open(encoding='utf-8') as handle:
            return json.load(handle).get('events', [])
    except (OSError, ValueError, TypeError):
        return []


def plot_trace_if_supported(row, figures_dir):
    trace_path = row.get('trace_path')
    if not trace_path:
        return []
    events = load_events(trace_path)
    if not events:
        return []
    figures_dir.mkdir(parents=True, exist_ok=True)
    prefix = '{}-{}'.format(row['case_id'], row['scheduler'])
    outputs = []
    energy_points = []
    scheduled = []
    for event in events:
        if not isinstance(event, dict):
            continue
        try:
            time = float(event.get('time'))
        except (TypeError, ValueError):
            continue
        if 'current_energy_mJ' in event:
            try:
                energy_points.append((
                    time, float(event['current_energy_mJ']) / 1000.0
                ))
            except (TypeError, ValueError):
                pass
        if event.get('event_type') == 'scheduled' and event.get('task_name'):
            scheduled.append((time, str(event['task_name'])))
    if energy_points:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(*zip(*energy_points))
        ax.set_xlabel('Time (ms)')
        ax.set_ylabel('Battery energy (J)')
        ax.grid(True, linestyle='--', alpha=0.4)
        fig.tight_layout()
        path = figures_dir / '{}-battery_over_time.png'.format(prefix)
        fig.savefig(path, dpi=180)
        plt.close(fig)
        outputs.append(path)
    if scheduled:
        tasks = sorted({task for _, task in scheduled})
        y = {task: index for index, task in enumerate(tasks)}
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.scatter(
            [time for time, _ in scheduled],
            [y[task] for _, task in scheduled], s=10,
        )
        ax.set_yticks(range(len(tasks)))
        ax.set_yticklabels(tasks)
        ax.set_xlabel('Scheduled event time (ms)')
        ax.set_ylabel('Task')
        ax.grid(True, linestyle='--', alpha=0.3)
        fig.tight_layout()
        path = figures_dir / '{}-execution_timeline.png'.format(prefix)
        fig.savefig(path, dpi=180)
        plt.close(fig)
        outputs.append(path)
    return outputs


def write_mechanism_analysis(case_summary, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.read_csv(case_summary, keep_default_na=False)
    summary = summarize_cases(frame)
    summary.to_csv(output_dir / 'mechanism_case_summary.csv', index=False)
    figures = output_dir / 'figures'
    for _, row in frame.iterrows():
        plot_trace_if_supported(row, figures)
    print(TRACE_LIMITATION)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Analyze mechanism case summaries and available traces. '
        + TRACE_LIMITATION
    )
    parser.add_argument('--case-summary', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(argv)
    write_mechanism_analysis(args.case_summary, args.output_dir)


if __name__ == '__main__':
    main()
