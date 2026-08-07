#!/usr/bin/env python3
"""Create a finite RTA4 V3 campaign YAML template without running it."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_formal_config_v3 import RTA4_CORES_V3  # noqa: E402
from experiments.v9_3.rta4_core3_contracts_v6 import (  # noqa: E402
    default_core3_artifact_storage_v1,
    default_core3_energy_conservation_rule_v1,
)


METHODS = ["CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ"]
SOURCE1 = {
    "core": "CORE-1", "source_scope": "CORE1_TASKSET_STORE",
    "source_campaign_config_sha256": "0" * 64,
    "source_plan_sha256": "0" * 64,
    "source_taskset_store_identity": "0" * 64,
    "taskset_count": 800,
}
SOURCE4 = {
    "core": "CORE-4", "source_scope": "CORE4_BASELINE",
    "source_campaign_config_sha256": "0" * 64,
    "source_plan_sha256": "0" * 64,
    "source_taskset_store_identity": "0" * 64,
    "taskset_count": 500,
}
BASELINE = {
    "e0": "1/20", "normalized_utilization": "1/2",
    "service_scale": "1", "power_scale": "1",
    "deadline_slack_fraction": "3/4",
}


def campaign_template(core: str) -> dict:
    if core == "CORE-1":
        return {
            "campaign_id": "asap-block-rta4-e1-critical-e0-v1", "core": core,
            "processors": 4, "task_count": 10,
            "normalized_utilization": [
                "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5",
            ],
            "tasksets_per_utilization": 100,
            "e0": ["1/2", "21/40", "11/20"], "methods": METHODS,
        }
    if core == "CORE-2":
        return {
            "campaign_id": "replace-core2-campaign-id", "core": core,
            "source": SOURCE1, "e0": ["1/2", "21/40", "11/20"],
            "methods": ["CW_D", "LOC_D", "PH_D", "SEQ_D", "CW_THETA_CW", "SEQ_THETA_SEQ"],
            "referenced_recursive_methods": ["LOC_THETA_LOC", "PH_THETA_PH"],
        }
    if core == "CORE-3":
        return {
            "campaign_id": "replace-core3-campaign-id", "core": core,
            "source": SOURCE1, "release_modes": ["ASYNC_HASH_PHASE_V1", "SYNC_V1"],
            "finite_battery_capacities": ["20", "100"],
            "projection_methods": METHODS,
            "projection_e0": ["1/2", "21/40", "11/20"],
            "simulation_horizon": {
                "release_horizon": 100000,
                "observation_horizon": "release_horizon_plus_dmax",
            },
        }
    if core == "CORE-4":
        return {
            "campaign_id": "replace-core4-campaign-id", "core": core,
            "processors": 4, "task_count": 10,
            "normalized_utilization": ["3/10", "2/5", "1/2", "3/5", "7/10"],
            "skeletons_per_utilization": 100,
            "baseline": {
                key: value for key, value in BASELINE.items()
                if key != "normalized_utilization"
            },
            "axes": {
                "e0": ["0", "1/100", "1/50", "3/100", "1/20", "1/5", "1"],
                "service_scale": ["1/2", "3/4", "1", "5/4", "3/2"],
                "power_scale": ["1/2", "3/4", "1", "5/4", "3/2"],
                "deadline_slack_fraction": ["1/4", "1/2", "3/4", "1"],
            },
            "methods": METHODS,
        }
    if core == "CORE-5A":
        return {
            "campaign_id": "replace-core5a-campaign-id", "core": core,
            "baseline": BASELINE,
            "task_count_axis": {"values": [5, 10, 20, 30], "processors": 4, "tasksets": 100},
            "processor_axis": {"values": [2, 4, 8], "task_count": 10, "tasksets": 100},
            "integer_time_scale_axis": {"values": [1, 2, 4, 8], "base_tasksets": 100},
            "methods": METHODS,
        }
    if core == "CORE-5B":
        return {
            "campaign_id": "replace-core5b-campaign-id", "core": core,
            "source": SOURCE4,
            "utilization_strata": ["3/10", "2/5", "1/2", "3/5", "7/10"],
            "candidates_per_method_stratum": 100,
            "selected_per_method_stratum": 75,
            "methods": METHODS, "workers": [1, 2, 4, 8],
        }
    raise ValueError(f"unknown core: {core}")


def core3_v6_campaign_template() -> dict:
    """Return the opt-in CORE-3 contract without changing legacy V5 input."""

    value = campaign_template("CORE-3")
    energy_rule = default_core3_energy_conservation_rule_v1()
    energy_rule.pop("rule_identity")
    artifact_storage = default_core3_artifact_storage_v1()
    artifact_storage.pop("storage_contract_identity")
    value.update({
        "campaign_id": "replace-core3-v6-campaign-id",
        "physical_initial_energy": "0",
        "theorem_battery_capacity": "1000000000",
        "core3_campaign_type": "FORMAL",
        "energy_conservation_rule": energy_rule,
        "artifact_storage": artifact_storage,
        "projection_e0": ["34", "35", "36", "37", "38", "39", "40"],
    })
    return value


def core3_v7_campaign_template() -> dict:
    """Return the explicit CORE-3 model-energy to joule projection."""

    value = core3_v6_campaign_template()
    value.update({
        "campaign_id": "replace-core3-v7-campaign-id",
        "simulation_tick_ms": 1,
        "core3_simulation_contract_version": (
            "ASAP_BLOCK_V9_3_RTA4_CORE3_SIMULATION_CONTRACT_V7"
        ),
        "model_energy_unit_joules": "1/1000",
    })
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True, choices=RTA4_CORES_V3)
    core3_version = parser.add_mutually_exclusive_group()
    core3_version.add_argument(
        "--core3-v6", action="store_true",
        help="emit the opt-in CORE-3 schema-3/sidecar contract",
    )
    core3_version.add_argument(
        "--core3-v7", action="store_true",
        help="emit the CORE-3 explicit model-energy/joule projection",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing campaign: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.core3_v6 and args.core != "CORE-3":
        raise SystemExit("--core3-v6 requires --core CORE-3")
    if args.core3_v7 and args.core != "CORE-3":
        raise SystemExit("--core3-v7 requires --core CORE-3")
    document = (
        core3_v7_campaign_template()
        if args.core3_v7
        else core3_v6_campaign_template()
        if args.core3_v6
        else campaign_template(args.core)
    )
    args.output.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
