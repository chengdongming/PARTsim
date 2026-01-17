#!/usr/bin/env python3
import sys
sys.path.insert(0, '..')
from energy_manager import EnergyManager

# 简单配置
config = {
    "cpu_islands": [{
        "name": "test_cpu",
        "numcpus": 2,
        "kernel": {
            "scheduler": "gpfp_epp",
            "task_placement": "global"
        },
        "volts": [0.92, 1.00, 1.14],
        "freqs": [7000, 8100, 10500],
        "base_freq": 8100
    }],
    "energy_management": {
        "initial_energy": 100.0,
        "max_energy": 1000.0
    }
}

print("Starting quick EPP test...")
print("This should complete quickly if the fix works")

# 运行测试
try:
    manager = EnergyManager(config)
    print("Test completed successfully!")
except Exception as e:
    print(f"Test failed: {e}")
