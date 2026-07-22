from copy import deepcopy
from pathlib import Path

from experiments.v9_3.performance_config import load_performance_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def config(name="v9_3_b4_smoke.yaml"):
    return deepcopy(load_performance_config(PROJECT_ROOT / "configs" / name))


def task_payload():
    rows = []
    powers = ["4", "3", "2", "1", "1", "1", "1", "1", "1", "1"]
    for rank in range(10):
        rows.append({
            "task_id": str(rank), "source_name": f"task_{rank}",
            "priority_rank": rank, "C": 1, "D": 5 + rank,
            "T": 10 + rank, "P": powers[rank],
            "workload": "bzip2", "arrival_offset": 0,
        })
    return rows
