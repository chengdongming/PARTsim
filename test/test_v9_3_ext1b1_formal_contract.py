from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest
import yaml

from experiments.v9_3.config import canonical_json
from experiments.v9_3.ext1b_config import (
    ext1b_config_hash,
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import Ext1BRunner
import experiments.v9_3.ext1b_formal_contract as formal
from experiments.v9_3.ext1b_formal_contract import (
    FormalContractError,
    canonical_payload_hash,
    formal_shard_config,
    verify_formal_contract,
)
from experiments.v9_3.scheduler_registry import SCHEDULERS, SCHEDULER_IDS


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/v9_3_ext1b1_formal_r1.yaml"
TEMPLATE = ROOT / "configs/v9_3_ext1b_formal_template.yaml"
CONTRACT = ROOT / "docs/experiments/v9_3_ext1b1_formal_r1_contract.json"
PLAN = ROOT / "docs/experiments/v9_3_ext1b1_formal_r1_request_plan.json"
LEDGER = ROOT / "docs/experiments/v9_3_ext1b1_formal_r1_seed_ledger.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_static_tree(tmp_path: Path) -> Path:
    for relative in (
        "configs/v9_3_ext1b1_formal_r1.yaml",
        "configs/v9_3_ext1b_formal_template.yaml",
        "docs/experiments/v9_3_ext1b1_formal_r1_contract.json",
        "docs/experiments/v9_3_ext1b1_formal_r1_request_plan.json",
        "docs/experiments/v9_3_ext1b1_formal_r1_seed_ledger.json",
    ):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return tmp_path


def test_independent_formal_config_is_strict_and_statically_verified(monkeypatch):
    native_calls = []
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_engine.run_paired_simulation",
        lambda *args, **kwargs: native_calls.append((args, kwargs)),
    )
    config = load_ext1b_config(CONFIG)
    assert config["parameter_status"] == "FROZEN_FOR_FORMAL_EXECUTION"
    assert config["seed_space"] == "EXT1B_FORMAL"
    assert config["scenario"]["kind"] == "BYPASS_STRESS"
    assert config["scenario"]["subtype"] == "B1"
    assert config["grid"]["tasksets_per_cell"] == 200
    assert config["grid"]["taskset_index_start"] == 10000
    assert ext1b_config_hash(config) == formal.EXPECTED_CONFIG_SEMANTIC_HASH
    result = verify_formal_contract(ROOT)
    assert result["status"] == "B1_FORMAL_CONTRACT_STATICALLY_VERIFIED"
    assert result["native_simulation_invocations"] == 0
    assert result["formal_run_status"] == "FORMAL_NOT_RUN"
    assert native_calls == []


def test_original_template_remains_non_executable_and_status_only_promotion_fails(monkeypatch):
    template = load_ext1b_config(TEMPLATE)
    assert template["parameter_status"] == "UNFROZEN_FORMAL_TEMPLATE"
    calls = []
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_engine.run_paired_simulation",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    with pytest.raises(RuntimeError, match="formal execution is not authorized"):
        Ext1BRunner(template).run()

    raw = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    raw["parameter_status"] = "FROZEN_FOR_FORMAL_EXECUTION"
    promoted = validate_ext1b_config(raw)
    assert ext1b_config_hash(promoted) != formal.EXPECTED_CONFIG_SEMANTIC_HASH
    with pytest.raises(RuntimeError, match="formal execution is not authorized"):
        Ext1BRunner(promoted).run()
    assert calls == []


def test_six_shards_have_exact_unique_base_seeds_and_config_hashes():
    anchor = load_ext1b_config(CONFIG)
    assert len(formal.FORMAL_CELLS) == 6
    assert len({cell.cell_id for cell in formal.FORMAL_CELLS}) == 6
    assert len({cell.base_seed for cell in formal.FORMAL_CELLS}) == 6
    assert len({cell.bootstrap_seed for cell in formal.FORMAL_CELLS}) == 6
    shard_hashes = {
        ext1b_config_hash(formal_shard_config(anchor, cell))
        for cell in formal.FORMAL_CELLS
    }
    assert len(shard_hashes) == 6


def test_seed_and_taskset_index_domains_are_disjoint_and_collision_free():
    ledger = _json(LEDGER)["payload"]
    entries = ledger["generation_preimages"]
    assert len(entries) == 19200
    assert len({row["seed_preimage_sha256"] for row in entries}) == 19200
    formal_seeds = {row["derived_seed"] for row in entries}
    assert len(formal_seeds) == 19200
    assert formal_seeds.isdisjoint(ledger["registered_derived_seeds"])
    assert min(row["logical_taskset_index"] for row in entries) == 10000
    assert max(row["logical_taskset_index"] for row in entries) == 10199
    assert min(row["source_taskset_index"] for row in entries) == 160000
    assert max(row["source_taskset_index"] for row in entries) == 163199
    for domain in ledger["registered_domains"]:
        assert (
            domain["logical_taskset_index_end"] < 10000
            or domain["logical_taskset_index_start"] > 10199
        )
    audit = ledger["collision_audit"]
    assert audit["audit_result"] == "NO_COLLISION"
    assert audit["formal_internal_collision_count"] == 0
    assert audit["formal_vs_registered_collision_count"] == 0


def test_plan_cardinality_scheduler_order_and_identity_closure():
    plan = _json(PLAN)["payload"]
    pairs = plan["paired_tasksets"]
    requests = plan["requests"]
    assert len(pairs) == 1200
    assert len({row["paired_taskset_identity"] for row in pairs}) == 1200
    assert Counter(row["cell_id"] for row in pairs) == Counter({
        cell.cell_id: 200 for cell in formal.FORMAL_CELLS
    })
    assert Counter(row["stratum"] for row in pairs) == Counter({
        "LOW": 400, "MEDIUM": 400, "HIGH": 400,
    })
    assert len(requests) == 10800
    assert len({row["request_identity"] for row in requests}) == 10800
    assert len(SCHEDULER_IDS) == len(set(SCHEDULER_IDS)) == 9
    grouped = defaultdict(list)
    for row in requests:
        grouped[row["paired_taskset_identity"]].append(row["scheduler_id"])
    assert len(grouped) == 1200
    assert all(order == list(SCHEDULER_IDS) for order in grouped.values())
    assert plan["native_simulation_invocations_during_materialization"] == 0


def test_config_contract_plan_and_ledger_hashes_close_exactly():
    contract = _json(CONTRACT)
    plan = _json(PLAN)
    ledger = _json(LEDGER)
    assert canonical_payload_hash(contract["payload"]) == contract["contract_sha256"] == formal.EXPECTED_CONTRACT_SHA256
    assert canonical_payload_hash(plan["payload"]) == plan["request_plan_sha256"] == formal.EXPECTED_REQUEST_PLAN_SHA256
    assert canonical_payload_hash(ledger["payload"]) == ledger["seed_ledger_sha256"] == formal.EXPECTED_SEED_LEDGER_SHA256
    tracked = contract["payload"]["tracked_config"]
    assert tracked["semantic_hash"] == plan["payload"]["config_semantic_hash"]
    assert tracked["semantic_hash"] == ledger["payload"]["config_semantic_hash"]
    assert contract["payload"]["request_plan"]["sha256"] == plan["request_plan_sha256"]
    assert contract["payload"]["seed_ledger"]["sha256"] == ledger["seed_ledger_sha256"]


def test_technical_gates_scientific_criteria_and_denominators_are_separate():
    payload = _json(CONTRACT)["payload"]
    gates = payload["technical_validity_hard_gates"]
    science = payload["scientific_replication_criteria"]
    denominators = payload["fixed_denominators"]
    assert gates["complete_nine_way_paired_tasksets"] == 1200
    assert gates["terminals"] == gates["retained_traces"] == 10800
    assert science["per_cell_structural_activation"] == "200/200"
    assert science["per_cell_runtime_observable"] == "200/200"
    assert science["per_cell_native_bypass_activation_minimum"] == "180/200"
    assert "does not invalidate technically valid data" in science["failure_rule"]
    assert denominators == {
        "analysis_unit": "paired_taskset",
        "per_cell": 200,
        "per_stratum": 400,
        "overall": 1200,
        "unconditional": True,
        "prohibited_exclusions": [
            "deadline outcome", "bypass absence or presence", "algorithm performance",
            "effect sign or magnitude", "scientific replication failure",
        ],
        "rule": "all planned paired tasksets remain in planned denominators; every reduction to an observable or comparison denominator is separately counted and technically classified",
    }


def test_comparison_and_bootstrap_contract_is_closed_and_paired():
    payload = _json(CONTRACT)["payload"]
    eligibility = payload["comparison_eligible_definition"]
    reporting = payload["denominator_reporting"]
    bootstrap = payload["bootstrap"]
    estimands = payload["estimands"]
    assert eligibility["closed"] and eligibility["mechanical"]
    assert eligibility["deadline_miss_is_eligible"]
    assert eligibility["bypass_activation_is_not_an_eligibility_condition"]
    assert reporting["required_outputs"] == [
        "planned_count", "terminal_count", "observable_count",
        "comparison_count", "not_applicable_count", "technical_failure_count",
    ]
    assert bootstrap["unit"] == "paired_taskset"
    assert bootstrap["resamples"] == 10000
    assert "independently sample 200" in bootstrap["stratum_rule"]
    assert bootstrap["binary_effect"] == "mean of within-paired-taskset binary differences"
    assert bootstrap["continuous_effect"] == "median after first computing one within-paired-taskset difference per pair"
    assert "no unadjusted significance decision" in estimands["significance_policy"]


@pytest.mark.parametrize("artifact", ["config", "contract", "plan", "ledger"])
def test_any_frozen_artifact_mutation_fails_even_if_envelope_is_rehashed(
    tmp_path, monkeypatch, artifact,
):
    static_root = _copy_static_tree(tmp_path)
    monkeypatch.setattr(formal, "audited_scheduler_registry", lambda root: SCHEDULERS)
    if artifact == "config":
        path = static_root / "configs/v9_3_ext1b1_formal_r1.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["simulation"]["horizon"] = 401
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    else:
        suffix, hash_field = {
            "contract": ("contract.json", "contract_sha256"),
            "plan": ("request_plan.json", "request_plan_sha256"),
            "ledger": ("seed_ledger.json", "seed_ledger_sha256"),
        }[artifact]
        path = static_root / f"docs/experiments/v9_3_ext1b1_formal_r1_{suffix}"
        envelope = _json(path)
        if artifact == "contract":
            envelope["payload"]["scale"]["requests"] = 10799
        elif artifact == "plan":
            envelope["payload"]["requests"][0]["scheduler_id"] = "changed"
        else:
            envelope["payload"]["generation_preimages"][0]["derived_seed"] += 1
        envelope[hash_field] = canonical_payload_hash(envelope["payload"])
        path.write_text(json.dumps(envelope, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with pytest.raises((FormalContractError, ValueError)):
        verify_formal_contract(static_root)


def test_contract_static_verification_never_calls_native(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_engine.run_paired_simulation",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    assert verify_formal_contract(ROOT)["native_simulation_invocations"] == 0
    assert calls == []
