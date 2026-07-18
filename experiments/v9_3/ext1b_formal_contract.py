"""Fail-closed static verifier for the frozen EXT-1B B1 formal campaign.

This module never materializes a taskset and never invokes the native
simulator.  The one-time request-plan/seed-ledger materialization used by the
freeze is intentionally separate from this verifier.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .cell_model import derive_seed, expand_cells
from .config import canonical_json, domain_hash
from .ext1b_config import ext1b_config_hash, load_ext1b_config, validate_ext1b_config
from .scheduler_registry import SCHEDULER_IDS, audited_scheduler_registry


BASE_COMMIT = "a3c723d5b8870f2b7871a1b72221740575db2495"
BASE_TREE = "4da2b3ef765ffa5ee58aa4bc2fbc1b99ff88f172"
SIMULATOR_SHA256 = "80d3da0ed4890a5cd08e786544f2a410d18a6ca478efc69bfa5fd043fe5f60cc"
FORMAL_DOMAIN = "EXT1B_B1_FORMAL_R1"
FORMAL_STATUS = "FROZEN_FOR_FORMAL_EXECUTION"
OUTPUT_ROOT = "/root/autodl-tmp/asap_block_v9_3_ext1b_b1_formal_r1"
TASKSET_STORE = "/root/autodl-tmp/asap_block_v9_3_ext1b_b1_formal_r1_tasksets"
TASKSETS_PER_CELL = 200
TASKSET_INDEX_START = 10000
TASKSET_INDEX_END = 10199
STRUCTURAL_RETRY_LIMIT = 16
RETRY_SOURCE_INDEX_START = 160000
RETRY_SOURCE_INDEX_END = 163199
PAIRED_TASKSET_COUNT = 1200
REQUEST_COUNT = 10800
BOOTSTRAP_RESAMPLES = 10000

# Replaced with immutable values after the artifacts are materialized.
EXPECTED_CONFIG_SEMANTIC_HASH = "f0e8b266e7016dfa429ecc75746005d8fb4d186f41b7f5d703a1981c735293e3"
EXPECTED_CONTRACT_SHA256 = "6801f3184fcfb47169b2aef5176ff9345b0b10e73f71d2569445a5f1eaac4397"
EXPECTED_REQUEST_PLAN_SHA256 = "0208022c75a5314f9e17ae4807cfd669bfb9fc9ebfca3b23efe06ee1ada1b7c4"
EXPECTED_SEED_LEDGER_SHA256 = "02c70e8f533998a3161d69693219132d55af62c42caa34c89cfec73ab5ff5661"


@dataclass(frozen=True)
class FormalCell:
    ordinal: int
    stratum: str
    cell_id: str
    utilization: str
    eta: str
    rho: str
    base_seed: int
    bootstrap_seed: int


FORMAL_CELLS = (
    FormalCell(0, "LOW", "u2of5_eta4of5_rho3of4", "2/5", "4/5", "3/4", 971812602, 1605045777),
    FormalCell(1, "LOW", "u3of5_eta1_rho3of4", "3/5", "1", "3/4", 679148436, 927139569),
    FormalCell(2, "MEDIUM", "u3of5_eta4of5_rho3of4", "3/5", "4/5", "3/4", 1371499604, 1537368511),
    FormalCell(3, "MEDIUM", "u3of5_eta4of5_rho1of4", "3/5", "4/5", "1/4", 1758024457, 665932110),
    FormalCell(4, "HIGH", "u4of5_eta4of5_rho3of4", "4/5", "4/5", "3/4", 546579808, 873693510),
    FormalCell(5, "HIGH", "u4of5_eta3of5_rho1of2", "4/5", "3/5", "1/2", 1129661266, 913691139),
)


class FormalContractError(RuntimeError):
    """Raised when any frozen B1 formal binding does not close exactly."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(canonical_json(payload).encode("utf-8"))


def seed_material(cell: FormalCell) -> Dict[str, str]:
    return {
        "scenario": "BYPASS_STRESS:B1",
        "utilization": cell.utilization,
        "eta": cell.eta,
        "rho": cell.rho,
    }


def bounded_seed(domain: str, material: Mapping[str, Any]) -> int:
    return int(domain_hash(domain, material)[:16], 16) % 2147483647


def bounded_bootstrap_seed(domain: str, material: Mapping[str, Any]) -> int:
    return int(domain_hash(f"{domain}:BOOTSTRAP", material)[:16], 16) % 2147483646 + 1


def shard_name(cell: FormalCell) -> str:
    return f"{cell.ordinal:02d}_{cell.cell_id}"


def formal_shard_config(anchor: Mapping[str, Any], cell: FormalCell) -> Dict[str, Any]:
    """Expand the single-cell campaign anchor into one frozen shard config."""

    raw = deepcopy(dict(anchor))
    raw.pop("analysis", None)
    raw["experiment_id"] = f"asap-block-v9.3-ext1b1-formal-r1-{cell.cell_id}"
    raw["grid"]["utilization_points"] = [cell.utilization]
    raw["grid"]["base_seed"] = cell.base_seed
    raw["scenario"]["nominal_energy_supply_ratios"] = [cell.eta]
    raw["scenario"]["interpolation_rho"] = cell.rho
    raw["statistics"]["bootstrap_seed"] = cell.bootstrap_seed
    raw["execution"]["output_root"] = f"{OUTPUT_ROOT}/shards/{shard_name(cell)}"
    raw["execution"]["taskset_store"] = f"{TASKSET_STORE}/shards/{shard_name(cell)}"
    return validate_ext1b_config(raw)


def generation_id_for_cell(anchor: Mapping[str, Any], cell: FormalCell) -> str:
    expanded = expand_cells(formal_shard_config(anchor, cell))
    if len(expanded) != 1:
        raise FormalContractError(f"formal cell did not expand exactly once: {cell.cell_id}")
    return expanded[0].generation_id


def _load_envelope(path: Path, hash_field: str) -> tuple[Dict[str, Any], Dict[str, Any], str]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FormalContractError(f"cannot read frozen artifact: {path}") from exc
    if not isinstance(envelope, dict) or set(envelope) != {
        "schema", "hash_algorithm", hash_field, "payload",
    }:
        raise FormalContractError(f"invalid frozen envelope shape: {path}")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise FormalContractError(f"frozen payload is not a mapping: {path}")
    observed = canonical_payload_hash(payload)
    if envelope.get(hash_field) != observed:
        raise FormalContractError(f"frozen payload hash mismatch: {path}")
    return envelope, payload, observed


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FormalContractError(message)


def _verify_anchor(anchor: Mapping[str, Any], template: Mapping[str, Any]) -> None:
    _require(template["parameter_status"] == "UNFROZEN_FORMAL_TEMPLATE", "formal template was promoted")
    _require(anchor["parameter_status"] == FORMAL_STATUS, "formal status mismatch")
    _require(anchor["seed_space"] == "EXT1B_FORMAL", "formal seed space mismatch")
    _require(anchor["experiment_id"] == "asap-block-v9.3-ext1b1-formal-r1", "formal experiment identity mismatch")
    _require(anchor["scenario"]["kind"] == "BYPASS_STRESS", "formal scenario mismatch")
    _require(anchor["scenario"]["subtype"] == "B1", "formal subtype mismatch")
    _require(anchor["scenario"]["structural_retry_limit"] == STRUCTURAL_RETRY_LIMIT, "formal retry limit mismatch")
    _require(anchor["grid"]["tasksets_per_cell"] == TASKSETS_PER_CELL, "formal taskset count mismatch")
    _require(anchor["grid"]["taskset_index_start"] == TASKSET_INDEX_START, "formal taskset index mismatch")
    _require(anchor["simulation"]["horizon"] == 400, "formal horizon mismatch")
    _require(anchor["simulation"]["maximum_horizon"] == 400, "formal maximum horizon mismatch")
    _require(anchor["simulation"]["horizon_extension_policy"] == "none", "formal horizon policy mismatch")
    _require(anchor["simulation"]["retain_trace"] is True, "formal traces are not retained")
    _require(anchor["execution"]["worker_count"] == 1, "formal worker policy mismatch")
    _require(anchor["execution"]["output_root"] == OUTPUT_ROOT, "formal output root mismatch")
    _require(anchor["execution"]["taskset_store"] == TASKSET_STORE, "formal taskset store mismatch")
    _require(anchor["statistics"]["bootstrap_resamples"] == BOOTSTRAP_RESAMPLES, "formal bootstrap count mismatch")
    _require(ext1b_config_hash(anchor) == EXPECTED_CONFIG_SEMANTIC_HASH, "formal config semantic hash mismatch")


def _verify_cells(anchor: Mapping[str, Any], contract: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    contract_cells = contract.get("ordered_cells")
    plan_shards = plan.get("shards")
    _require(isinstance(contract_cells, list) and len(contract_cells) == 6, "contract cell list mismatch")
    _require(isinstance(plan_shards, list) and len(plan_shards) == 6, "plan shard list mismatch")
    for cell, frozen, shard in zip(FORMAL_CELLS, contract_cells, plan_shards):
        for key, value in asdict(cell).items():
            _require(frozen.get(key) == value, f"contract cell field mismatch: {cell.cell_id}/{key}")
            _require(shard.get(key) == value, f"plan shard field mismatch: {cell.cell_id}/{key}")
        material = seed_material(cell)
        _require(bounded_seed(FORMAL_DOMAIN, material) == cell.base_seed, f"base seed mismatch: {cell.cell_id}")
        _require(bounded_bootstrap_seed(FORMAL_DOMAIN, material) == cell.bootstrap_seed, f"bootstrap seed mismatch: {cell.cell_id}")
        config = formal_shard_config(anchor, cell)
        _require(shard.get("semantic_config_hash") == ext1b_config_hash(config), f"shard config hash mismatch: {cell.cell_id}")
        _require(shard.get("generation_id") == generation_id_for_cell(anchor, cell), f"generation ID mismatch: {cell.cell_id}")


def _verify_seed_ledger(anchor: Mapping[str, Any], ledger: Mapping[str, Any]) -> None:
    entries = ledger.get("generation_preimages")
    _require(isinstance(entries, list) and len(entries) == 19200, "seed ledger cardinality mismatch")
    expected_cells = {cell.cell_id: cell for cell in FORMAL_CELLS}
    generation_ids = {
        cell.cell_id: generation_id_for_cell(anchor, cell) for cell in FORMAL_CELLS
    }
    preimage_ids: set[str] = set()
    seeds: list[int] = []
    per_cell = Counter()
    source_indexes: list[int] = []
    for row in entries:
        cell = expected_cells.get(str(row.get("cell_id")))
        _require(cell is not None, "unknown cell in seed ledger")
        logical = int(row.get("logical_taskset_index"))
        attempt = int(row.get("attempt_index"))
        source = int(row.get("source_taskset_index"))
        _require(TASKSET_INDEX_START <= logical <= TASKSET_INDEX_END, "logical taskset index escaped formal domain")
        _require(0 <= attempt < STRUCTURAL_RETRY_LIMIT, "attempt index escaped formal domain")
        _require(source == logical * STRUCTURAL_RETRY_LIMIT + attempt, "retry source preimage mismatch")
        generation_id = generation_ids[cell.cell_id]
        _require(row.get("generation_id") == generation_id, "seed ledger generation ID mismatch")
        preimage = {"base_seed": cell.base_seed, "generation_id": generation_id, "taskset_index": source}
        preimage_id = domain_hash("ASAP_BLOCK:V9.3:TASKSET_SEED_PREIMAGE:v1", preimage)
        seed = derive_seed(cell.base_seed, generation_id, source)
        _require(row.get("seed_preimage") == preimage, "seed preimage payload mismatch")
        _require(row.get("seed_preimage_sha256") == preimage_id, "seed preimage hash mismatch")
        _require(row.get("derived_seed") == seed, "derived seed mismatch")
        _require(preimage_id not in preimage_ids, "duplicate formal generation preimage")
        preimage_ids.add(preimage_id)
        seeds.append(seed)
        source_indexes.append(source)
        per_cell[cell.cell_id] += 1
    _require(all(per_cell[cell.cell_id] == 3200 for cell in FORMAL_CELLS), "per-cell seed cardinality mismatch")
    _require(min(source_indexes) == RETRY_SOURCE_INDEX_START and max(source_indexes) == RETRY_SOURCE_INDEX_END, "retry source range mismatch")
    _require(len(seeds) == len(set(seeds)), "formal 31-bit seed collision")
    registered = ledger.get("registered_derived_seeds")
    _require(isinstance(registered, list) and all(isinstance(value, int) for value in registered), "registered seed inventory mismatch")
    _require(not set(seeds).intersection(registered), "formal seed collides with a registered domain")
    audit = ledger.get("collision_audit")
    _require(isinstance(audit, dict), "collision audit missing")
    _require(audit.get("formal_internal_collision_count") == 0, "formal internal collision audit failed")
    _require(audit.get("formal_vs_registered_collision_count") == 0, "registered collision audit failed")
    _require(audit.get("formal_preimage_count") == 19200, "collision audit preimage count mismatch")
    _require(audit.get("formal_unique_seed_count") == 19200, "collision audit seed count mismatch")


def _verify_plan(plan: Mapping[str, Any]) -> None:
    pairs = plan.get("paired_tasksets")
    requests = plan.get("requests")
    _require(isinstance(pairs, list) and len(pairs) == PAIRED_TASKSET_COUNT, "paired-taskset plan cardinality mismatch")
    _require(isinstance(requests, list) and len(requests) == REQUEST_COUNT, "request plan cardinality mismatch")
    pair_ids = [str(row.get("paired_taskset_identity")) for row in pairs]
    _require(len(pair_ids) == len(set(pair_ids)), "duplicate paired-taskset identity")
    per_cell_pairs = Counter(str(row.get("cell_id")) for row in pairs)
    _require(all(per_cell_pairs[cell.cell_id] == 200 for cell in FORMAL_CELLS), "per-cell pair count mismatch")
    per_stratum_pairs = Counter(str(row.get("stratum")) for row in pairs)
    _require(per_stratum_pairs == Counter({"LOW": 400, "MEDIUM": 400, "HIGH": 400}), "stratum pair count mismatch")
    pair_set = set(pair_ids)
    grouped: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    request_ids: set[str] = set()
    for row in requests:
        pair_id = str(row.get("paired_taskset_identity"))
        scheduler = str(row.get("scheduler_id"))
        request_id = str(row.get("request_identity"))
        _require(pair_id in pair_set, "request references an unknown pair")
        _require(scheduler in SCHEDULER_IDS, "request references an unknown scheduler")
        expected = domain_hash(
            "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_REQUEST:v1",
            {"paired_instance_id": pair_id, "scheduler_id": scheduler},
        )
        _require(request_id == expected, "request identity mismatch")
        _require(request_id not in request_ids, "duplicate request identity")
        request_ids.add(request_id)
        grouped[pair_id].append(row)
    _require(set(grouped) == pair_set, "request plan does not cover every pair")
    for pair_id, members in grouped.items():
        ordered = [str(row["scheduler_id"]) for row in members]
        _require(ordered == list(SCHEDULER_IDS), f"scheduler plan mismatch: {pair_id}")


def verify_formal_contract(project_root: Path | None = None) -> Dict[str, Any]:
    """Validate the complete frozen campaign without generating or executing it."""

    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    config_path = root / "configs/v9_3_ext1b1_formal_r1.yaml"
    template_path = root / "configs/v9_3_ext1b_formal_template.yaml"
    contract_path = root / "docs/experiments/v9_3_ext1b1_formal_r1_contract.json"
    plan_path = root / "docs/experiments/v9_3_ext1b1_formal_r1_request_plan.json"
    ledger_path = root / "docs/experiments/v9_3_ext1b1_formal_r1_seed_ledger.json"

    anchor = load_ext1b_config(config_path)
    template = load_ext1b_config(template_path)
    _verify_anchor(anchor, template)
    audited_scheduler_registry(root)

    _, contract, contract_hash = _load_envelope(contract_path, "contract_sha256")
    _, plan, plan_hash = _load_envelope(plan_path, "request_plan_sha256")
    _, ledger, ledger_hash = _load_envelope(ledger_path, "seed_ledger_sha256")
    _require(EXPECTED_CONTRACT_SHA256 != "TO_BE_FROZEN", "verifier hashes are not frozen")
    _require(contract_hash == EXPECTED_CONTRACT_SHA256, "contract hash is not the frozen hash")
    _require(plan_hash == EXPECTED_REQUEST_PLAN_SHA256, "request plan hash is not the frozen hash")
    _require(ledger_hash == EXPECTED_SEED_LEDGER_SHA256, "seed ledger hash is not the frozen hash")

    _require(contract.get("base") == {"commit": BASE_COMMIT, "tree": BASE_TREE}, "base identity mismatch")
    _require(contract.get("simulator", {}).get("sha256") == SIMULATOR_SHA256, "simulator hash mismatch")
    tracked = contract.get("tracked_config", {})
    _require(tracked.get("path") == "configs/v9_3_ext1b1_formal_r1.yaml", "tracked config path mismatch")
    _require(tracked.get("file_sha256") == sha256_file(config_path), "tracked config file hash mismatch")
    _require(tracked.get("semantic_hash") == EXPECTED_CONFIG_SEMANTIC_HASH, "tracked config semantic hash mismatch")
    _require(contract.get("request_plan", {}).get("sha256") == plan_hash, "contract/plan hash mismatch")
    _require(contract.get("seed_ledger", {}).get("sha256") == ledger_hash, "contract/ledger hash mismatch")
    for payload, label in ((plan, "plan"), (ledger, "ledger")):
        _require(payload.get("config_semantic_hash") == EXPECTED_CONFIG_SEMANTIC_HASH, f"config/{label} hash mismatch")
        _require(payload.get("base") == {"commit": BASE_COMMIT, "tree": BASE_TREE}, f"base/{label} mismatch")
        _require(payload.get("formal_domain") == FORMAL_DOMAIN, f"formal domain/{label} mismatch")

    _verify_cells(anchor, contract, plan)
    _verify_seed_ledger(anchor, ledger)
    _verify_plan(plan)
    _require(contract.get("scale", {}).get("paired_tasksets") == PAIRED_TASKSET_COUNT, "contract pair scale mismatch")
    _require(contract.get("scale", {}).get("requests") == REQUEST_COUNT, "contract request scale mismatch")
    _require(contract.get("scheduler_registry") == list(SCHEDULER_IDS), "contract scheduler registry mismatch")

    return {
        "status": "B1_FORMAL_CONTRACT_STATICALLY_VERIFIED",
        "config_semantic_hash": EXPECTED_CONFIG_SEMANTIC_HASH,
        "contract_sha256": contract_hash,
        "request_plan_sha256": plan_hash,
        "seed_ledger_sha256": ledger_hash,
        "cells": 6,
        "paired_tasksets": PAIRED_TASKSET_COUNT,
        "requests": REQUEST_COUNT,
        "generation_preimages": 19200,
        "native_simulation_invocations": 0,
        "formal_run_status": "FORMAL_NOT_RUN",
    }
