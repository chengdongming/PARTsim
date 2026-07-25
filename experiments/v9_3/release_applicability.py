"""Deterministic release projection and simulation applicability for v9.3.

This module is an explicit opt-in layer above the PR-B task-set certificate.
Release phase, simulation horizons, batteries, and applicability categories
never enter the task-set or RTA identities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    "ASAP_BLOCK_V9_3_RELEASE_CUTOFF_TRACE_V2"
)
RELEASE_SNAPSHOT_STAGE = "post_harvest_pre_consumption"

EXPECTED_ARRIVAL_SET_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXPECTED_ARRIVAL_SET:v1"
)
OBSERVED_ARRIVAL_SET_DOMAIN = (
    "ASAP_BLOCK:V9.3:OBSERVED_ARRIVAL_SET:v1"
)
RELEASE_SAMPLES_DOMAIN = "ASAP_BLOCK:V9.3:RELEASE_SAMPLES:v1"
RELEASE_TRACE_AUDIT_DOMAIN = (
    "ASAP_BLOCK:V9.3:RELEASE_TRACE_AUDIT:v1"
)
E0_EVALUATION_DOMAIN = "ASAP_BLOCK:V9.3:E0_EVALUATION:v1"
NO_OVERFLOW_EVIDENCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:NO_OVERFLOW_EVIDENCE:v1"
)
VALIDATED_SIMULATION_EVIDENCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:VALIDATED_SIMULATION_EVIDENCE:v1"
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
RTA_PASS = "RTA_PASS"
RTA_FAIL = "RTA_FAIL"
SIM_DEADLINE_MISS = "SIM_DEADLINE_MISS"
SIM_NO_DEADLINE_MISS = "SIM_NO_DEADLINE_MISS"

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


def _fraction_material(value: Fraction) -> Dict[str, int]:
    if type(value) is not Fraction:
        raise ReleaseApplicabilityError(
            "identity energy must be an exact Fraction"
        )
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
    }


@dataclass(frozen=True)
class ReleaseTaskParameters:
    task_id: str
    priority_rank: int
    wcet: int
    relative_deadline: int
    period: int
    arrival_offset: int

    def __post_init__(self) -> None:
        _canonical_string(self.task_id, "release task task_id")
        _plain_int(self.priority_rank, "release task priority_rank")
        wcet = _plain_int(self.wcet, "release task C", 1)
        deadline = _plain_int(
            self.relative_deadline, "release task D", 1
        )
        period = _plain_int(self.period, "release task T", 1)
        offset = _plain_int(
            self.arrival_offset, "release task arrival_offset"
        )
        if not wcet <= deadline <= period:
            raise ReleaseApplicabilityError(
                "release task must satisfy 1 <= C <= D <= T"
            )
        if offset >= period:
            raise ReleaseApplicabilityError(
                "release task must satisfy 0 <= O_i < T_i"
            )

    def material(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "priority_rank": self.priority_rank,
            "C": self.wcet,
            "D": self.relative_deadline,
            "T": self.period,
            "arrival_offset": self.arrival_offset,
        }


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
            "energy_exact": _fraction_material(self.energy_exact),
        }


def _expected_arrivals(
    tasks: Tuple[ReleaseTaskParameters, ...],
    release_horizon: int,
) -> Tuple[Tuple[str, int], ...]:
    # ``tasks`` is already in canonical contiguous priority order.  Keeping
    # each task's releases in increasing order gives a canonical sequence
    # without sorting the full release set, so reconstruction remains O(N).
    return tuple(
        (task.task_id, release)
        for task in tasks
        for release in range(
            task.arrival_offset, release_horizon, task.period
        )
    )


def _arrival_set_digest(
    domain: str, arrivals: Sequence[Tuple[str, int]]
) -> str:
    seen = set()
    material = []
    for task_id, release in arrivals:
        if (task_id, release) in seen:
            raise ReleaseApplicabilityError(
                "arrival set digest cannot contain duplicates"
            )
        seen.add((task_id, release))
        material.append({"task_id": task_id, "release": release})
    return _identity_hash(
        domain,
        material,
    )


def _samples_digest(samples: Sequence[ReleaseEnergySample]) -> str:
    return _identity_hash(
        RELEASE_SAMPLES_DOMAIN,
        [row.material() for row in samples],
    )


_RELEASE_TRACE_AUDIT_TOKEN = object()


@dataclass(frozen=True)
class ReleaseTraceAudit:
    simulation_id: str
    taskset_id: str
    taskset_hash: str
    release_projection_id: str
    release_vector_hash: str
    scheduler: str
    trace_contract_version: str
    release_horizon: int
    observation_horizon: int
    release_cutoff_enabled: bool
    observation_horizon_reached: bool
    observed_simulation_end: int
    simulation_completion_reason: str
    simulation_outcome: str
    tasks: Tuple[ReleaseTaskParameters, ...]
    expected_release_count: int
    expected_arrival_set_digest: str
    samples: Tuple[ReleaseEnergySample, ...]
    observed_arrival_set_digest: str
    samples_digest: str
    minimum_release_energy_exact: Fraction
    release_trace_audit_id: str
    _validation_token: Any = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self._validation_token is not _RELEASE_TRACE_AUDIT_TOKEN:
            raise ReleaseApplicabilityError(
                "ReleaseTraceAudit must come from validated trace parsing"
            )
        for value, label in (
            (self.simulation_id, "simulation_id"),
            (self.taskset_id, "taskset_id"),
            (self.taskset_hash, "taskset_hash"),
            (self.release_projection_id, "release_projection_id"),
            (self.release_vector_hash, "release_vector_hash"),
            (self.expected_arrival_set_digest, "expected arrival digest"),
            (self.observed_arrival_set_digest, "observed arrival digest"),
            (self.samples_digest, "samples digest"),
            (self.release_trace_audit_id, "release_trace_audit_id"),
        ):
            _sha256(value, label)
        _canonical_string(self.scheduler, "scheduler")
        if self.trace_contract_version != SIMULATOR_TRACE_CONTRACT_VERSION:
            raise ReleaseApplicabilityError(
                "release audit trace contract version mismatch"
            )
        _plain_int(self.release_horizon, "release_horizon", 1)
        _plain_int(self.observation_horizon, "observation_horizon", 1)
        if self.observation_horizon <= self.release_horizon:
            raise ReleaseApplicabilityError(
                "observation_horizon must exceed release_horizon"
            )
        if self.release_cutoff_enabled is not True:
            raise ReleaseApplicabilityError("release cutoff is not enabled")
        if self.observation_horizon_reached is not True:
            raise ReleaseApplicabilityError(
                "observation horizon was not reached"
            )
        if (
            self.observed_simulation_end != self.observation_horizon
            or self.simulation_completion_reason != "reached_horizon"
        ):
            raise ReleaseApplicabilityError(
                "simulation observation horizon is incomplete"
            )
        if self.simulation_outcome not in {
            SIM_DEADLINE_MISS,
            SIM_NO_DEADLINE_MISS,
        }:
            raise ReleaseApplicabilityError(
                "invalid simulation outcome"
            )
        if type(self.tasks) is not tuple or not self.tasks:
            raise ReleaseApplicabilityError(
                "release audit has no canonical task parameters"
            )
        if any(type(row) is not ReleaseTaskParameters for row in self.tasks):
            raise ReleaseApplicabilityError(
                "release audit contains invalid task parameters"
            )
        ranks = [row.priority_rank for row in self.tasks]
        task_ids = [row.task_id for row in self.tasks]
        if ranks != list(range(len(self.tasks))):
            raise ReleaseApplicabilityError(
                "release task ranks are not contiguous/canonical"
            )
        if len(set(task_ids)) != len(task_ids):
            raise ReleaseApplicabilityError(
                "duplicate release task ID"
            )
        if self.observation_horizon != (
            self.release_horizon
            + max(row.relative_deadline for row in self.tasks)
        ):
            raise ReleaseApplicabilityError(
                "release audit observation horizon/D_max mismatch"
            )
        expected = _expected_arrivals(
            self.tasks, self.release_horizon
        )
        if (
            self.expected_release_count != len(expected)
            or self.expected_release_count != len(self.samples)
        ):
            raise ReleaseApplicabilityError(
                "expected release count/sample count mismatch"
            )
        if self.expected_arrival_set_digest != _arrival_set_digest(
            EXPECTED_ARRIVAL_SET_DOMAIN, expected
        ):
            raise ReleaseApplicabilityError(
                "expected arrival set digest mismatch"
            )
        if type(self.samples) is not tuple or not self.samples:
            raise ReleaseApplicabilityError(
                "release trace has no evaluated releases"
            )
        if any(type(row) is not ReleaseEnergySample for row in self.samples):
            raise ReleaseApplicabilityError(
                "release trace contains invalid samples"
            )
        rank_by_task = {
            row.task_id: row.priority_rank for row in self.tasks
        }
        if any(
            row.task_id not in rank_by_task
            or row.priority_rank != rank_by_task[row.task_id]
            for row in self.samples
        ):
            raise ReleaseApplicabilityError(
                "release sample task/rank mismatch"
            )
        observed = tuple(
            (row.task_id, row.release) for row in self.samples
        )
        if observed != expected:
            raise ReleaseApplicabilityError(
                "release samples are not the canonical expected arrivals"
            )
        if self.observed_arrival_set_digest != _arrival_set_digest(
            OBSERVED_ARRIVAL_SET_DOMAIN, observed
        ):
            raise ReleaseApplicabilityError(
                "observed arrival set digest mismatch"
            )
        if self.samples_digest != _samples_digest(self.samples):
            raise ReleaseApplicabilityError(
                "release samples digest mismatch"
            )
        minimum = min(row.energy_exact for row in self.samples)
        if self.minimum_release_energy_exact != minimum:
            raise ReleaseApplicabilityError(
                "minimum release energy mismatch"
            )
        expected_audit_id = _identity_hash(
            RELEASE_TRACE_AUDIT_DOMAIN,
            self._identity_material(),
        )
        if self.release_trace_audit_id != expected_audit_id:
            raise ReleaseApplicabilityError(
                "release trace audit identity mismatch"
            )

    def _identity_material(self) -> Dict[str, Any]:
        return {
            "trace_contract_version": self.trace_contract_version,
            "simulation_id": self.simulation_id,
            "taskset_id": self.taskset_id,
            "taskset_hash": self.taskset_hash,
            "release_projection_id": self.release_projection_id,
            "release_vector_hash": self.release_vector_hash,
            "scheduler": self.scheduler,
            "release_horizon": self.release_horizon,
            "observation_horizon": self.observation_horizon,
            "release_cutoff_enabled": self.release_cutoff_enabled,
            "observation_horizon_reached": (
                self.observation_horizon_reached
            ),
            "observed_simulation_end": self.observed_simulation_end,
            "simulation_completion_reason": (
                self.simulation_completion_reason
            ),
            "simulation_outcome": self.simulation_outcome,
            "tasks": [row.material() for row in self.tasks],
            "expected_release_count": self.expected_release_count,
            "expected_arrival_set_digest": (
                self.expected_arrival_set_digest
            ),
            "observed_arrival_set_digest": (
                self.observed_arrival_set_digest
            ),
            "samples_digest": self.samples_digest,
            "minimum_release_energy_exact": _fraction_material(
                self.minimum_release_energy_exact
            ),
        }

    @property
    def evaluated_release_count(self) -> int:
        return len(self.samples)

    def material(self) -> Dict[str, Any]:
        return {
            **self._identity_material(),
            "evaluated_release_count": self.evaluated_release_count,
            "release_trace_audit_id": self.release_trace_audit_id,
        }

    @classmethod
    def _validated(cls, **values: Any) -> "ReleaseTraceAudit":
        result = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(result, name, value)
        object.__setattr__(
            result, "_validation_token", _RELEASE_TRACE_AUDIT_TOKEN
        )
        result.__post_init__()
        return result


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
    exact_joules = Fraction.from_float(binary64) / 1000
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
    expected_certificate: TasksetIdentityCertificate,
    expected_projection: ReleaseProjection,
    window: ReleaseObservationWindow,
    expected_scheduler: str = TARGET_SCHEDULER,
) -> ReleaseTraceAudit:
    """Validate V2 cutoff, complete arrivals, and exact release snapshots."""

    simulation_id = _sha256(
        expected_simulation_id, "expected_simulation_id"
    )
    taskset_hash = _sha256(
        expected_taskset_hash, "expected_taskset_hash"
    )
    if type(expected_certificate) is not TasksetIdentityCertificate:
        raise ReleaseApplicabilityError(
            "expected_certificate must be a TasksetIdentityCertificate"
        )
    if type(expected_projection) is not ReleaseProjection:
        raise ReleaseApplicabilityError(
            "expected_projection must be a ReleaseProjection"
        )
    _validate_projection_binding(
        expected_certificate, expected_projection
    )
    if expected_certificate.taskset_hash != taskset_hash:
        raise ReleaseApplicabilityError(
            "expected taskset certificate/hash mismatch"
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
    observed_end = _trace_tick(
        data.get("observed_simulation_end_ms"),
        "observed simulation horizon",
    )
    completion_reason = data.get("simulation_completion_reason")
    if (
        data.get("simulation_completed") is not True
        or completion_reason != "reached_horizon"
        or _trace_tick(
            data.get("expected_simulation_horizon_ms"),
            "expected simulation horizon",
        )
        != window.observation_horizon
        or observed_end != window.observation_horizon
    ):
        raise ReleaseApplicabilityError(
            "simulation observation horizon is incomplete"
        )

    tasks = []
    runtime_names: Dict[str, str] = {}
    for index, row in enumerate(task_payload):
        if not isinstance(row, Mapping):
            raise ReleaseApplicabilityError(
                f"task payload {index} is not a mapping"
        )
        task_id = _canonical_string(row.get("task_id"), "task_id")
        rank = _plain_int(row.get("priority_rank"), "priority_rank")
        wcet = _plain_int(row.get("C"), "wcet", 1)
        deadline = _plain_int(row.get("D"), "relative deadline", 1)
        period = _plain_int(row.get("T"), "period", 1)
        offset = _plain_int(
            row.get("arrival_offset"), "arrival_offset"
        )
        if "ph" in row:
            phase = _plain_int(row.get("ph"), "ph")
            if phase != offset:
                raise ReleaseApplicabilityError(
                    "payload offset/ph mismatch"
                )
        task = ReleaseTaskParameters(
            task_id, rank, wcet, deadline, period, offset
        )
        if index >= len(expected_projection.offsets):
            raise ReleaseApplicabilityError(
                "payload/projection task count mismatch"
            )
        certificate_task = expected_certificate.tasks[index]
        if (
            certificate_task.task_id != task_id
            or certificate_task.priority_rank != rank
            or certificate_task.wcet != wcet
            or certificate_task.relative_deadline != deadline
            or certificate_task.period != period
        ):
            raise ReleaseApplicabilityError(
                "simulation payload/taskset certificate mismatch"
            )
        if window.release_horizon - 1 + deadline > window.observation_horizon:
            raise ReleaseApplicabilityError(
                "observation horizon does not cover a pre-cutoff deadline"
            )
        projected = expected_projection.offsets[index]
        if (
            projected.task_id != task_id
            or projected.priority_rank != rank
            or projected.period != period
            or projected.arrival_offset != offset
        ):
            raise ReleaseApplicabilityError(
                "payload offset with projection mismatch"
            )
        name = runtime_task_name_for_source_id(task_id)
        if name in runtime_names:
            raise ReleaseApplicabilityError(
                "duplicate runtime task name"
            )
        runtime_names[name] = task_id
        tasks.append(task)
    canonical_tasks = tuple(tasks)
    if len(canonical_tasks) != len(expected_projection.offsets):
        raise ReleaseApplicabilityError(
            "payload/projection task count mismatch"
        )
    ranks = [row.priority_rank for row in canonical_tasks]
    task_ids = [row.task_id for row in canonical_tasks]
    if ranks != list(range(len(canonical_tasks))):
        raise ReleaseApplicabilityError(
            "payload ranks are not contiguous/canonical"
        )
    if len(set(task_ids)) != len(task_ids):
        raise ReleaseApplicabilityError(
            "duplicate task identity in payload"
        )
    expected = _expected_arrivals(
        canonical_tasks, window.release_horizon
    )
    expected_set = set(expected)
    rank_by_task = {
        row.task_id: row.priority_rank for row in canonical_tasks
    }
    task_by_id = {
        row.task_id: row for row in canonical_tasks
    }

    observed = set()
    arrival_positions: Dict[Tuple[str, int], int] = {}
    snapshots: Dict[Tuple[str, int], Fraction] = {}
    snapshot_positions: Dict[Tuple[str, int], int] = {}
    first_consumption_position: Dict[int, int] = {}
    deadline_miss = False
    deadline_miss_keys = set()
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
        event_type = event.get("event_type")
        if event_type in {"scheduled", "energy_consumption"}:
            first_consumption_position.setdefault(event_time, position)
        if event_type == "dline_miss":
            name = event.get("task_name")
            if name not in runtime_names:
                raise ReleaseApplicabilityError(
                    "deadline miss has unknown runtime task name"
                )
            release = _trace_tick(
                event.get("arrival_time"),
                "deadline miss release",
            )
            task_id = runtime_names[str(name)]
            key = (task_id, release)
            task = task_by_id[task_id]
            deadline = _trace_tick(
                event.get("deadline"),
                "deadline miss deadline",
            )
            remaining = event.get("remaining_execution_ms")
            if (
                key not in expected_set
                or deadline != release + task.relative_deadline
                or event_time < deadline
                or type(remaining) not in {int, float}
                or not math.isfinite(float(remaining))
                or float(remaining) <= 0
                or event.get("job_id")
                != f"{name}@{release}"
                or key in deadline_miss_keys
            ):
                raise ReleaseApplicabilityError(
                    "invalid simulation deadline miss evidence"
                )
            deadline_miss_keys.add(key)
            deadline_miss = True
        if event_type not in {"arrival", "release_energy_snapshot"}:
            continue
        name = event.get("task_name")
        if name not in runtime_names:
            label = (
                "arrival"
                if event_type == "arrival"
                else "snapshot"
            )
            raise ReleaseApplicabilityError(
                f"{label} has unknown runtime task name"
            )
        release = _trace_tick(
            event.get("arrival_time"),
            f"{event_type} release",
        )
        if release != event_time:
            raise ReleaseApplicabilityError(
                f"{event_type} event time/release mismatch"
            )
        if release >= window.release_horizon:
            raise ReleaseApplicabilityError(
                "trace contains release at/after release horizon"
        )
        task_id = runtime_names[str(name)]
        key = (task_id, release)
        if event_type == "arrival":
            if key in observed:
                raise ReleaseApplicabilityError(
                    "duplicate arrivals"
                )
            if key not in expected_set:
                raise ReleaseApplicabilityError(
                    "extra arrivals: off-grid arrivals"
                )
            observed.add(key)
            arrival_positions[key] = position
            continue
        if event.get("sampling_stage") != RELEASE_SNAPSHOT_STAGE:
            raise ReleaseApplicabilityError(
                "release energy snapshot stage mismatch"
            )
        if event.get("scheduler") != scheduler:
            raise ReleaseApplicabilityError(
                "release energy snapshot scheduler mismatch"
            )
        if (
            event.get("trace_contract_version")
            != SIMULATOR_TRACE_CONTRACT_VERSION
        ):
            raise ReleaseApplicabilityError(
                "release energy snapshot contract mismatch"
            )
        if key not in expected_set:
            raise ReleaseApplicabilityError(
                "release energy snapshot without arrival"
            )
        if key in snapshots:
            raise ReleaseApplicabilityError(
                "duplicate release energy snapshots"
            )
        snapshots[key] = _exact_trace_energy_j(
            event.get("available_energy_mJ"),
            "release snapshot available_energy_mJ",
        )
        snapshot_positions[key] = position

    missing = expected_set - observed
    if missing:
        raise ReleaseApplicabilityError(
            f"missing arrivals: {len(missing)}"
        )
    extra = observed - expected_set
    if extra:
        raise ReleaseApplicabilityError(
            f"extra arrivals: {len(extra)}"
        )
    snapshot_keys = set(snapshots)
    without_arrival = snapshot_keys - observed
    if without_arrival:
        raise ReleaseApplicabilityError(
            "release energy snapshot without arrival"
        )
    missing_snapshots = expected_set - snapshot_keys
    if missing_snapshots:
        raise ReleaseApplicabilityError(
            f"missing release energy snapshots: {len(missing_snapshots)}"
        )
    for key in expected:
        if snapshot_positions[key] <= arrival_positions[key]:
            raise ReleaseApplicabilityError(
                "release energy snapshot precedes its arrival"
            )
        consumption = first_consumption_position.get(key[1])
        if (
            consumption is not None
            and snapshot_positions[key] >= consumption
        ):
            raise ReleaseApplicabilityError(
                "release energy snapshot appears after consumption"
            )
    energies_by_time: Dict[int, Fraction] = {}
    for key, energy in snapshots.items():
        prior = energies_by_time.setdefault(key[1], energy)
        if prior != energy:
            raise ReleaseApplicabilityError(
                "same-tick release energy snapshots differ"
            )

    samples = tuple(
        ReleaseEnergySample(
            task_id,
            rank_by_task[task_id],
            release,
            snapshots[(task_id, release)],
        )
        for task_id, release in expected
    )
    expected_digest = _arrival_set_digest(
        EXPECTED_ARRIVAL_SET_DOMAIN, expected
    )
    observed_keys = tuple(
        (row.task_id, row.release) for row in samples
    )
    observed_digest = _arrival_set_digest(
        OBSERVED_ARRIVAL_SET_DOMAIN, observed_keys
    )
    sample_digest = _samples_digest(samples)
    minimum = min(row.energy_exact for row in samples)
    values = {
        "simulation_id": simulation_id,
        "taskset_id": expected_certificate.taskset_id,
        "taskset_hash": taskset_hash,
        "release_projection_id": (
            expected_projection.release_projection_id
        ),
        "release_vector_hash": expected_projection.release_vector_hash,
        "scheduler": scheduler,
        "trace_contract_version": SIMULATOR_TRACE_CONTRACT_VERSION,
        "release_horizon": window.release_horizon,
        "observation_horizon": window.observation_horizon,
        "release_cutoff_enabled": True,
        "observation_horizon_reached": True,
        "observed_simulation_end": observed_end,
        "simulation_completion_reason": str(completion_reason),
        "simulation_outcome": (
            SIM_DEADLINE_MISS
            if deadline_miss
            else SIM_NO_DEADLINE_MISS
        ),
        "tasks": canonical_tasks,
        "expected_release_count": len(expected),
        "expected_arrival_set_digest": expected_digest,
        "samples": samples,
        "observed_arrival_set_digest": observed_digest,
        "samples_digest": sample_digest,
        "minimum_release_energy_exact": minimum,
    }
    provisional = object.__new__(ReleaseTraceAudit)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    audit_id = _identity_hash(
        RELEASE_TRACE_AUDIT_DOMAIN,
        provisional._identity_material(),
    )
    return ReleaseTraceAudit._validated(
        **values, release_trace_audit_id=audit_id
    )


@dataclass(frozen=True)
class E0Evaluation:
    release_trace_audit_id: str
    expected_release_count: int
    samples_digest: str
    minimum_release_energy_exact: Fraction
    first_violating_task_id: Optional[str]
    first_violating_release: Optional[int]
    requested_e0: Fraction
    e0_condition_satisfied: bool
    status: str
    evaluation_id: str

    def __post_init__(self) -> None:
        _sha256(
            self.release_trace_audit_id,
            "release_trace_audit_id",
        )
        _sha256(self.samples_digest, "samples_digest")
        _sha256(self.evaluation_id, "evaluation_id")
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
            self.expected_release_count,
            "expected_release_count",
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
        if self.evaluation_id != _identity_hash(
            E0_EVALUATION_DOMAIN, self._identity_material()
        ):
            raise ReleaseApplicabilityError(
                "E0 evaluation identity mismatch"
            )

    def _identity_material(self) -> Dict[str, Any]:
        return {
            "release_trace_audit_id": self.release_trace_audit_id,
            "expected_release_count": self.expected_release_count,
            "samples_digest": self.samples_digest,
            "minimum_release_energy_exact": _fraction_material(
                self.minimum_release_energy_exact
            ),
            "first_violation": (
                []
                if self.first_violating_task_id is None
                else [{
                    "task_id": self.first_violating_task_id,
                    "release": self.first_violating_release,
                }]
            ),
            "requested_e0": _fraction_material(self.requested_e0),
            "e0_condition_satisfied": self.e0_condition_satisfied,
            "status": self.status,
        }

    def row(self) -> Dict[str, Any]:
        return {
            **self._identity_material(),
            "evaluated_release_count": self.expected_release_count,
            "first_violating_task_id": self.first_violating_task_id,
            "first_violating_release": self.first_violating_release,
            "evaluation_id": self.evaluation_id,
        }

    material = row

    @property
    def evaluated_release_count(self) -> int:
        return self.expected_release_count


def evaluate_e0_condition(
    audit: ReleaseTraceAudit, requested_e0: Any
) -> E0Evaluation:
    if (
        type(audit) is not ReleaseTraceAudit
        or audit._validation_token is not _RELEASE_TRACE_AUDIT_TOKEN
    ):
        raise ReleaseApplicabilityError(
            "audit must be a validated ReleaseTraceAudit"
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
    values = {
        "release_trace_audit_id": audit.release_trace_audit_id,
        "expected_release_count": audit.expected_release_count,
        "samples_digest": audit.samples_digest,
        "minimum_release_energy_exact": (
            audit.minimum_release_energy_exact
        ),
        "first_violating_task_id": (
            None if violating is None else violating.task_id
        ),
        "first_violating_release": (
            None if violating is None else violating.release
        ),
        "requested_e0": exact_e0,
        "e0_condition_satisfied": satisfied,
        "status": (
            E0_CONDITION_SATISFIED
            if satisfied
            else E0_CONDITION_NOT_SATISFIED
        ),
    }
    provisional = object.__new__(E0Evaluation)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    return E0Evaluation(
        **values,
        evaluation_id=_identity_hash(
            E0_EVALUATION_DOMAIN,
            provisional._identity_material(),
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
class NoOverflowEvidence:
    initial_battery: Fraction
    battery_capacity: Fraction
    offered_harvest: Fraction
    required_margin: Fraction
    required_capacity: Fraction
    remaining_headroom: Fraction
    valid: bool
    service_identity: str
    observation_horizon: int
    evidence_id: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.initial_battery, "initial battery"),
            (self.battery_capacity, "battery capacity"),
            (self.offered_harvest, "offered harvest"),
            (self.required_margin, "required margin"),
        ):
            if type(value) is not Fraction or value < 0:
                raise ReleaseApplicabilityError(
                    f"{label} must be a non-negative exact Fraction"
                )
        if (
            self.battery_capacity <= 0
            or self.initial_battery > self.battery_capacity
        ):
            raise ReleaseApplicabilityError(
                "no-overflow battery values are invalid"
            )
        if type(self.required_capacity) is not Fraction:
            raise ReleaseApplicabilityError(
                "required capacity must be an exact Fraction"
            )
        if type(self.remaining_headroom) is not Fraction:
            raise ReleaseApplicabilityError(
                "remaining headroom must be an exact Fraction"
            )
        expected_required = self.initial_battery + self.offered_harvest
        expected_headroom = self.battery_capacity - expected_required
        expected_valid = expected_headroom >= self.required_margin
        if (
            self.required_capacity != expected_required
            or self.remaining_headroom != expected_headroom
            or self.valid is not expected_valid
        ):
            raise ReleaseApplicabilityError(
                "no-overflow evidence derivation mismatch"
            )
        _sha256(self.service_identity, "service_identity")
        _plain_int(
            self.observation_horizon,
            "no-overflow observation_horizon",
            1,
        )
        _sha256(self.evidence_id, "no-overflow evidence_id")
        if self.evidence_id != _identity_hash(
            NO_OVERFLOW_EVIDENCE_DOMAIN,
            self._identity_material(),
        ):
            raise ReleaseApplicabilityError(
                "no-overflow evidence identity mismatch"
            )

    def _identity_material(self) -> Dict[str, Any]:
        return {
            "initial_battery": _fraction_material(
                self.initial_battery
            ),
            "battery_capacity": _fraction_material(
                self.battery_capacity
            ),
            "offered_harvest": _fraction_material(
                self.offered_harvest
            ),
            "required_margin": _fraction_material(
                self.required_margin
            ),
            "required_capacity": _fraction_material(
                self.required_capacity
            ),
            "remaining_headroom": _fraction_material(
                self.remaining_headroom
            ),
            "valid": self.valid,
            "service_identity": self.service_identity,
            "observation_horizon": self.observation_horizon,
        }

    def material(self) -> Dict[str, Any]:
        return {
            **self._identity_material(),
            "evidence_id": self.evidence_id,
        }


def _exact_evidence_energy(value: Any, label: str) -> Fraction:
    try:
        return exact_energy.exact_e0_lower_bound(value, label)
    except exact_energy.ExactEnergyError as exc:
        raise ReleaseApplicabilityError(str(exc)) from exc


def build_no_overflow_evidence(
    *,
    initial_battery: Any,
    battery_capacity: Any,
    offered_harvest: Any,
    required_margin: Any,
    service_identity: str,
    observation_horizon: int,
) -> NoOverflowEvidence:
    initial = _exact_evidence_energy(
        initial_battery, "initial battery"
    )
    capacity = _exact_evidence_energy(
        battery_capacity, "battery capacity"
    )
    harvest = _exact_evidence_energy(
        offered_harvest, "offered harvest"
    )
    margin = _exact_evidence_energy(
        required_margin, "required margin"
    )
    service = _sha256(service_identity, "service_identity")
    horizon = _plain_int(
        observation_horizon, "no-overflow observation_horizon", 1
    )
    required = initial + harvest
    headroom = capacity - required
    values = {
        "initial_battery": initial,
        "battery_capacity": capacity,
        "offered_harvest": harvest,
        "required_margin": margin,
        "required_capacity": required,
        "remaining_headroom": headroom,
        "valid": headroom >= margin,
        "service_identity": service,
        "observation_horizon": horizon,
    }
    provisional = object.__new__(NoOverflowEvidence)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    return NoOverflowEvidence(
        **values,
        evidence_id=_identity_hash(
            NO_OVERFLOW_EVIDENCE_DOMAIN,
            provisional._identity_material(),
        ),
    )


_VALIDATED_SIMULATION_EVIDENCE_TOKEN = object()


@dataclass(frozen=True)
class ValidatedSimulationEvidence:
    simulation_id: str
    taskset_id: str
    taskset_hash: str
    release_projection_id: str
    release_vector_hash: str
    scheduler: str
    service_identity: str
    initial_battery: Fraction
    battery_capacity: Fraction
    release_horizon: int
    observation_horizon: int
    applicability_track: str
    trace_contract_version: str
    release_cutoff_enabled: bool
    observed_simulation_end: int
    completion_reason: str
    simulation_outcome: str
    release_trace_audit_id: str
    evidence_id: str
    _validation_token: Any = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if (
            self._validation_token
            is not _VALIDATED_SIMULATION_EVIDENCE_TOKEN
        ):
            raise ReleaseApplicabilityError(
                "ValidatedSimulationEvidence must come from validated trace"
            )
        for value, label in (
            (self.simulation_id, "simulation_id"),
            (self.taskset_id, "taskset_id"),
            (self.taskset_hash, "taskset_hash"),
            (self.release_projection_id, "release_projection_id"),
            (self.release_vector_hash, "release_vector_hash"),
            (self.service_identity, "service_identity"),
            (self.release_trace_audit_id, "release_trace_audit_id"),
            (self.evidence_id, "simulation evidence_id"),
        ):
            _sha256(value, label)
        _canonical_string(self.scheduler, "scheduler")
        if (
            type(self.initial_battery) is not Fraction
            or self.initial_battery < 0
            or type(self.battery_capacity) is not Fraction
            or self.battery_capacity <= 0
            or self.initial_battery > self.battery_capacity
        ):
            raise ReleaseApplicabilityError(
                "validated simulation battery values are invalid"
            )
        window = ReleaseObservationWindow(
            self.release_horizon,
            self.observation_horizon - self.release_horizon,
            self.observation_horizon,
        )
        if self.applicability_track not in APPLICABILITY_TRACKS:
            raise ReleaseApplicabilityError(
                "unknown applicability track"
            )
        if self.trace_contract_version != SIMULATOR_TRACE_CONTRACT_VERSION:
            raise ReleaseApplicabilityError(
                "validated simulation requires trace V2"
            )
        if (
            self.release_cutoff_enabled is not True
            or self.observed_simulation_end != self.observation_horizon
            or self.completion_reason != "reached_horizon"
        ):
            raise ReleaseApplicabilityError(
                "validated simulation is incomplete"
            )
        if self.simulation_outcome not in {
            SIM_DEADLINE_MISS,
            SIM_NO_DEADLINE_MISS,
        }:
            raise ReleaseApplicabilityError(
                "invalid simulation outcome"
            )
        expected_simulation_id = simulation_applicability_identity(
            taskset_id=self.taskset_id,
            release_projection_id=self.release_projection_id,
            scheduler=self.scheduler,
            service_identity=self.service_identity,
            initial_battery=self.initial_battery,
            battery_capacity=self.battery_capacity,
            window=window,
            applicability_track=self.applicability_track,
        )
        if self.simulation_id != expected_simulation_id:
            raise ReleaseApplicabilityError(
                "validated simulation identity derivation mismatch"
            )
        if self.evidence_id != _identity_hash(
            VALIDATED_SIMULATION_EVIDENCE_DOMAIN,
            self._identity_material(),
        ):
            raise ReleaseApplicabilityError(
                "validated simulation evidence identity mismatch"
            )

    def _identity_material(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "taskset_id": self.taskset_id,
            "taskset_hash": self.taskset_hash,
            "release_projection_id": self.release_projection_id,
            "release_vector_hash": self.release_vector_hash,
            "scheduler": self.scheduler,
            "service_identity": self.service_identity,
            "initial_battery": _fraction_material(
                self.initial_battery
            ),
            "battery_capacity": _fraction_material(
                self.battery_capacity
            ),
            "release_horizon": self.release_horizon,
            "observation_horizon": self.observation_horizon,
            "applicability_track": self.applicability_track,
            "trace_contract_version": self.trace_contract_version,
            "release_cutoff_enabled": self.release_cutoff_enabled,
            "observed_simulation_end": self.observed_simulation_end,
            "completion_reason": self.completion_reason,
            "simulation_outcome": self.simulation_outcome,
            "release_trace_audit_id": self.release_trace_audit_id,
        }

    def material(self) -> Dict[str, Any]:
        return {
            **self._identity_material(),
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def _validated(
        cls, **values: Any
    ) -> "ValidatedSimulationEvidence":
        result = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(result, name, value)
        object.__setattr__(
            result,
            "_validation_token",
            _VALIDATED_SIMULATION_EVIDENCE_TOKEN,
        )
        result.__post_init__()
        return result


def validate_simulation_evidence(
    audit: ReleaseTraceAudit,
    *,
    service_identity: str,
    initial_battery: Any,
    battery_capacity: Any,
    applicability_track: str,
) -> ValidatedSimulationEvidence:
    if (
        type(audit) is not ReleaseTraceAudit
        or audit._validation_token is not _RELEASE_TRACE_AUDIT_TOKEN
    ):
        raise ReleaseApplicabilityError(
            "simulation evidence requires a validated release audit"
        )
    service = _sha256(service_identity, "service_identity")
    initial = _exact_evidence_energy(
        initial_battery, "initial battery"
    )
    capacity = _exact_evidence_energy(
        battery_capacity, "battery capacity"
    )
    values = {
        "simulation_id": audit.simulation_id,
        "taskset_id": audit.taskset_id,
        "taskset_hash": audit.taskset_hash,
        "release_projection_id": audit.release_projection_id,
        "release_vector_hash": audit.release_vector_hash,
        "scheduler": audit.scheduler,
        "service_identity": service,
        "initial_battery": initial,
        "battery_capacity": capacity,
        "release_horizon": audit.release_horizon,
        "observation_horizon": audit.observation_horizon,
        "applicability_track": applicability_track,
        "trace_contract_version": audit.trace_contract_version,
        "release_cutoff_enabled": audit.release_cutoff_enabled,
        "observed_simulation_end": audit.observed_simulation_end,
        "completion_reason": audit.simulation_completion_reason,
        "simulation_outcome": audit.simulation_outcome,
        "release_trace_audit_id": audit.release_trace_audit_id,
    }
    provisional = object.__new__(ValidatedSimulationEvidence)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    return ValidatedSimulationEvidence._validated(
        **values,
        evidence_id=_identity_hash(
            VALIDATED_SIMULATION_EVIDENCE_DOMAIN,
            provisional._identity_material(),
        ),
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
    release_trace_audit: ReleaseTraceAudit,
    requested_e0: Any,
    e0_evaluation: E0Evaluation,
    no_overflow_evidence: NoOverflowEvidence,
    simulation_evidence: ValidatedSimulationEvidence,
    expected_taskset_id: str,
    expected_taskset_hash: str,
    expected_release_projection_id: str,
    expected_simulation_id: str,
    rta_outcome: str,
    simulation_outcome: str,
) -> ApplicabilityAssessment:
    if requested_track not in APPLICABILITY_TRACKS:
        raise ReleaseApplicabilityError("unknown applicability track")
    if (
        type(release_trace_audit) is not ReleaseTraceAudit
        or release_trace_audit._validation_token
        is not _RELEASE_TRACE_AUDIT_TOKEN
    ):
        raise ReleaseApplicabilityError(
            "applicability requires a validated ReleaseTraceAudit"
        )
    if type(e0_evaluation) is not E0Evaluation:
        raise ReleaseApplicabilityError(
            "e0_evaluation must be an E0Evaluation"
        )
    if type(no_overflow_evidence) is not NoOverflowEvidence:
        raise ReleaseApplicabilityError(
            "no_overflow_evidence must be strict evidence"
        )
    if (
        type(simulation_evidence) is not ValidatedSimulationEvidence
        or simulation_evidence._validation_token
        is not _VALIDATED_SIMULATION_EVIDENCE_TOKEN
    ):
        raise ReleaseApplicabilityError(
            "simulation_evidence must come from validated trace"
        )
    taskset_id = _sha256(expected_taskset_id, "expected_taskset_id")
    taskset_hash = _sha256(
        expected_taskset_hash, "expected_taskset_hash"
    )
    projection_id = _sha256(
        expected_release_projection_id,
        "expected_release_projection_id",
    )
    simulation_id = _sha256(
        expected_simulation_id, "expected_simulation_id"
    )
    if rta_outcome not in {RTA_PASS, RTA_FAIL}:
        raise ReleaseApplicabilityError("invalid RTA outcome")
    if simulation_outcome not in {
        SIM_DEADLINE_MISS,
        SIM_NO_DEADLINE_MISS,
    }:
        raise ReleaseApplicabilityError("invalid simulation outcome")
    audit = release_trace_audit
    evidence = simulation_evidence
    if (
        evidence.release_trace_audit_id != audit.release_trace_audit_id
        or evidence.simulation_id != audit.simulation_id
        or evidence.taskset_id != audit.taskset_id
        or evidence.taskset_hash != audit.taskset_hash
        or evidence.release_projection_id
        != audit.release_projection_id
        or evidence.release_vector_hash != audit.release_vector_hash
        or evidence.scheduler != audit.scheduler
        or evidence.release_horizon != audit.release_horizon
        or evidence.observation_horizon != audit.observation_horizon
        or evidence.trace_contract_version != audit.trace_contract_version
        or evidence.simulation_outcome != audit.simulation_outcome
    ):
        raise ReleaseApplicabilityError(
            "simulation evidence/release audit mismatch"
        )
    if (
        audit.taskset_id != taskset_id
        or audit.taskset_hash != taskset_hash
        or audit.release_projection_id != projection_id
        or audit.simulation_id != simulation_id
    ):
        raise ReleaseApplicabilityError(
            "expected applicability identity mismatch"
        )
    if evidence.applicability_track != requested_track:
        raise ReleaseApplicabilityError(
            "simulation evidence applicability track mismatch"
        )
    if evidence.scheduler != TARGET_SCHEDULER:
        raise ReleaseApplicabilityError(
            "applicability scheduler mismatch"
        )
    if simulation_outcome != evidence.simulation_outcome:
        raise ReleaseApplicabilityError(
            "simulation outcome/evidence mismatch"
        )
    if (
        no_overflow_evidence.service_identity
        != evidence.service_identity
        or no_overflow_evidence.initial_battery
        != evidence.initial_battery
        or no_overflow_evidence.battery_capacity
        != evidence.battery_capacity
        or no_overflow_evidence.observation_horizon
        != evidence.observation_horizon
    ):
        raise ReleaseApplicabilityError(
            "no-overflow/simulation evidence mismatch"
        )
    recomputed_e0 = evaluate_e0_condition(audit, requested_e0)
    if e0_evaluation != recomputed_e0:
        raise ReleaseApplicabilityError(
            "E0 evaluation/release audit mismatch"
        )
    if not recomputed_e0.e0_condition_satisfied:
        return ApplicabilityAssessment(
            E0_CONDITION_NOT_SATISFIED,
            False,
            False,
            False,
            "release_energy_below_requested_e0",
        )
    observed_difference = (
        rta_outcome == RTA_PASS
        and simulation_outcome == SIM_DEADLINE_MISS
    )
    if requested_track == FINITE_BATTERY_EMPIRICAL:
        return ApplicabilityAssessment(
            FINITE_BATTERY_EMPIRICAL,
            False,
            False,
            observed_difference,
            "finite_battery_empirical_comparison",
        )
    if not no_overflow_evidence.valid:
        raise ReleaseApplicabilityError(
            "THEOREM_ALIGNED prerequisites failed: no_overflow"
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
    "NoOverflowEvidence",
    "RELEASE_HORIZON",
    "RELEASE_MODES",
    "RELEASE_OFFSET_DOMAIN",
    "RELEASE_PROJECTION_CONTRACT_VERSION",
    "RELEASE_PROJECTION_DOMAIN",
    "RELEASE_VECTOR_DOMAIN",
    "RELEASE_SNAPSHOT_STAGE",
    "RTA_FAIL",
    "RTA_PASS",
    "ReleaseApplicabilityError",
    "ReleaseEnergySample",
    "ReleaseObservationWindow",
    "ReleaseOffset",
    "ReleaseProjection",
    "ReleaseTaskParameters",
    "ReleaseTraceAudit",
    "SIM_DEADLINE_MISS",
    "SIM_NO_DEADLINE_MISS",
    "SIMULATION_APPLICABILITY_CONTRACT_VERSION",
    "SIMULATION_ID_DOMAIN",
    "SIMULATOR_TRACE_CONTRACT_VERSION",
    "SYNC_V1",
    "TARGET_SCHEDULER",
    "THEOREM_ALIGNED",
    "ValidatedSimulationEvidence",
    "apply_release_projection",
    "assess_applicability",
    "build_no_overflow_evidence",
    "build_release_projection",
    "derive_release_offset",
    "evaluate_e0_condition",
    "evaluate_e0_grid",
    "parse_release_trace",
    "project_certificate_for_simulation",
    "simulation_applicability_identity",
    "simulation_identity_material",
    "validate_simulation_evidence",
]
