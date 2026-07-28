#!/usr/bin/env python3
"""Independent, fail-closed CPU-only admission for B4-PE base tasksets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

import yaml

import manifest_common as manifest
import materialization_common as materialization
import observability_validation as observability


B4_DIR = Path(__file__).resolve().parent
REPO_ROOT = B4_DIR.parents[1]
PROTOCOL_PATH = B4_DIR / "base_pool_admission_protocol_v1.json"
OBSERVABILITY_CONTRACT_PATH = B4_DIR / "observability_summary_contract_v2.json"
CPU_SYSTEM_RELPATH = "artifacts/cpu-only-admission/system.yml"
HORIZON_MS = 30000
PROCESSORS = 4
UNBOUNDED_ENERGY_J = 1_000_000_000.0

CPU_PRIORITY_BLOCK = """priority_energy:
  enabled: false
  profile_id: b4_pe_three_stage_v1
  alpha_w: 0.0
  horizon_ms: 30000
  tick_ms: 1
"""
CPU_ENERGY_BOUNDS = """  initial_energy: 1000000000.0
  max_energy: 1000000000.0
"""
CPU_LEGACY_SOURCE = """  day_of_year: 187
  time_of_day_ms: 21900000
  base_harvesting_rate: 0.0
  harvesting_scale: 1.0

  use_real_solar_data: false
  solar_data_file: "unused-cpu-only-admission.csv"
  pv_efficiency: 0.18
  pv_area_m2: 1.0

"""


class AdmissionError(RuntimeError):
    """A base taskset cannot be admitted under the frozen CPU-only gate."""


def _require(condition, message):
    if not condition:
        raise AdmissionError(message)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_protocol(path=PROTOCOL_PATH):
    try:
        protocol = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdmissionError("admission protocol is not readable JSON") from exc
    required = {
        "admission_contract", "governance", "identity_protocol_ref",
        "identity_protocol_sha256", "manifest_protocol_ref",
        "manifest_protocol_sha256", "observability_contract_ref",
        "observability_contract_sha256", "protocol_name", "schema_version",
        "status", "system_template_ref", "system_template_sha256",
        "task_generator_ref", "task_generator_sha256",
    }
    _require(set(protocol) == required, "admission protocol fields mismatch")
    _require(
        protocol["schema_version"] == 1
        and protocol["protocol_name"]
        == "B4-PE-base-pool-admission-v1-draft"
        and protocol["status"] == "draft",
        "admission protocol identity mismatch",
    )
    _require(
        protocol["admission_contract"]
        == {
            "deadline_miss_requirement":
                "zero_for_all_adjudicable_jobs",
            "horizon_ms": HORIZON_MS,
            "processor_count": PROCESSORS,
            "priority_order": "(period_ms,task_id)",
            "resource_model": "priority_energy_disabled",
        },
        "CPU-only admission contract mismatch",
    )
    _require(
        protocol["manifest_protocol_ref"]
        == manifest.MANIFEST_PROTOCOL_V4_PATH.name
        and protocol["manifest_protocol_sha256"]
        == file_sha256(manifest.MANIFEST_PROTOCOL_V4_PATH),
        "admission manifest protocol identity mismatch",
    )
    _require(
        protocol["identity_protocol_ref"] == manifest.IDENTITY_PROTOCOL_PATH.name
        and protocol["identity_protocol_sha256"]
        == file_sha256(manifest.IDENTITY_PROTOCOL_PATH),
        "admission identity protocol mismatch",
    )
    _require(
        protocol["observability_contract_ref"]
        == OBSERVABILITY_CONTRACT_PATH.name
        and protocol["observability_contract_sha256"]
        == file_sha256(OBSERVABILITY_CONTRACT_PATH),
        "admission observability contract mismatch",
    )
    _require(
        protocol["system_template_sha256"]
        == file_sha256(materialization.SYSTEM_TEMPLATE_PATH)
        and protocol["task_generator_sha256"]
        == file_sha256(materialization.TASK_GENERATOR_PATH),
        "admission input implementation identity mismatch",
    )
    _require(
        protocol["governance"]
        == {
            "formal_runs_authorized": False,
            "negative_control_runs_authorized": False,
            "paper_result_authorized": False,
            "pilot_runs_authorized": False,
        },
        "admission draft governance mismatch",
    )
    return protocol


def render_cpu_only_system():
    template = materialization.SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = materialization._replace_once(
        template,
        materialization.PRIORITY_ENERGY_PLACEHOLDER,
        CPU_PRIORITY_BLOCK,
        "priority-energy",
    )
    rendered = materialization._replace_once(
        rendered,
        materialization.ENERGY_BOUNDS_PLACEHOLDER,
        CPU_ENERGY_BOUNDS,
        "energy-bounds",
    )
    rendered = materialization._replace_once(
        rendered,
        materialization.LEGACY_SOURCE_PLACEHOLDER,
        CPU_LEGACY_SOURCE,
        "legacy-source",
    )
    document = yaml.safe_load(rendered)
    _require(
        document["priority_energy"]["enabled"] is False,
        "CPU-only system unexpectedly enables priority energy",
    )
    island = document["cpu_islands"][0]
    _require(
        island["numcpus"] == PROCESSORS
        and island["kernel"]["scheduler"] == "gpfp_asap_block"
        and island["kernel"]["task_placement"] == "global",
        "CPU-only system is not four-core global GFP-RM",
    )
    energy = document["energy_management"]
    _require(
        float(energy["initial_energy"]) == UNBOUNDED_ENERGY_J
        and float(energy["max_energy"]) == UNBOUNDED_ENERGY_J,
        "CPU-only system energy bounds mismatch",
    )
    return rendered.encode("utf-8")


def _validate_records(records):
    _require(isinstance(records, list) and records, "admission records missing")
    representatives = {}
    for record in records:
        _require(
            isinstance(record, dict)
            and record.get("schema_version") == 4,
            "admission requires manifest v4 records",
        )
        expected = manifest.build_case(
            record["phase"],
            record["utilization"],
            record["replicate_index"],
            record["lambda_E"],
            record["rho_E"],
            record["algorithm"],
            manifest.PROTOCOL_V4,
        )
        _require(record == expected, "admission record does not match v4")
        owner = representatives.setdefault(record["taskset_id"], record)
        _require(
            owner["taskset_pool"] == record["taskset_pool"]
            and owner["utilization"] == record["utilization"]
            and owner["replicate_index"] == record["replicate_index"]
            and owner["taskset_seed"] == record["taskset_seed"]
            and owner["base_taskset_artifact_relpath"]
            == record["base_taskset_artifact_relpath"]
            and owner["base_pool_admission_inventory_relpath"]
            == record["base_pool_admission_inventory_relpath"],
            "base admission identity is inconsistent",
        )
    inventory_paths = {
        item["base_pool_admission_inventory_relpath"]
        for item in representatives.values()
    }
    _require(len(inventory_paths) == 1, "admission inventory path is inconsistent")
    return representatives


def _validate_simulator(path):
    simulator = Path(path).resolve(strict=True)
    metadata = simulator.stat()
    _require(
        stat.S_ISREG(metadata.st_mode)
        and not simulator.is_symlink()
        and metadata.st_mode & 0o111,
        "CPU-only simulator must be a regular executable",
    )
    return simulator


def _run_cpu_gate(simulator, system_path, taskset_path, document, semantic_hash):
    with tempfile.TemporaryDirectory(prefix="b4pe-cpu-admission-run-") as temp:
        run_root = Path(temp)
        trace_path = run_root / "trace.json"
        argv = [
            str(simulator),
            str(system_path),
            str(taskset_path),
            str(HORIZON_MS),
            "-t",
            str(trace_path),
            "--run-id",
            "b4-pe-cpu-only-admission",
            "--taskset-semantic-hash",
            semantic_hash,
            "--b4-observability-summary",
            "--b4-summary-horizon",
            str(HORIZON_MS),
            "--b4-observability-contract-version",
            "2",
        ]
        completed = subprocess.run(
            argv,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env={
                **os.environ,
                "PARTSIM_LOG_DIR": str(run_root / "logs"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            },
        )
        _require(
            completed.returncode == 0,
            "CPU-only simulator exited {}: {}".format(
                completed.returncode, (completed.stderr or "")[-2000:]
            ),
        )
        _require(trace_path.is_file(), "CPU-only simulator produced no trace")
        try:
            result = json.loads(trace_path.read_text(encoding="utf-8"))
            observability.validate_schema3_summary(
                result,
                expected_horizon_ms=HORIZON_MS,
                initial_energy_j=UNBOUNDED_ENERGY_J,
                capacity_j=UNBOUNDED_ENERGY_J,
                processor_count=PROCESSORS,
                expected_task_ranks=observability.task_ranks_from_taskset(
                    document
                ),
                taskset_document=document,
                expected_contract_version=2,
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            observability.ObservabilityValidationError,
        ) as exc:
            raise AdmissionError(
                f"CPU-only observability validation failed: {exc}"
            ) from exc
        adjudicable = sum(
            item["adjudicable_jobs"] for item in result["per_task_summary"]
        )
        misses = sum(
            item["deadline_miss_jobs"] for item in result["per_task_summary"]
        )
        return adjudicable, misses


def admit_records(records, output_root, manifest_sha256, simulator_path):
    representatives = _validate_records(records)
    root = Path(output_root)
    _require(root.is_absolute(), "admission output root must be absolute")
    resolved_root = root.resolve(strict=False)
    _require(
        not (
            resolved_root == materialization.REPO_ROOT
            or materialization.REPO_ROOT in resolved_root.parents
        ),
        "admission output root must be outside the repository",
    )
    _require(
        isinstance(manifest_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is not None,
        "manifest SHA must be lowercase SHA-256",
    )
    protocol = load_protocol()
    simulator = _validate_simulator(simulator_path)
    root.mkdir(parents=True, exist_ok=True)
    system_payload = render_cpu_only_system()
    system_path = materialization._publish_identical_or_fail(
        root, CPU_SYSTEM_RELPATH, system_payload
    )
    simulator_sha = file_sha256(simulator)
    system_sha = materialization.bytes_sha256(system_payload)
    entries = []
    accepted_payloads = {}
    for taskset_id, record in sorted(representatives.items()):
        document, payload = materialization.generate_base_taskset(record)
        semantic_hash = materialization.taskset_semantic_hash_bytes(payload)
        with tempfile.TemporaryDirectory(
            prefix="b4pe-cpu-admission-input-"
        ) as temp:
            taskset_path = Path(temp) / "base.yml"
            taskset_path.write_bytes(payload)
            adjudicable, misses = _run_cpu_gate(
                simulator,
                system_path,
                taskset_path,
                document,
                semantic_hash,
            )
        status = "accepted" if misses == 0 else "rejected"
        if status == "accepted":
            accepted_payloads[record["base_taskset_artifact_relpath"]] = payload
        entries.append(
            {
                "taskset_pool": record["taskset_pool"],
                "utilization": record["utilization"],
                "replicate_index": record["replicate_index"],
                "taskset_seed": record["taskset_seed"],
                "taskset_id": taskset_id,
                "base_taskset_path":
                    record["base_taskset_artifact_relpath"],
                "base_taskset_sha256":
                    materialization.bytes_sha256(payload),
                "base_semantic_hash": semantic_hash,
                "cpu_only_simulator_sha256": simulator_sha,
                "cpu_only_system_config_sha256": system_sha,
                "horizon_ms": HORIZON_MS,
                "adjudicable_job_count": adjudicable,
                "deadline_miss_count": misses,
                "admission_status": status,
            }
        )
    inventory = {
        "schema_version": 1,
        "protocol_name": "B4-PE-base-pool-admission-v1-draft",
        "admission_protocol_sha256": file_sha256(PROTOCOL_PATH),
        "manifest_file_sha256": manifest_sha256,
        "manifest_protocol_sha256":
            file_sha256(manifest.MANIFEST_PROTOCOL_V4_PATH),
        "identity_protocol_sha256":
            file_sha256(manifest.IDENTITY_PROTOCOL_PATH),
        "task_generator_sha256":
            file_sha256(materialization.TASK_GENERATOR_PATH),
        "cpu_only_system_config_path": CPU_SYSTEM_RELPATH,
        "cpu_only_system_config_sha256": system_sha,
        "cpu_only_simulator_sha256": simulator_sha,
        "base_tasksets": entries,
    }
    for relative, payload in sorted(accepted_payloads.items()):
        materialization._publish_identical_or_fail(root, relative, payload)
    inventory_path = next(iter(representatives.values()))[
        "base_pool_admission_inventory_relpath"
    ]
    materialization._publish_identical_or_fail(
        root, inventory_path, materialization.canonical_json_bytes(inventory)
    )
    _require(
        all(item["admission_status"] == "accepted" for item in entries),
        "one or more base tasksets failed CPU-only admission",
    )
    return inventory
