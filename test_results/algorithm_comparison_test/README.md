# Algorithm Comparison Test

## Overview

This test compares three energy-aware scheduling algorithms (TIE, TGF, and BTIE) under different initial energy conditions on a dual-core system.

## Test Configuration

### Task Set
- **Task 1**: Period=20ms, WCET=5ms, Workload=bzip2
- **Task 2**: Period=30ms, WCET=10ms, Workload=bzip2
- **Task 3**: Period=40ms, WCET=15ms, Workload=bzip2

Task configuration file: `/home/devcontainers/PARTSim-project/test_results/tasks.yml`

### System Configuration
- **CPU Cores**: 2
- **Scheduling Algorithms**: TIE, TGF, BTIE
- **Initial Energy Levels**: 0J, 100J, 12mJ, 15mJ
- **Simulation Time**: 200ms
- **Solar Energy**: Disabled (time_of_day_ms: 0, no solar charging)

### Algorithms Tested

1. **TIE (Tick-based Instant Energy-aware)**
   - Strict priority-based blocking scheduling
   - Stops scheduling when high-priority task lacks energy
   - Ensures strict RM priority ordering

2. **TGF (Tick-based Greedy First)**
   - Greedy filling strategy
   - Skips energy-insufficient tasks and continues scanning
   - Maximizes CPU utilization

3. **BTIE (Batch Tick-based Instant Energy-aware)**
   - Batch scheduling with "all-or-nothing" energy check
   - Schedules tasks in batches based on available cores
   - Ensures multi-core synchronization

## Test Results Summary

### Key Findings

#### Energy Level: 0J (No Initial Energy)
- **All algorithms**: 0 tasks completed, 19 deadline misses
- **Result**: No tasks can execute without initial energy

#### Energy Level: 100J (Abundant Energy)
- **All algorithms**: 22 tasks completed, 0 deadline misses
- **TIE**: Consumed 117.000 mJ, Final energy: 99883.000 mJ
- **TGF**: Consumed 105.600 mJ, Final energy: 99894.400 mJ ⭐ **Most efficient**
- **BTIE**: Consumed 117.000 mJ, Final energy: 99883.000 mJ
- **Result**: TGF is slightly more energy-efficient

#### Energy Level: 12mJ (Limited Energy)
- **All algorithms**: 2 tasks completed, 17 deadline misses
- **All consumed**: 12.000 mJ (depleted all available energy)
- **Completions**: task_1 (1), task_2 (1)
- **Result**: All algorithms perform identically under severe energy constraints

#### Energy Level: 15mJ (Limited Energy)
- **All algorithms**: 2 tasks completed, 17 deadline misses
- **All consumed**: 15.000 mJ (depleted all available energy)
- **Completions**: task_1 (1), task_2 (1)
- **Result**: Similar to 12mJ case, all algorithms perform identically

### Overall Performance

| Algorithm | Total Completed | Total Misses | Total Consumed (mJ) |
|-----------|----------------|--------------|---------------------|
| TIE       | 26             | 53           | 144.000             |
| TGF       | 26             | 53           | 132.600 ⭐          |
| BTIE      | 26             | 53           | 144.000             |

**Winner**: TGF is the most energy-efficient algorithm (8% less energy consumed than TIE/BTIE)

## Analysis

### Algorithm Behavior Observations

1. **Under Abundant Energy (100J)**:
   - All algorithms complete all schedulable tasks
   - TGF shows better energy efficiency due to its greedy filling strategy
   - TIE and BTIE have identical energy consumption patterns

2. **Under Limited Energy (12mJ, 15mJ)**:
   - All algorithms perform identically
   - Energy constraints dominate scheduling decisions
   - Only highest priority tasks (task_1, task_2) get partial execution
   - task_3 (lowest priority) never completes

3. **Under Zero Energy (0J)**:
   - No algorithm can schedule any tasks
   - All tasks miss their deadlines

### Key Insights

1. **TGF's Advantage**: The greedy filling strategy allows TGF to utilize energy more efficiently by scheduling lower-priority tasks when higher-priority tasks cannot run due to energy constraints.

2. **TIE/BTIE Similarity**: Under this workload, TIE and BTIE show identical behavior, suggesting that the batch scheduling mechanism in BTIE doesn't provide additional benefits for this specific task set.

3. **Energy Threshold**: The task set requires approximately 12-15mJ to complete 2 task instances before energy depletion. To complete all tasks in 200ms, significantly more energy is needed (>117mJ).

## Files Generated

### Configuration Files
- `configs/system_2core_gpfp_{algorithm}_{energy}.yml` (12 files)

### Trace Files
- `traces/trace_{algorithm}_{energy}.json` (12 files)

### Analysis Files
- `test_summary.txt` - Test execution summary
- `analysis_report.txt` - Detailed performance analysis
- `test_output.log` - Full test execution log

### Scripts
- `generate_configs.sh` - Generates all configuration files
- `run_tests.sh` - Executes all test combinations
- `analyze_results.py` - Analyzes trace files and generates reports

## Running the Tests

### Prerequisites
```bash
# Build the project
cd /home/devcontainers/PARTSim-project/build
make
```

### Execute Tests
```bash
cd /home/devcontainers/PARTSim-project/test_results/algorithm_comparison_test
./run_tests.sh
```

### Analyze Results
```bash
python3 analyze_results.py
```

## Command Reference

### Manual Test Execution
```bash
./build/rtsim/rtsim \
    test_results/algorithm_comparison_test/configs/system_2core_gpfp_tie_12mj.yml \
    test_results/tasks.yml \
    200 \
    -t test_results/algorithm_comparison_test/traces/trace_tie_12mj.json
```

### Parameters
- `<system_config>`: System configuration file (scheduler, energy, CPU settings)
- `<task_config>`: Task set configuration file
- `<simulation_time>`: Simulation duration in milliseconds
- `-t <trace_file>`: Output trace file path (JSON format)

## Conclusion

This test demonstrates that:

1. **TGF is the most energy-efficient** algorithm for this workload, consuming 8% less energy than TIE/BTIE
2. **Under severe energy constraints**, all algorithms perform identically
3. **Under abundant energy**, all algorithms meet all deadlines, but with different energy consumption patterns
4. The **greedy filling strategy** in TGF provides tangible energy savings without compromising task completion rates

For energy-constrained embedded systems, **TGF is recommended** when energy efficiency is the primary concern and strict priority ordering can be relaxed.
