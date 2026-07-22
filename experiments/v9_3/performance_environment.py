"""Fail-closed source/binary/data provenance seal for every B4 stage."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from typing import Any, Dict, Mapping, Optional

from .config import domain_hash, task_workload_contract_material
from .performance_config import OUTCOME_VERSION, scientific_config_material
from .performance_energy import ENERGY_CONTRACT_VERSION, raw_solar_reference
from .performance_identity import REQUEST_CONTRACT_VERSION


STAGE_ENVIRONMENT_SCHEMA = "B4_STAGE_ENVIRONMENT_V1"
STAGE_ENVIRONMENT_DOMAIN = "ASAP_BLOCK:V9.3:B4:STAGE_ENVIRONMENT:v1"
STAGE_CONFIG_DOMAIN = "ASAP_BLOCK:V9.3:B4:STAGE_SCIENTIFIC_CONFIG:v1"

SHARED_ENVIRONMENT_FIELDS = (
    "exact_source_commit", "simulator_binary_sha256", "system_template_sha256",
    "solar_data_sha256", "workload_power_contract_identity",
    "outcome_contract_version", "outcome_source_sha256",
    "energy_contract_version", "request_contract_version",
)


class StageEnvironmentError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_scientific_config_hash(config: Mapping[str, Any]) -> str:
    return domain_hash(STAGE_CONFIG_DOMAIN, scientific_config_material(config))


def build_stage_environment(
    config: Mapping[str, Any], *, project_root: Path,
    simulator_path: Optional[Path] = None,
) -> Dict[str, Any]:
    project_root = Path(project_root).resolve()
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    if completed.returncode:
        raise StageEnvironmentError("cannot inspect tracked B4 worktree state")
    tracked_clean = not completed.stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project_root),
        capture_output=True, text=True, check=False,
    )
    if commit.returncode:
        raise StageEnvironmentError("cannot resolve B4 source commit")
    simulator = Path(simulator_path or config["simulation"]["simulator_bin"])
    if not simulator.is_absolute():
        simulator = project_root / simulator
    if not simulator.is_file():
        raise StageEnvironmentError(f"simulator binary is missing: {simulator}")
    template = Path(config["generation"]["system_template"])
    if not template.is_absolute():
        template = project_root / template
    if not template.is_file():
        raise StageEnvironmentError(f"system template is missing: {template}")
    solar = raw_solar_reference(template)
    workload = task_workload_contract_material(
        config["generation"]["workload_candidates"], template,
    )
    outcome_source = Path(__file__).with_name("performance_outcome.py")
    material = {
        "schema": STAGE_ENVIRONMENT_SCHEMA,
        "exact_source_commit": commit.stdout.strip(),
        "tracked_worktree_clean": tracked_clean,
        "simulator_binary_sha256": _sha256(simulator),
        "system_template_sha256": _sha256(template),
        "solar_data_sha256": str(solar["solar_source_hash"]),
        "workload_power_contract_identity": str(workload["contract_identity"]),
        "outcome_contract_version": OUTCOME_VERSION,
        "outcome_source_sha256": _sha256(outcome_source),
        "energy_contract_version": ENERGY_CONTRACT_VERSION,
        "request_contract_version": REQUEST_CONTRACT_VERSION,
        "stage_config_hash": stage_scientific_config_hash(config),
    }
    material["environment_identity"] = domain_hash(STAGE_ENVIRONMENT_DOMAIN, material)
    return material


def validate_stage_environment(environment: Mapping[str, Any]) -> None:
    if environment.get("schema") != STAGE_ENVIRONMENT_SCHEMA:
        raise StageEnvironmentError("stage environment schema mismatch")
    material = {key: value for key, value in environment.items() if key != "environment_identity"}
    if domain_hash(STAGE_ENVIRONMENT_DOMAIN, material) != environment.get("environment_identity"):
        raise StageEnvironmentError("stage environment identity mismatch")
    if environment.get("tracked_worktree_clean") is not True:
        raise StageEnvironmentError("B4 tracked worktree is not clean")


def assert_environment_compatible(
    current: Mapping[str, Any], sealed: Mapping[str, Any], *,
    require_stage_config: bool = False,
) -> None:
    validate_stage_environment(current)
    validate_stage_environment(sealed)
    fields = list(SHARED_ENVIRONMENT_FIELDS)
    if require_stage_config:
        fields.append("stage_config_hash")
    mismatches = [field for field in fields if current.get(field) != sealed.get(field)]
    if mismatches:
        raise StageEnvironmentError(f"B4 stage environment mismatch: {mismatches}")
