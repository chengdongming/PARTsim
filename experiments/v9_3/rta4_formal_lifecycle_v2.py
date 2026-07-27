"""Strict, isolated lifecycle artifacts for bounded formal RTA4 V2 runs.

The checked-in V2 configurations remain unauthorized.  This module therefore
defines a disjoint TEST_ONLY authorization domain for bounded end-to-end
verification, plus the store/writer primitives later used by production once
an independently reviewed production authorization exists.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from . import exact_energy
from .result_writer import atomic_write_json, atomic_write_text
from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_config_v2 import (
    RTA4_FORMAL_PARAMETER_STATUS_V2,
    RTA4_FORMAL_PROFILE_V2,
    formal_taskset_store_identity_v2,
    rta4_formal_config_hash_v2,
    validate_rta4_formal_config_v2,
)
from .rta4_formal_environment import load_strict_json
from .rta4_formal_plan_v2 import describe_formal_plan_v2
from .rta4_formal_schema_v2 import (
    V2_ATTEMPT_REQUIRED_FIELDS,
    V2_RESULT_ROW_REQUIRED_FIELDS,
    formal_schema_hash_v2,
)
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_shared_energy import TaskEnergyMaterial
from .rta4_taskset_v2 import TasksetIdentityCertificateV2


RTA4_PREPARED_CONFIG_SCHEMA_V2 = "ASAP_BLOCK_V9_3_RTA4_PREPARED_CONFIG_V2_SHARED_ENERGY"
RTA4_PREPARED_CONFIG_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_PREPARED_CONFIG:v2-shared-energy"
RTA4_TEST_AUTHORIZATION_SCHEMA_V2 = "ASAP_BLOCK_V9_3_RTA4_TEST_ONLY_AUTHORIZATION_V2_SHARED_ENERGY"
RTA4_TEST_AUTHORIZATION_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_TEST_ONLY_AUTHORIZATION:v2-shared-energy"
RTA4_TEST_PARAMETER_STATUS_V2 = "TEST_ONLY_BOUNDED_NOT_FORMAL_AUTHORIZATION"
RTA4_TASKSET_STORE_MANIFEST_V2 = "formal_taskset_store_manifest_v2_shared_energy.json"
RTA4_TASKSET_STORE_ENTRY_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_TASKSET_STORE_ENTRY:v2"
RTA4_RESULT_ROW_SCHEMA_V2 = "ASAP_BLOCK_V9_3_RTA4_RESULT_ROW_V2_SHARED_ENERGY"
RTA4_RESULT_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_RESULT:v2"
RTA4_RETRY_RESUME_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_RETRY_RESUME:v2"
RTA4_RUN_MANIFEST_V2 = "formal_run_manifest_v2_shared_energy.json"
RTA4_CHECKPOINT_V2 = "formal_checkpoint_v2_shared_energy.json"
RTA4_RESULT_DIRECTORY_V2 = "formal_terminal_results_v2_shared_energy"


class RTA4FormalLifecycleV2Error(RuntimeError):
    """Raised before a mixed-version or drifting lifecycle is mutated."""


def _absolute(value: Any, label: str, *, existing_file: bool = False) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise RTA4FormalLifecycleV2Error(f"{label} must be an absolute path")
    try:
        path = Path(value).resolve(strict=existing_file)
    except OSError as exc:
        raise RTA4FormalLifecycleV2Error(f"{label} does not exist") from exc
    if existing_file and not path.is_file():
        raise RTA4FormalLifecycleV2Error(f"{label} must be a file")
    return str(path)


def _sha(value: Any, label: str) -> str:
    if (
        type(value) is not str or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RTA4FormalLifecycleV2Error(f"{label} must be a lowercase SHA-256")
    return value


def _git_oid(value: Any, label: str) -> str:
    if (
        type(value) is not str or len(value) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RTA4FormalLifecycleV2Error(
            f"{label} must be a lowercase Git object identity"
        )
    return value


def _forbidden_key(value: Any) -> str | None:
    forbidden = {
        "actual_power", "P_exact", "watts", "power_w",
        "linear_service_scale_identity", "v1_store_identity",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in forbidden:
                return str(key)
            nested = _forbidden_key(item)
            if nested is not None:
                return nested
    elif isinstance(value, (tuple, list)):
        for item in value:
            nested = _forbidden_key(item)
            if nested is not None:
                return nested
    return None


def _normalize_timeout_contract(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise RTA4FormalLifecycleV2Error("V2 timeout contract must be a mapping")
    result: Dict[str, Any] = {}
    for method, row in value.items():
        if not isinstance(method, str) or not isinstance(row, Mapping) or set(row) != {
            "initial_timeout_seconds", "retry_timeout_seconds", "maximum_attempts",
        }:
            raise RTA4FormalLifecycleV2Error("V2 timeout method contract mismatch")
        initial = row["initial_timeout_seconds"]
        retry = row["retry_timeout_seconds"]
        attempts = row["maximum_attempts"]
        if (
            type(initial) is not int or initial < 0
            or type(retry) is not int or retry < max(1, initial)
            or type(attempts) is not int or attempts not in {1, 2}
        ):
            raise RTA4FormalLifecycleV2Error("V2 timeout/retry bounds are invalid")
        result[method] = dict(row)
    return result


def build_test_prepared_config_v2(
    config: Mapping[str, Any], *,
    output_root: Path | str,
    taskset_store: Path | str,
    production_manifest_path: Path | str,
    source_root: Path | str,
    selected_ordinals: Sequence[int],
    timeout_contract: Mapping[str, Any],
    worker_count: int = 1,
    max_in_flight: int | None = None,
    simulation_timeout_seconds: int = 60,
) -> Dict[str, Any]:
    """Build a bounded TEST_ONLY prepared artifact; never production authority."""

    normalized = validate_rta4_formal_config_v2(config)
    root = Path(source_root).resolve(strict=True)
    if not root.is_dir():
        raise RTA4FormalLifecycleV2Error("V2 source root must be a directory")
    ordinals = tuple(selected_ordinals)
    if (
        not ordinals or len(ordinals) > 32
        or any(type(value) is not int or value < 0 for value in ordinals)
        or tuple(sorted(set(ordinals))) != ordinals
    ):
        raise RTA4FormalLifecycleV2Error("TEST_ONLY ordinals must be sorted, unique, and bounded")
    if type(worker_count) is not int or worker_count < 1 or worker_count > 4:
        raise RTA4FormalLifecycleV2Error("TEST_ONLY worker_count must be in 1..4")
    if type(simulation_timeout_seconds) is not int or simulation_timeout_seconds < 1:
        raise RTA4FormalLifecycleV2Error("simulation timeout must be positive")
    in_flight = worker_count if max_in_flight is None else max_in_flight
    if type(in_flight) is not int or in_flight < worker_count or in_flight > 32:
        raise RTA4FormalLifecycleV2Error("TEST_ONLY max_in_flight is invalid")
    timeouts = _normalize_timeout_contract(timeout_contract)
    manifest_path = _absolute(
        str(production_manifest_path), "production manifest", existing_file=True,
    )
    try:
        manifest_identity = _sha(
            load_strict_json(manifest_path).get("manifest_id"),
            "production manifest identity",
        )
    except Exception as exc:
        raise RTA4FormalLifecycleV2Error(
            "prepared V2 artifact cannot bind the production manifest identity"
        ) from exc
    material: Dict[str, Any] = {
        "prepared_schema": RTA4_PREPARED_CONFIG_SCHEMA_V2,
        "profile": RTA4_FORMAL_PROFILE_V2,
        "parameter_status": RTA4_TEST_PARAMETER_STATUS_V2,
        "formal_authorization": False,
        "core": normalized["core"],
        "scientific_config": normalized,
        "config_identity": rta4_formal_config_hash_v2(normalized),
        "plan_identity": describe_formal_plan_v2(normalized)["plan_sha256"],
        "production_build_manifest_identity": manifest_identity,
        "selected_ordinals": list(ordinals),
        "timeout_contract": timeouts,
        "operational": {
            "output_root": str(Path(output_root).resolve()),
            "taskset_store": str(Path(taskset_store).resolve()),
            "production_manifest_path": manifest_path,
            "source_root": str(root),
            "system_config_path": str(root / "system_config_unified_template.yml"),
            "energy_support_path": str(root / "configs/v9_3_rta4_shared_energy_support_v2.yaml"),
            "worker_count": worker_count,
            "max_in_flight": in_flight,
            "simulation_timeout_seconds": simulation_timeout_seconds,
        },
    }
    material["prepared_config_id"] = domain_hash(RTA4_PREPARED_CONFIG_DOMAIN_V2, material)
    return validate_prepared_config_v2(material)


def validate_prepared_config_v2(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RTA4FormalLifecycleV2Error("V2 prepared config must be a mapping")
    exact = {
        "prepared_schema", "profile", "parameter_status", "formal_authorization",
        "core", "scientific_config", "config_identity", "plan_identity",
        "production_build_manifest_identity",
        "selected_ordinals", "timeout_contract", "operational", "prepared_config_id",
    }
    if set(value) != exact or value.get("prepared_schema") != RTA4_PREPARED_CONFIG_SCHEMA_V2:
        raise RTA4FormalLifecycleV2Error("V2 prepared config field/schema mismatch")
    if (
        value.get("profile") != RTA4_FORMAL_PROFILE_V2
        or value.get("parameter_status") != RTA4_TEST_PARAMETER_STATUS_V2
        or value.get("formal_authorization") is not False
    ):
        raise RTA4FormalLifecycleV2Error("V2 prepared config is not TEST_ONLY")
    config = validate_rta4_formal_config_v2(value["scientific_config"], expected_core=value["core"])
    if config["experiment_contract"]["parameter_status"] != RTA4_FORMAL_PARAMETER_STATUS_V2:
        raise RTA4FormalLifecycleV2Error("checked-in V2 scientific status drifted")
    if (
        value["config_identity"] != rta4_formal_config_hash_v2(config)
        or value["plan_identity"] != describe_formal_plan_v2(config)["plan_sha256"]
    ):
        raise RTA4FormalLifecycleV2Error("V2 prepared scientific identity mismatch")
    _sha(
        value["production_build_manifest_identity"],
        "prepared production manifest identity",
    )
    ordinals = value["selected_ordinals"]
    if (
        type(ordinals) is not list or not ordinals or len(ordinals) > 32
        or any(type(item) is not int or item < 0 for item in ordinals)
        or sorted(set(ordinals)) != ordinals
    ):
        raise RTA4FormalLifecycleV2Error("V2 prepared ordinals are invalid")
    timeouts = _normalize_timeout_contract(value["timeout_contract"])
    operation = value["operational"]
    if not isinstance(operation, Mapping) or set(operation) != {
        "output_root", "taskset_store", "production_manifest_path", "source_root",
        "system_config_path", "energy_support_path", "worker_count", "max_in_flight",
        "simulation_timeout_seconds",
    }:
        raise RTA4FormalLifecycleV2Error("V2 prepared operational field mismatch")
    root = Path(_absolute(operation["source_root"], "source_root")).resolve(strict=True)
    normalized_operation = {
        **dict(operation),
        "output_root": _absolute(operation["output_root"], "output_root"),
        "taskset_store": _absolute(operation["taskset_store"], "taskset_store"),
        "production_manifest_path": _absolute(
            operation["production_manifest_path"], "production manifest", existing_file=True,
        ),
        "source_root": str(root),
        "system_config_path": _absolute(
            operation["system_config_path"], "system config", existing_file=True,
        ),
        "energy_support_path": _absolute(
            operation["energy_support_path"], "energy support", existing_file=True,
        ),
    }
    if normalized_operation["system_config_path"] != str(root / "system_config_unified_template.yml"):
        raise RTA4FormalLifecycleV2Error("V2 system source may not be overridden")
    if normalized_operation["energy_support_path"] != str(
        root / "configs/v9_3_rta4_shared_energy_support_v2.yaml"
    ):
        raise RTA4FormalLifecycleV2Error("V2 support source may not be overridden")
    workers = operation["worker_count"]
    in_flight = operation["max_in_flight"]
    if (
        type(workers) is not int or workers < 1 or workers > 4
        or type(in_flight) is not int or in_flight < workers or in_flight > 32
    ):
        raise RTA4FormalLifecycleV2Error("V2 worker bounds are invalid")
    if (
        type(operation["simulation_timeout_seconds"]) is not int
        or operation["simulation_timeout_seconds"] < 1
    ):
        raise RTA4FormalLifecycleV2Error("V2 simulation timeout is invalid")
    unsigned = dict(value)
    observed = unsigned.pop("prepared_config_id")
    if observed != domain_hash(RTA4_PREPARED_CONFIG_DOMAIN_V2, unsigned):
        raise RTA4FormalLifecycleV2Error("V2 prepared config identity mismatch")
    result = deepcopy(dict(value))
    result["scientific_config"] = config
    result["timeout_contract"] = timeouts
    result["operational"] = normalized_operation
    return result


def build_test_authorization_v2(prepared_config: Mapping[str, Any]) -> Dict[str, Any]:
    prepared = validate_prepared_config_v2(prepared_config)
    material = {
        "authorization_schema": RTA4_TEST_AUTHORIZATION_SCHEMA_V2,
        "authorization_domain": "TEST_ONLY",
        "profile": RTA4_FORMAL_PROFILE_V2,
        "parameter_status": RTA4_TEST_PARAMETER_STATUS_V2,
        "formal_authorization": False,
        "prepared_config_id": prepared["prepared_config_id"],
        "core": prepared["core"],
        "output_root": prepared["operational"]["output_root"],
        "taskset_store": prepared["operational"]["taskset_store"],
        "selected_ordinals": list(prepared["selected_ordinals"]),
    }
    material["authorization_id"] = domain_hash(RTA4_TEST_AUTHORIZATION_DOMAIN_V2, material)
    return validate_test_authorization_v2(material, prepared_config=prepared)


def validate_test_authorization_v2(
    value: Mapping[str, Any], *, prepared_config: Mapping[str, Any],
) -> Dict[str, Any]:
    prepared = validate_prepared_config_v2(prepared_config)
    if not isinstance(value, Mapping) or set(value) != {
        "authorization_schema", "authorization_domain", "profile", "parameter_status",
        "formal_authorization", "prepared_config_id", "core", "output_root",
        "taskset_store", "selected_ordinals", "authorization_id",
    }:
        raise RTA4FormalLifecycleV2Error("V2 test authorization field mismatch")
    if (
        value.get("authorization_schema") != RTA4_TEST_AUTHORIZATION_SCHEMA_V2
        or value.get("authorization_domain") != "TEST_ONLY"
        or value.get("profile") != RTA4_FORMAL_PROFILE_V2
        or value.get("parameter_status") != RTA4_TEST_PARAMETER_STATUS_V2
        or value.get("formal_authorization") is not False
    ):
        raise RTA4FormalLifecycleV2Error("V2 production authorization was not established")
    for field, expected in (
        ("prepared_config_id", prepared["prepared_config_id"]),
        ("core", prepared["core"]),
        ("output_root", prepared["operational"]["output_root"]),
        ("taskset_store", prepared["operational"]["taskset_store"]),
        ("selected_ordinals", prepared["selected_ordinals"]),
    ):
        if value.get(field) != expected:
            raise RTA4FormalLifecycleV2Error(f"V2 authorization {field} drift")
    unsigned = dict(value)
    observed = unsigned.pop("authorization_id")
    if observed != domain_hash(RTA4_TEST_AUTHORIZATION_DOMAIN_V2, unsigned):
        raise RTA4FormalLifecycleV2Error("V2 test authorization identity mismatch")
    return deepcopy(dict(value))


def _store_manifest(production_manifest_identity: str) -> Dict[str, Any]:
    _sha(production_manifest_identity, "production manifest identity")
    material = {
        "store_version": "ASAP_BLOCK_V9_3_RTA4_TASKSET_STORE_V2_SHARED_ENERGY",
        "store_domain": "V2_SHARED_ENERGY_ONLY",
        "store_identity": formal_taskset_store_identity_v2(),
        "production_build_manifest_identity": production_manifest_identity,
        "certificate_schema": "ASAP_BLOCK_V9_3_RTA4_W_FREE_TASKSET_CERTIFICATE_V2",
        "task_energy_schema": "ASAP_BLOCK_V9_3_RTA4_TASK_ENERGY_MATERIAL_V2",
        "legacy_v1_accepted": False,
    }
    return material


class RTA4FormalTasksetStoreV2:
    """Atomic W-free certificate and TaskEnergyMaterial store."""

    def __init__(
        self, root: Path | str, *, production_manifest_identity: str,
        require_existing_namespace: bool = False,
    ) -> None:
        self.root = Path(root)
        self.certificates = self.root / "certificates_v2"
        self.task_energy = self.root / "task_energy_materials_v2"
        self.bindings = self.root / "bindings_v2"
        marker = self.root / RTA4_TASKSET_STORE_MANIFEST_V2
        expected = _store_manifest(production_manifest_identity)
        if self.root.exists() and any(self.root.iterdir()) and not marker.is_file():
            raise RTA4FormalLifecycleV2Error("V2 store refuses a V1/unknown namespace")
        if marker.is_file() and load_strict_json(marker) != expected:
            raise RTA4FormalLifecycleV2Error("V2 taskset store manifest drift")
        if require_existing_namespace and (
            not marker.is_file()
            or any(not path.is_dir() for path in (
                self.certificates, self.task_energy, self.bindings,
            ))
        ):
            raise RTA4FormalLifecycleV2Error("V2 resume store namespace is incomplete")
        for path in (self.certificates, self.task_energy, self.bindings):
            path.mkdir(parents=True, exist_ok=True)
        if not marker.is_file():
            atomic_write_json(marker, expected)
        self.production_manifest_identity = production_manifest_identity

    @staticmethod
    def _consistent_write(path: Path, payload: bytes) -> None:
        if path.is_file():
            if path.read_bytes() != payload:
                raise RTA4FormalLifecycleV2Error(f"V2 store content conflict: {path.name}")
        else:
            atomic_write_text(path, payload.decode("utf-8"))

    def put(
        self, certificate: TasksetIdentityCertificateV2,
        task_energy: TaskEnergyMaterial,
    ) -> Mapping[str, Any]:
        if type(certificate) is not TasksetIdentityCertificateV2:
            raise RTA4FormalLifecycleV2Error("V2 store rejects a V1 certificate")
        if type(task_energy) is not TaskEnergyMaterial:
            raise RTA4FormalLifecycleV2Error("V2 store requires TaskEnergyMaterial")
        certificate.validate()
        if (
            task_energy.taskset_id != certificate.taskset_id
            or task_energy.taskset_store_identity != formal_taskset_store_identity_v2()
            or task_energy.production_build_manifest_identity != self.production_manifest_identity
        ):
            raise RTA4FormalLifecycleV2Error("V2 store material binding mismatch")
        cert_payload = certificate.canonical_bytes()
        energy_payload = canonical_json(task_energy.material()).encode("utf-8")
        cert_path = self.certificates / f"{certificate.taskset_id}.json"
        energy_path = self.task_energy / f"{task_energy.task_energy_material_identity}.json"
        self._consistent_write(cert_path, cert_payload)
        self._consistent_write(energy_path, energy_payload)
        base = {
            "store_identity": formal_taskset_store_identity_v2(),
            "production_build_manifest_identity": self.production_manifest_identity,
            "taskset_identity": certificate.taskset_id,
            "taskset_source_sha256": certificate.taskset_source_sha256,
            "certificate_sha256": hashlib.sha256(cert_payload).hexdigest(),
            "task_energy_material_identity": task_energy.task_energy_material_identity,
            "task_energy_sha256": hashlib.sha256(energy_payload).hexdigest(),
        }
        entry = {**base, "store_entry_identity": domain_hash(RTA4_TASKSET_STORE_ENTRY_DOMAIN_V2, base)}
        path = self.bindings / f"{certificate.taskset_id}.json"
        encoded = (canonical_json(entry) + "\n").encode("utf-8")
        self._consistent_write(path, encoded)
        return entry

    def load_certificate(self, taskset_identity: str) -> TasksetIdentityCertificateV2:
        try:
            value = TasksetIdentityCertificateV2.from_canonical_bytes(
                (self.certificates / f"{taskset_identity}.json").read_bytes()
            )
        except Exception as exc:
            raise RTA4FormalLifecycleV2Error("cannot load V2 taskset certificate") from exc
        if value.taskset_id != taskset_identity:
            raise RTA4FormalLifecycleV2Error("V2 certificate filename drift")
        return value

    def validate_binding(
        self, certificate: TasksetIdentityCertificateV2,
        task_energy: TaskEnergyMaterial,
    ) -> Mapping[str, Any]:
        stored = self.load_certificate(certificate.taskset_id)
        if stored != certificate:
            raise RTA4FormalLifecycleV2Error("V2 resume certificate drift")
        path = self.task_energy / f"{task_energy.task_energy_material_identity}.json"
        binding_path = self.bindings / f"{certificate.taskset_id}.json"
        try:
            payload = path.read_bytes()
            binding = load_strict_json(binding_path)
        except Exception as exc:
            raise RTA4FormalLifecycleV2Error("V2 resume store binding is incomplete") from exc
        if payload != canonical_json(task_energy.material()).encode("utf-8"):
            raise RTA4FormalLifecycleV2Error("V2 resume task-energy content drift")
        expected = self.put(certificate, task_energy)
        if binding != expected:
            raise RTA4FormalLifecycleV2Error("V2 resume store entry drift")
        return expected


def retry_resume_identity_v2(
    *, prepared_config_id: str, authorization_id: str, plan_identity: str,
    production_manifest_identity: str, plan_record_identity: str,
    taskset_identity: str, task_energy_material_identity: str,
    service_material_identity: str, beta_material_identity: str,
    method: str, exact_e0: str, timeout_sequence: Sequence[int],
) -> str:
    material = {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "prepared_config_id": prepared_config_id,
        "authorization_id": authorization_id,
        "plan_identity": plan_identity,
        "plan_record_identity": plan_record_identity,
        "production_build_manifest_identity": production_manifest_identity,
        "taskset_identity": taskset_identity,
        "task_energy_material_identity": task_energy_material_identity,
        "service_material_identity": service_material_identity,
        "beta_material_identity": beta_material_identity,
        "method": method,
        "exact_e0": exact_e0,
        "timeout_sequence": list(timeout_sequence),
    }
    return domain_hash(RTA4_RETRY_RESUME_DOMAIN_V2, material)


def validate_result_row_v2(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(V2_RESULT_ROW_REQUIRED_FIELDS):
        raise RTA4FormalLifecycleV2Error("V2 result row field set mismatch")
    row = deepcopy(dict(value))
    if row["row_schema"] != RTA4_RESULT_ROW_SCHEMA_V2 or row["profile"] != RTA4_FORMAL_PROFILE_V2:
        raise RTA4FormalLifecycleV2Error("V2 result row schema/profile mismatch")
    if row["schema_sha256"] != formal_schema_hash_v2():
        raise RTA4FormalLifecycleV2Error("V2 result schema identity mismatch")
    if row["numeric_contract_sha256"] != RTA4_NUMERIC_CONTRACT_V2_SHA256:
        raise RTA4FormalLifecycleV2Error("V2 result numeric identity mismatch")
    if row["theory_document_sha256"] != exact_energy.THEORY_DOCUMENT_SHA256:
        raise RTA4FormalLifecycleV2Error("V2 result theory identity mismatch")
    for key in (
        "config_identity", "plan_identity", "plan_record_identity",
        "execution_identity",
        "production_build_manifest_identity",
        "taskset_source_sha256", "taskset_identity",
        "task_energy_material_identity", "service_material_identity",
        "beta_material_identity", "retry_resume_identity", "result_identity",
    ):
        _sha(row[key], key)
    _git_oid(row["source_commit"], "source_commit")
    _git_oid(row["source_tree"], "source_tree")
    attempts = row["attempts"]
    if type(attempts) is not list or not attempts:
        raise RTA4FormalLifecycleV2Error("V2 result requires attempt evidence")
    for index, attempt in enumerate(attempts):
        if not isinstance(attempt, Mapping) or set(attempt) != set(V2_ATTEMPT_REQUIRED_FIELDS):
            raise RTA4FormalLifecycleV2Error("V2 attempt field set mismatch")
        if attempt["attempt_index"] != index:
            raise RTA4FormalLifecycleV2Error("V2 attempts are not strictly ordered")
        if type(attempt["timeout_seconds"]) is not int or attempt["timeout_seconds"] < 0:
            raise RTA4FormalLifecycleV2Error("V2 attempt timeout is invalid")
        for key in (
            "analysis_identity", "taskset_identity", "task_energy_material_identity",
            "service_material_identity", "beta_material_identity",
            "production_build_manifest_identity",
        ):
            _sha(attempt[key], f"attempt.{key}")
        for key in (
            "taskset_identity", "task_energy_material_identity",
            "service_material_identity", "beta_material_identity",
            "production_build_manifest_identity",
        ):
            if attempt[key] != row[key]:
                raise RTA4FormalLifecycleV2Error("V2 retry changed frozen material")
    if (
        type(row["timeout_seconds"]) is not int
        or row["timeout_seconds"] != attempts[-1]["timeout_seconds"]
        or row["status"] != attempts[-1]["status"]
        or not isinstance(row["method"], str) or not row["method"]
        or not isinstance(row["exact_e0"], str) or not row["exact_e0"]
    ):
        raise RTA4FormalLifecycleV2Error("V2 result/terminal attempt mismatch")
    forbidden = _forbidden_key(row)
    if forbidden is not None:
        raise RTA4FormalLifecycleV2Error(f"V2 row contains forbidden V1 field: {forbidden}")
    unsigned = dict(row)
    observed = unsigned.pop("result_identity")
    if observed != domain_hash(RTA4_RESULT_DOMAIN_V2, unsigned):
        raise RTA4FormalLifecycleV2Error("V2 result identity mismatch")
    return row


class RTA4FormalResultWriterV2:
    """Atomic terminal JSON writer with exact V2 resume validation."""

    def __init__(
        self, root: Path | str, *, prepared_config: Mapping[str, Any],
        authorization: Mapping[str, Any], production_manifest: Mapping[str, Any],
        records: Sequence[Any], require_existing_namespace: bool = False,
    ) -> None:
        self.prepared = validate_prepared_config_v2(prepared_config)
        self.authorization = validate_test_authorization_v2(
            authorization, prepared_config=self.prepared,
        )
        if not isinstance(production_manifest, Mapping):
            raise RTA4FormalLifecycleV2Error("V2 writer requires a validated manifest")
        manifest_identity = _sha(production_manifest.get("manifest_id"), "manifest identity")
        repository = production_manifest.get("repository")
        if not isinstance(repository, Mapping):
            raise RTA4FormalLifecycleV2Error("V2 writer manifest repository is absent")
        source_commit = _git_oid(repository.get("git_commit"), "source commit")
        source_tree = _git_oid(repository.get("git_tree"), "source tree")
        plan_rows = [
            {"ordinal": record.ordinal, "plan_record_identity": record.record_id,
             "execution_identity": record.execution_id, "kind": record.kind}
            for record in records
        ]
        run_material = {
            "run_schema": "ASAP_BLOCK_V9_3_RTA4_RUN_MANIFEST_V2_SHARED_ENERGY",
            "profile": RTA4_FORMAL_PROFILE_V2,
            "execution_class": RTA4_TEST_PARAMETER_STATUS_V2,
            "formal_authorization": False,
            "prepared_config_id": self.prepared["prepared_config_id"],
            "authorization_id": self.authorization["authorization_id"],
            "core": self.prepared["core"],
            "schema_sha256": formal_schema_hash_v2(),
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "config_identity": self.prepared["config_identity"],
            "plan_identity": self.prepared["plan_identity"],
            "production_build_manifest_identity": manifest_identity,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "taskset_store_identity": formal_taskset_store_identity_v2(),
            "output_root": str(Path(root).resolve()),
            "plan_records": plan_rows,
        }
        self.run_manifest = {
            **run_material,
            "run_identity": domain_hash("ASAP_BLOCK:V9.3:RTA4_RUN_MANIFEST:v2", run_material),
        }
        self.root = Path(root)
        self.terminals = self.root / RTA4_RESULT_DIRECTORY_V2
        marker = self.root / RTA4_RUN_MANIFEST_V2
        if self.root.exists() and any(self.root.iterdir()) and not marker.is_file():
            raise RTA4FormalLifecycleV2Error("V2 writer refuses a V1/unknown output root")
        if require_existing_namespace and (not marker.is_file() or not self.terminals.is_dir()):
            raise RTA4FormalLifecycleV2Error("V2 resume namespace is incomplete")
        if marker.is_file() and load_strict_json(marker) != self.run_manifest:
            raise RTA4FormalLifecycleV2Error("V2 resume run identity drift")
        self.terminals.mkdir(parents=True, exist_ok=True)
        if not marker.is_file():
            atomic_write_json(marker, self.run_manifest)
        self.production_manifest_identity = manifest_identity
        self.source_commit = source_commit
        self.source_tree = source_tree

    def completed_rows(self) -> Mapping[str, Mapping[str, Any]]:
        expected = {
            str(row["execution_identity"]): row
            for row in self.run_manifest["plan_records"]
        }
        observed: Dict[str, Mapping[str, Any]] = {}
        for path in sorted(self.terminals.glob("*.json")):
            row = validate_result_row_v2(load_strict_json(path))
            identity = str(row["execution_identity"])
            if identity not in expected or path.stem != identity:
                raise RTA4FormalLifecycleV2Error("V2 terminal lies outside the frozen plan")
            if row["plan_record_identity"] != expected[identity]["plan_record_identity"]:
                raise RTA4FormalLifecycleV2Error("V2 terminal plan binding drift")
            for key, expected_value in (
                ("schema_sha256", self.run_manifest["schema_sha256"]),
                ("numeric_contract_sha256", self.run_manifest["numeric_contract_sha256"]),
                ("theory_document_sha256", self.run_manifest["theory_document_sha256"]),
                ("config_identity", self.run_manifest["config_identity"]),
                ("plan_identity", self.run_manifest["plan_identity"]),
                ("production_build_manifest_identity", self.production_manifest_identity),
                ("source_commit", self.source_commit), ("source_tree", self.source_tree),
            ):
                if row[key] != expected_value:
                    raise RTA4FormalLifecycleV2Error(f"V2 resume {key} drift")
            observed[identity] = row
        return observed

    def write_result(self, row: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = validate_result_row_v2(row)
        if normalized["execution_identity"] not in {
            item["execution_identity"] for item in self.run_manifest["plan_records"]
        }:
            raise RTA4FormalLifecycleV2Error("V2 result is outside the plan")
        path = self.terminals / f"{normalized['execution_identity']}.json"
        payload = (canonical_json(normalized) + "\n").encode("utf-8")
        if path.is_file():
            if path.read_bytes() != payload:
                raise RTA4FormalLifecycleV2Error("V2 duplicate result conflict")
        else:
            atomic_write_text(path, payload.decode("utf-8"))
        return normalized

    def write_checkpoint(self, completed_execution_ids: Iterable[str]) -> Mapping[str, Any]:
        completed = sorted(set(completed_execution_ids))
        material = {
            "checkpoint_schema": "ASAP_BLOCK_V9_3_RTA4_CHECKPOINT_V2_SHARED_ENERGY",
            "run_identity": self.run_manifest["run_identity"],
            "authorization_id": self.authorization["authorization_id"],
            "plan_identity": self.prepared["plan_identity"],
            "production_build_manifest_identity": self.production_manifest_identity,
            "completed_execution_ids": completed,
            "complete": len(completed) == len(self.run_manifest["plan_records"]),
        }
        checkpoint = {
            **material,
            "checkpoint_identity": domain_hash("ASAP_BLOCK:V9.3:RTA4_CHECKPOINT:v2", material),
        }
        atomic_write_json(self.root / RTA4_CHECKPOINT_V2, checkpoint)
        return checkpoint


__all__ = [
    "RTA4_CHECKPOINT_V2", "RTA4FormalLifecycleV2Error",
    "RTA4FormalResultWriterV2", "RTA4FormalTasksetStoreV2",
    "RTA4_PREPARED_CONFIG_SCHEMA_V2", "RTA4_RESULT_DIRECTORY_V2",
    "RTA4_RESULT_ROW_SCHEMA_V2", "RTA4_RUN_MANIFEST_V2",
    "RTA4_TASKSET_STORE_MANIFEST_V2", "RTA4_TEST_AUTHORIZATION_SCHEMA_V2",
    "RTA4_TEST_PARAMETER_STATUS_V2", "build_test_authorization_v2",
    "build_test_prepared_config_v2", "retry_resume_identity_v2",
    "validate_prepared_config_v2", "validate_result_row_v2",
    "validate_test_authorization_v2",
]
