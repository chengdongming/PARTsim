from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from experiments.v9_3.cell_model import derive_seed, expand_cells
from experiments.v9_3.config import canonical_json, domain_hash
from experiments.v9_3.ext1b_config import (
    ext1b_config_hash,
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import Ext1BRunner
from experiments.v9_3.scheduler_registry import SCHEDULER_IDS


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "v9_3_ext1b1_confirm_pilot_r1.yaml"
CONTRACT_PATH = (
    ROOT / "docs" / "experiments" / "v9_3_ext1b1_confirm_pilot_r1_contract.md"
)
BEGIN = "<!-- EXT1B_B1_CONFIRM_CONTRACT_JSON_BEGIN -->"
END = "<!-- EXT1B_B1_CONFIRM_CONTRACT_JSON_END -->"

EXPECTED_CANDIDATES = [
    ("LOW", "u2of5_eta4of5_rho3of4", "2/5", "4/5", "3/4"),
    ("LOW", "u3of5_eta1_rho3of4", "3/5", "1", "3/4"),
    ("MEDIUM", "u3of5_eta4of5_rho3of4", "3/5", "4/5", "3/4"),
    ("MEDIUM", "u3of5_eta4of5_rho1of4", "3/5", "4/5", "1/4"),
    ("HIGH", "u4of5_eta4of5_rho3of4", "4/5", "4/5", "3/4"),
    ("HIGH", "u4of5_eta3of5_rho1of2", "4/5", "3/5", "1/2"),
]


def _envelope() -> dict:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    fenced = text.split(BEGIN, 1)[1].split(END, 1)[0]
    raw = fenced.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(raw)


def _contract_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _raw_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _seed_material(utilization: str, eta: str, rho: str) -> dict[str, str]:
    return {
        "scenario": "BYPASS_STRESS:B1",
        "utilization": utilization,
        "eta": eta,
        "rho": rho,
    }


def _bounded_seed(domain: str, material: dict[str, str]) -> int:
    return int(domain_hash(domain, material)[:16], 16) % 2147483647


def _bootstrap_seed(domain: str, material: dict[str, str]) -> int:
    return int(domain_hash(f"{domain}:BOOTSTRAP", material)[:16], 16) % 2147483646 + 1


def test_contract_hash_is_canonical_and_reproducible() -> None:
    envelope = _envelope()
    observed = _contract_hash(envelope["payload"])
    assert observed == envelope["contract_sha256"]
    assert observed == _contract_hash(deepcopy(envelope["payload"]))
    assert envelope["payload"]["contract_status"] == "FROZEN"


def test_candidate_order_scale_horizon_and_scheduler_order_are_frozen() -> None:
    payload = _envelope()["payload"]
    observed = [
        (
            row["stratum"], row["cell_id"], row["utilization"],
            row["eta"], row["rho"],
        )
        for row in payload["candidates"]
    ]
    assert observed == EXPECTED_CANDIDATES
    assert payload["scale"] == {
        "candidate_cells": 6,
        "pairs_per_cell": 50,
        "requests_per_cell": 450,
        "retained_traces_required": 2700,
        "schedulers_per_pair": 9,
        "total_pairs": 300,
        "total_requests": 2700,
    }
    assert payload["parameters"]["horizon"] == 400
    assert payload["parameters"]["maximum_horizon"] == 400
    assert payload["scheduler_order"] == list(SCHEDULER_IDS)


def test_config_is_strict_pilot_first_shard_and_matches_contract() -> None:
    envelope = _envelope()
    payload = envelope["payload"]
    config = load_ext1b_config(CONFIG_PATH)
    first = payload["candidates"][0]
    assert config["parameter_status"] == "PILOT"
    assert config["seed_space"] == "EXT1B_PILOT"
    assert config["grid"]["tasksets_per_cell"] == 50
    assert config["grid"]["taskset_index_start"] == 1000
    assert config["grid"]["utilization_points"] == [first["utilization"]]
    assert config["grid"]["base_seed"] == first["base_seed"]
    assert config["simulation"]["horizon"] == 400
    assert config["simulation"]["retain_trace"] is True
    assert config["scenario"]["kind"] == "BYPASS_STRESS"
    assert config["scenario"]["subtype"] == "B1"
    assert config["scenario"]["nominal_energy_supply_ratios"] == [first["eta"]]
    assert config["scenario"]["interpolation_rho"] == first["rho"]
    assert ext1b_config_hash(config) == payload["tracked_config"]["semantic_config_hash"]
    assert hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest() == payload["tracked_config"]["file_sha256"]


def test_seed_chain_binds_phase_scenario_cell_index_and_retry() -> None:
    payload = _envelope()["payload"]
    domain = payload["generation"]["domain"]
    raw_template = _raw_config()
    seen_base_seeds: set[int] = set()
    seen_bootstrap_seeds: set[int] = set()
    for candidate in payload["candidates"]:
        material = _seed_material(
            candidate["utilization"], candidate["eta"], candidate["rho"]
        )
        assert _bounded_seed(domain, material) == candidate["base_seed"]
        assert _bootstrap_seed(domain, material) == candidate["bootstrap_seed"]
        assert _bounded_seed("EXT1B_B1_SCREEN_V1", material) != candidate["base_seed"]
        seen_base_seeds.add(candidate["base_seed"])
        seen_bootstrap_seeds.add(candidate["bootstrap_seed"])

        raw = deepcopy(raw_template)
        raw["grid"]["utilization_points"] = [candidate["utilization"]]
        raw["grid"]["base_seed"] = candidate["base_seed"]
        raw["scenario"]["nominal_energy_supply_ratios"] = [candidate["eta"]]
        raw["scenario"]["interpolation_rho"] = candidate["rho"]
        config = validate_ext1b_config(raw)
        generation_id = expand_cells(config)[0].generation_id
        first_source = 1000 * 16
        assert derive_seed(candidate["base_seed"], generation_id, first_source) != derive_seed(
            candidate["base_seed"], generation_id, first_source + 1
        )
        assert derive_seed(candidate["base_seed"], generation_id, first_source) != derive_seed(
            candidate["base_seed"], generation_id, first_source + 16
        )

        changed_scenario = dict(material)
        changed_scenario["scenario"] = "SYNC_BATCH_STRESS:B2"
        assert _bounded_seed(domain, changed_scenario) != candidate["base_seed"]
    assert len(seen_base_seeds) == 6
    assert len(seen_bootstrap_seeds) == 6
    assert payload["generation"]["scheduler_enters_generation_seed"] is False


def test_confirm_and_r2_identity_domains_are_disjoint_and_fully_audited() -> None:
    payload = _envelope()["payload"]
    evidence = payload["evidence"]
    # Actual derive_seed preimages use source_taskset_index. These full retry
    # ranges are disjoint regardless of base-seed or generation-ID values.
    r2_sources = {
        logical * 16 + attempt for logical in range(0, 5) for attempt in range(16)
    }
    confirm_sources = {
        logical * 16 + attempt
        for logical in range(1000, 1050)
        for attempt in range(16)
    }
    assert len(r2_sources) == 80
    assert len(confirm_sources) == 800
    assert r2_sources.isdisjoint(confirm_sources)
    assert payload["generation"]["independent_generation_identities"] == 300
    assert payload["generation"]["all_retry_preimage_count"] == 4800
    assert evidence["no_overlap_ledger_sha256"] == (
        "41cb7955afdcf43c8ce3c60679f4f4c54c23e79f992b8ab3e77748c3e258dc98"
    )
    assert evidence["confirm_plan_sha256"] == (
        "f2e55900f9805399819b6002f5d8d1484861cbb7b1912016d6cdf643c0b11b21"
    )


def test_contract_hash_changes_for_candidate_seed_horizon_or_threshold() -> None:
    payload = _envelope()["payload"]
    original = _contract_hash(payload)
    mutations = []

    candidate = deepcopy(payload)
    candidate["candidates"][0]["cell_id"] = "changed"
    mutations.append(candidate)

    seed = deepcopy(payload)
    seed["candidates"][0]["base_seed"] += 1
    mutations.append(seed)

    horizon = deepcopy(payload)
    horizon["parameters"]["horizon"] = 401
    mutations.append(horizon)

    threshold = deepcopy(payload)
    threshold["gates"]["mechanism_per_cell"][
        "asap_nonblock_native_bypass_activation_minimum"
    ] = 44
    mutations.append(threshold)

    assert all(_contract_hash(item) != original for item in mutations)


def test_secondary_outcomes_cannot_enter_candidate_selection() -> None:
    metrics = _envelope()["payload"]["metrics"]
    assert metrics["secondary_outcomes_may_select_candidates"] is False
    selection = " ".join(metrics["candidate_selection_inputs"]).lower()
    for prohibited in (
        "pass ratio", "winner", "risk difference", "p-value",
        "response time", "first miss", "deadline-miss difference",
    ):
        assert prohibited not in selection


def test_contract_is_b1_pilot_only_and_formal_runner_remains_blocked(monkeypatch) -> None:
    payload = _envelope()["payload"]
    assert {"B2", "B3", "EXT-1A", "formal execution"} <= set(payload["prohibitions"])
    config = load_ext1b_config(CONFIG_PATH)
    formal = deepcopy(config)
    formal["parameter_status"] = "FROZEN_FOR_FORMAL_EXECUTION"
    formal["seed_space"] = "EXT1B_FORMAL"
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_engine.run_paired_simulation",
        lambda *args, **kwargs: pytest.fail("formal gate reached native simulator"),
    )
    with pytest.raises(RuntimeError, match="formal execution is not authorized"):
        Ext1BRunner(formal).run()


def test_dry_run_description_does_not_call_native_simulator(tmp_path, monkeypatch) -> None:
    raw = _raw_config()
    raw["execution"]["output_root"] = str(tmp_path / "must-not-be-created")
    raw["execution"]["taskset_store"] = str(tmp_path / "store-must-not-be-created")
    config = validate_ext1b_config(raw)
    calls = []
    monkeypatch.setattr(
        "experiments.v9_3.ext1b_engine.run_paired_simulation",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    description = Ext1BRunner(config).describe()
    assert description["paired_instance_count"] == 50
    assert description["simulation_request_count"] == 450
    assert description["scheduler_ids"] == list(SCHEDULER_IDS)
    assert calls == []
    assert not Path(raw["execution"]["output_root"]).exists()
    assert not Path(raw["execution"]["taskset_store"]).exists()
    assert _envelope()["payload"]["native_confirm_invocations_at_freeze"] == 0
