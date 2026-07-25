"""Deterministic release projection and simulation applicability for v9.3.

This module is an explicit opt-in layer above the PR-B task-set certificate.
Release phase, simulation horizons, batteries, and applicability categories
never enter the task-set or RTA identities.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .config import fraction_text
from .constrained_taskset_identity import (
    TasksetIdentityCertificate,
    canonical_identity_bytes,
)
from . import exact_energy
from .task_identity import runtime_task_name_for_source_id


RELEASE_PROJECTION_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RELEASE_PROJECTION_V1"
)
RELEASE_OFFSET_DOMAIN = "ASAP_BLOCK:V9.3:RELEASE_OFFSET:v1"
RELEASE_VECTOR_DOMAIN = "ASAP_BLOCK:V9.3:RELEASE_VECTOR:v1"
RELEASE_PROJECTION_DOMAIN = "ASAP_BLOCK:V9.3:RELEASE_PROJECTION:v1"

SYNC_V1 = "SYNC_V1"
ASYNC_HASH_PHASE_V1 = "ASYNC_HASH_PHASE_V1"
RELEASE_MODES = frozenset({SYNC_V1, ASYNC_HASH_PHASE_V1})

RELEASE_HORIZON = 30_000
SIMULATION_APPLICABILITY_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_SIMULATION_APPLICABILITY_V1"
)
SIMULATION_ID_DOMAIN = "ASAP_BLOCK:V9.3:SIMULATION_APPLICABILITY:v1"
SIMULATOR_TRACE_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RELEASE_CUTOFF_TRACE_V1"
)

THEOREM_ALIGNED = "THEOREM_ALIGNED"
FINITE_BATTERY_EMPIRICAL = "FINITE_BATTERY_EMPIRICAL"
E0_CONDITION_SATISFIED = "E0_CONDITION_SATISFIED"
E0_CONDITION_NOT_SATISFIED = "E0_CONDITION_NOT_SATISFIED"
APPLICABILITY_TRACKS = frozenset({
    THEOREM_ALIGNED,
    FINITE_BATTERY_EMPIRICAL,
})
TARGET_SCHEDULER = "gpfp_asap_block"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReleaseApplicabilityError(ValueError):
    """Raised when release/applicability material is not canonical."""


def _plain_int(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ReleaseApplicabilityError(
            f"{label} must be a plain integer at least {minimum}"
        )
    return value


def _sha256(value: Any, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ReleaseApplicabilityError(
            f"{label} must be a canonical lowercase SHA-256"
        )
    return value


def _canonical_string(value: Any, label: str) -> str:
    if type(value) is not str:
        raise ReleaseApplicabilityError(
            f"{label} must be a canonical identity string"
        )
    try:
        canonical_identity_bytes({"value": value})
    except ValueError as exc:
        raise ReleaseApplicabilityError(
            f"{label} must be a canonical identity string"
        ) from exc
    return value


def _exact_keys(
    value: Any, expected: Sequence[str], label: str
) -> Dict[str, Any]:
    if type(value) is not dict or set(value) != set(expected):
        raise ReleaseApplicabilityError(
            f"{label} does not have its exact canonical field set"
        )
    return value


def _identity_hash(domain: str, material: Any) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + canonical_identity_bytes(material)
    ).hexdigest()


@dataclass(frozen=True)
class ReleaseOffset:
    task_id: str
    priority_rank: int
    period: int
    arrival_offset: int

    def __post_init__(self) -> None:
        _canonical_string(self.task_id, "release task_id")
        _plain_int(self.priority_rank, "release priority_rank")
        period = _plain_int(self.period, "release period", 1)
        offset = _plain_int(self.arrival_offset, "release arrival_offset")
        if offset >= period:
            raise ReleaseApplicabilityError(
                "release arrival_offset must satisfy 0 <= O_i < T_i"
            )

    def material(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "priority_rank": self.priority_rank,
            "period": self.period,
            "arrival_offset": self.arrival_offset,
        }

    @classmethod
    def from_material(cls, value: Any) -> "ReleaseOffset":
        row = _exact_keys(
            value,
            ("task_id", "priority_rank", "period", "arrival_offset"),
            "release offset",
        )
        return cls(
            row["task_id"],
            row["priority_rank"],
            row["period"],
            row["arrival_offset"],
        )


def derive_release_offset(
    *,
    taskset_skeleton_id: str,
    task_id: str,
    priority_rank: int,
    period: int,
    release_mode: str,
) -> int:
    """Derive one phase without RNG state or deadline-variant provenance."""

    skeleton_id = _sha256(taskset_skeleton_id, "taskset_skeleton_id")
    canonical_task_id = _canonical_string(task_id, "task_id")
    rank = _plain_int(priority_rank, "priority_rank")
    task_period = _plain_int(period, "period", 1)
    if release_mode not in RELEASE_MODES:
        raise ReleaseApplicabilityError("unknown release projection mode")
    if release_mode == SYNC_V1:
        return 0
    material = {
        "release_projection_contract_version": (
            RELEASE_PROJECTION_CONTRACT_VERSION
        ),
        "taskset_skeleton_id": skeleton_id,
        "canonical_task_id": canonical_task_id,
        "priority_rank": rank,
        "period": task_period,
        "release_mode": release_mode,
    }
    digest = hashlib.sha256(
        RELEASE_OFFSET_DOMAIN.encode("ascii")
        + b"\0"
        + canonical_identity_bytes(material)
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=False) % task_period


def _release_vector_hash(
    taskset_skeleton_id: str,
    release_mode: str,
    offsets: Tuple[ReleaseOffset, ...],
) -> str:
    return _identity_hash(
        RELEASE_VECTOR_DOMAIN,
        {
            "release_projection_contract_version": (
                RELEASE_PROJECTION_CONTRACT_VERSION
            ),
            "taskset_skeleton_id": taskset_skeleton_id,
            "release_mode": release_mode,
            "offsets": [row.material() for row in offsets],
        },
    )


def _release_projection_id(
    taskset_id: str, release_vector_hash: str
) -> str:
    return _identity_hash(
        RELEASE_PROJECTION_DOMAIN,
        {
            "release_projection_contract_version": (
                RELEASE_PROJECTION_CONTRACT_VERSION
            ),
            "taskset_id": taskset_id,
            "release_vector_hash": release_vector_hash,
        },
    )


@dataclass(frozen=True)
class ReleaseProjection:
    taskset_skeleton_id: str
    taskset_id: str
    release_mode: str
    offsets: Tuple[ReleaseOffset, ...]
    release_vector_hash: str
    release_projection_id: str

    def __post_init__(self) -> None:
        _sha256(self.taskset_skeleton_id, "taskset_skeleton_id")
        _sha256(self.taskset_id, "taskset_id")
        if self.release_mode not in RELEASE_MODES:
            raise ReleaseApplicabilityError("unknown release projection mode")
        if type(self.offsets) is not tuple or not self.offsets:
            raise ReleaseApplicabilityError(
                "release offsets must be a non-empty immutable tuple"
            )
        if any(type(row) is not ReleaseOffset for row in self.offsets):
            raise ReleaseApplicabilityError("invalid release offset record")
        ranks = [row.priority_rank for row in self.offsets]
        task_ids = [row.task_id for row in self.offsets]
        if ranks != list(range(len(self.offsets))):
            raise ReleaseApplicabilityError(
                "release offsets are not in canonical priority order"
            )
        if len(set(task_ids)) != len(task_ids):
            raise ReleaseApplicabilityError("duplicate release task ID")
        expected_offsets = tuple(
            derive_release_offset(
                taskset_skeleton_id=self.taskset_skeleton_id,
                task_id=row.task_id,
                priority_rank=row.priority_rank,
                period=row.period,
                release_mode=self.release_mode,
            )
            for row in self.offsets
        )
        if tuple(row.arrival_offset for row in self.offsets) != expected_offsets:
            raise ReleaseApplicabilityError("release offset derivation mismatch")
        _sha256(self.release_vector_hash, "release_vector_hash")
        expected_vector = _release_vector_hash(
            self.taskset_skeleton_id, self.release_mode, self.offsets
        )
        if self.release_vector_hash != expected_vector:
            raise ReleaseApplicabilityError("release_vector_hash mismatch")
        _sha256(self.release_projection_id, "release_projection_id")
        expected_projection = _release_projection_id(
            self.taskset_id, self.release_vector_hash
        )
        if self.release_projection_id != expected_projection:
            raise ReleaseApplicabilityError("release_projection_id mismatch")

    def material(self) -> Dict[str, Any]:
        return {
            "release_projection_contract_version": (
                RELEASE_PROJECTION_CONTRACT_VERSION
            ),
            "taskset_skeleton_id": self.taskset_skeleton_id,
            "taskset_id": self.taskset_id,
            "release_mode": self.release_mode,
            "offsets": [row.material() for row in self.offsets],
            "release_vector_hash": self.release_vector_hash,
            "release_projection_id": self.release_projection_id,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_identity_bytes(self.material())

    @classmethod
    def from_material(cls, value: Any) -> "ReleaseProjection":
        material = _exact_keys(
            value,
            (
                "release_projection_contract_version",
                "taskset_skeleton_id",
                "taskset_id",
                "release_mode",
                "offsets",
                "release_vector_hash",
                "release_projection_id",
            ),
            "release projection",
        )
        if (
            material["release_projection_contract_version"]
            != RELEASE_PROJECTION_CONTRACT_VERSION
        ):
            raise ReleaseApplicabilityError(
                "release projection contract version mismatch"
            )
        rows = material["offsets"]
        if type(rows) is not list:
            raise ReleaseApplicabilityError("release offsets must be a list")
        return cls(
            material["taskset_skeleton_id"],
            material["taskset_id"],
            material["release_mode"],
            tuple(ReleaseOffset.from_material(row) for row in rows),
            material["release_vector_hash"],
            material["release_projection_id"],
        )

    @classmethod
    def from_canonical_bytes(cls, value: bytes) -> "ReleaseProjection":
        if type(value) is not bytes:
            raise ReleaseApplicabilityError(
                "release projection encoding must be bytes"
            )

        def unique_object(
            pairs: Sequence[Tuple[str, Any]]
        ) -> Dict[str, Any]:
            result: Dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ReleaseApplicabilityError(
                        f"duplicate JSON key: {key}"
                    )
                result[key] = item
            return result

        try:
            material = json.loads(
                value.decode("utf-8"), object_pairs_hook=unique_object
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseApplicabilityError(
                "invalid release projection JSON"
            ) from exc
        if canonical_identity_bytes(material) != value:
            raise ReleaseApplicabilityError(
                "release projection JSON is not canonical"
            )
        return cls.from_material(material)


def build_release_projection(
    certificate: TasksetIdentityCertificate,
    *,
    release_mode: str,
) -> ReleaseProjection:
    if type(certificate) is not TasksetIdentityCertificate:
        raise ReleaseApplicabilityError(
            "certificate must be a TasksetIdentityCertificate"
        )
    if release_mode not in RELEASE_MODES:
        raise ReleaseApplicabilityError("unknown release projection mode")
    offsets = tuple(
        ReleaseOffset(
            task.task_id,
            task.priority_rank,
            task.period,
            derive_release_offset(
                taskset_skeleton_id=certificate.taskset_skeleton_id,
                task_id=task.task_id,
                priority_rank=task.priority_rank,
                period=task.period,
                release_mode=release_mode,
            ),
        )
        for task in certificate.skeleton_tasks
    )
    vector_hash = _release_vector_hash(
        certificate.taskset_skeleton_id, release_mode, offsets
    )
    return ReleaseProjection(
        certificate.taskset_skeleton_id,
        certificate.taskset_id,
        release_mode,
        offsets,
        vector_hash,
        _release_projection_id(certificate.taskset_id, vector_hash),
    )


@dataclass(frozen=True)
class ReleaseObservationWindow:
    release_horizon: int
    maximum_relative_deadline: int
    observation_horizon: int

    def __post_init__(self) -> None:
        if self.release_horizon != RELEASE_HORIZON:
            raise ReleaseApplicabilityError(
                f"release_horizon must be frozen at {RELEASE_HORIZON}"
            )
        maximum = _plain_int(
            self.maximum_relative_deadline,
            "maximum_relative_deadline",
            1,
        )
        if self.observation_horizon != self.release_horizon + maximum:
            raise ReleaseApplicabilityError(
                "observation_horizon must equal release_horizon + D_max"
            )

    def material(self) -> Dict[str, int]:
        return {
            "release_horizon": self.release_horizon,
            "maximum_relative_deadline": self.maximum_relative_deadline,
            "observation_horizon": self.observation_horizon,
        }

    @classmethod
    def for_certificate(
        cls, certificate: TasksetIdentityCertificate
    ) -> "ReleaseObservationWindow":
        if type(certificate) is not TasksetIdentityCertificate:
            raise ReleaseApplicabilityError(
                "certificate must be a TasksetIdentityCertificate"
            )
        maximum = max(task.relative_deadline for task in certificate.tasks)
        return cls(RELEASE_HORIZON, maximum, RELEASE_HORIZON + maximum)


def project_certificate_for_simulation(
    certificate: TasksetIdentityCertificate,
    projection: ReleaseProjection,
) -> Tuple[Mapping[str, Any], ...]:
    """Return canonical mathematical payload plus simulation-only fields."""

    _validate_projection_binding(certificate, projection)
    return tuple(
        {
            "task_id": task.task_id,
            "priority_rank": task.priority_rank,
            "C": task.wcet,
            "D": task.relative_deadline,
            "T": task.period,
            "P": fraction_text(task.actual_power),
            "arrival_offset": offset.arrival_offset,
            "ph": offset.arrival_offset,
        }
        for task, offset in zip(certificate.tasks, projection.offsets)
    )


def _payload_power(value: Any, label: str) -> Fraction:
    if type(value) is Fraction:
        return value
    if type(value) in {str, int}:
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ReleaseApplicabilityError(
                f"{label} is not exact rational data"
            ) from exc
    raise ReleaseApplicabilityError(
        f"{label} must not use binary floating-point data"
    )


def _validate_projection_binding(
    certificate: TasksetIdentityCertificate,
    projection: ReleaseProjection,
) -> None:
    if type(certificate) is not TasksetIdentityCertificate:
        raise ReleaseApplicabilityError(
            "certificate must be a TasksetIdentityCertificate"
        )
    if type(projection) is not ReleaseProjection:
        raise ReleaseApplicabilityError(
            "projection must be a ReleaseProjection"
        )
    if (
        projection.taskset_skeleton_id != certificate.taskset_skeleton_id
        or projection.taskset_id != certificate.taskset_id
        or len(projection.offsets) != len(certificate.tasks)
    ):
        raise ReleaseApplicabilityError(
            "release projection/taskset certificate mismatch"
        )
    for task, offset in zip(certificate.tasks, projection.offsets):
        if (
            offset.task_id != task.task_id
            or offset.priority_rank != task.priority_rank
            or offset.period != task.period
        ):
            raise ReleaseApplicabilityError(
                "release projection task binding mismatch"
            )


def apply_release_projection(
    certificate: TasksetIdentityCertificate,
    projection: ReleaseProjection,
    task_payload: Sequence[Mapping[str, Any]],
) -> Tuple[Mapping[str, Any], ...]:
    """Overlay only simulation release controls on an existing payload."""

    canonical = project_certificate_for_simulation(certificate, projection)
    if len(task_payload) != len(canonical):
        raise ReleaseApplicabilityError("simulation task payload count mismatch")
    result = []
    for index, (source, expected) in enumerate(zip(task_payload, canonical)):
        if not isinstance(source, Mapping):
            raise ReleaseApplicabilityError(
                f"simulation task payload {index} is not a mapping"
            )
        if (
            str(source.get("task_id")) != expected["task_id"]
            or type(source.get("priority_rank")) is not int
            or source["priority_rank"] != expected["priority_rank"]
            or type(source.get("C")) is not int
            or source["C"] != expected["C"]
            or type(source.get("D")) is not int
            or source["D"] != expected["D"]
            or type(source.get("T")) is not int
            or source["T"] != expected["T"]
            or _payload_power(source.get("P"), f"task {index} power")
            != certificate.tasks[index].actual_power
        ):
            raise ReleaseApplicabilityError(
                "simulation payload/certificate mathematical mismatch"
            )
        projected = dict(source)
        projected["arrival_offset"] = expected["arrival_offset"]
        projected["ph"] = expected["ph"]
        result.append(projected)
    return tuple(result)


def simulation_identity_material(
    *,
    taskset_id: str,
    release_projection_id: str,
    scheduler: str,
    service_identity: str,
    initial_battery: Any,
    battery_capacity: Any,
    window: ReleaseObservationWindow,
    applicability_track: str,
) -> Dict[str, Any]:
    taskset = _sha256(taskset_id, "taskset_id")
    release_projection = _sha256(
        release_projection_id, "release_projection_id"
    )
    scheduler_id = _canonical_string(scheduler, "scheduler")
    service = _sha256(service_identity, "service_identity")
    if type(window) is not ReleaseObservationWindow:
        raise ReleaseApplicabilityError(
            "window must be a ReleaseObservationWindow"
        )
    if applicability_track not in APPLICABILITY_TRACKS:
        raise ReleaseApplicabilityError("unknown applicability track")
    try:
        initial = exact_energy.exact_e0_lower_bound(
            initial_battery, "initial battery"
        )
        capacity = exact_energy.exact_e0_lower_bound(
            battery_capacity, "battery capacity"
        )
    except exact_energy.ExactEnergyError as exc:
        raise ReleaseApplicabilityError(str(exc)) from exc
    if capacity <= 0 or initial > capacity:
        raise ReleaseApplicabilityError(
            "battery values must satisfy 0 <= initial <= capacity"
        )
    return {
        "simulation_applicability_contract_version": (
            SIMULATION_APPLICABILITY_CONTRACT_VERSION
        ),
        "taskset_id": taskset,
        "release_projection_id": release_projection,
        "scheduler": scheduler_id,
        "service_harvest_identity": service,
        "initial_battery": {
            "numerator": initial.numerator,
            "denominator": initial.denominator,
        },
        "battery_capacity": {
            "numerator": capacity.numerator,
            "denominator": capacity.denominator,
        },
        "release_horizon": window.release_horizon,
        "observation_horizon": window.observation_horizon,
        "applicability_track": applicability_track,
        "simulator_trace_contract_version": (
            SIMULATOR_TRACE_CONTRACT_VERSION
        ),
    }


def simulation_applicability_identity(**kwargs: Any) -> str:
    return _identity_hash(
        SIMULATION_ID_DOMAIN, simulation_identity_material(**kwargs)
    )


@dataclass(frozen=True)
class ReleaseEnergySample:
    task_id: str
    priority_rank: int
    release: int
    energy_exact: Fraction

    def __post_init__(self) -> None:
        _canonical_string(self.task_id, "release sample task_id")
        _plain_int(self.priority_rank, "release sample priority_rank")
        _plain_int(self.release, "release sample release")
        if type(self.energy_exact) is not Fraction or self.energy_exact < 0:
            raise ReleaseApplicabilityError(
                "release sample energy must be a non-negative exact Fraction"
            )

    def material(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "priority_rank": self.priority_rank,
            "release": self.release,
            "energy_exact": fraction_text(self.energy_exact),
        }


@dataclass(frozen=True)
class ReleaseTraceAudit:
    simulation_id: str
    taskset_hash: str
    scheduler: str
    release_horizon: int
    observation_horizon: int
    release_cutoff_enabled: bool
    observation_horizon_reached: bool
    samples: Tuple[ReleaseEnergySample, ...]
    minimum_release_energy_exact: Fraction

    def __post_init__(self) -> None:
        _sha256(self.simulation_id, "simulation_id")
        _sha256(self.taskset_hash, "taskset_hash")
        _canonical_string(self.scheduler, "scheduler")
        _plain_int(self.release_horizon, "release_horizon", 1)
        _plain_int(self.observation_horizon, "observation_horizon", 1)
        if self.observation_horizon <= self.release_horizon:
            raise ReleaseApplicabilityError(
                "observation_horizon must exceed release_horizon"
            )
        if (
            type(self.release_cutoff_enabled) is not bool
            or not self.release_cutoff_enabled
        ):
            raise ReleaseApplicabilityError("release cutoff is not enabled")
        if (
            type(self.observation_horizon_reached) is not bool
            or not self.observation_horizon_reached
        ):
            raise ReleaseApplicabilityError(
                "observation horizon was not reached"
            )
        if type(self.samples) is not tuple or not self.samples:
            raise ReleaseApplicabilityError(
                "release trace has no evaluated releases"
            )
        if any(type(row) is not ReleaseEnergySample for row in self.samples):
            raise ReleaseApplicabilityError(
                "release trace contains invalid samples"
            )
        if any(row.release >= self.release_horizon for row in self.samples):
            raise ReleaseApplicabilityError(
                "release trace contains release at/after cutoff"
            )
        minimum = min(row.energy_exact for row in self.samples)
        if self.minimum_release_energy_exact != minimum:
            raise ReleaseApplicabilityError(
                "minimum release energy mismatch"
            )

    @property
    def evaluated_release_count(self) -> int:
        return len(self.samples)

    def material(self) -> Dict[str, Any]:
        return {
            "simulator_trace_contract_version": (
                SIMULATOR_TRACE_CONTRACT_VERSION
            ),
            "simulation_id": self.simulation_id,
            "taskset_hash": self.taskset_hash,
            "scheduler": self.scheduler,
            "release_horizon": self.release_horizon,
            "observation_horizon": self.observation_horizon,
            "release_cutoff_enabled": self.release_cutoff_enabled,
            "observation_horizon_reached": (
                self.observation_horizon_reached
            ),
            "minimum_release_energy_exact": fraction_text(
                self.minimum_release_energy_exact
            ),
            "evaluated_release_count": self.evaluated_release_count,
            "release_samples": [row.material() for row in self.samples],
        }


def _strict_trace(path: Path) -> Mapping[str, Any]:
    def reject_constant(token: str) -> None:
        raise ReleaseApplicabilityError(
            f"non-finite trace JSON token: {token}"
        )

    def unique_object(
        pairs: Sequence[Tuple[str, Any]]
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ReleaseApplicabilityError(
                    f"duplicate trace JSON key: {key}"
                )
            result[key] = item
        return result

    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseApplicabilityError(
            f"cannot read release trace: {exc}"
        ) from exc
    if type(value) is not dict or type(value.get("events")) is not list:
        raise ReleaseApplicabilityError(
            "release trace must contain an event list"
        )
    return value


def _trace_tick(value: Any, label: str) -> int:
    if type(value) is int:
        return value
    if type(value) is str:
        try:
            exact = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ReleaseApplicabilityError(
                f"{label} must be an integer tick"
            ) from exc
        if exact.denominator == 1:
            return exact.numerator
    raise ReleaseApplicabilityError(f"{label} must be an integer tick")


def _exact_trace_energy_j(value: Any, label: str) -> Fraction:
    if type(value) not in {int, float}:
        raise ReleaseApplicabilityError(
            f"{label} must be a JSON binary64 number"
        )
    try:
        binary64 = float(value)
    except (OverflowError, ValueError) as exc:
        raise ReleaseApplicabilityError(
            f"{label} must be a finite binary64 number"
        ) from exc
    if not math.isfinite(binary64):
        raise ReleaseApplicabilityError(
            f"{label} must be a finite binary64 number"
        )
    try:
        exact_millijoules = exact_energy.materialize_supply_lower_bound(
            binary64, label
        ).exact_value
    except exact_energy.ExactEnergyError as exc:
        raise ReleaseApplicabilityError(str(exc)) from exc
    exact_joules = exact_millijoules / 1000
    if exact_joules < 0:
        raise ReleaseApplicabilityError(
            f"{label} must be non-negative"
        )
    return exact_joules


def parse_release_trace(
    trace_path: Path,
    task_payload: Sequence[Mapping[str, Any]],
    *,
    expected_simulation_id: str,
    expected_taskset_hash: str,
    window: ReleaseObservationWindow,
    expected_scheduler: str = TARGET_SCHEDULER,
) -> ReleaseTraceAudit:
    """Validate cutoff metadata and capture exact energy at every release."""

    simulation_id = _sha256(
        expected_simulation_id, "expected_simulation_id"
    )
    taskset_hash = _sha256(
        expected_taskset_hash, "expected_taskset_hash"
    )
    scheduler = _canonical_string(
        expected_scheduler, "expected_scheduler"
    )
    if type(window) is not ReleaseObservationWindow:
        raise ReleaseApplicabilityError(
            "window must be a ReleaseObservationWindow"
        )
    data = _strict_trace(trace_path)
    if data.get("trace_schema_version") != 2:
        raise ReleaseApplicabilityError("unsupported trace schema version")
    if (
        data.get("simulator_trace_contract_version")
        != SIMULATOR_TRACE_CONTRACT_VERSION
    ):
        raise ReleaseApplicabilityError(
            "simulator trace contract version mismatch"
        )
    if data.get("run_id") != simulation_id:
        raise ReleaseApplicabilityError("simulation identity mismatch")
    if data.get("taskset_semantic_hash") != taskset_hash:
        raise ReleaseApplicabilityError("taskset identity mismatch")
    if data.get("configured_scheduler") != scheduler:
        raise ReleaseApplicabilityError("scheduler identity mismatch")
    if data.get("release_cutoff_enabled") is not True:
        raise ReleaseApplicabilityError("trace release cutoff is not enabled")
    if (
        _trace_tick(data.get("release_horizon_ms"), "release horizon")
        != window.release_horizon
    ):
        raise ReleaseApplicabilityError("trace release horizon mismatch")
    if (
        _trace_tick(
            data.get("observation_horizon_ms"),
            "observation horizon",
        )
        != window.observation_horizon
    ):
        raise ReleaseApplicabilityError(
            "trace observation horizon mismatch"
        )
    if data.get("observation_horizon_reached") is not True:
        raise ReleaseApplicabilityError(
            "trace did not reach observation horizon"
        )
    if (
        data.get("simulation_completed") is not True
        or data.get("simulation_completion_reason") != "reached_horizon"
        or _trace_tick(
            data.get("expected_simulation_horizon_ms"),
            "expected simulation horizon",
        )
        != window.observation_horizon
        or _trace_tick(
            data.get("observed_simulation_end_ms"),
            "observed simulation horizon",
        )
        != window.observation_horizon
    ):
        raise ReleaseApplicabilityError(
            "simulation observation horizon is incomplete"
        )

    definitions: Dict[str, Mapping[str, Any]] = {}
    runtime_names: Dict[str, str] = {}
    ranks: Dict[str, int] = {}
    for index, row in enumerate(task_payload):
        if not isinstance(row, Mapping):
            raise ReleaseApplicabilityError(
                f"task payload {index} is not a mapping"
            )
        task_id = _canonical_string(row.get("task_id"), "task_id")
        rank = _plain_int(row.get("priority_rank"), "priority_rank")
        deadline = _plain_int(row.get("D"), "relative deadline", 1)
        if task_id in definitions or rank in ranks.values():
            raise ReleaseApplicabilityError(
                "duplicate task identity in payload"
            )
        if window.release_horizon - 1 + deadline > window.observation_horizon:
            raise ReleaseApplicabilityError(
                "observation horizon does not cover a pre-cutoff deadline"
            )
        definitions[task_id] = row
        ranks[task_id] = rank
        runtime_names[runtime_task_name_for_source_id(task_id)] = task_id
    if len(runtime_names) != len(definitions):
        raise ReleaseApplicabilityError("duplicate runtime task name")

    samples = []
    seen = set()
    for position, event in enumerate(data["events"]):
        if type(event) is not dict:
            raise ReleaseApplicabilityError(
                f"trace event {position} is not an object"
            )
        event_time = _trace_tick(
            event.get("time"), f"event {position} time"
        )
        if event_time < 0 or event_time > window.observation_horizon:
            raise ReleaseApplicabilityError(
                "trace event lies outside observation horizon"
            )
        if event.get("event_type") != "arrival":
            continue
        name = event.get("task_name")
        if name not in runtime_names:
            raise ReleaseApplicabilityError(
                "arrival has unknown runtime task name"
            )
        release = _trace_tick(
            event.get("arrival_time"), "arrival release"
        )
        if release != event_time:
            raise ReleaseApplicabilityError(
                "arrival event time/release mismatch"
            )
        if release >= window.release_horizon:
            raise ReleaseApplicabilityError(
                "trace contains release at/after release horizon"
            )
        task_id = runtime_names[str(name)]
        key = (task_id, release)
        if key in seen:
            raise ReleaseApplicabilityError(
                "duplicate arrival energy observation"
            )
        seen.add(key)
        samples.append(ReleaseEnergySample(
            task_id,
            ranks[task_id],
            release,
            _exact_trace_energy_j(
                event.get("current_energy_mJ"),
                "arrival current_energy_mJ",
            ),
        ))
    if not samples:
        raise ReleaseApplicabilityError(
            "trace contains no pre-cutoff arrivals"
        )
    ordered = tuple(sorted(
        samples,
        key=lambda row: (row.release, row.priority_rank, row.task_id),
    ))
    return ReleaseTraceAudit(
        simulation_id,
        taskset_hash,
        scheduler,
        window.release_horizon,
        window.observation_horizon,
        True,
        True,
        ordered,
        min(row.energy_exact for row in ordered),
    )


@dataclass(frozen=True)
class E0Evaluation:
    minimum_release_energy_exact: Fraction
    evaluated_release_count: int
    first_violating_task_id: Optional[str]
    first_violating_release: Optional[int]
    requested_e0: Fraction
    e0_condition_satisfied: bool
    status: str

    def __post_init__(self) -> None:
        if (
            type(self.minimum_release_energy_exact) is not Fraction
            or self.minimum_release_energy_exact < 0
            or type(self.requested_e0) is not Fraction
            or self.requested_e0 < 0
        ):
            raise ReleaseApplicabilityError(
                "E0 evaluation energies must be non-negative exact Fractions"
            )
        _plain_int(
            self.evaluated_release_count,
            "evaluated_release_count",
            1,
        )
        if type(self.e0_condition_satisfied) is not bool:
            raise ReleaseApplicabilityError(
                "e0_condition_satisfied must be a strict boolean"
            )
        expected_status = (
            E0_CONDITION_SATISFIED
            if self.e0_condition_satisfied
            else E0_CONDITION_NOT_SATISFIED
        )
        if self.status != expected_status:
            raise ReleaseApplicabilityError("E0 evaluation status mismatch")
        if self.e0_condition_satisfied:
            if (
                self.first_violating_task_id is not None
                or self.first_violating_release is not None
            ):
                raise ReleaseApplicabilityError(
                    "satisfied E0 evaluation cannot name a violation"
                )
        else:
            _canonical_string(
                self.first_violating_task_id,
                "first_violating_task_id",
            )
            _plain_int(
                self.first_violating_release,
                "first_violating_release",
            )

    def row(self) -> Dict[str, Any]:
        return {
            "minimum_release_energy_exact": fraction_text(
                self.minimum_release_energy_exact
            ),
            "evaluated_release_count": self.evaluated_release_count,
            "first_violating_task_id": self.first_violating_task_id,
            "first_violating_release": self.first_violating_release,
            "requested_e0": fraction_text(self.requested_e0),
            "e0_condition_satisfied": self.e0_condition_satisfied,
            "status": self.status,
        }

    material = row


def evaluate_e0_condition(
    audit: ReleaseTraceAudit, requested_e0: Any
) -> E0Evaluation:
    if type(audit) is not ReleaseTraceAudit:
        raise ReleaseApplicabilityError(
            "audit must be a ReleaseTraceAudit"
        )
    try:
        exact_e0 = exact_energy.exact_e0_lower_bound(
            requested_e0, "requested E0"
        )
    except exact_energy.ExactEnergyError as exc:
        raise ReleaseApplicabilityError(str(exc)) from exc
    violating = next(
        (row for row in audit.samples if row.energy_exact < exact_e0),
        None,
    )
    satisfied = violating is None
    return E0Evaluation(
        audit.minimum_release_energy_exact,
        audit.evaluated_release_count,
        None if violating is None else violating.task_id,
        None if violating is None else violating.release,
        exact_e0,
        satisfied,
        (
            E0_CONDITION_SATISFIED
            if satisfied
            else E0_CONDITION_NOT_SATISFIED
        ),
    )


def evaluate_e0_grid(
    audit: ReleaseTraceAudit, requested_e0_values: Sequence[Any]
) -> Tuple[E0Evaluation, ...]:
    if not isinstance(requested_e0_values, (tuple, list)):
        raise ReleaseApplicabilityError(
            "requested E0 grid must be an ordered sequence"
        )
    return tuple(
        evaluate_e0_condition(audit, value)
        for value in requested_e0_values
    )


@dataclass(frozen=True)
class ApplicabilityAssessment:
    category: str
    theorem_comparison_eligible: bool
    theorem_applicable_soundness_counterexample: bool
    empirical_difference: bool
    reason: str

    def __post_init__(self) -> None:
        if self.category not in {
            THEOREM_ALIGNED,
            FINITE_BATTERY_EMPIRICAL,
            E0_CONDITION_NOT_SATISFIED,
        }:
            raise ReleaseApplicabilityError(
                "unknown applicability assessment category"
            )
        if any(
            type(value) is not bool
            for value in (
                self.theorem_comparison_eligible,
                self.theorem_applicable_soundness_counterexample,
                self.empirical_difference,
            )
        ):
            raise ReleaseApplicabilityError(
                "applicability results must be strict booleans"
            )
        _canonical_string(self.reason, "applicability reason")

    def material(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "theorem_comparison_eligible": (
                self.theorem_comparison_eligible
            ),
            "theorem_applicable_soundness_counterexample": (
                self.theorem_applicable_soundness_counterexample
            ),
            "empirical_difference": self.empirical_difference,
            "reason": self.reason,
        }

    row = material


def assess_applicability(
    *,
    requested_track: str,
    e0_evaluation: E0Evaluation,
    release_cutoff_valid: bool,
    observation_horizon_complete: bool,
    no_overflow_valid: bool,
    identity_match: bool,
    scheduler_is_target: bool,
    rta_pass: bool,
    simulation_deadline_miss: bool,
) -> ApplicabilityAssessment:
    if requested_track not in APPLICABILITY_TRACKS:
        raise ReleaseApplicabilityError("unknown applicability track")
    values = {
        "release_cutoff_valid": release_cutoff_valid,
        "observation_horizon_complete": observation_horizon_complete,
        "no_overflow_valid": no_overflow_valid,
        "identity_match": identity_match,
        "scheduler_is_target": scheduler_is_target,
        "rta_pass": rta_pass,
        "simulation_deadline_miss": simulation_deadline_miss,
    }
    if any(type(value) is not bool for value in values.values()):
        raise ReleaseApplicabilityError(
            "applicability gates must be strict booleans"
        )
    if type(e0_evaluation) is not E0Evaluation:
        raise ReleaseApplicabilityError(
            "e0_evaluation must be an E0Evaluation"
        )
    if not e0_evaluation.e0_condition_satisfied:
        return ApplicabilityAssessment(
            E0_CONDITION_NOT_SATISFIED,
            False,
            False,
            False,
            "release_energy_below_requested_e0",
        )
    observed_difference = rta_pass and simulation_deadline_miss
    if requested_track == FINITE_BATTERY_EMPIRICAL:
        return ApplicabilityAssessment(
            FINITE_BATTERY_EMPIRICAL,
            False,
            False,
            observed_difference,
            "finite_battery_empirical_comparison",
        )
    prerequisites = {
        "release_cutoff": release_cutoff_valid,
        "observation_horizon": observation_horizon_complete,
        "no_overflow": no_overflow_valid,
        "identity": identity_match,
        "scheduler": scheduler_is_target,
    }
    missing = sorted(
        name for name, satisfied in prerequisites.items() if not satisfied
    )
    if missing:
        raise ReleaseApplicabilityError(
            "THEOREM_ALIGNED prerequisites failed: " + ",".join(missing)
        )
    return ApplicabilityAssessment(
        THEOREM_ALIGNED,
        True,
        observed_difference,
        False,
        "theorem_contracts_satisfied",
    )


__all__ = [
    "APPLICABILITY_TRACKS",
    "ASYNC_HASH_PHASE_V1",
    "ApplicabilityAssessment",
    "E0Evaluation",
    "E0_CONDITION_SATISFIED",
    "E0_CONDITION_NOT_SATISFIED",
    "FINITE_BATTERY_EMPIRICAL",
    "RELEASE_HORIZON",
    "RELEASE_MODES",
    "RELEASE_OFFSET_DOMAIN",
    "RELEASE_PROJECTION_CONTRACT_VERSION",
    "RELEASE_PROJECTION_DOMAIN",
    "RELEASE_VECTOR_DOMAIN",
    "ReleaseApplicabilityError",
    "ReleaseEnergySample",
    "ReleaseObservationWindow",
    "ReleaseOffset",
    "ReleaseProjection",
    "ReleaseTraceAudit",
    "SIMULATION_APPLICABILITY_CONTRACT_VERSION",
    "SIMULATION_ID_DOMAIN",
    "SIMULATOR_TRACE_CONTRACT_VERSION",
    "SYNC_V1",
    "TARGET_SCHEDULER",
    "THEOREM_ALIGNED",
    "apply_release_projection",
    "assess_applicability",
    "build_release_projection",
    "derive_release_offset",
    "evaluate_e0_condition",
    "evaluate_e0_grid",
    "parse_release_trace",
    "project_certificate_for_simulation",
    "simulation_applicability_identity",
    "simulation_identity_material",
]
