"""Authorized parameterized RTA4 V3 execution, storage, and resume."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence
import hashlib
import pickle
import signal
import threading
import time

from . import exact_energy
from .result_writer import atomic_write_json, atomic_write_text
from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_config_v2 import default_rta4_formal_config_v2
from .rta4_formal_config_v3 import RTA4_FORMAL_PROFILE_V3
from .rta4_formal_environment import load_strict_json
from .rta4_formal_execution import (
    ProductionRTAExecutorV2, ProductionSimulationExecutorV2,
    RTA4ExecutionError,
)
from .rta4_formal_lifecycle_v3 import (
    RTA4_CHECKPOINT_SCHEMA_V3, validate_authorization_v3,
    validate_checkpoint_v3, validate_prepared_config_v3,
)
from .rta4_formal_plan_v3 import iter_formal_plan_v3
from .rta4_formal_schema_v3 import formal_schema_hash_v3
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_physical_core_slots_v3 import (
    PHYSICAL_CORE_EXECUTION_BACKEND_V3,
    PhysicalCoreSlotPoolV3,
    PhysicalCoreSlotV3Error,
    SlotCompletionV3,
    SlotStartedV3,
    SlotTimeoutV3,
    SlotWorkerExitV3,
    discover_cpu_topology_v3,
)
from .rta4_production_build_manifest_v3 import (
    PRODUCTION_BUILD_MANIFEST_SCHEMA_V3, PRODUCTION_BUILD_PROFILE_V3,
    load_production_build_manifest_v3,
)
from .rta4_shared_energy import (
    FrozenMapping, SharedEnergyRunContext, TaskEnergyMaterial,
    _initialize_shared_energy_run,
    construct_shared_solar_input,
)
from .rta4_taskset_v2 import (
    ProductionTasksetProviderV2, TasksetIdentityCertificateV2,
)


RTA4_TASKSET_STORE_MANIFEST_V3 = "formal_taskset_store_manifest_v3.json"
RTA4_TASKSET_STORE_ENTRY_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4:TASKSET_STORE_ENTRY:v3"
RTA4_RESULT_ROW_SCHEMA_V3 = "ASAP_BLOCK_V9_3_RTA4_RESULT_ROW_V3_PARAMETERIZED"
RTA4_RESULT_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4:RESULT:v3"
RTA4_MATHEMATICAL_RESULT_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4:MATH_RESULT:v3"
RTA4_RETRY_RESUME_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4:RETRY_RESUME:v3"
RTA4_RUN_MANIFEST_V3 = "formal_run_manifest_v3.json"
RTA4_RUN_MANIFEST_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4:RUN_MANIFEST:v3"
RTA4_CHECKPOINT_V3 = "formal_checkpoint_v3.json"
RTA4_CHECKPOINT_DOMAIN_V3 = (
    "ASAP_BLOCK:V9.3:RTA4_CHECKPOINT:v3-physical-core-slots-r1"
)
RTA4_RESULT_DIRECTORY_V3 = "formal_terminal_results_v3"


def _initialize_shared_energy_run_v3(
    records: Sequence[Any], *, taskset_provider: Any,
    production_build_manifest_path: Path | str,
    system_config_path: Path | str, energy_support_path: Path | str,
    source_root: Path | str, taskset_store_identity: str,
) -> Any:
    """Live-check the V3 manifest before entering the shared material core."""

    if isinstance(production_build_manifest_path, Mapping):
        raise RTA4FormalRunnerV3Error(
            "formal V3 run-init accepts only a manifest file path"
        )
    manifest = load_production_build_manifest_v3(
        production_build_manifest_path, live=True,
    )
    return _initialize_shared_energy_run(
        records, taskset_provider=taskset_provider,
        production_build_manifest=manifest,
        system_config_path=system_config_path,
        energy_support_path=energy_support_path,
        source_root=source_root,
        taskset_store_identity=taskset_store_identity,
        service_constructor=construct_shared_solar_input,
        formal_ready=True,
        expected_manifest_schema=PRODUCTION_BUILD_MANIFEST_SCHEMA_V3,
        shared_profile_id=PRODUCTION_BUILD_PROFILE_V3,
    )


class RTA4FormalRunnerV3Error(RTA4ExecutionError):
    """Raised before a V3 identity boundary can be crossed."""


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _sha(value: Any, label: str) -> str:
    if (
        type(value) is not str or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RTA4FormalRunnerV3Error(f"{label} must be a lowercase SHA-256")
    return value


def _consistent_write(path: Path, payload: bytes) -> None:
    if path.is_file():
        if path.read_bytes() != payload:
            raise RTA4FormalRunnerV3Error(f"V3 content conflict: {path.name}")
    else:
        atomic_write_text(path, payload.decode("utf-8"))


def _store_marker(
    prepared: Mapping[str, Any], manifest_identity: str,
) -> Dict[str, Any]:
    scientific = prepared["normalized_scientific_config"]
    material = {
        "store_schema": "ASAP_BLOCK_V9_3_RTA4_TASKSET_STORE_V3_PARAMETERIZED",
        "profile": RTA4_FORMAL_PROFILE_V3,
        "store_identity": prepared["taskset_store_identity"],
        "campaign_id": scientific["campaign_id"],
        "core": scientific["core"],
        "normalized_scientific_config_sha256": prepared[
            "normalized_scientific_config_sha256"
        ],
        "plan_sha256": prepared["plan_sha256"],
        "production_build_manifest_identity": manifest_identity,
        "source_binding": prepared["source_binding"],
        "certificate_schema": "ASAP_BLOCK_V9_3_RTA4_W_FREE_TASKSET_CERTIFICATE_V2",
        "task_energy_schema": "ASAP_BLOCK_V9_3_RTA4_TASK_ENERGY_MATERIAL_V2",
        "legacy_store_accepted": False,
    }
    return {
        **material,
        "store_manifest_id": domain_hash(
            "ASAP_BLOCK:V9.3:RTA4:TASKSET_STORE_MANIFEST:v3", material,
        ),
    }


class RTA4FormalTasksetStoreV3:
    """Campaign-owned atomic V2-certificate/V3-binding store."""

    def __init__(
        self, root: Path | str, *, prepared_config: Mapping[str, Any],
        production_manifest_identity: str,
        require_existing_namespace: bool = False,
    ) -> None:
        self.prepared = validate_prepared_config_v3(prepared_config)
        self.root = Path(root)
        if self.root.resolve() != Path(
            self.prepared["operational"]["taskset_store"]
        ).resolve():
            raise RTA4FormalRunnerV3Error("V3 taskset store path drift")
        self.certificates = self.root / "certificates_v3"
        self.task_energy = self.root / "task_energy_materials_v3"
        self.bindings = self.root / "slot_bindings_v3"
        self.source_index = self.root / "source_index_v3"
        self.marker_path = self.root / RTA4_TASKSET_STORE_MANIFEST_V3
        expected = _store_marker(self.prepared, production_manifest_identity)
        if self.root.exists() and any(self.root.iterdir()) and not self.marker_path.is_file():
            raise RTA4FormalRunnerV3Error(
                "V3 store refuses a V1/V2/unknown namespace"
            )
        if self.marker_path.is_file() and load_strict_json(self.marker_path) != expected:
            raise RTA4FormalRunnerV3Error(
                "taskset store belongs to another campaign"
            )
        directories = (
            self.certificates, self.task_energy, self.bindings, self.source_index,
        )
        if require_existing_namespace and (
            not self.marker_path.is_file()
            or any(not path.is_dir() for path in directories)
        ):
            raise RTA4FormalRunnerV3Error("V3 resume store namespace is incomplete")
        for path in directories:
            path.mkdir(parents=True, exist_ok=True)
        if not self.marker_path.is_file():
            atomic_write_json(self.marker_path, expected)
        self.marker = expected
        self.production_manifest_identity = production_manifest_identity

    def _binding_path(self, taskset_slot_id: str) -> Path:
        return self.bindings / f"{_sha(taskset_slot_id, 'taskset slot')}.json"

    def load_for_record(self, record: Any) -> TasksetIdentityCertificateV2 | None:
        path = self._binding_path(str(record.taskset_slot_id))
        if not path.is_file():
            return None
        entry = load_strict_json(path)
        unsigned = dict(entry)
        observed = unsigned.pop("store_entry_identity", None)
        if (
            entry.get("taskset_slot_id") != record.taskset_slot_id
            or entry.get("store_identity") != self.prepared["taskset_store_identity"]
            or entry.get("plan_sha256") != self.prepared["plan_sha256"]
            or observed != domain_hash(RTA4_TASKSET_STORE_ENTRY_DOMAIN_V3, unsigned)
        ):
            raise RTA4FormalRunnerV3Error("V3 store slot binding drift")
        certificate = self.load_certificate(str(entry.get("taskset_identity", "")))
        certificate_path = self.certificates / f"{certificate.taskset_id}.json"
        energy_path = self.task_energy / (
            f"{entry.get('task_energy_material_identity', '')}.json"
        )
        try:
            certificate_sha = hashlib.sha256(certificate_path.read_bytes()).hexdigest()
            energy_sha = hashlib.sha256(energy_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise RTA4FormalRunnerV3Error("V3 store material is incomplete") from exc
        if (
            certificate_sha != entry.get("certificate_sha256")
            or energy_sha != entry.get("task_energy_sha256")
            or certificate.generation_request_id
            != entry.get("generation_request_identity")
        ):
            raise RTA4FormalRunnerV3Error("V3 store material content drift")
        return certificate

    def load_certificate(self, taskset_identity: str) -> TasksetIdentityCertificateV2:
        try:
            certificate = TasksetIdentityCertificateV2.from_canonical_bytes(
                (self.certificates / f"{_sha(taskset_identity, 'taskset identity')}.json")
                .read_bytes()
            )
        except Exception as exc:
            raise RTA4FormalRunnerV3Error(
                "cannot load V3 taskset certificate"
            ) from exc
        if certificate.taskset_id != taskset_identity:
            raise RTA4FormalRunnerV3Error("V3 certificate filename drift")
        return certificate

    def put(
        self, record: Any, certificate: TasksetIdentityCertificateV2,
        task_energy: TaskEnergyMaterial, *, source_index: int | None,
    ) -> Mapping[str, Any]:
        if type(certificate) is not TasksetIdentityCertificateV2:
            raise RTA4FormalRunnerV3Error("V3 store requires a V2 W-free certificate")
        if type(task_energy) is not TaskEnergyMaterial:
            raise RTA4FormalRunnerV3Error("V3 store requires shared task-energy material")
        certificate.validate()
        if (
            task_energy.taskset_id != certificate.taskset_id
            or task_energy.taskset_store_identity != self.prepared["taskset_store_identity"]
            or task_energy.production_build_manifest_identity
            != self.production_manifest_identity
        ):
            raise RTA4FormalRunnerV3Error("V3 store material binding mismatch")
        cert_payload = certificate.canonical_bytes()
        energy_payload = canonical_json(task_energy.material()).encode("utf-8")
        _consistent_write(
            self.certificates / f"{certificate.taskset_id}.json", cert_payload,
        )
        _consistent_write(
            self.task_energy
            / f"{task_energy.task_energy_material_identity}.json",
            energy_payload,
        )
        taskset_material = {
            key: _plain(record.material[key])
            for key in (
                "power_scale", "deadline_variant", "normalized_utilization",
                "replicate_index", "processor_count", "task_count",
            )
            if key in record.material
        }
        base = {
            "store_identity": self.prepared["taskset_store_identity"],
            "plan_sha256": self.prepared["plan_sha256"],
            "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
            "taskset_slot_id": record.taskset_slot_id,
            "generation_request_identity": certificate.generation_request_id,
            "taskset_skeleton_identity": certificate.taskset_skeleton_id,
            "taskset_identity": certificate.taskset_id,
            "taskset_source_sha256": certificate.taskset_source_sha256,
            "certificate_sha256": hashlib.sha256(cert_payload).hexdigest(),
            "task_energy_material_identity": task_energy.task_energy_material_identity,
            "task_energy_sha256": hashlib.sha256(energy_payload).hexdigest(),
            "source_request_material": taskset_material,
        }
        entry = {
            **base,
            "store_entry_identity": domain_hash(
                RTA4_TASKSET_STORE_ENTRY_DOMAIN_V3, base,
            ),
        }
        encoded = (canonical_json(entry) + "\n").encode("utf-8")
        _consistent_write(self._binding_path(record.taskset_slot_id), encoded)
        if source_index is not None:
            source_material = dict(taskset_material)
            if self.prepared["normalized_scientific_config"]["core"] == "CORE-4":
                source_material.update({
                    key: _plain(record.material[key])
                    for key in ("exact_e0", "service_scale")
                    if key in record.material
                })
            source_base = {
                **base, "source_index": source_index,
                "source_request_material": source_material,
            }
            source_entry = {
                **source_base,
                "store_entry_identity": domain_hash(
                    RTA4_TASKSET_STORE_ENTRY_DOMAIN_V3, source_base,
                ),
            }
            _consistent_write(
                self.source_index / f"{source_index:08d}.json",
                (canonical_json(source_entry) + "\n").encode("utf-8"),
            )
        return entry


class SourceTasksetReaderV3:
    """Read-only, source-campaign-bound view of a completed V3 store."""

    def __init__(self, root: Path | str, source_binding: Mapping[str, Any]) -> None:
        self.root = Path(root)
        marker_path = self.root / RTA4_TASKSET_STORE_MANIFEST_V3
        try:
            marker = load_strict_json(marker_path)
        except Exception as exc:
            raise RTA4FormalRunnerV3Error("source V3 store marker is absent") from exc
        expected = {
            "core": source_binding["core"],
            "normalized_scientific_config_sha256": source_binding[
                "source_campaign_config_sha256"
            ],
            "plan_sha256": source_binding["source_plan_sha256"],
            "store_identity": source_binding["source_taskset_store_identity"],
        }
        if any(marker.get(key) != value for key, value in expected.items()):
            raise RTA4FormalRunnerV3Error("source campaign/store binding mismatch")
        self.marker = marker
        self.certificates = self.root / "certificates_v3"
        self.source_index = self.root / "source_index_v3"
        if not self.certificates.is_dir() or not self.source_index.is_dir():
            raise RTA4FormalRunnerV3Error("source V3 store is incomplete")

    def _entry(self, index: int) -> Mapping[str, Any]:
        if type(index) is not int or index < 0:
            raise RTA4FormalRunnerV3Error("source taskset index is invalid")
        try:
            return load_strict_json(self.source_index / f"{index:08d}.json")
        except Exception as exc:
            raise RTA4FormalRunnerV3Error("source taskset index is absent") from exc

    def by_index(
        self, index: int,
    ) -> tuple[TasksetIdentityCertificateV2, Mapping[str, Any]]:
        return self._load(self._entry(index))

    def by_coordinate(
        self, utilization: str, candidate: int,
    ) -> tuple[TasksetIdentityCertificateV2, Mapping[str, Any]]:
        matches = []
        for path in sorted(self.source_index.glob("*.json")):
            entry = load_strict_json(path)
            material = entry.get("source_request_material", {})
            if material.get("normalized_utilization") == utilization:
                matches.append(entry)
        if candidate < 0 or candidate >= len(matches):
            raise RTA4FormalRunnerV3Error("source stratum candidate is absent")
        return self._load(matches[candidate])

    def _load(
        self, entry: Mapping[str, Any],
    ) -> tuple[TasksetIdentityCertificateV2, Mapping[str, Any]]:
        identity = _sha(entry.get("taskset_identity"), "source taskset identity")
        try:
            certificate = TasksetIdentityCertificateV2.from_canonical_bytes(
                (self.certificates / f"{identity}.json").read_bytes()
            )
        except Exception as exc:
            raise RTA4FormalRunnerV3Error("source certificate is invalid") from exc
        if certificate.taskset_id != identity:
            raise RTA4FormalRunnerV3Error("source certificate identity drift")
        return certificate, entry


@dataclass(frozen=True)
class _ExecutionRecord:
    kind: str
    core: str
    ordinal: int
    mathematical_request_id: str
    execution_id: str
    taskset_skeleton_slot_id: str
    taskset_slot_id: str
    material: Mapping[str, Any]
    record_id: str


class V3ProductionTasksetProvider:
    """Reuse the official V2 generator or a bound upstream V3 certificate."""

    def __init__(
        self, scientific_config: Mapping[str, Any], *,
        store: RTA4FormalTasksetStoreV3,
        source_store: Path | str | None,
    ) -> None:
        self.scientific = scientific_config
        self.store = store
        self.core = str(scientific_config["core"])
        self.generated = (
            ProductionTasksetProviderV2(default_rta4_formal_config_v2(self.core))
            if self.core in {"CORE-1", "CORE-4", "CORE-5A"} else None
        )
        source = scientific_config.get("source")
        self.source = (
            None if source is None else SourceTasksetReaderV3(source_store, source)
        )
        self._cache: Dict[str, TasksetIdentityCertificateV2] = {}
        self._source_entries: Dict[str, Mapping[str, Any]] = {}

    def _source_certificate(
        self, record: Any,
    ) -> tuple[TasksetIdentityCertificateV2, Mapping[str, Any]]:
        if self.source is None:
            raise RTA4FormalRunnerV3Error("record has no bound source store")
        if self.core == "CORE-5B":
            return self.source.by_coordinate(
                str(record.material["utilization_stratum"]),
                int(record.material["candidate_index"]),
            )
        return self.source.by_index(int(record.material["source_taskset_index"]))

    def resolve_record(self, record: Any) -> Any:
        if self.core != "CORE-5B":
            return record
        certificate, entry = self._source_certificate(record)
        self._cache[record.taskset_slot_id] = certificate
        self._source_entries[record.taskset_slot_id] = entry
        material = {**dict(entry["source_request_material"]), **dict(record.material)}
        if "exact_e0" not in material:
            raise RTA4FormalRunnerV3Error("CORE-5B source has no exact E0 binding")
        return _ExecutionRecord(
            record.kind, record.core, record.ordinal,
            record.mathematical_request_id, record.execution_id,
            record.taskset_skeleton_slot_id, record.taskset_slot_id,
            material, record.record_id,
        )

    def __call__(self, record: Any) -> TasksetIdentityCertificateV2:
        slot = str(record.taskset_slot_id)
        if slot in self._cache:
            return self._cache[slot]
        frozen = self.store.load_for_record(record)
        if frozen is not None:
            self._cache[slot] = frozen
            return frozen
        if self.generated is not None:
            certificate = self.generated(record)
        else:
            certificate, entry = self._source_certificate(record)
            self._source_entries[slot] = entry
        self._cache[slot] = certificate
        return certificate

    def workloads_for(
        self, record: Any, certificate: TasksetIdentityCertificateV2,
    ) -> tuple[str, ...]:
        if self.generated is not None:
            return self.generated.workloads_for(record, certificate)
        return tuple(task.workload for task in certificate.tasks)


class RTA4FormalResultWriterV3:
    """Atomic terminal writer whose namespace is the complete V3 plan."""

    def __init__(
        self, root: Path | str, *, prepared_config: Mapping[str, Any],
        authorization: Mapping[str, Any], production_manifest: Mapping[str, Any],
        records: Sequence[Any], require_existing_namespace: bool = False,
    ) -> None:
        self.prepared = validate_prepared_config_v3(prepared_config)
        self.authorization = validate_authorization_v3(
            authorization, prepared_config=self.prepared,
        )
        manifest_identity = _sha(
            production_manifest.get("manifest_id"), "production manifest identity",
        )
        repository = production_manifest.get("repository")
        if not isinstance(repository, Mapping):
            raise RTA4FormalRunnerV3Error("V3 manifest repository binding is absent")
        self.source_commit = str(repository.get("git_commit", ""))
        self.source_tree = str(repository.get("git_tree", ""))
        if len(self.source_commit) not in {40, 64} or len(self.source_tree) not in {40, 64}:
            raise RTA4FormalRunnerV3Error("V3 manifest Git identity is invalid")
        plan_rows = [{
            "ordinal": record.ordinal,
            "plan_record_identity": record.record_id,
            "mathematical_request_identity": record.mathematical_request_id,
            "execution_identity": record.execution_id,
            "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
            "taskset_slot_id": record.taskset_slot_id,
            "kind": record.kind,
        } for record in records]
        material = {
            "run_schema": "ASAP_BLOCK_V9_3_RTA4_RUN_MANIFEST_V3_PARAMETERIZED",
            "profile": RTA4_FORMAL_PROFILE_V3,
            "execution_class": "FORMAL_AUTHORIZED",
            "formal_authorization": True,
            "prepared_config_id": self.prepared["prepared_config_id"],
            "authorization_id": self.authorization["authorization_id"],
            "campaign_file_sha256": self.prepared["campaign_file"][
                "raw_campaign_file_sha256"
            ],
            "normalized_scientific_config_sha256": self.prepared[
                "normalized_scientific_config_sha256"
            ],
            "plan_sha256": self.prepared["plan_sha256"],
            "ordered_stream_digest": self.prepared["ordered_stream_digest"],
            "production_build_manifest_identity": manifest_identity,
            "source_commit": self.source_commit,
            "source_tree": self.source_tree,
            "taskset_store_identity": self.prepared["taskset_store_identity"],
            "source_binding": self.prepared["source_binding"],
            "output_root": str(Path(root).resolve()),
            "plan_records": plan_rows,
        }
        self.run_manifest = {
            **material,
            "run_identity": domain_hash(RTA4_RUN_MANIFEST_DOMAIN_V3, material),
        }
        self.root = Path(root)
        if self.root.resolve() != Path(
            self.prepared["operational"]["output_root"]
        ).resolve():
            raise RTA4FormalRunnerV3Error("V3 output root path drift")
        self.terminals = self.root / RTA4_RESULT_DIRECTORY_V3
        self.marker = self.root / RTA4_RUN_MANIFEST_V3
        if self.root.exists() and any(self.root.iterdir()) and not self.marker.is_file():
            raise RTA4FormalRunnerV3Error(
                "V3 writer refuses a V1/V2/unknown output root"
            )
        if require_existing_namespace and (
            not self.marker.is_file() or not self.terminals.is_dir()
        ):
            raise RTA4FormalRunnerV3Error("V3 resume namespace is incomplete")
        if self.marker.is_file() and load_strict_json(self.marker) != self.run_manifest:
            raise RTA4FormalRunnerV3Error("output root belongs to another campaign")
        self.terminals.mkdir(parents=True, exist_ok=True)
        if not self.marker.is_file():
            atomic_write_json(self.marker, self.run_manifest)
        self.production_manifest_identity = manifest_identity
        self._plan = {row["execution_identity"]: row for row in plan_rows}

    def _validate_row(self, value: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, Mapping):
            raise RTA4FormalRunnerV3Error("V3 result row must be a mapping")
        row = dict(value)
        observed = row.pop("result_identity", None)
        if (
            value.get("row_schema") != RTA4_RESULT_ROW_SCHEMA_V3
            or value.get("profile") != RTA4_FORMAL_PROFILE_V3
            or observed != domain_hash(RTA4_RESULT_DOMAIN_V3, row)
        ):
            raise RTA4FormalRunnerV3Error("V3 result schema/identity mismatch")
        execution = str(value.get("execution_identity", ""))
        expected = self._plan.get(execution)
        if expected is None or value.get("plan_record_identity") != expected[
            "plan_record_identity"
        ]:
            raise RTA4FormalRunnerV3Error("V3 result lies outside the plan")
        for key, expected_value in (
            ("prepared_config_id", self.prepared["prepared_config_id"]),
            ("authorization_id", self.authorization["authorization_id"]),
            ("plan_sha256", self.prepared["plan_sha256"]),
            ("production_build_manifest_identity", self.production_manifest_identity),
            ("source_commit", self.source_commit),
            ("source_tree", self.source_tree),
        ):
            if value.get(key) != expected_value:
                raise RTA4FormalRunnerV3Error(f"V3 result {key} drift")
        attempts = value.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise RTA4FormalRunnerV3Error("V3 result has no attempt history")
        return dict(value)

    def completed_rows(self) -> Mapping[str, Mapping[str, Any]]:
        observed: Dict[str, Mapping[str, Any]] = {}
        for path in sorted(self.terminals.glob("*.json")):
            row = self._validate_row(load_strict_json(path))
            identity = str(row["execution_identity"])
            if path.stem != identity or identity in observed:
                raise RTA4FormalRunnerV3Error("V3 terminal inventory conflict")
            observed[identity] = row
        checkpoint_path = self.root / RTA4_CHECKPOINT_V3
        if checkpoint_path.is_file():
            checkpoint = validate_checkpoint_v3(
                load_strict_json(checkpoint_path),
                prepared_config=self.prepared,
                authorization=self.authorization,
            )
            if checkpoint["run_identity"] != self.run_manifest["run_identity"]:
                raise RTA4FormalRunnerV3Error("V3 checkpoint run identity drift")
            checkpoint_ids = set(checkpoint["completed_execution_ids"])
            if not checkpoint_ids.issubset(observed):
                raise RTA4FormalRunnerV3Error(
                    "V3 checkpoint references a missing terminal"
                )
        return observed

    def write_result(self, row: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = self._validate_row(row)
        path = self.terminals / f"{normalized['execution_identity']}.json"
        payload = (canonical_json(normalized) + "\n").encode("utf-8")
        _consistent_write(path, payload)
        return normalized

    def write_checkpoint(
        self, completed_execution_ids: Iterable[str],
    ) -> Mapping[str, Any]:
        completed = sorted(set(completed_execution_ids))
        if not set(completed).issubset(self._plan):
            raise RTA4FormalRunnerV3Error("V3 checkpoint contains an unknown result")
        material = {
            "checkpoint_schema": RTA4_CHECKPOINT_SCHEMA_V3,
            "prepared_config_id": self.prepared["prepared_config_id"],
            "authorization_id": self.authorization["authorization_id"],
            "plan_sha256": self.prepared["plan_sha256"],
            "run_identity": self.run_manifest["run_identity"],
            "production_build_manifest_identity": self.production_manifest_identity,
            "ordered_stream_count": self.prepared["ordered_stream_count"],
            "completed_execution_ids": completed,
            "complete": len(completed) == self.prepared["ordered_stream_count"],
        }
        checkpoint = {
            **material,
            "checkpoint_id": domain_hash(RTA4_CHECKPOINT_DOMAIN_V3, material),
        }
        validate_checkpoint_v3(
            checkpoint, prepared_config=self.prepared,
            authorization=self.authorization,
        )
        atomic_write_json(self.root / RTA4_CHECKPOINT_V3, checkpoint)
        return checkpoint


@dataclass(frozen=True)
class ExecutionSummaryV3:
    core: str
    execution_class: str
    authorization_id: str
    production_build_manifest_identity: str
    processed_records: int
    pending_records: int
    complete: bool
    checkpoint_path: Path
    execution_backend: str = PHYSICAL_CORE_EXECUTION_BACKEND_V3
    physical_core_binding_required: bool = True
    requested_physical_worker_count: int = 0
    available_physical_core_count: int = 0
    selected_physical_cores: tuple[Mapping[str, Any], ...] = ()
    worker_process_ids: tuple[int, ...] = ()
    worker_intervals_ns: tuple[tuple[int, int, int], ...] = ()
    worker_affinity_bindings: tuple[Mapping[str, Any], ...] = ()
    worker_intervals: tuple[Mapping[str, Any], ...] = ()
    max_concurrent_active_slots: int = 0
    mean_concurrent_active_slots: float = 0.0
    slot_replacement_count: int = 0
    timeout_kill_count: int = 0
    checkpoint_write_count: int = 0
    terminal_write_count: int = 0


class _CheckpointThrottleV3:
    """Bound full checkpoint rewrites while terminals remain immediate."""

    def __init__(
        self, writer: RTA4FormalResultWriterV3,
        completed: Mapping[str, Mapping[str, Any]], *,
        every_records: int, every_seconds: int,
    ) -> None:
        self.writer = writer
        self.completed = completed
        self.every_records = every_records
        self.every_seconds = every_seconds
        self.records_since_write = 0
        self.last_write = time.monotonic()
        self.write_count = 0
        self.last_checkpoint: Mapping[str, Any] | None = None

    def terminal_committed(self) -> None:
        self.records_since_write += 1

    def write_if_due(self, *, force: bool = False) -> Mapping[str, Any] | None:
        due = (
            force
            or self.records_since_write >= self.every_records
            or time.monotonic() - self.last_write >= self.every_seconds
        )
        if not due:
            return None
        self.last_checkpoint = self.writer.write_checkpoint(self.completed)
        self.write_count += 1
        self.records_since_write = 0
        self.last_write = time.monotonic()
        return self.last_checkpoint


def _slot_concurrency(
    intervals: Sequence[Mapping[str, Any]],
) -> tuple[int, float]:
    pairs = [(
        int(row["attempt_started_monotonic_ns"]),
        int(row["attempt_finished_monotonic_ns"]),
    ) for row in intervals]
    if not pairs:
        return 0, 0.0
    events: list[tuple[int, int]] = []
    for start, finish in pairs:
        events.extend(((start, 1), (finish, -1)))
    events.sort(key=lambda row: (row[0], row[1]))
    first = min(row[0] for row in pairs)
    last = max(row[1] for row in pairs)
    active = maximum = area = 0
    previous = first
    for instant, delta in events:
        area += active * max(0, instant - previous)
        active += delta
        maximum = max(maximum, active)
        previous = instant
    return maximum, (0.0 if last <= first else area / (last - first))


class AuthorizedRTA4RunnerV3:
    """Fail-closed formal runner for a finite, externally configured V3 plan."""

    def __init__(
        self, prepared_config: Mapping[str, Any], authorization: Mapping[str, Any],
        *, worker_observer: Callable[[str, str, int], None] | None = None,
        _manifest_loader: Callable[..., Mapping[str, Any]] | None = None,
        _provider_factory: Callable[..., Any] | None = None,
        _context_factory: Callable[..., Any] | None = None,
        _rta_executor_factory: Callable[..., Any] | None = None,
        _simulation_executor_factory: Callable[..., Any] | None = None,
        _test_worker_backend: str | None = None,
    ) -> None:
        self.prepared = validate_prepared_config_v3(prepared_config)
        self.authorization = validate_authorization_v3(
            authorization, prepared_config=self.prepared,
        )
        self.scientific = self.prepared["normalized_scientific_config"]
        self.worker_observer = worker_observer
        self._manifest_loader = _manifest_loader or load_production_build_manifest_v3
        self._provider_factory = _provider_factory or V3ProductionTasksetProvider
        self._context_factory = (
            _context_factory or _initialize_shared_energy_run_v3
        )
        self._rta_factory = _rta_executor_factory or ProductionRTAExecutorV2
        self._simulation_factory = (
            _simulation_executor_factory or ProductionSimulationExecutorV2
        )
        injected_test_components = any(value is not None for value in (
            _manifest_loader,
            _provider_factory,
            _context_factory,
            _rta_executor_factory,
            _simulation_executor_factory,
        ))
        if _test_worker_backend is not None:
            if _test_worker_backend != "thread" or not injected_test_components:
                raise RTA4FormalRunnerV3Error(
                    "thread workers are test-only and forbidden in production"
                )
        self._test_worker_backend = _test_worker_backend

    def _timeout_contract(self, records: Sequence[Any]) -> Dict[str, Dict[str, int]]:
        timeout = self.prepared["operational"]["timeout_seconds"]
        methods = sorted({
            str(record.material["method"])
            for record in records if record.kind != "simulation"
        })
        return {
            method: {
                "initial_timeout_seconds": timeout,
                "retry_timeout_seconds": timeout * 2,
                "maximum_attempts": 2,
            }
            for method in methods
        }

    @staticmethod
    def _source_indices(records: Sequence[Any], core: str) -> Mapping[str, int]:
        result: Dict[str, int] = {}
        if core not in {"CORE-1", "CORE-4"}:
            return result
        for record in records:
            if core == "CORE-4" and record.material.get("axis") != "baseline":
                continue
            result.setdefault(record.taskset_slot_id, len(result))
        return result

    def _row(
        self, *, writer: RTA4FormalResultWriterV3, record: Any,
        certificate: TasksetIdentityCertificateV2, binding: Mapping[str, str],
        service: Any, result: Mapping[str, Any], timeout_contract: Mapping[str, Any],
    ) -> Dict[str, Any]:
        simulation = record.kind == "simulation"
        if simulation:
            timed = result
            response = _plain(timed["result"])
            status = str(timed["status"])
            method = "CORE3_SIMULATION_V3"
            exact_e0 = str(record.material["physical_initial_energy"])
            attempts = [{
                "attempt_index": 0,
                "timeout_seconds": self.prepared["operational"]["timeout_seconds"],
                "status": status,
                "runtime_wall_seconds": str(timed["runtime_wall_seconds"]),
                "runtime_cpu_seconds": str(timed["runtime_cpu_seconds"]),
                "peak_rss_bytes": 0,
                "error_classification": str(timed["error_classification"]),
                "analysis_identity": response.get(
                    "simulation_identity",
                    domain_hash("ASAP_BLOCK:V9.3:RTA4:FAILED_SIMULATION:v3", {
                        "execution_identity": record.execution_id,
                    }),
                ),
                "taskset_identity": certificate.taskset_id,
                "task_energy_material_identity": binding[
                    "task_energy_material_identity"
                ],
                "service_material_identity": binding["service_material_identity"],
                "beta_material_identity": service.beta_material_identity,
                "production_build_manifest_identity": writer.production_manifest_identity,
            }]
        else:
            response = {
                key: _plain(value) for key, value in result.items()
                if key not in {
                    "attempts", "timeout_seconds", "runtime_wall_seconds",
                    "runtime_cpu_seconds", "peak_rss_bytes",
                }
            }
            status = str(result["solver_status"])
            method = str(record.material["method"])
            exact_e0 = str(record.material["exact_e0"])
            attempts = _plain(result["attempts"])
        timeout_sequence = [row["timeout_seconds"] for row in attempts]
        retry_material = {
            "prepared_config_id": self.prepared["prepared_config_id"],
            "authorization_id": self.authorization["authorization_id"],
            "plan_sha256": self.prepared["plan_sha256"],
            "plan_record_identity": record.record_id,
            "taskset_identity": certificate.taskset_id,
            "task_energy_material_identity": binding[
                "task_energy_material_identity"
            ],
            "service_material_identity": binding["service_material_identity"],
            "beta_material_identity": service.beta_material_identity,
            "method": method,
            "exact_e0": exact_e0,
            "timeout_sequence": timeout_sequence,
            "timeout_contract": _plain(timeout_contract),
        }
        mathematical_material = {
            "mathematical_request_identity": record.mathematical_request_id,
            "taskset_identity": certificate.taskset_id,
            "task_energy_material_identity": binding[
                "task_energy_material_identity"
            ],
            "service_material_identity": binding["service_material_identity"],
            "method": method,
            "exact_e0": exact_e0,
            "status": status,
            "response_result": response,
        }
        material = {
            "row_schema": RTA4_RESULT_ROW_SCHEMA_V3,
            "profile": RTA4_FORMAL_PROFILE_V3,
            "execution_class": "FORMAL_AUTHORIZED",
            "schema_sha256": formal_schema_hash_v3(),
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "prepared_config_id": self.prepared["prepared_config_id"],
            "authorization_id": self.authorization["authorization_id"],
            "normalized_scientific_config_sha256": self.prepared[
                "normalized_scientific_config_sha256"
            ],
            "plan_sha256": self.prepared["plan_sha256"],
            "plan_record_identity": record.record_id,
            "mathematical_request_identity": record.mathematical_request_id,
            "execution_identity": record.execution_id,
            "production_build_manifest_identity": writer.production_manifest_identity,
            "source_commit": writer.source_commit,
            "source_tree": writer.source_tree,
            "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
            "taskset_slot_id": record.taskset_slot_id,
            "generation_request_identity": certificate.generation_request_id,
            "taskset_source_sha256": certificate.taskset_source_sha256,
            "taskset_identity": certificate.taskset_id,
            "task_energy_material_identity": binding[
                "task_energy_material_identity"
            ],
            "service_material_identity": binding["service_material_identity"],
            "beta_material_identity": service.beta_material_identity,
            "method": method,
            "exact_e0": exact_e0,
            "status": status,
            "response_result": response,
            "attempts": attempts,
            "retry_resume_identity": domain_hash(
                RTA4_RETRY_RESUME_DOMAIN_V3, retry_material,
            ),
            "mathematical_result_identity": domain_hash(
                RTA4_MATHEMATICAL_RESULT_DOMAIN_V3, mathematical_material,
            ),
        }
        return {
            **material,
            "result_identity": domain_hash(RTA4_RESULT_DOMAIN_V3, material),
        }

    @staticmethod
    def _record_context(context: SharedEnergyRunContext, record: Any) -> Any:
        """Minimize each pickle payload to one immutable record binding."""

        if type(context) is not SharedEnergyRunContext:
            # Test doubles may deliberately provide another pickle-safe context.
            return context
        binding = context.binding_for(record.record_id)
        task_identity = str(binding["task_energy_material_identity"])
        service_identity = str(binding["service_material_identity"])
        return SharedEnergyRunContext(
            context.production_build_manifest_identity,
            FrozenMapping({
                task_identity: context.task_energy_materials[task_identity],
            }),
            FrozenMapping({
                service_identity: context.service_materials[service_identity],
            }),
            FrozenMapping({record.record_id: FrozenMapping(dict(binding))}),
            FrozenMapping({}),
            True,
        )

    @staticmethod
    def _infrastructure_result(
        *, record: Any, certificate: TasksetIdentityCertificateV2,
        context: Any, timeout_contract: Mapping[str, Any],
        classification: str,
    ) -> Mapping[str, Any]:
        """Canonical terminal material for pool/transport failures."""

        if record.kind == "simulation":
            return {
                "result": {"failure_reason": classification[:500]},
                "status": "INTERNAL_ERROR",
                "error_classification": classification[:500],
                "runtime_wall_seconds": "0",
                "runtime_cpu_seconds": "0",
            }
        binding = context.binding_for(record.record_id)
        task_energy = context.task_energy_materials[
            binding["task_energy_material_identity"]
        ]
        service = context.service_materials[
            binding["service_material_identity"]
        ]
        analysis_identity = domain_hash(
            "ASAP_BLOCK:V9.3:RTA4:PROCESS_FAILURE:v3",
            {
                "plan_record_identity": record.record_id,
                "taskset_identity": certificate.taskset_id,
                "classification": classification[:500],
            },
        )
        mapped = ProductionRTAExecutorV2._internal_result_v2(
            certificate,
            RTA4FormalRunnerV3Error(classification[:500]),
            task_energy=task_energy,
            service=service,
            analysis_identity=analysis_identity,
        )
        method_timeout = timeout_contract[str(record.material["method"])]
        attempt = {
            "attempt_index": 0,
            "timeout_seconds": method_timeout["initial_timeout_seconds"],
            "status": "INTERNAL_ERROR",
            "runtime_wall_seconds": "0",
            "runtime_cpu_seconds": "0",
            "peak_rss_bytes": 0,
            "error_classification": classification[:500],
            "analysis_identity": analysis_identity,
            "taskset_identity": certificate.taskset_id,
            "task_energy_material_identity": (
                task_energy.task_energy_material_identity
            ),
            "service_material_identity": service.service_material_identity,
            "beta_material_identity": service.beta_material_identity,
            "production_build_manifest_identity": (
                context.production_build_manifest_identity
            ),
        }
        return FrozenMapping({
            **dict(mapped),
            "attempts": (FrozenMapping(attempt),),
            "timeout_seconds": attempt["timeout_seconds"],
            "runtime_wall_seconds": "0",
            "runtime_cpu_seconds": "0",
            "peak_rss_bytes": 0,
        })

    def run(
        self, *, resume: bool = False, validate_only: bool = False,
        max_records: int | None = None,
    ) -> ExecutionSummaryV3:
        if type(resume) is not bool or type(validate_only) is not bool:
            raise RTA4FormalRunnerV3Error("V3 execution flags must be boolean")
        if resume and validate_only:
            raise RTA4FormalRunnerV3Error("resume and validate-only are exclusive")
        if max_records is not None and (
            type(max_records) is not int or max_records < 0
        ):
            raise RTA4FormalRunnerV3Error("V3 max_records must be non-negative")
        operation = self.prepared["operational"]
        manifest_path = self.prepared["production_manifest"]["absolute_path"]
        manifest = self._manifest_loader(manifest_path, live=True)
        if manifest.get("manifest_id") != self.prepared["production_manifest"][
            "production_build_manifest_identity"
        ]:
            raise RTA4FormalRunnerV3Error("V3 prepared/manifest identity drift")
        records = tuple(iter_formal_plan_v3(
            self.scientific,
            observed_source_binding=self.prepared["source_binding"],
        ))
        if len(records) != self.prepared["ordered_stream_count"]:
            raise RTA4FormalRunnerV3Error("V3 authorized record range drift")
        output = Path(operation["output_root"])
        store_root = Path(operation["taskset_store"])
        if validate_only:
            if self.prepared["source_binding"] is not None:
                SourceTasksetReaderV3(
                    operation["source_taskset_store"],
                    self.prepared["source_binding"],
                )
            if output.exists() and any(output.iterdir()):
                writer = RTA4FormalResultWriterV3(
                    output, prepared_config=self.prepared,
                    authorization=self.authorization,
                    production_manifest=manifest, records=records,
                    require_existing_namespace=True,
                )
                completed = writer.completed_rows()
            else:
                completed = {}
            if store_root.exists() and any(store_root.iterdir()):
                RTA4FormalTasksetStoreV3(
                    store_root, prepared_config=self.prepared,
                    production_manifest_identity=str(manifest["manifest_id"]),
                    require_existing_namespace=True,
                )
            return ExecutionSummaryV3(
                str(self.scientific["core"]), "FORMAL_AUTHORIZED",
                self.authorization["authorization_id"], str(manifest["manifest_id"]),
                0, len(records) - len(completed), len(completed) == len(records),
                output / RTA4_CHECKPOINT_V3,
            )
        if not resume and output.exists() and any(output.iterdir()):
            raise RTA4FormalRunnerV3Error(
                "non-resume V3 execution refuses a non-empty output root"
            )
        store = RTA4FormalTasksetStoreV3(
            store_root, prepared_config=self.prepared,
            production_manifest_identity=str(manifest["manifest_id"]),
            require_existing_namespace=resume,
        )
        writer = RTA4FormalResultWriterV3(
            output, prepared_config=self.prepared,
            authorization=self.authorization, production_manifest=manifest,
            records=records, require_existing_namespace=resume,
        )
        completed = dict(writer.completed_rows())
        resume_checkpoint_writes = 0
        if resume:
            # A terminal is the authoritative committed record.  If a crash
            # occurred between its atomic rename and the following checkpoint
            # rename, reconcile the parent-owned checkpoint before dispatch.
            writer.write_checkpoint(completed)
            resume_checkpoint_writes = 1
        by_execution = {record.execution_id: record for record in records}
        for execution_id, row in completed.items():
            frozen = store.load_for_record(by_execution[execution_id])
            if frozen is None or any(
                row.get(key) != expected for key, expected in (
                    ("taskset_identity", frozen.taskset_id),
                    ("generation_request_identity", frozen.generation_request_id),
                    ("taskset_source_sha256", frozen.taskset_source_sha256),
                )
            ):
                raise RTA4FormalRunnerV3Error(
                    "V3 resume completed taskset binding drift"
                )
        pending = [
            record for record in records if record.execution_id not in completed
        ]
        effective_limit = (
            operation["max_records"] if max_records is None else max_records
        )
        if effective_limit is not None:
            pending = pending[:effective_limit]
        if not pending:
            checkpoint = writer.write_checkpoint(completed)
            return ExecutionSummaryV3(
                str(self.scientific["core"]), "FORMAL_AUTHORIZED",
                self.authorization["authorization_id"], str(manifest["manifest_id"]),
                0, len(records) - len(completed), bool(checkpoint["complete"]),
                output / RTA4_CHECKPOINT_V3,
            )
        provider = self._provider_factory(
            self.scientific, store=store,
            source_store=operation.get("source_taskset_store"),
        )
        execution_records = tuple(provider.resolve_record(record) for record in pending)
        root = Path(str(manifest["repository"]["source_root"])).resolve(strict=True)
        context = self._context_factory(
            execution_records, taskset_provider=provider,
            production_build_manifest_path=manifest_path,
            system_config_path=root / "system_config_unified_template.yml",
            energy_support_path=root
            / "configs/v9_3_rta4_shared_energy_support_v2.yaml",
            source_root=root,
            taskset_store_identity=self.prepared["taskset_store_identity"],
        )
        if not context.formal_ready or context.production_build_manifest_identity != manifest[
            "manifest_id"
        ]:
            raise RTA4FormalRunnerV3Error("V3 shared-energy context is not formal-ready")
        certificates = {
            record.record_id: provider(record) for record in execution_records
        }
        source_indices = self._source_indices(records, str(self.scientific["core"]))
        for record in execution_records:
            certificate = certificates[record.record_id]
            binding = context.binding_for(record.record_id)
            task_energy = context.task_energy_materials[
                binding["task_energy_material_identity"]
            ]
            source_index = source_indices.get(record.taskset_slot_id)
            if (
                self.scientific["core"] == "CORE-4"
                and record.material.get("axis") != "baseline"
            ):
                source_index = None
            store.put(
                record, certificate, task_energy,
                source_index=source_index,
            )
        timeout_contract = self._timeout_contract(execution_records)
        v2_config = default_rta4_formal_config_v2(str(self.scientific["core"]))
        identity_contract = {
            "formal_profile": RTA4_FORMAL_PROFILE_V3,
            "analysis_domain": "ASAP_BLOCK:V9.3:RTA4:FORMAL_ANALYSIS:v3",
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "timeout_contract": timeout_contract,
        }
        from .rta4_formal_workers_v3 import (
            V3AttemptRequest, V3AttemptResponse, V3WorkerBootstrap,
            V3WorkerRequest, V3WorkerResponse, combine_attempt_results_v3,
            execute_worker_attempt_in_slot_v3, execute_worker_request_v3,
            project_hard_timeout_result_v3,
        )

        processed = 0
        workers = operation["worker_count"]
        max_in_flight = operation["max_in_flight"]
        backend = (
            "THREAD_POOL_TEST_ONLY"
            if self._test_worker_backend == "thread"
            else PHYSICAL_CORE_EXECUTION_BACKEND_V3
        )
        observed_worker_pids: set[int] = set()
        observed_worker_intervals: list[tuple[int, int, int]] = []
        checkpoint_throttle = _CheckpointThrottleV3(
            writer, completed,
            every_records=operation["checkpoint_every_records"],
            every_seconds=operation["checkpoint_every_seconds"],
        )
        checkpoint_throttle.write_count = resume_checkpoint_writes

        def persist(record: Any, result: Mapping[str, Any]) -> None:
            nonlocal processed
            certificate = certificates[record.record_id]
            binding = context.binding_for(record.record_id)
            service = context.service_materials[
                binding["service_material_identity"]
            ]
            row = self._row(
                writer=writer,
                record=record,
                certificate=certificate,
                binding=binding,
                service=service,
                result=result,
                timeout_contract=timeout_contract,
            )
            writer.write_result(row)
            completed[record.execution_id] = row
            processed += 1
            checkpoint_throttle.terminal_committed()
            checkpoint_throttle.write_if_due()

        def complete_thread_future(record: Any, future: Future[Any]) -> None:
            try:
                response = future.result()
                if (
                    type(response) is not V3WorkerResponse
                    or response.plan_record_identity != record.record_id
                    or response.execution_identity != record.execution_id
                    or response.worker_pid <= 0
                    or response.finished_monotonic_ns
                    < response.started_monotonic_ns
                ):
                    raise RTA4FormalRunnerV3Error(
                        "V3 test worker response identity drift"
                    )
                observed_worker_pids.add(response.worker_pid)
                observed_worker_intervals.append((
                    response.worker_pid, response.started_monotonic_ns,
                    response.finished_monotonic_ns,
                ))
                result = response.result
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                result = self._infrastructure_result(
                    record=record,
                    certificate=certificates[record.record_id],
                    context=context, timeout_contract=timeout_contract,
                    classification=(
                        f"THREAD_TEST_WORKER_FAILURE:{type(exc).__name__}:{exc}"
                    ),
                )
            persist(record, result)

        def thread_request(record: Any) -> V3WorkerRequest:
            return V3WorkerRequest(
                record=record, certificate=certificates[record.record_id],
                v2_config=v2_config,
                run_context=self._record_context(context, record),
                timeout_contract=timeout_contract,
                identity_contract=identity_contract,
                production_manifest=manifest,
                system_config_path=str(root / "system_config_unified_template.yml"),
                energy_support_path=str(
                    root / "configs/v9_3_rta4_shared_energy_support_v2.yaml"
                ),
                output_root=str(output),
                simulation_timeout_seconds=operation["timeout_seconds"],
                rta_executor_factory=self._rta_factory,
                simulation_executor_factory=self._simulation_factory,
            )

        topology = discover_cpu_topology_v3()
        selected_cores = topology.select(workers)
        if (
            operation.get("execution_backend")
            != PHYSICAL_CORE_EXECUTION_BACKEND_V3
            or operation.get("physical_core_binding_required") is not True
            or operation.get("topology_selection_policy")
            != topology.selection_policy
            or operation.get("topology_fingerprint")
            != topology.topology_fingerprint
            or operation.get("available_physical_core_count")
            != topology.physical_core_count
            or operation.get("selected_physical_cores")
            != [row.as_dict() for row in selected_cores]
            or operation.get("selected_logical_cpu_ids")
            != [row.logical_cpu_id for row in selected_cores]
        ):
            raise RTA4FormalRunnerV3Error(
                "V3 prepared physical CPU topology drift"
            )

        slot_pool: PhysicalCoreSlotPoolV3 | None = None
        previous_sigterm_handler: Any = None
        sigterm_handler_installed = False
        try:
            if threading.current_thread() is threading.main_thread():
                previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

                def request_graceful_termination(
                    signum: int, _frame: Any,
                ) -> None:
                    raise SystemExit(128 + signum)

                signal.signal(signal.SIGTERM, request_graceful_termination)
                sigterm_handler_installed = True
            if backend == "THREAD_POOL_TEST_ONLY":
                remaining = deque(execution_records)
                futures: Dict[Future[Any], Any] = {}
                with ThreadPoolExecutor(max_workers=workers) as thread_pool:
                    while remaining or futures:
                        while remaining and len(futures) < max_in_flight:
                            record = remaining.popleft()
                            futures[thread_pool.submit(
                                execute_worker_request_v3, thread_request(record),
                            )] = record
                        future = next(as_completed(tuple(futures)))
                        record = futures.pop(future)
                        complete_thread_future(record, future)
            else:
                bootstrap = V3WorkerBootstrap(
                    v2_config=v2_config,
                    timeout_contract=timeout_contract,
                    identity_contract=identity_contract,
                    production_manifest=manifest,
                    system_config_path=str(
                        root / "system_config_unified_template.yml"
                    ),
                    energy_support_path=str(
                        root / "configs/v9_3_rta4_shared_energy_support_v2.yaml"
                    ),
                    output_root=str(output),
                    simulation_timeout_seconds=operation["timeout_seconds"],
                    rta_executor_factory=self._rta_factory,
                    simulation_executor_factory=self._simulation_factory,
                )
                try:
                    pickle.dumps(bootstrap, protocol=pickle.HIGHEST_PROTOCOL)
                except Exception as exc:
                    raise RTA4FormalRunnerV3Error(
                        "V3 physical slot bootstrap is not serializable"
                    ) from exc
                slot_pool = PhysicalCoreSlotPoolV3(
                    selected_cores,
                    worker_callable=execute_worker_attempt_in_slot_v3,
                    worker_state=bootstrap,
                    start_method="spawn",
                )
                slot_pool.start()
                pending_attempts = deque((record, 0) for record in execution_records)
                active_attempts: Dict[str, tuple[Any, V3AttemptRequest]] = {}
                attempt_results: Dict[str, Dict[int, Mapping[str, Any]]] = {}

                def attempt_budget(record: Any, attempt_index: int) -> int:
                    if record.kind == "simulation":
                        return operation["timeout_seconds"]
                    contract = timeout_contract[str(record.material["method"])]
                    return (
                        contract["initial_timeout_seconds"]
                        if attempt_index == 0
                        else contract["retry_timeout_seconds"]
                    )

                def finish_attempt(
                    record: Any, request: V3AttemptRequest,
                    result: Mapping[str, Any],
                ) -> None:
                    if record.kind == "simulation":
                        persist(record, result)
                        return
                    history = attempt_results.setdefault(record.execution_id, {})
                    history[request.attempt_index] = result
                    status = str(result.get("solver_status"))
                    if status == "TIMEOUT" and request.attempt_index == 0:
                        pending_attempts.append((record, 1))
                    else:
                        persist(record, combine_attempt_results_v3(history))

                while pending_attempts or active_attempts:
                    for slot_id in slot_pool.idle_slot_ids:
                        if not pending_attempts:
                            break
                        record, attempt_index = pending_attempts.popleft()
                        budget = attempt_budget(record, attempt_index)
                        request = V3AttemptRequest(
                            record, certificates[record.record_id],
                            self._record_context(context, record),
                            attempt_index, budget,
                        )
                        task_id = f"{record.execution_id}:{attempt_index}"
                        slot_pool.submit(slot_id, task_id, request, budget)
                        active_attempts[task_id] = (record, request)
                    event = slot_pool.poll()
                    if event is None:
                        continue
                    if isinstance(event, SlotStartedV3):
                        record, _request = active_attempts[event.task_id]
                        observed_worker_pids.add(event.worker.worker_pid)
                        if self.worker_observer is not None:
                            self.worker_observer(
                                "start", record.record_id,
                                event.worker.worker_pid,
                            )
                        continue
                    if isinstance(event, SlotCompletionV3):
                        record, request = active_attempts.pop(event.task_id)
                        observed_worker_pids.add(event.worker.worker_pid)
                        observed_worker_intervals.append((
                            event.worker.worker_pid,
                            event.started_monotonic_ns,
                            event.finished_monotonic_ns,
                        ))
                        if self.worker_observer is not None:
                            self.worker_observer(
                                "finish", record.record_id,
                                event.worker.worker_pid,
                            )
                        if event.error_classification is not None:
                            persist(record, self._infrastructure_result(
                                record=record,
                                certificate=certificates[record.record_id],
                                context=context,
                                timeout_contract=timeout_contract,
                                classification=(
                                    "PHYSICAL_SLOT_ATTEMPT_FAILURE:"
                                    f"{event.error_classification}"
                                ),
                            ))
                            continue
                        response = event.result
                        if (
                            type(response) is not V3AttemptResponse
                            or response.plan_record_identity != record.record_id
                            or response.execution_identity != record.execution_id
                            or response.attempt_index != request.attempt_index
                            or response.timeout_seconds != request.timeout_seconds
                        ):
                            raise RTA4FormalRunnerV3Error(
                                "V3 physical slot response identity drift"
                            )
                        finish_attempt(record, request, response.result)
                        continue
                    if isinstance(event, SlotTimeoutV3):
                        record, request = active_attempts.pop(event.task_id)
                        slot_pool.worker_intervals.append({
                            **event.worker.as_dict(),
                            "task_id": event.task_id,
                            "attempt_started_monotonic_ns": (
                                event.started_monotonic_ns
                            ),
                            "attempt_finished_monotonic_ns": (
                                event.timed_out_monotonic_ns
                            ),
                            "timed_out": True,
                        })
                        observed_worker_intervals.append((
                            event.worker.worker_pid,
                            event.started_monotonic_ns,
                            event.timed_out_monotonic_ns,
                        ))
                        slot_pool.replace(event.slot_id, timeout_kill=True)
                        if record.kind == "simulation":
                            persist(record, self._infrastructure_result(
                                record=record,
                                certificate=certificates[record.record_id],
                                context=context,
                                timeout_contract=timeout_contract,
                                classification="SIMULATION_PHYSICAL_SLOT_TIMEOUT",
                            ))
                        else:
                            projected = project_hard_timeout_result_v3(
                                bootstrap, request,
                            )
                            finish_attempt(record, request, projected)
                        continue
                    if isinstance(event, SlotWorkerExitV3):
                        slot_pool.replace(event.slot_id)
                        if event.task_id is None:
                            continue
                        record, _request = active_attempts.pop(event.task_id)
                        persist(record, self._infrastructure_result(
                            record=record,
                            certificate=certificates[record.record_id],
                            context=context,
                            timeout_contract=timeout_contract,
                            classification=(
                                "PHYSICAL_SLOT_WORKER_EXIT:"
                                f"{event.exitcode}"
                            ),
                        ))
        except (KeyboardInterrupt, SystemExit):
            checkpoint_throttle.write_if_due(force=True)
            raise
        finally:
            try:
                if slot_pool is not None:
                    slot_pool.shutdown()
            finally:
                if sigterm_handler_installed:
                    signal.signal(signal.SIGTERM, previous_sigterm_handler)
        checkpoint = checkpoint_throttle.write_if_due(force=True)
        assert checkpoint is not None
        affinity_bindings = (
            () if slot_pool is None
            else tuple(slot_pool.worker_affinity_bindings)
        )
        detailed_intervals = (
            () if slot_pool is None else tuple(slot_pool.worker_intervals)
        )
        maximum, mean = _slot_concurrency(detailed_intervals)
        all_worker_pids = (
            observed_worker_pids
            if not affinity_bindings
            else {int(row["worker_pid"]) for row in affinity_bindings}
        )
        return ExecutionSummaryV3(
            core=str(self.scientific["core"]),
            execution_class="FORMAL_AUTHORIZED",
            authorization_id=self.authorization["authorization_id"],
            production_build_manifest_identity=str(manifest["manifest_id"]),
            processed_records=processed,
            pending_records=len(records) - len(completed),
            complete=bool(checkpoint["complete"]),
            checkpoint_path=output / RTA4_CHECKPOINT_V3,
            execution_backend=backend,
            physical_core_binding_required=(
                backend == PHYSICAL_CORE_EXECUTION_BACKEND_V3
            ),
            requested_physical_worker_count=workers,
            available_physical_core_count=topology.physical_core_count,
            selected_physical_cores=tuple(
                row.as_dict() for row in selected_cores
            ),
            worker_process_ids=tuple(sorted(all_worker_pids)),
            worker_intervals_ns=tuple(observed_worker_intervals),
            worker_affinity_bindings=affinity_bindings,
            worker_intervals=detailed_intervals,
            max_concurrent_active_slots=maximum,
            mean_concurrent_active_slots=mean,
            slot_replacement_count=(
                0 if slot_pool is None else slot_pool.slot_replacement_count
            ),
            timeout_kill_count=(
                0 if slot_pool is None else slot_pool.timeout_kill_count
            ),
            checkpoint_write_count=checkpoint_throttle.write_count,
            terminal_write_count=processed,
        )


__all__ = [
    "AuthorizedRTA4RunnerV3", "ExecutionSummaryV3",
    "RTA4_CHECKPOINT_V3", "RTA4FormalResultWriterV3",
    "RTA4FormalRunnerV3Error", "RTA4FormalTasksetStoreV3",
    "RTA4_RESULT_DIRECTORY_V3", "RTA4_RESULT_ROW_SCHEMA_V3",
    "RTA4_RUN_MANIFEST_V3", "RTA4_TASKSET_STORE_MANIFEST_V3",
    "SourceTasksetReaderV3", "V3ProductionTasksetProvider",
]
