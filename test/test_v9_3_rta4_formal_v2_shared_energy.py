from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest

from experiments.v9_3 import exact_energy
from experiments.v9_3.rta4_formal_config import (
    RTA4FormalConfigError,
    load_rta4_formal_config,
)
from experiments.v9_3.rta4_formal_config_v2 import (
    RTA4_FORMAL_PROFILE_V2,
    RTA4FormalConfigV2Error,
    load_rta4_formal_config_v2,
)
from experiments.v9_3.rta4_formal_plan import iter_formal_plan
from experiments.v9_3.rta4_formal_plan_v2 import (
    describe_all_formal_plans_v2,
    iter_formal_plan_v2,
)
from experiments.v9_3.rta4_formal_schema import formal_schema_hash
from experiments.v9_3.rta4_formal_schema_v2 import (
    FORMAL_TABLES_V2,
    formal_schema_hash_v2,
    formal_schema_manifest_v2,
)
from experiments.v9_3.rta4_numeric_contract_v2 import (
    RTA4_NUMERIC_CONTRACT_V2_SHA256,
    numeric_contract_v2_metadata,
)


ROOT = Path(__file__).resolve().parents[1]
CORES = ("CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B")
V1_HASHES = {
    "CORE-1": "1a891fcf4d9aa493c94b5b74997a930471bafc57f3cdb4782bd5bd895db0882c",
    "CORE-2": "4371ec3755e09358ed3b6d04dd3580e3121d9a1100ccd67641190a04ad4e8a47",
    "CORE-3": "98ee60bd33b20f39a8ad6b628f8d93dfa8d4bb08f252b852d6759b21f59168b4",
    "CORE-4": "4b95bc5b64e3b4757fa1b8d8079055517309ceb1e251d54fc154770accf14479",
    "CORE-5A": "c3bdc90958cda8658a9c0731c161c5e882eba86dfe486f96b6e701108fd0d4fe",
    "CORE-5B": "98ddae38a5bd63f7ccc4a74651dcda58f6461b452002852889928313192d2b44",
}


def _path(core, version):
    slug = core.lower().replace("-", "")
    return ROOT / "configs" / f"v9_3_rta4_{slug}_unauthorized_pre_pilot_{version}.yaml"


def _configs_v2():
    return {
        core: load_rta4_formal_config_v2(
            _path(core, "v2_shared_energy"), expected_core=core,
        )
        for core in CORES
    }


def test_v1_files_are_byte_stable_and_v1_v2_loaders_reject_cross_version():
    for core in CORES:
        v1 = _path(core, "v1")
        v2 = _path(core, "v2_shared_energy")
        assert hashlib.sha256(v1.read_bytes()).hexdigest() == V1_HASHES[core]
        load_rta4_formal_config(v1, expected_core=core)
        load_rta4_formal_config_v2(v2, expected_core=core)
        with pytest.raises(RTA4FormalConfigV2Error):
            load_rta4_formal_config_v2(v1, expected_core=core)
        with pytest.raises(RTA4FormalConfigError):
            load_rta4_formal_config(v2, expected_core=core)


def test_v2_schema_and_numeric_contract_are_new_unit_explicit_identities():
    manifest = formal_schema_manifest_v2()
    assert formal_schema_hash_v2() != formal_schema_hash()
    assert manifest["unit_contract"] == {
        "energy_demand": "J/tick", "service": "J", "horizon": "ticks",
    }
    assert "P_exact" not in FORMAL_TABLES_V2["formal_tasks.csv"]
    assert "energy_j_per_tick" in FORMAL_TABLES_V2["formal_tasks.csv"]
    numeric = numeric_contract_v2_metadata()
    assert RTA4_NUMERIC_CONTRACT_V2_SHA256 != exact_energy.NUMERIC_CONTRACT_SHA256
    assert numeric["theory_document_sha256"] == exact_energy.THEORY_DOCUMENT_SHA256
    assert numeric["task_total_energy_divided_by_seconds_forbidden"] is True
    assert numeric["linear_service_scale_times_length_forbidden"] is True


def test_six_v2_configs_are_unauthorized_and_use_separate_output_and_store_roots():
    for core, config in _configs_v2().items():
        assert config["experiment_contract"]["profile"] == RTA4_FORMAL_PROFILE_V2
        assert config["experiment_contract"]["parameter_status"] == "UNAUTHORIZED_PRE_PILOT"
        assert "v2_shared_energy" in config["execution"]["output_root"]
        assert config["execution"]["taskset_store"].endswith("v2_shared_energy")
        v1 = load_rta4_formal_config(_path(core, "v1"), expected_core=core)
        assert config["execution"]["output_root"] != v1["execution"]["output_root"]
        assert config["execution"]["taskset_store"] != v1["execution"]["taskset_store"]
        assert config["shared_energy"]["linear_beta_forbidden"] is True


def test_v2_plan_counts_grid_and_pairing_are_unchanged_but_identities_change():
    configs = _configs_v2()
    plans = describe_all_formal_plans_v2(configs)
    assert plans["total_unique_rta_requests"] == 124_400
    assert plans["total_simulations"] == 6_400
    assert plans["core5b_mathematical_requests"] == 3_000
    assert plans["core5b_executions"] == 12_000
    expected = {
        "CORE-1": 19_200, "CORE-2": 28_800, "CORE-3": 6_400,
        "CORE-4": 72_000, "CORE-5A": 4_400, "CORE-5B": 12_000,
    }
    for core in CORES:
        description = plans["plans"][core]
        assert description["ordered_stream_count"] == expected[core]
        v2_record = next(iter_formal_plan_v2(configs[core]))
        v1_config = load_rta4_formal_config(_path(core, "v1"), expected_core=core)
        v1_record = next(iter_formal_plan(v1_config))
        assert v2_record.taskset_slot_id == v1_record.taskset_slot_id
        assert v2_record.taskset_skeleton_slot_id == v1_record.taskset_skeleton_slot_id
        if v1_record.mathematical_request_id is None:
            assert v2_record.mathematical_request_id is None
        else:
            assert v2_record.mathematical_request_id != v1_record.mathematical_request_id
        assert v2_record.execution_id != v1_record.execution_id
        for field in (
            "method", "exact_e0", "service_scale", "power_scale",
            "deadline_variant", "normalized_utilization", "replicate_index",
        ):
            assert v2_record.material.get(field) == v1_record.material.get(field)


def test_v2_contract_builder_check_passes_without_authorization():
    completed = subprocess.run(
        [
            "python3", "scripts/build_v9_3_rta4_v2_contracts.py", "--check",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert "UNAUTHORIZED_PRE_PILOT" in completed.stdout
    assert "authorization" not in completed.stderr.lower()


def test_official_formal_cli_describes_v2_but_refuses_execution_without_authorization():
    config = _path("CORE-1", "v2_shared_energy")
    described = subprocess.run(
        [
            "python3", "scripts/run_v9_3_rta4_formal.py",
            "--config", str(config), "--dry-run",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert RTA4_FORMAL_PROFILE_V2 in described.stdout
    assert '"ordered_stream_count": 19200' in described.stdout
    refused = subprocess.run(
        [
            "python3", "scripts/run_v9_3_rta4_formal.py",
            "--config", str(config),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert refused.returncode == 2
    assert "UNAUTHORIZED_PRE_PILOT" in refused.stderr
