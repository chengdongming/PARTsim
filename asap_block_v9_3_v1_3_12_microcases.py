#!/usr/bin/env python3
"""Build deterministic v9.3 microcases in a complete v1.3.12 result package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
from asap_block_v1_3_12_schema_binding import DEFAULT_CONTRACT_ROOT, V1312SchemaBinding
from asap_block_v9_3_runner import (
    VARIANT_ORDER,
    SerializedAnalysis,
    run_five_configurations_v9_3,
    serialize_taskset_analysis_v1_3_12,
)


THEORY_SHA256 = "524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e"
PACKAGE_VERSION = "MICROCASE_V1"
PHASE = "DIAGNOSTIC"
FIXED_EVENT_TIME = "2026-07-13T00:00:00Z"
MICROCASE_TASKS = (
    ("0", 1, 5, 10, Fraction(1)),
    ("1", 1, 7, 12, Fraction(1)),
)
MICROCASE_C_TASKS = MICROCASE_TASKS + (("2", 1, 9, 15, Fraction(1)),)
CORE0A_VALIDATOR_FILES = (
    "core0a_v9_3_independent_aggregator.py",
    "core0a_v9_3_second_rebuild_verifier.py",
    "core0a_v9_3_evidence_schema.py",
    "core0a_v9_3_oracles.py",
    "core0a_v9_3_package_validator.py",
)


def _dump_yaml(path: Path, value: Any) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _hash(common: Any, domain: str, value: Any) -> str:
    return common.domain_hash(domain, value)


def _make_child_contracts(root: Path, binding: V1312SchemaBinding) -> Dict[str, str]:
    common = binding.common
    specs = (
        (
            "generator_contract.yaml",
            "ASAP_BLOCK_generator_contract_template_v1_3_12.yaml",
            "generator_contract_hash",
            "ASAP_BLOCK:GENERATOR_CONTRACT:v1.3.12",
        ),
        (
            "simulation_contract.yaml",
            "ASAP_BLOCK_simulation_contract_template_v1_3_12.yaml",
            "simulation_contract_hash",
            "ASAP_BLOCK:SIMULATION_CONTRACT:v1.3.12",
        ),
        (
            "trace_generator_contract.yaml",
            "ASAP_BLOCK_trace_generator_contract_template_v1_3_12.yaml",
            "trace_generator_contract_hash",
            "ASAP_BLOCK:TRACE_GENERATOR_CONTRACT:v1.3.12",
        ),
    )
    result: Dict[str, str] = {}
    for output, template, hash_field, domain in specs:
        obj = common.load_yaml_strict(root / template)
        if output == "generator_contract.yaml":
            obj["generator_parameters"].update(
                task_util_min="1/100",
                task_util_max="1",
                utilization_tolerance="0",
                period_distribution="UNIFORM_INTEGER",
                period_min=1,
                period_max=100,
                deadline_generation_rule="FROZEN_MICROCASE_CONSTANTS",
                deadline_delta_main="1",
                power_latent_distribution="FROZEN_MICROCASE_CONSTANTS",
                power_latent_mapping_version="MICROCASE_POWER_V1",
                max_resampling_attempts=1,
                generation_failure_threshold="1",
                rho_e_parameterization_rule="EXACT_MICROCASE_INPUT",
            )
        elif output == "simulation_contract.yaml":
            obj["scheduler_and_model"].update(
                scheduler_semantics_version="ASAP_BLOCK_MICROCASE_V1",
                event_order_version="MICROCASE_EVENT_ORDER_V1",
                energy_account_semantics_version="MICROCASE_ENERGY_ACCOUNT_V1",
                simulation_energy_account_mode="ANALYSIS_CONSISTENT_ACCOUNT",
                initial_energy="0",
                battery_mode="UNBOUNDED",
                battery_capacity=None,
            )
            obj["horizons_and_scenarios"].update(
                generation_horizon=1,
                observation_horizon=1,
                release_scenarios=["MICROCASE_RELEASE"],
                harvest_scenarios=["MICROCASE_HARVEST"],
                scenario_requests_per_taskset=1,
            )
            obj["execution_contract"]["unmodeled_overhead_policy"] = "ZERO"
        else:
            obj["release_trace_generator"].update(
                offset_distribution="FROZEN_ZERO",
                sporadic_gap_distribution="FROZEN_PERIODIC",
                trace_count_per_scenario=1,
                generator_version="MICROCASE_TRACE_V1",
            )
            obj["harvest_trace_generator"].update(
                method="FROZEN_ZERO",
                trace_count_per_scenario=1,
                generator_version="MICROCASE_TRACE_V1",
                service_curve_validation_domain="EXACT_INTEGER_TICKS",
            )
            obj["adversarial_search"].update(
                algorithm="DISABLED_FOR_MICROCASE",
                budget=1,
                stop_condition="SINGLE_FIXED_CASE",
            )
        obj["contract_metadata"][hash_field] = common.canonical_object_self_hash(
            obj, "contract_metadata." + hash_field, domain
        )
        _dump_yaml(root / output, obj)
        result[hash_field] = obj["contract_metadata"][hash_field]
    return result


def _fill_formal_contract(
    root: Path,
    binding: V1312SchemaBinding,
    child_hashes: Mapping[str, str],
    core0a_build_identity: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    common = binding.common
    formal = common.load_yaml_strict(
        root / "ASAP_BLOCK_formal_contract_template_v1_3_12.yaml"
    )
    formal["contract_metadata"].update(
        name="ASAP_BLOCK_formal_contract",
        status="FROZEN",
        formal_contract_version=PACKAGE_VERSION,
    )
    formal["child_contracts"].update(
        generator_contract_hash=child_hashes["generator_contract_hash"],
        generator_template_sha256=common.sha256_file(
            root / "ASAP_BLOCK_generator_contract_template_v1_3_12.yaml"
        ),
        simulation_contract_hash=child_hashes["simulation_contract_hash"],
        simulation_template_sha256=common.sha256_file(
            root / "ASAP_BLOCK_simulation_contract_template_v1_3_12.yaml"
        ),
        trace_generator_contract_hash=child_hashes["trace_generator_contract_hash"],
        trace_template_sha256=common.sha256_file(
            root / "ASAP_BLOCK_trace_generator_contract_template_v1_3_12.yaml"
        ),
    )
    public_seed = 931312
    seed_commitment = _hash(
        common, "ASAP_BLOCK:MICROCASE_SEED_COMMITMENT:v1.3.12", public_seed
    )
    build_requirement = _hash(
        common, "ASAP_BLOCK:MICROCASE_BUILD_REQUIREMENT:v1.3.12", PACKAGE_VERSION
    )
    formal["pre_core0a_commitments"].update(
        energy_numeric_mode="EXACT_RATIONAL",
        formal_master_seed_source="PUBLIC_CONSTANT",
        formal_master_seed_source_commitment_hash=seed_commitment,
        candidate_build_identity_requirement_hash=build_requirement,
    )
    formal["numeric_contract"].update(
        energy_numeric_mode="EXACT_RATIONAL",
        service_curve_integerization_mode="EXACT",
        energy_numeric_scale=None,
        numeric_integer_type="ARBITRARY_PRECISION_INTEGER",
        demand_rounding="EXACT",
        supply_rounding="EXACT",
        rho_e_tolerance="0",
        e0_rounding_tolerance="0",
        numeric_range_proof_id="MICROCASE_EXACT_RATIONAL",
    )
    cells = []
    for name, e0, count in (("A", 100, 2), ("B", 1, 2), ("C", 1, 3), ("D", 0, 0)):
        parameters = {"microcase": name, "E0": e0, "task_count": count, "M": 1}
        cells.append(
            {
                "parameter_cell_id": _hash(
                    common, "ASAP_BLOCK:PARAMETER_CELL:v1.3.12", parameters
                ),
                "parameters": parameters,
            }
        )
    cells.sort(key=lambda item: item["parameter_cell_id"])
    grid = {"cells": cells}
    formal["formal_grid_contract"]["formal_grid"] = grid
    formal["formal_grid_contract"]["formal_grid_hash"] = _hash(
        common, "ASAP_BLOCK:FORMAL_GRID:v1.3.12", grid
    )
    formal["pairing_contract"].update(
        paired_family_definition="NONE_FOR_DIAGNOSTIC_MICROCASE",
        base_generation_cell_definition="FROZEN_MICROCASE_CONSTANTS",
        transformation_contract_hash=_hash(
            common, "ASAP_BLOCK:MICROCASE_TRANSFORMATION:v1.3.12", "NONE"
        ),
    )
    formal["sample_request_contract"].update(
        pilot_generation_requests_per_cell=1,
        formal_generation_requests_per_cell=1,
    )
    formal["seed_contract"].update(
        formal_master_seed=public_seed,
        formal_master_seed_source="PUBLIC_CONSTANT",
        formal_master_seed_source_commitment_hash=seed_commitment,
    )
    formal["statistics_contract"].update(
        operational_comparison="NOT_RUN_DIAGNOSTIC",
        analytical_comparison="EXACT_MICROCASE_STATES",
        tightness_weighting="NOT_APPLICABLE_DIAGNOSTIC",
        multiple_testing_policy="NOT_APPLICABLE_DIAGNOSTIC",
        sample_size_power_target="NOT_APPLICABLE_DIAGNOSTIC",
    )
    formal["runtime_environment_contract"].update(
        thread_count=1,
        cpu_affinity="UNPINNED_DIAGNOSTIC",
        cpu_governor="NOT_RECORDED_DIAGNOSTIC",
        turbo_policy="NOT_RECORDED_DIAGNOSTIC",
        warmup_runs=0,
        measurement_repetitions=1,
        run_order_randomization="DISABLED",
        cache_policy="UNCONTROLLED_DIAGNOSTIC",
        rss_measurement_method="NOT_MEASURED",
    )
    builds = {}
    for name in (
        "generator",
        "trace_generator",
        "rta",
        "simulator",
        "scheduler",
        "audit",
    ):
        builds["approved_{}_build_identity_hash".format(name)] = _hash(
            common, "ASAP_BLOCK:MICROCASE_BUILD:{}:v1.3.12".format(name), PACKAGE_VERSION
        )
    if core0a_build_identity is not None:
        build_hash = core0a_build_identity["build_identity_hash"]
        for name in (
            "generator", "trace_generator", "rta", "simulator", "scheduler", "audit"
        ):
            builds["approved_{}_build_identity_hash".format(name)] = build_hash
    builds.update(
        approved_artifact_validator_sha256=common.sha256_file(
            root / "ASAP_BLOCK_artifact_validator_v1_3_12.py"
        ),
        approved_result_validator_sha256=common.sha256_file(
            root / "ASAP_BLOCK_result_validator_v1_3_12.py"
        ),
        approved_acceptance_validator_sha256=common.sha256_file(
            root / "ASAP_BLOCK_acceptance_report_validator_v1_3_12.py"
        ),
        approved_validation_common_sha256=common.sha256_file(
            root / "ASAP_BLOCK_validation_common_v1_3_12.py"
        ),
    )
    formal["approved_builds"].update(builds)
    gate_validator = {
        "validator_file": "ASAP_BLOCK_acceptance_report_validator_v1_3_12.py",
        "validator_version": "1.3.12",
        "validator_sha256": builds["approved_acceptance_validator_sha256"],
    }
    for section in ("CORE0A_gates", "CORE0B_gates"):
        for gate in formal["gate_validator_bindings"][section].values():
            gate.update(gate_validator)
    if core0a_build_identity is not None:
        independent_validator = {
            "validator_file": "core0a_v9_3_second_rebuild_verifier.py",
            "validator_version": "CORE0A-SECOND-REBUILD-INDEPENDENT-3.0",
            "validator_sha256": common.sha256_file(
                root / "core0a_v9_3_second_rebuild_verifier.py"
            ),
        }
        for gate in formal["gate_validator_bindings"]["CORE0A_gates"].values():
            gate.update(independent_validator)
        for filename in CORE0A_VALIDATOR_FILES:
            if filename not in formal["output_contract"]["required_files"]:
                formal["output_contract"]["required_files"].append(filename)
    plan_preimage = {
        "theory_contract": formal["theory_contract"],
        "pre_core0a_commitments": formal["pre_core0a_commitments"],
        "artifact_bindings": formal["artifact_bindings"],
    }
    formal["plan_context_contract"]["plan_context_hash"] = _hash(
        common, "ASAP_BLOCK:PLAN_CONTEXT:v1.3.12", plan_preimage
    )
    seed_preimage = {
        "plan_context_hash": formal["plan_context_contract"]["plan_context_hash"],
        "formal_grid_hash": formal["formal_grid_contract"]["formal_grid_hash"],
        "sample_request_contract": formal["sample_request_contract"],
        "formal_master_seed_source": "PUBLIC_CONSTANT",
        "formal_master_seed_source_commitment_hash": seed_commitment,
        "seed_derivation_algorithm": formal["seed_contract"]["seed_derivation_algorithm"],
    }
    formal["seed_contract"]["seed_derivation_context_hash"] = _hash(
        common, "ASAP_BLOCK:SEED_CONTEXT:v1.3.12", seed_preimage
    )
    return formal


def _seed_value(common: Any, formal: Mapping[str, Any], scope: str, replicate: int) -> int:
    preimage = {
        "seed_derivation_context_hash": formal["seed_contract"]["seed_derivation_context_hash"],
        "seed_scope_id": scope,
        "replicate_index": replicate,
        "formal_master_seed_or_revealed_beacon_value": formal["seed_contract"]["formal_master_seed"],
    }
    digest = hashlib.sha256(
        b"ASAP_BLOCK:DERIVED_SEED:v1.3.12\x00" + common.canonical_json_bytes(preimage)
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _plan_row(
    binding: V1312SchemaBinding,
    formal: Mapping[str, Any],
    request_type: str,
    cell_id: str,
    payload: Mapping[str, Any],
    label: str,
) -> Dict[str, Any]:
    common = binding.common
    row = binding.empty_row("run_plan_definition.csv")
    fields = binding.canonical["request_type_payload_fields"][request_type]
    if set(payload) != set(fields):
        raise ValueError("noncanonical payload for {}".format(request_type))
    payload_preimage = {
        field: binding._encode_value(
            payload[field], binding.fields("run_plan_definition.csv")[field]
        )
        for field in fields
    }
    payload_hash = _hash(
        common,
        "ASAP_BLOCK:REQUEST_PAYLOAD:{}:v1.3.12".format(request_type),
        payload_preimage,
    )
    request_preimage = {
        "plan_context_hash": formal["plan_context_contract"]["plan_context_hash"],
        "request_type": request_type,
        "run_phase": PHASE,
        "parameter_cell_id": cell_id,
        "replicate_index": 0,
        "request_payload_hash": payload_hash,
    }
    request_id = _hash(common, "ASAP_BLOCK:REQUEST:v1.3.12", request_preimage)
    output_type = binding.canonical["output_identity"]["mapping"][request_type][
        "output_type"
    ]
    output_preimage = {
        "request_id": request_id,
        "expected_output_type": output_type,
        "output_cardinality": "EXACTLY_ONE",
    }
    row.update(
        request_id=request_id,
        request_type=request_type,
        run_phase=PHASE,
        plan_context_hash=formal["plan_context_contract"]["plan_context_hash"],
        parameter_cell_id=cell_id,
        replicate_index=0,
        request_payload_hash=payload_hash,
        expected_output_id=_hash(
            common, "ASAP_BLOCK:EXPECTED_OUTPUT:v1.3.12", output_preimage
        ),
        expected_output_type=output_type,
        request_payload_schema_version="1.3.12",
        output_cardinality="EXACTLY_ONE",
        human_label=label,
    )
    row.update(payload)
    return row


def _case_input(
    binding: V1312SchemaBinding,
    formal: Mapping[str, Any],
    case_name: str,
    tasks: Sequence[Tuple[str, int, int, int, Fraction]],
    e0: int,
    taskset_hash: str,
    priority_hash: str,
    power_hash: str,
) -> taskset.TasksetAnalysisInput:
    common = binding.common
    context = taskset.DependencyContext(
        taskset_identity=taskset_hash,
        task_definitions_identity=_hash(
            common, "ASAP_BLOCK:MICROCASE_TASK_DEFINITIONS:v1.3.12", case_name
        ),
        priority_order_identity=priority_hash,
        e0_canonical_identity=_hash(common, "ASAP_BLOCK:E0:v1.3.12", str(e0)),
        service_curve_identity=_hash(
            common, "ASAP_BLOCK:SERVICE_CURVE:v1.3.12", "beta(t)=0"
        ),
        power_vector_identity=power_hash,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=THEORY_SHA256,
        fixed_carry_in_interface_sha256=THEORY_SHA256,
        formal_contract_identity=formal["contract_metadata"]["formal_contract_hash"],
    )
    return taskset.TasksetAnalysisInput(
        tuple(core.V93Task(*values) for values in tasks),
        1,
        Fraction(e0),
        lambda _index: Fraction(0),
        context,
    )


def _taskset_materialization(
    binding: V1312SchemaBinding,
    formal: Mapping[str, Any],
    case_name: str,
    tasks: Sequence[Tuple[str, int, int, int, Fraction]],
    e0: int,
    generation_plan: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any], taskset.TasksetAnalysisInput]:
    common = binding.common
    taskset_id = _hash(common, "ASAP_BLOCK:TASKSET_ID:v1.3.12", case_name)
    definitions: List[Dict[str, Any]] = []
    for rank, (name, c, d, t, power) in enumerate(tasks):
        row = binding.empty_row("task_definitions.csv")
        row.update(
            taskset_id=taskset_id,
            task_id=int(name),
            C_i=c,
            T_i=t,
            D_i=d,
            P_raw=str(power),
            P_analysis=str(power),
            priority_rank=rank,
            power_latent_value=str(power),
        )
        definitions.append(row)
    stub = {"M": 1, "n": len(tasks)}
    sem = [
        {
            "task_id": str(r["task_id"]),
            "C_i": str(r["C_i"]),
            "T_i": str(r["T_i"]),
            "D_i": str(r["D_i"]),
            "P_raw": str(r["P_raw"]),
            "P_analysis": str(r["P_analysis"]),
            "priority_rank": str(r["priority_rank"]),
        }
        for r in definitions
    ]
    priority = [
        {"task_id": str(r["task_id"]), "priority_rank": str(r["priority_rank"])}
        for r in definitions
    ]
    raw = [{"task_id": str(r["task_id"]), "P_raw": str(r["P_raw"])} for r in definitions]
    analysis = [
        {
            "task_id": str(r["task_id"]),
            "P_analysis": str(r["P_analysis"]),
            "P_analysis_scaled": None,
            "P_rounding_mode": None,
        }
        for r in definitions
    ]
    taskset_hash = _hash(
        common,
        "ASAP_BLOCK:TASKSET_SEMANTIC:v1.3.12",
        {"M": "1", "n": str(len(tasks)), "tasks": sem},
    )
    priority_hash = _hash(common, "ASAP_BLOCK:PRIORITY_RANK:v1.3.12", priority)
    raw_hash = _hash(common, "ASAP_BLOCK:POWER_VECTOR_RAW:v1.3.12", raw)
    analysis_hash = _hash(
        common, "ASAP_BLOCK:POWER_VECTOR_ANALYSIS:v1.3.12", analysis
    )
    generation_request_id = _hash(
        common, "ASAP_BLOCK:GENERATION_RESULT:v1.3.12", generation_plan["request_id"]
    )
    total_util = sum(Fraction(c, t) for _, c, _, t, _ in tasks)
    ts = binding.empty_row("tasksets.csv")
    ts.update(
        run_phase=PHASE,
        taskset_id=taskset_id,
        materialization_request_id=generation_plan["request_id"],
        source_generation_request_id=generation_request_id,
        parameter_cell_id=generation_plan["parameter_cell_id"],
        M=1,
        n=len(tasks),
        taskset_semantic_hash=taskset_hash,
        priority_rank_hash=priority_hash,
        power_vector_raw_hash=raw_hash,
        power_vector_analysis_hash=analysis_hash,
        build_input_hash=_hash(
            common, "ASAP_BLOCK:BUILD_INPUT:v1.3.12", taskset_hash
        ),
        plan_context_hash=formal["plan_context_contract"]["plan_context_hash"],
        actual_total_utilization=str(total_util),
        actual_rho_p=str(total_util),
        actual_rho_e_raw="0",
        actual_rho_e_analysis="0",
    )
    generation = binding.empty_row("generation_requests.csv")
    generation.update(
        run_phase=PHASE,
        generation_request_id=generation_request_id,
        request_id=generation_plan["request_id"],
        generator_contract_hash=formal["child_contracts"]["generator_contract_hash"],
        parameter_cell_id=generation_plan["parameter_cell_id"],
        base_generation_cell_id=generation_plan["base_generation_cell_id"],
        replicate_index=0,
        requested_seed=generation_plan["derived_seed"],
        seed_derivation_check_status="VALID",
        generation_status="SUCCESS",
        generation_attempts=1,
        max_resampling_reached=False,
        target_total_utilization=str(total_util),
        target_rho_p=str(total_util),
        target_rho_e="0",
        plan_context_hash=formal["plan_context_contract"]["plan_context_hash"],
        actual_total_utilization=str(total_util),
        actual_rho_p=str(total_util),
        actual_rho_e_analysis="0",
        taskset_id=taskset_id,
        taskset_semantic_hash=taskset_hash,
        formal_master_seed=str(formal["seed_contract"]["formal_master_seed"]),
        formal_master_seed_source="PUBLIC_CONSTANT",
        formal_master_seed_commitment_hash=formal["seed_contract"]["formal_master_seed_source_commitment_hash"],
        formal_seed_derivation_algorithm=formal["seed_contract"]["seed_derivation_algorithm"],
        seed_derivation_context_hash=formal["seed_contract"]["seed_derivation_context_hash"],
    )
    inp = _case_input(
        binding, formal, case_name, tasks, e0, taskset_hash, priority_hash, analysis_hash
    )
    return ts, definitions, generation, inp


def _analysis_base(
    binding: V1312SchemaBinding,
    formal: Mapping[str, Any],
    inp: taskset.TasksetAnalysisInput,
    plan: Mapping[str, Any],
    generation: Mapping[str, Any],
    ts: Mapping[str, Any],
) -> Dict[str, Any]:
    common = binding.common
    row = binding.empty_row("per_taskset_results.csv")
    total = generation["actual_total_utilization"]
    e0 = str(inp.e0)
    row.update(
        run_phase=PHASE,
        request_id=plan["request_id"],
        build_identity_hash=formal["approved_builds"]["approved_rta_build_identity_hash"],
        rta_implementation_hash=_hash(common, "ASAP_BLOCK:RTA_IMPLEMENTATION:v1.3.12", "v9.3"),
        generation_request_id=generation["generation_request_id"],
        taskset_id=ts["taskset_id"],
        taskset_materialization_request_id=ts["materialization_request_id"],
        generator_contract_hash=formal["child_contracts"]["generator_contract_hash"],
        experiment_config_version=PACKAGE_VERSION,
        experiment_config_hash=plan["analysis_config_hash"],
        M=inp.processors,
        n=len(inp.tasks),
        target_total_utilization=total,
        actual_total_utilization=total,
        target_rho_p=total,
        actual_rho_p=total,
        target_rho_e="0",
        actual_rho_e_raw="0",
        actual_rho_e_analysis="0",
        rho_e_tolerance="0",
        rho_e_tolerance_mode="EXACT",
        rho_e_parameterization_status="ACCEPTED",
        numeric_coverage_status="VALID",
        service_rate_reference="0",
        service_rate_r_raw="0",
        service_curve_integerization_mode="EXACT",
        power_scale_alpha="1",
        target_power_demand=str(len(inp.tasks)),
        actual_power_demand_raw=str(len(inp.tasks)),
        actual_power_demand_analysis=str(len(inp.tasks)),
        target_service_latency_ratio="0",
        realized_service_latency_L=0,
        realized_service_latency_ratio="0",
        power_latent_seed="0",
        power_latent_vector_hash=_hash(common, "ASAP_BLOCK:POWER_LATENT:v1.3.12", ts["taskset_id"]),
        power_latent_mapping_version="MICROCASE_POWER_V1",
        priority_reference_delta="DM",
        priority_rank_reference_hash=ts["priority_rank_hash"],
        E0_target_raw=e0,
        E0_analysis_effective=e0,
        E0_rounding_error="0",
        target_epsilon_0="0",
        realized_epsilon_0_analysis="0",
        e0_parameterization_policy="EXACT_GRID",
        e0_parameterization_status="ACCEPTED",
        theorem_conditioning_mode=(
            "UNCONDITIONAL_E0_ZERO" if inp.e0 == 0 else "CONDITIONAL_E0_POSITIVE"
        ),
        service_latency_L=0,
        service_curve_raw_spec="beta(t)=0",
        runtime_wall="0",
        runtime_cpu="0",
        rta_formula_version="v9.3",
        theory_document_sha256=THEORY_SHA256,
        fixed_carry_in_corollary_hash=THEORY_SHA256,
        taskset_semantic_hash=ts["taskset_semantic_hash"],
        priority_rank_hash=ts["priority_rank_hash"],
        power_vector_raw_hash=ts["power_vector_raw_hash"],
        analysis_E0_canonical_hash=_hash(common, "ASAP_BLOCK:E0:v1.3.12", e0),
        analysis_power_vector_canonical_hash=ts["power_vector_analysis_hash"],
        analysis_service_curve_canonical_hash=_hash(
            common, "ASAP_BLOCK:SERVICE_CURVE:v1.3.12", "beta(t)=0"
        ),
        energy_numeric_mode="EXACT_RATIONAL",
        energy_demand_rounding="EXACT",
        energy_supply_rounding="EXACT",
        numeric_integer_type="ARBITRARY_PRECISION_INTEGER",
        numeric_range_check_status="VALID",
        service_curve_raw_hash=_hash(common, "ASAP_BLOCK:SERVICE_CURVE_RAW:v1.3.12", "beta(t)=0"),
        plan_context_hash=formal["plan_context_contract"]["plan_context_hash"],
        analysis_energy_unit_hash=_hash(common, "ASAP_BLOCK:ENERGY_UNIT:v1.3.12", "MICROCASE_EXACT"),
    )
    return row


def _make_acceptance(
    root: Path,
    binding: V1312SchemaBinding,
    formal: Mapping[str, Any],
    core0a_result: Optional[Mapping[str, Any]] = None,
    core0a_inputs: Sequence[str] = (),
) -> None:
    common = binding.common
    report = common.load_yaml_strict(
        root / "ASAP_BLOCK_acceptance_report_template_v1_3_12.yaml"
    )
    report["acceptance_report_metadata"].update(
        status="EXECUTED_DIAGNOSTIC_NOT_RELEASED",
        plan_context_hash=formal["plan_context_contract"]["plan_context_hash"],
        formal_contract_hash=formal["contract_metadata"]["formal_contract_hash"],
        report_phase="CORE0B_FINAL",
    )
    acceptance_hash = common.sha256_file(
        root / "ASAP_BLOCK_acceptance_report_validator_v1_3_12.py"
    )
    report["validator_identity"].update(
        acceptance_validator_sha256=acceptance_hash,
        validator_build_identity_hash=_hash(
            common, "ASAP_BLOCK:ACCEPTANCE_VALIDATOR_BUILD:v1.3.12", acceptance_hash
        ),
    )
    observed = report["approved_builds_observed"]
    for prefix in ("generator", "trace_generator", "rta", "simulator", "scheduler", "audit"):
        observed[prefix + "_build_identity_hash"] = formal["approved_builds"][
            "approved_{}_build_identity_hash".format(prefix)
        ]
    if core0a_result is not None:
        if core0a_result.get("status") != "PASSED":
            raise ValueError("independent CORE0A aggregation is not PASSED")
        binding_record = formal["gate_validator_bindings"]["CORE0A_gates"]
        input_hashes = [common.sha256_file(root / name) for name in core0a_inputs]
        for gate_id, result_gate in core0a_result["gates"].items():
            record = report["CORE0A_gates"][gate_id]
            validator = binding_record[gate_id]
            bundle_name = "core0a_gate_{}.json".format(gate_id)
            bundle = {
                "evidence_bundle_metadata": {
                    "version": "1.3.12",
                    "gate_section": "CORE0A_gates",
                    "gate_id": gate_id,
                    "plan_context_hash": formal["plan_context_contract"]["plan_context_hash"],
                    "formal_contract_hash": formal["contract_metadata"]["formal_contract_hash"],
                    "validator_file": validator["validator_file"],
                    "validator_version": validator["validator_version"],
                    "validator_sha256": validator["validator_sha256"],
                    "replay_interface": "ASAP_BLOCK_GATE_REPLAY_V1",
                    "evidence_bundle_hash": None,
                },
                "predicate": record["predicate"],
                "counts": result_gate["counts"],
                "input_files": list(core0a_inputs),
                "input_sha256": input_hashes,
                "status": result_gate["status"],
            }
            bundle["evidence_bundle_metadata"]["evidence_bundle_hash"] = (
                common.canonical_object_self_hash(
                    bundle,
                    "evidence_bundle_metadata.evidence_bundle_hash",
                    "ASAP_BLOCK:GATE_EVIDENCE_BUNDLE:v1.3.12",
                )
            )
            (root / bundle_name).write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            bundle_hash = common.sha256_file(root / bundle_name)
            record.update(
                status=result_gate["status"],
                counts=result_gate["counts"],
                evidence_bundle_file=bundle_name,
                evidence_bundle_sha256=bundle_hash,
                evidence_files=[bundle_name] + list(core0a_inputs),
                evidence_sha256=[bundle_hash] + input_hashes,
                validator_name=validator["validator_file"],
                validator_version=validator["validator_version"],
                validator_sha256=validator["validator_sha256"],
                notes="independently aggregated from row-level CORE0A evidence",
            )
    failed = []
    for section in ("CORE0A_gates", "CORE0B_gates"):
        failed.extend(
            gate_id
            for gate_id, gate in report[section].items()
            if gate.get("required", True) and gate.get("status") != "PASSED"
        )
    report["overall_release_gate"] = {
        "status": "FAILED",
        "failed_gate_ids": sorted(failed),
        "release_authorized": False,
    }
    report["acceptance_report_metadata"]["acceptance_report_hash"] = (
        common.canonical_object_self_hash(
            report,
            "acceptance_report_metadata.acceptance_report_hash",
            "ASAP_BLOCK:ACCEPTANCE_REPORT:v1.3.12",
        )
    )
    _dump_yaml(root / "acceptance_report.yaml", report)


def _install_core0a_evidence(
    root: Path,
    evidence_root: Path,
    build_identity: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    """Copy bound raw evidence and independently recompute all gate counts."""

    evidence_root = Path(evidence_root)
    raw_manifest = json.loads(
        (evidence_root / "raw_evidence_manifest.json").read_text(encoding="utf-8")
    )
    if raw_manifest.get("build_identity_hash") != build_identity["build_identity_hash"]:
        raise ValueError("raw evidence build identity differs from package identity")
    raw_names = tuple(sorted(raw_manifest["files"]))
    support_names = (
        raw_manifest["build_identity_file"],
        raw_manifest["finite_state_domain_file"],
        "raw_evidence_manifest.json",
    )
    for name in raw_names + support_names:
        if Path(name).name != name:
            raise ValueError("CORE0A evidence requires root basenames")
        shutil.copyfile(evidence_root / name, root / name)
    copied_identity = json.loads((root / "build_identity.json").read_text(encoding="utf-8"))
    if copied_identity != build_identity:
        raise ValueError("copied build identity is not byte-semantic equal")
    replay_name = "gate_replay_counts.json"
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "core0a_v9_3_second_rebuild_verifier.py",
            "--evidence-root",
            ".",
            "--template",
            "ASAP_BLOCK_acceptance_report_template_v1_3_12.yaml",
            "--output",
            replay_name,
        ],
        cwd=root,
        env={
            "PATH": __import__("os").environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if process.returncode:
        raise ValueError(
            "independent CORE0A aggregation failed: {}".format(process.stdout[-1000:])
        )
    result = json.loads((root / replay_name).read_text(encoding="utf-8"))
    inputs = tuple(
        sorted(
            set(raw_names + support_names + (replay_name,))
            | {"core0a_v9_3_evidence_schema.py", "core0a_v9_3_oracles.py"}
        )
    )
    return result, inputs


def _output_bundle(
    binding: V1312SchemaBinding,
    request_type: str,
    request_id: str,
    top: Mapping[str, str],
    rows: Mapping[str, List[Dict[str, str]]],
) -> Dict[str, Any]:
    common = binding.common
    output: Dict[str, Any] = {}

    def add(table: str, selected: Iterable[Mapping[str, str]]) -> None:
        selected = sorted(
            selected,
            key=lambda row: common.canonical_json_bytes(
                [row[column] for column in binding.primary_key(table)]
            ),
        )
        output[table] = [common.row_object(dict(row)) for row in selected]

    top_table = binding.canonical["output_identity"]["mapping"][request_type]["top_table"]
    add(top_table, [top])
    if request_type == "GENERATION":
        selected = [r for r in rows["tasksets.csv"] if r["materialization_request_id"] == request_id]
        add("tasksets.csv", selected)
        ids = {r["taskset_id"] for r in selected}
        add("task_definitions.csv", [r for r in rows["task_definitions.csv"] if r["taskset_id"] in ids])
    elif request_type == "ANALYSIS":
        aid = top["analysis_run_id"]
        add("per_task_results.csv", [r for r in rows["per_task_results.csv"] if r["analysis_run_id"] == aid])
        add("rta_dependency_records.csv", [r for r in rows["rta_dependency_records.csv"] if r["analysis_run_id"] == aid])
    return output


def _write_manifest(root: Path, binding: V1312SchemaBinding, formal: Mapping[str, Any]) -> None:
    common = binding.common
    required = set(formal["output_contract"]["required_files"])
    report = common.load_yaml_strict(root / "acceptance_report.yaml")
    evidence = {
        name
        for section in ("CORE0A_gates", "CORE0B_gates")
        for gate in report[section].values()
        for name in gate.get("evidence_files", [])
    }
    allowed = required | evidence
    manifest_files = sorted(allowed - {"manifest.json", "sha256sum.txt"})
    manifest = {
        "manifest_version": "1.3.12",
        "plan_context_hash": formal["plan_context_contract"]["plan_context_hash"],
        "formal_contract_hash": formal["contract_metadata"]["formal_contract_hash"],
        "schema_sha256": common.sha256_file(root / "ASAP_BLOCK_experiment_schema_v1_3_12.yaml"),
        "dictionary_sha256": common.sha256_file(root / "ASAP_BLOCK_data_dictionary_v1_3_12.yaml"),
        "canonical_sha256": common.sha256_file(root / "ASAP_BLOCK_canonical_serialization_v1_3_12.yaml"),
        "validation_common_sha256": common.sha256_file(root / "ASAP_BLOCK_validation_common_v1_3_12.py"),
        "artifact_validator_sha256": common.sha256_file(root / "ASAP_BLOCK_artifact_validator_v1_3_12.py"),
        "result_validator_sha256": common.sha256_file(root / "ASAP_BLOCK_result_validator_v1_3_12.py"),
        "acceptance_validator_sha256": common.sha256_file(root / "ASAP_BLOCK_acceptance_report_validator_v1_3_12.py"),
        "files": {name: common.sha256_file(root / name) for name in manifest_files},
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sums = []
    for name in sorted(allowed - {"sha256sum.txt"}):
        sums.append("{}  {}".format(common.sha256_file(root / name), name))
    (root / "sha256sum.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")


def build_microcase_package(
    output_root: Path,
    zip_path: Optional[Path] = None,
    core0a_evidence_root: Optional[Path] = None,
    build_identity_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create the complete package and return a deterministic summary."""

    output_root = Path(output_root)
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    binding = V1312SchemaBinding()
    common = binding.common
    core0a_build_identity = None
    if (core0a_evidence_root is None) != (build_identity_path is None):
        raise ValueError("CORE0A evidence root and build identity must be provided together")
    if build_identity_path is not None:
        core0a_build_identity = json.loads(
            Path(build_identity_path).read_text(encoding="utf-8")
        )
        if core0a_build_identity.get("git_status_clean") is not True:
            raise ValueError("formal CORE0A build identity must record a clean worktree")
    formal_template = common.load_yaml_strict(
        DEFAULT_CONTRACT_ROOT / "ASAP_BLOCK_formal_contract_template_v1_3_12.yaml"
    )
    for name in formal_template["output_contract"]["required_files"]:
        source = DEFAULT_CONTRACT_ROOT / name
        if source.exists():
            shutil.copyfile(source, output_root / name)
    if core0a_build_identity is not None:
        project_root = Path(__file__).resolve().parent
        for name in CORE0A_VALIDATOR_FILES:
            shutil.copyfile(project_root / name, output_root / name)
    child_hashes = _make_child_contracts(output_root, binding)
    formal = _fill_formal_contract(
        output_root, binding, child_hashes, core0a_build_identity
    )
    cells_by_name = {
        cell["parameters"]["microcase"]: cell["parameter_cell_id"]
        for cell in formal["formal_grid_contract"]["formal_grid"]["cells"]
    }

    plans: List[Dict[str, Any]] = []
    generation_plans: Dict[str, Dict[str, Any]] = {}
    analysis_plans: Dict[Tuple[str, str], Dict[str, Any]] = {}
    seed_rows = []
    variants = {
        "A": [variant.value for variant in VARIANT_ORDER],
        "B": ["CW-Theta^cw", "LOC-Theta^cw"],
        "C": ["CW-Theta^cw"],
    }
    for case_name in ("A", "B", "C", "D"):
        cell_id = cells_by_name[case_name]
        scope_preimage = {
            "request_type": "GENERATION",
            "parameter_cell_id": cell_id,
            "scenario_id_or_null": None,
            "stream_label_or_null": None,
            "stream_index_or_null": None,
        }
        scope = _hash(common, "ASAP_BLOCK:SEED_SCOPE:v1.3.12", scope_preimage)
        derived = _seed_value(common, formal, scope, 0)
        payload = {
            "base_generation_cell_id": cell_id,
            "seed_scope_id": scope,
            "derived_seed": derived,
            "generator_contract_hash": child_hashes["generator_contract_hash"],
        }
        plan = _plan_row(binding, formal, "GENERATION", cell_id, payload, "Microcase {} generation".format(case_name))
        plans.append(plan)
        generation_plans[case_name] = plan
        seed_rows.append({"seed_scope_id": scope, "replicate_index": 0, "derived_seed": derived})
        for variant in variants.get(case_name, ["CW-D"]):
            analysis_config_hash = _hash(
                common,
                "ASAP_BLOCK:ANALYSIS_CONFIG:v1.3.12",
                {"microcase": case_name, "variant": variant, "numeric_mode": "EXACT_RATIONAL"},
            )
            payload = {
                "taskset_request_id": plan["request_id"],
                "analysis_variant_or_scenario": variant,
                "analysis_config_hash": analysis_config_hash,
                "approved_rta_build_identity_hash": formal["approved_builds"]["approved_rta_build_identity_hash"],
            }
            ap = _plan_row(binding, formal, "ANALYSIS", cell_id, payload, "Microcase {} {}".format(case_name, variant))
            plans.append(ap)
            analysis_plans[(case_name, variant)] = ap
    dependencies = []
    for plan in plans:
        for relation in binding.canonical["request_dependency_contract"][plan["request_type"]]:
            dep = plan.get(relation["field"])
            if dep:
                row = binding.empty_row("run_plan_dependencies.csv")
                row.update(request_id=plan["request_id"], dependency_request_id=dep, dependency_role=relation["role"])
                dependencies.append(row)
    formal["seed_contract"]["formal_seed_set_hash"] = _hash(
        common,
        "ASAP_BLOCK:SEED_SET:v1.3.12",
        sorted(seed_rows, key=lambda row: (row["seed_scope_id"], row["replicate_index"], row["derived_seed"])),
    )
    empty = {name: [] for name in binding.table_names}
    empty["run_plan_definition.csv"] = plans
    empty["run_plan_dependencies.csv"] = dependencies
    binding.write_tables(output_root, empty)
    bundle_preimage = {
        "run_plan_definition_sha256": common.canonical_csv_sha256(output_root / "run_plan_definition.csv"),
        "run_plan_dependencies_sha256": common.canonical_csv_sha256(output_root / "run_plan_dependencies.csv"),
    }
    formal["run_plan_contract"]["run_plan_bundle_hash"] = _hash(
        common, "ASAP_BLOCK:RUN_PLAN_BUNDLE:v1.3.12", bundle_preimage
    )
    formal["contract_metadata"]["formal_contract_hash"] = common.canonical_object_self_hash(
        formal,
        "contract_metadata.formal_contract_hash",
        "ASAP_BLOCK:FORMAL_CONTRACT:v1.3.12",
    )
    _dump_yaml(output_root / "formal_contract.yaml", formal)

    rows: Dict[str, List[Dict[str, Any]]] = {name: [] for name in binding.table_names}
    rows["run_plan_definition.csv"] = plans
    rows["run_plan_dependencies.csv"] = dependencies
    serializations: Dict[Tuple[str, str], SerializedAnalysis] = {}
    for case_name, task_values, e0 in (
        ("A", MICROCASE_TASKS, 100),
        ("B", MICROCASE_TASKS, 1),
        ("C", MICROCASE_C_TASKS, 1),
    ):
        ts, defs, generation, inp = _taskset_materialization(
            binding, formal, case_name, task_values, e0, generation_plans[case_name]
        )
        rows["tasksets.csv"].append(ts)
        rows["task_definitions.csv"].extend(defs)
        rows["generation_requests.csv"].append(generation)
        def_map = {str(row["task_id"]): row for row in defs}
        ids = {
            variant: _hash(
                common,
                "ASAP_BLOCK:ANALYSIS_RUN:v1.3.12",
                analysis_plans[(case_name, variant.value)]["request_id"],
            )
            for variant in VARIANT_ORDER
            if (case_name, variant.value) in analysis_plans
        }
        if case_name == "A":
            results = run_five_configurations_v9_3(inp, ids).by_variant()
            selected = list(VARIANT_ORDER)
        elif case_name == "B":
            source_variant = taskset.AnalysisVariant.CW_THETA_CW
            source = taskset.analyze_taskset_v9_3(ids[source_variant], source_variant, inp)
            target_variant = taskset.AnalysisVariant.LOC_THETA_CW
            target = taskset.analyze_taskset_v9_3(
                ids[target_variant], target_variant, inp, source=source,
                dependency_check_status=taskset.DependencyVectorCheckStatus.INVALID,
            )
            results = {source_variant: source, target_variant: target}
            selected = [source_variant, target_variant]
        else:
            source_variant = taskset.AnalysisVariant.CW_THETA_CW
            results = {source_variant: taskset.analyze_taskset_v9_3(ids[source_variant], source_variant, inp)}
            selected = [source_variant]
        for variant in selected:
            result = results[variant]
            plan = analysis_plans[(case_name, variant.value)]
            source_serialized = serializations.get((case_name, "CW-Theta^cw")) if variant is taskset.AnalysisVariant.LOC_THETA_CW else None
            serialized = serialize_taskset_analysis_v1_3_12(
                result,
                binding,
                _analysis_base(binding, formal, inp, plan, generation, ts),
                def_map,
                source=source_serialized,
            )
            serializations[(case_name, variant.value)] = serialized
            rows["per_taskset_results.csv"].append(dict(serialized.taskset_row))
            rows["per_task_results.csv"].extend(dict(row) for row in serialized.task_rows)
            rows["rta_dependency_records.csv"].extend(dict(row) for row in serialized.dependency_rows)

    failed_plan = generation_plans["D"]
    failed_generation = binding.empty_row("generation_requests.csv")
    failed_generation.update(
        run_phase=PHASE,
        generation_request_id=_hash(common, "ASAP_BLOCK:GENERATION_RESULT:v1.3.12", failed_plan["request_id"]),
        request_id=failed_plan["request_id"],
        generator_contract_hash=child_hashes["generator_contract_hash"],
        parameter_cell_id=failed_plan["parameter_cell_id"],
        base_generation_cell_id=failed_plan["base_generation_cell_id"],
        replicate_index=0,
        requested_seed=failed_plan["derived_seed"],
        seed_derivation_check_status="VALID",
        generation_status="GENERATION_FAILURE",
        generation_attempts=1,
        max_resampling_reached=True,
        target_total_utilization="1",
        target_rho_p="1",
        target_rho_e="0",
        plan_context_hash=formal["plan_context_contract"]["plan_context_hash"],
        generation_failure_reason="FROZEN_DIAGNOSTIC_GENERATION_FAILURE",
    )
    rows["generation_requests.csv"].append(failed_generation)

    encoded_without_outputs = binding.validate_dataset(rows)
    top_by_type = {
        "GENERATION": "generation_requests.csv",
        "ANALYSIS": "per_taskset_results.csv",
    }
    for plan in plans:
        failed_dependency = plan["request_type"] == "ANALYSIS" and plan["taskset_request_id"] == failed_plan["request_id"]
        events = ["STARTED", "NOT_RUN_DEPENDENCY" if failed_dependency else "FINISHED"]
        output_row = None
        output_hash = None
        if not failed_dependency:
            top_table = top_by_type[plan["request_type"]]
            top = next(row for row in encoded_without_outputs[top_table] if row["request_id"] == plan["request_id"])
            bundle = _output_bundle(binding, plan["request_type"], plan["request_id"], top, encoded_without_outputs)
            preimage = {
                "request_id": plan["request_id"],
                "expected_output_id": plan["expected_output_id"],
                "output_type": plan["expected_output_type"],
                "tables": bundle,
            }
            output_hash = _hash(
                common,
                "ASAP_BLOCK:OUTPUT_BUNDLE:{}:v1.3.12".format(plan["request_type"]),
                preimage,
            )
            output_row = binding.empty_row("request_outputs.csv")
            output_row.update(
                request_id=plan["request_id"], output_index=0, run_phase=PHASE,
                plan_context_hash=formal["plan_context_contract"]["plan_context_hash"],
                expected_output_id=plan["expected_output_id"], actual_output_id=plan["expected_output_id"],
                output_type=plan["expected_output_type"], result_table=top_table,
                result_primary_key_canonical=common.canonical_json_bytes(
                    [top[column] for column in binding.primary_key(top_table)]
                ).decode("utf-8"),
                output_hash=output_hash, request_output_status="MATERIALIZED",
            )
            rows["request_outputs.csv"].append(output_row)
        build_field = {
            "GENERATION": "approved_generator_build_identity_hash",
            "ANALYSIS": "approved_rta_build_identity_hash",
        }[plan["request_type"]]
        for index, status in enumerate(events):
            log = binding.empty_row("run_execution_log.csv")
            log.update(
                request_id=plan["request_id"], attempt_index=0, execution_event_index=index,
                build_identity_hash=formal["approved_builds"][build_field], execution_status=status,
                event_time_utc=FIXED_EVENT_TIME, run_phase=PHASE,
                plan_context_hash=formal["plan_context_contract"]["plan_context_hash"], max_attempts=1,
            )
            if status == "FINISHED":
                log.update(actual_output_id=plan["expected_output_id"], actual_output_hash=output_hash, actual_output_type=plan["expected_output_type"])
            if status == "NOT_RUN_DEPENDENCY":
                log.update(infrastructure_failure_class="DEPENDENCY_NOT_RUN", infrastructure_failure_code="SEMANTIC_GENERATION_FAILURE")
            rows["run_execution_log.csv"].append(log)
    binding.write_tables(output_root, rows)

    (output_root / "config.yaml").write_text(
        yaml.safe_dump({"profile": PHASE, "rta_version": "v9.3", "microcases": ["A", "B", "C", "D_GENERATION_FAILURE"]}, sort_keys=False),
        encoding="utf-8",
    )
    commit_text = "MICROCASE_GENERATOR_INPUT"
    status_text = "DETERMINISTIC_DIAGNOSTIC_PACKAGE"
    core0a_result = None
    core0a_inputs: Tuple[str, ...] = ()
    if core0a_build_identity is not None:
        commit_text = core0a_build_identity["implementation_commit_sha"]
        status_text = "CLEAN"
        core0a_result, core0a_inputs = _install_core0a_evidence(
            output_root, Path(core0a_evidence_root), core0a_build_identity
        )
    (output_root / "git_commit.txt").write_text(commit_text + "\n", encoding="utf-8")
    (output_root / "git_status.txt").write_text(status_text + "\n", encoding="utf-8")
    _make_acceptance(
        output_root, binding, formal, core0a_result, core0a_inputs
    )
    _write_manifest(output_root, binding, formal)

    if zip_path is not None:
        zip_path = Path(zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output_root.iterdir(), key=lambda item: item.name):
                info = zipfile.ZipInfo(path.name, (2026, 7, 13, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())

    failures = Counter(
        row["task_failure_reason_code"] for row in rows["per_task_results.csv"]
    )
    return {
        "analysis_count": len(rows["per_taskset_results.csv"]),
        "certified_taskset_count": sum(
            row["analysis_certification_status"] == "CERTIFIED_TASKSET"
            for row in rows["per_taskset_results.csv"]
        ),
        "dependency_record_count": len(rows["rta_dependency_records.csv"]),
        "failure_provenance_counts": dict(sorted(failures.items())),
        "file_count": len(list(output_root.iterdir())),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--zip", dest="zip_path", type=Path)
    parser.add_argument("--core0a-evidence-root", type=Path)
    parser.add_argument("--build-identity", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = build_microcase_package(
            args.output_root,
            args.zip_path,
            args.core0a_evidence_root,
            args.build_identity,
        )
    except Exception as exc:
        print("microcase package generation failed: {}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
