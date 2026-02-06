# Quick Start Guide

## Test Location
```
/home/devcontainers/PARTSim-project/test_results/algorithm_comparison_test/
```

## Quick Commands

### Run All Tests
```bash
cd /home/devcontainers/PARTSim-project/test_results/algorithm_comparison_test
./run_tests.sh
```

### Analyze Results
```bash
python3 analyze_results.py
```

### View Summary
```bash
cat test_summary.txt
cat analysis_report.txt
```

## Test Matrix

| Algorithm | Energy Levels | Total Tests |
|-----------|---------------|-------------|
| TIE       | 0J, 100J, 12mJ, 15mJ | 4 |
| TGF       | 0J, 100J, 12mJ, 15mJ | 4 |
| BTIE      | 0J, 100J, 12mJ, 15mJ | 4 |
| **Total** | | **12 tests** |

## Key Results

✅ **All 12 tests passed successfully**

### Winner: TGF Algorithm
- Most energy-efficient: 132.6 mJ consumed (vs 144.0 mJ for TIE/BTIE)
- Same task completion rate as other algorithms
- 8% energy savings

## File Structure
```
algorithm_comparison_test/
├── configs/              # 12 system configuration files
├── traces/               # 12 JSON trace files
├── logs/                 # Log files
├── generate_configs.sh   # Config generator script
├── run_tests.sh          # Test execution script
├── analyze_results.py    # Analysis script
├── test_summary.txt      # Test execution summary
├── analysis_report.txt   # Detailed analysis
├── test_output.log       # Full execution log
├── README.md             # Detailed documentation
└── QUICK_START.md        # This file
```

## Manual Test Example
```bash
./build/rtsim/rtsim \
    test_results/algorithm_comparison_test/configs/system_2core_gpfp_tie_12mj.yml \
    test_results/tasks.yml \
    200 \
    -t test_results/algorithm_comparison_test/traces/trace_tie_12mj.json
```

## View Trace Files
```bash
# Pretty print a trace file
python3 -m json.tool traces/trace_tie_12mj.json | less
```

## Next Steps

1. Review [README.md](README.md) for detailed analysis
2. Examine trace files in `traces/` directory
3. Check `analysis_report.txt` for performance metrics
4. Modify energy levels in configs and re-run tests
