#!/usr/bin/env python3
"""Compare scheduler outcomes and trace signatures for audit tasksets."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import (
    diagnostic_output_directory, finalize_diagnostic_outputs,
    validate_attested_analyzer_input,
)


PAIR_GROUPS = [
    ('gpfp_asap_nonblock', 'gpfp_asap_sync'),
    ('gpfp_asap_nonblock', 'gpfp_st_block'),
    ('gpfp_asap_nonblock', 'gpfp_st_sync'),
    ('gpfp_alap_block', 'gpfp_alap_nonblock'),
    ('gpfp_alap_block', 'gpfp_alap_sync'),
    ('gpfp_asap_block', 'gpfp_asap_nonblock'),
    ('gpfp_asap_block', 'gpfp_asap_sync'),
    ('gpfp_asap_block', 'gpfp_alap_block'),
]
SUMMARY_FIELDS = [
    'audit_case_id', 'category', 'scheduler_count',
    'accepted_same', 'first_missed_task_same',
    'deadline_miss_time_same', 'execution_timeline_hash_same',
    'battery_curve_hash_same', 'trace_file_size_same', 'conclusion',
]
PAIRWISE_FIELDS = [
    'audit_case_id', 'category', 'scheduler_a', 'scheduler_b',
    'accepted_a', 'accepted_b', 'accepted_same',
    'first_missed_task_same', 'deadline_miss_time_same',
    'execution_timeline_hash_same', 'battery_curve_hash_same',
    'trace_file_size_same', 'classification',
]


def canonical_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return int(number) if number.is_integer() else number


def digest(items):
    payload = json.dumps(
        items, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def trace_signatures(trace_path):
    path = Path(str(trace_path))
    if not trace_path or not path.is_file():
        return {
            'execution_timeline_hash': '',
            'battery_curve_hash': '',
            'trace_file_size': '',
        }
    try:
        with path.open(encoding='utf-8') as handle:
            events = json.load(handle).get('events', [])
    except (OSError, ValueError, TypeError):
        return {
            'execution_timeline_hash': '',
            'battery_curve_hash': '',
            'trace_file_size': path.stat().st_size,
        }
    timeline = []
    battery = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get('event_type')
        if event_type in {'scheduled', 'descheduled', 'end_instance'}:
            timeline.append([
                canonical_number(event.get('time', '')),
                event_type,
                str(event.get('task_name', '')),
                canonical_number(event.get('arrival_time', '')),
                canonical_number(event.get('execution_time_ms', '')),
                canonical_number(event.get('executed_time_ms', '')),
            ])
        if 'current_energy_mJ' in event:
            battery.append([
                canonical_number(event.get('time', '')),
                canonical_number(event.get('current_energy_mJ', '')),
            ])
    return {
        'execution_timeline_hash': digest(timeline) if timeline else '',
        'battery_curve_hash': digest(battery) if battery else '',
        'trace_file_size': path.stat().st_size,
    }


def optional_same(values):
    values = [str(value) for value in values]
    if not values or any(value == '' for value in values):
        return ''
    return len(set(values)) == 1


def misses_same(values):
    values = [str(value) for value in values]
    if not values or all(value == '' for value in values):
        return ''
    return optional_same(values)


def accepted_value(row):
    value = str(row.get('accepted', '')).strip().lower()
    if value in {'1', '1.0', 'true'}:
        return True
    if value in {'0', '0.0', 'false'}:
        return False
    return None


def enrich_rows(frame):
    rows = []
    for _, row in frame.iterrows():
        data = row.to_dict()
        data.update(trace_signatures(data.get('trace_path', '')))
        rows.append(data)
    return pd.DataFrame(rows)


def case_conclusion(group):
    outcomes = {
        row['scheduler']: accepted_value(row)
        for _, row in group.iterrows()
    }
    known = [value for value in outcomes.values() if value is not None]
    if (
        outcomes.get('gpfp_asap_block') is True
        and known
        and all(
            value is False for scheduler, value in outcomes.items()
            if scheduler != 'gpfp_asap_block'
        )
    ):
        return 'asap_block_only_accept'
    first_same = misses_same(group['first_missed_task'])
    deadline_same = misses_same(group['deadline_miss_time'])
    if known and len(known) == len(group) and not any(known):
        if first_same is True and deadline_same is True:
            return 'all_reject_same_miss'
        if first_same is False or deadline_same is False:
            return 'all_reject_different_miss'
        return ''
    outcome_same = len(known) == len(group) and len(set(known)) == 1
    timeline_same = optional_same(group['execution_timeline_hash'])
    battery_same = optional_same(group['battery_curve_hash'])
    size_same = optional_same(group['trace_file_size'])
    if (
        outcome_same and timeline_same is True
        and battery_same is True and size_same is True
    ):
        return 'outcome_same_trace_same'
    if outcome_same and (
        timeline_same is False or battery_same is False or size_same is False
    ):
        return 'outcome_same_trace_different'
    if first_same is True:
        return 'same_first_miss'
    if first_same is False:
        return 'different_first_miss'
    return ''


def summarize_audit(frame):
    enriched = enrich_rows(frame)
    summaries = []
    pairwise = []
    for case_id, group in enriched.groupby('audit_case_id', sort=True):
        category = group.iloc[0].get('category', '')
        outcomes = [accepted_value(row) for _, row in group.iterrows()]
        accepted_same = (
            len(set(outcomes)) == 1 if None not in outcomes else ''
        )
        summaries.append({
            'audit_case_id': case_id,
            'category': category,
            'scheduler_count': len(group),
            'accepted_same': accepted_same,
            'first_missed_task_same': misses_same(
                group['first_missed_task']
            ),
            'deadline_miss_time_same': misses_same(
                group['deadline_miss_time']
            ),
            'execution_timeline_hash_same': optional_same(
                group['execution_timeline_hash']
            ),
            'battery_curve_hash_same': optional_same(
                group['battery_curve_hash']
            ),
            'trace_file_size_same': optional_same(
                group['trace_file_size']
            ),
            'conclusion': case_conclusion(group),
        })
        by_scheduler = {
            row['scheduler']: row for _, row in group.iterrows()
        }
        for scheduler_a, scheduler_b in PAIR_GROUPS:
            if scheduler_a not in by_scheduler or scheduler_b not in by_scheduler:
                continue
            a = by_scheduler[scheduler_a]
            b = by_scheduler[scheduler_b]
            pairwise.append(compare_pair(
                case_id, category, scheduler_a, scheduler_b, a, b
            ))
    return (
        pd.DataFrame(summaries, columns=SUMMARY_FIELDS),
        pd.DataFrame(pairwise, columns=PAIRWISE_FIELDS),
    )


def compare_pair(case_id, category, scheduler_a, scheduler_b, a, b):
    accepted_a = accepted_value(a)
    accepted_b = accepted_value(b)
    accepted_same = (
        accepted_a == accepted_b
        if accepted_a is not None and accepted_b is not None else ''
    )
    first_same = misses_same([
        a.get('first_missed_task', ''), b.get('first_missed_task', '')
    ])
    deadline_same = misses_same([
        a.get('deadline_miss_time', ''), b.get('deadline_miss_time', '')
    ])
    timeline_same = optional_same([
        a.get('execution_timeline_hash', ''),
        b.get('execution_timeline_hash', ''),
    ])
    battery_same = optional_same([
        a.get('battery_curve_hash', ''), b.get('battery_curve_hash', '')
    ])
    size_same = optional_same([
        a.get('trace_file_size', ''), b.get('trace_file_size', '')
    ])
    if (
        scheduler_a == 'gpfp_asap_block'
        and accepted_a is True and accepted_b is False
    ):
        classification = 'asap_block_only_accept'
    elif (
        accepted_same is True and timeline_same is True
        and battery_same is True and size_same is True
    ):
        classification = 'outcome_same_trace_same'
    elif accepted_same is True and (
        timeline_same is False or battery_same is False or size_same is False
    ):
        classification = 'outcome_same_trace_different'
    elif first_same is True:
        classification = 'same_first_miss'
    elif first_same is False:
        classification = 'different_first_miss'
    else:
        classification = ''
    return {
        'audit_case_id': case_id,
        'category': category,
        'scheduler_a': scheduler_a,
        'scheduler_b': scheduler_b,
        'accepted_a': '' if accepted_a is None else accepted_a,
        'accepted_b': '' if accepted_b is None else accepted_b,
        'accepted_same': accepted_same,
        'first_missed_task_same': first_same,
        'deadline_miss_time_same': deadline_same,
        'execution_timeline_hash_same': timeline_same,
        'battery_curve_hash_same': battery_same,
        'trace_file_size_same': size_same,
        'classification': classification,
    }


def write_audit_analysis(audit_runs, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.read_csv(audit_runs, keep_default_na=False)
    required = {
        'audit_case_id', 'scheduler', 'accepted', 'first_missed_task',
        'deadline_miss_time', 'trace_path',
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError('audit CSV is missing columns: {}'.format(
            ', '.join(sorted(missing))
        ))
    summary, pairwise = summarize_audit(frame)
    summary.to_csv(
        output_dir / 'scheduler_diversity_summary.csv', index=False
    )
    pairwise.to_csv(
        output_dir / 'pairwise_trace_comparison.csv', index=False
    )
    return summary, pairwise


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            'Compare binary outcomes, first misses, execution timelines, '
            'battery curves, and trace sizes for scheduler audit runs.'
        )
    )
    parser.add_argument('--audit-runs', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument(
        '--allow-unattested-diagnostic-input', action='store_true'
    )
    args = parser.parse_args(argv)
    try:
        output_dir = Path(args.output_dir)
        if args.allow_unattested_diagnostic_input:
            output_dir = diagnostic_output_directory(output_dir)
        else:
            validate_attested_analyzer_input(args.audit_runs)
        result = write_audit_analysis(args.audit_runs, output_dir)
        if args.allow_unattested_diagnostic_input:
            finalize_diagnostic_outputs(output_dir)
        return result
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
