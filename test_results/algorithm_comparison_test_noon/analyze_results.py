#!/usr/bin/env python3

"""
Algorithm Comparison Analysis Script
Analyzes trace files from TIE, TGF, and BTIE scheduler tests
"""

import json
import os
from pathlib import Path
from collections import defaultdict

def analyze_trace(trace_file):
    """Analyze a single trace file and extract key metrics"""
    with open(trace_file, 'r') as f:
        data = json.load(f)

    events = data.get('events', [])

    # Initialize metrics
    metrics = {
        'completed_tasks': 0,
        'deadline_misses': 0,
        'scheduled_count': 0,
        'descheduled_count': 0,
        'initial_energy_mJ': 0,
        'final_energy_mJ': 0,
        'total_consumed_mJ': 0,
        'total_harvested_mJ': 0,
        'energy_insufficient_count': 0,
        'task_completions': defaultdict(int),
        'task_misses': defaultdict(int)
    }

    if not events:
        return metrics

    # Get initial and final energy
    metrics['initial_energy_mJ'] = events[0].get('current_energy_mJ', 0)
    metrics['final_energy_mJ'] = events[-1].get('current_energy_mJ', 0)
    metrics['total_consumed_mJ'] = events[-1].get('total_consumed_mJ', 0)
    metrics['total_harvested_mJ'] = events[-1].get('total_harvested_mJ', 0)

    # Analyze events
    for event in events:
        event_type = event.get('event_type', '')
        task_name = event.get('task_name', '')

        if event_type == 'end_instance':
            metrics['completed_tasks'] += 1
            metrics['task_completions'][task_name] += 1

        elif event_type == 'dline_miss':
            metrics['deadline_misses'] += 1
            metrics['task_misses'][task_name] += 1

        elif event_type == 'scheduled':
            metrics['scheduled_count'] += 1

        elif event_type == 'descheduled':
            metrics['descheduled_count'] += 1
            if event.get('reason') == 'insufficient_energy':
                metrics['energy_insufficient_count'] += 1

    return metrics

def main():
    base_dir = Path("/home/devcontainers/PARTSim-project/test_results/algorithm_comparison_test_noon")
    traces_dir = base_dir / "traces"

    algorithms = ['tie', 'tgf', 'btie']
    energy_levels = ['0j', '100j', '12mj', '15mj']

    print("=" * 100)
    print("Algorithm Comparison Analysis - Detailed Results")
    print("=" * 100)
    print()

    results = {}

    for algo in algorithms:
        results[algo] = {}
        for energy in energy_levels:
            trace_file = traces_dir / f"trace_{algo}_{energy}.json"

            if trace_file.exists():
                try:
                    metrics = analyze_trace(trace_file)
                    results[algo][energy] = metrics

                    print(f"{algo.upper()} with {energy}:")
                    print(f"  Completed Tasks: {metrics['completed_tasks']}")
                    print(f"  Deadline Misses: {metrics['deadline_misses']}")
                    print(f"  Scheduled Count: {metrics['scheduled_count']}")
                    print(f"  Descheduled Count: {metrics['descheduled_count']}")
                    print(f"  Energy Insufficient Preemptions: {metrics['energy_insufficient_count']}")
                    print(f"  Initial Energy: {metrics['initial_energy_mJ']:.3f} mJ")
                    print(f"  Final Energy: {metrics['final_energy_mJ']:.6f} mJ")
                    print(f"  Total Consumed: {metrics['total_consumed_mJ']:.3f} mJ")
                    print(f"  Total Harvested: {metrics['total_harvested_mJ']:.3f} mJ")

                    if metrics['task_completions']:
                        print(f"  Task Completions: {dict(metrics['task_completions'])}")
                    if metrics['task_misses']:
                        print(f"  Task Misses: {dict(metrics['task_misses'])}")
                    print()
                except Exception as e:
                    print(f"{algo.upper()} with {energy}: Error - {e}")
                    print()
            else:
                print(f"{algo.upper()} with {energy}: Trace file not found")
                print()

    # Comparison summary
    print("=" * 100)
    print("Comparison Summary by Energy Level")
    print("=" * 100)
    print()

    for energy in energy_levels:
        print(f"Energy Level: {energy}")
        print("-" * 100)
        print(f"{'Algorithm':<10} {'Completed':<12} {'Misses':<10} {'Consumed (mJ)':<15} {'Final Energy (mJ)':<20}")
        print("-" * 100)
        for algo in algorithms:
            if energy in results[algo]:
                metrics = results[algo][energy]
                print(f"{algo.upper():<10} {metrics['completed_tasks']:<12} {metrics['deadline_misses']:<10} "
                      f"{metrics['total_consumed_mJ']:<15.3f} {metrics['final_energy_mJ']:<20.6f}")
        print()

    # Algorithm comparison across all energy levels
    print("=" * 100)
    print("Algorithm Performance Summary")
    print("=" * 100)
    print()

    for algo in algorithms:
        print(f"{algo.upper()} Algorithm:")
        total_completed = sum(results[algo][e]['completed_tasks'] for e in energy_levels if e in results[algo])
        total_misses = sum(results[algo][e]['deadline_misses'] for e in energy_levels if e in results[algo])
        total_consumed = sum(results[algo][e]['total_consumed_mJ'] for e in energy_levels if e in results[algo])

        print(f"  Total Completed Tasks: {total_completed}")
        print(f"  Total Deadline Misses: {total_misses}")
        print(f"  Total Energy Consumed: {total_consumed:.3f} mJ")
        print()

if __name__ == "__main__":
    main()
