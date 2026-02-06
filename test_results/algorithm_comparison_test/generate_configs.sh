#!/bin/bash

# Script to generate all system configuration files for algorithm comparison test

BASE_DIR="/home/devcontainers/PARTSim-project/test_results/algorithm_comparison_test"
TEMPLATE_FILE="${BASE_DIR}/configs/system_2core_gpfp_tie_0j.yml"

# Algorithms to test
ALGORITHMS=("tie" "tgf" "btie")

# Energy levels: 0J, 100J, 12mJ (0.012J), 15mJ (0.015J)
declare -A ENERGY_LEVELS
ENERGY_LEVELS["0j"]="0.0"
ENERGY_LEVELS["100j"]="100.0"
ENERGY_LEVELS["12mj"]="0.012"
ENERGY_LEVELS["15mj"]="0.015"

# Generate config files for each combination
for algo in "${ALGORITHMS[@]}"; do
    for energy_label in "${!ENERGY_LEVELS[@]}"; do
        energy_value="${ENERGY_LEVELS[$energy_label]}"
        output_file="${BASE_DIR}/configs/system_2core_gpfp_${algo}_${energy_label}.yml"

        echo "Generating: $output_file"

        # Copy template and modify
        cp "$TEMPLATE_FILE" "$output_file"

        # Replace scheduler type
        sed -i "s/scheduler: gpfp_tie/scheduler: gpfp_${algo}/g" "$output_file"

        # Replace initial energy
        sed -i "s/initial_energy: 0.0/initial_energy: ${energy_value}/g" "$output_file"

        # Replace log file path
        sed -i "s/charging_gpfp_tie_0j.log/charging_gpfp_${algo}_${energy_label}.log/g" "$output_file"
    done
done

echo "All configuration files generated successfully!"
