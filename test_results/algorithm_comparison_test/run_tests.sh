#!/bin/bash

# =============================================
# Algorithm Comparison Test Script
# Tests TIE, TGF, and BTIE schedulers with different initial energy levels
# =============================================

set -e  # Exit on error

# Configuration
BASE_DIR="/home/devcontainers/PARTSim-project"
TEST_DIR="${BASE_DIR}/test_results/algorithm_comparison_test"
TASK_FILE="${BASE_DIR}/test_results/tasks.yml"
RTSIM_BIN="${BASE_DIR}/build/rtsim/rtsim"
SIMULATION_TIME=200  # milliseconds

# Check if rtsim binary exists
if [ ! -f "$RTSIM_BIN" ]; then
    echo "Error: rtsim binary not found at $RTSIM_BIN"
    echo "Please build the project first: cd build && make"
    exit 1
fi

# Check if task file exists
if [ ! -f "$TASK_FILE" ]; then
    echo "Error: Task file not found at $TASK_FILE"
    exit 1
fi

# Algorithms to test
ALGORITHMS=("tie" "tgf" "btie")

# Energy levels
ENERGY_LEVELS=("0j" "100j" "12mj" "15mj")

# Create results summary file
SUMMARY_FILE="${TEST_DIR}/test_summary.txt"
echo "Algorithm Comparison Test Results" > "$SUMMARY_FILE"
echo "=================================" >> "$SUMMARY_FILE"
echo "Task Set: tasks.yml (3 periodic tasks)" >> "$SUMMARY_FILE"
echo "Simulation Time: ${SIMULATION_TIME}ms" >> "$SUMMARY_FILE"
echo "CPU Cores: 2" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"

# Change to project root directory to ensure relative paths work correctly
cd "$BASE_DIR" || exit 1

# Run tests
echo "Starting algorithm comparison tests..."
echo ""

test_count=0
success_count=0
failed_tests=()

for algo in "${ALGORITHMS[@]}"; do
    for energy in "${ENERGY_LEVELS[@]}"; do
        test_count=$((test_count + 1))

        config_file="${TEST_DIR}/configs/system_2core_gpfp_${algo}_${energy}.yml"
        trace_file="${TEST_DIR}/traces/trace_${algo}_${energy}.json"

        echo "=========================================="
        echo "Test $test_count: Algorithm=${algo^^}, Energy=${energy}"
        echo "=========================================="
        echo "Config: $config_file"
        echo "Trace: $trace_file"
        echo ""

        # Run simulation
        if "$RTSIM_BIN" "$config_file" "$TASK_FILE" "$SIMULATION_TIME" -t "$trace_file"; then
            echo "✅ Test completed successfully"
            success_count=$((success_count + 1))

            # Log to summary
            echo "✅ ${algo^^} with ${energy}: SUCCESS" >> "$SUMMARY_FILE"
        else
            echo "❌ Test failed"
            failed_tests+=("${algo}_${energy}")

            # Log to summary
            echo "❌ ${algo^^} with ${energy}: FAILED" >> "$SUMMARY_FILE"
        fi

        echo ""
    done
done

# Print summary
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo "Total tests: $test_count"
echo "Successful: $success_count"
echo "Failed: $((test_count - success_count))"
echo ""

if [ ${#failed_tests[@]} -gt 0 ]; then
    echo "Failed tests:"
    for test in "${failed_tests[@]}"; do
        echo "  - $test"
    done
    echo ""
fi

echo "Summary saved to: $SUMMARY_FILE"
echo "Traces saved to: ${TEST_DIR}/traces/"
echo "Logs saved to: ${TEST_DIR}/logs/"
echo ""

# Final status
if [ $success_count -eq $test_count ]; then
    echo "🎉 All tests passed!"
    exit 0
else
    echo "⚠️  Some tests failed. Check the logs for details."
    exit 1
fi
