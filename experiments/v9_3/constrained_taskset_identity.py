"""Exact constrained-deadline generation and task-set identities for v9.3.

This module is deliberately independent of the formal runners, result schemas,
RTA methods, service curves, release offsets, and authorization machinery.  It
builds a task skeleton first and applies deadlines and power variants without
using or mutating any Python random-number-generator state.

The existing exact-energy input identity remains authoritative for RTA inputs.
The power-vector helper below is only a thin wrapper around the already frozen
``ASAP_BLOCK:V9.3:POWER_VECTOR:v1`` identity used by ``taskset_store``.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Dict, Optional, Sequence, Tuple

from .cell_model import (
    GENERATION_DIMENSIONS_SEED_MODE,
    TASKSET_SEED_DERIVATION_DOMAIN,
    derive_seed,
)
from .config import domain_hash as legacy_domain_hash
from .config import fraction_text


GENERATION_REQUEST_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_GENERATION_REQUEST_V1"
)
TASKSET_SKELETON_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_TASKSET_SKELETON_V1"
)
TASKSET_CONTENT_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_TASKSET_CONTENT_V1"
)
TASKSET_IDENTITY_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_TASKSET_ID_V1"
)
DEADLINE_CONTRACT_VERSION = "ASAP_BLOCK_V9_3_DEADLINE_V1"

GENERATION_REQUEST_DOMAIN = "ASAP_BLOCK:V9.3:GENERATION_REQUEST:v1"
TASKSET_SKELETON_DOMAIN = "ASAP_BLOCK:V9.3:TASKSET_SKELETON:v1"
TASKSET_CONTENT_DOMAIN = "ASAP_BLOCK:V9.3:TASKSET_CONTENT:v1"
TASKSET_ID_DOMAIN = "ASAP_BLOCK:V9.3:TASKSET_ID:v1"
DEADLINE_DRAW_DOMAIN = "ASAP_BLOCK:V9.3:DEADLINE_DRAW:v1"

# This domain is pre-existing and intentionally not versioned again here.
POWER_VECTOR_DOMAIN = "ASAP_BLOCK:V9.3:POWER_VECTOR:v1"

IMPLICIT_DEADLINE_MODE = "implicit"
CONSTRAINED_UNIFORM_SLACK_MODE = "constrained_uniform_slack_v1"
FIXED_SLACK_FRACTION_VARIANT = "fixed_slack_fraction_v1"
PRIMARY_DEADLINE_GENERATION_MODES = frozenset({
    IMPLICIT_DEADLINE_MODE,
    CONSTRAINED_UNIFORM_SLACK_MODE,
})

BASE_POWER_VARIANT = "base_power_v1"
SCALED_POWER_VARIANT = "scaled_power_v1"

UINT64_MAX = (1 << 64) - 1
CERTIFICATE_SCHEMA = "ASAP_BLOCK_V9_3_TASKSET_IDENTITY_CERTIFICATE_V1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TasksetIdentityError(ValueError):
    """Raised when generation or identity material is not canonical."""


def _plain_int(value: Any, label: str, minimum: Optional[int] = None) -> int:
    if type(value) is not int:
        raise TasksetIdentityError(f"{label} must be a plain integer")
    if minimum is not None and value < minimum:
        raise TasksetIdentityError(f"{label} must be at least {minimum}")
    return value


def _canonical_string(value: Any, label: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise TasksetIdentityError(
            f"{label} must be a non-empty canonical string"
        )
    if unicodedata.normalize("NFC", value) != value:
        raise TasksetIdentityError(f"{label} must already be NFC-normalized")
    if unicodedata.combining(value[0]):
        raise TasksetIdentityError(
            f"{label} must not start with a combining character"
        )
    if any(
        unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise TasksetIdentityError(f"{label} must not contain control characters")
    return value


def _sha256(value: Any, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise TasksetIdentityError(
            f"{label} must be a canonical lowercase SHA-256"
        )
    return value


def _exact_fraction(
    value: Any,
    label: str,
    *,
    minimum: Optional[Fraction] = None,
    maximum: Optional[Fraction] = None,
) -> Fraction:
    if type(value) is not Fraction:
        raise TasksetIdentityError(f"{label} must be an exact Fraction")
    if value.denominator <= 0:
        raise TasksetIdentityError(f"{label} must have a positive denominator")
    if math.gcd(abs(value.numerator), value.denominator) != 1:
        raise TasksetIdentityError(f"{label} must be reduced")
    if minimum is not None and value < minimum:
        raise TasksetIdentityError(f"{label} is below its minimum")
    if maximum is not None and value > maximum:
        raise TasksetIdentityError(f"{label} is above its maximum")
    return value


def canonical_fraction_material(value: Fraction) -> Dict[str, int]:
    """Return the single canonical structured encoding for a rational."""

    exact = _exact_fraction(value, "fraction")
    return {
        "numerator": exact.numerator,
        "denominator": exact.denominator,
    }


def fraction_from_canonical_material(value: Any, label: str) -> Fraction:
    """Decode a reduced rational object and reject repairable encodings."""

    if type(value) is not dict or set(value) != {"numerator", "denominator"}:
        raise TasksetIdentityError(
            f"{label} must contain exactly numerator and denominator"
        )
    numerator = _plain_int(value["numerator"], f"{label}.numerator")
    denominator = _plain_int(
        value["denominator"], f"{label}.denominator", 1
    )
    if math.gcd(abs(numerator), denominator) != 1:
        raise TasksetIdentityError(f"{label} must be reduced")
    return Fraction(numerator, denominator)


def _validate_plain_identity_material(value: Any, path: str) -> None:
    if type(value) is str:
        _canonical_string(value, path)
        return
    if type(value) in {int, bool}:
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_plain_identity_material(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        if set(value) == {"numerator", "denominator"}:
            fraction_from_canonical_material(value, path)
            return
        for key, item in value.items():
            if type(key) is not str:
                raise TasksetIdentityError(
                    f"{path} contains a non-string object key"
                )
            _canonical_string(key, f"{path} object key")
            _validate_plain_identity_material(item, f"{path}.{key}")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TasksetIdentityError(f"{path} contains NaN or Inf")
        raise TasksetIdentityError(f"{path} contains a float")
    raise TasksetIdentityError(
        f"{path} contains unsupported identity type {type(value).__name__}"
    )


def canonical_identity_bytes(value: Any) -> bytes:
    """Serialize strict identity material without floats, reprs, or nulls."""

    _validate_plain_identity_material(value, "identity")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _identity_hash(domain: str, value: Any) -> str:
    _canonical_string(domain, "identity domain")
    try:
        prefix = domain.encode("ascii")
    except UnicodeEncodeError as exc:
        raise TasksetIdentityError("identity domain must be ASCII") from exc
    return hashlib.sha256(prefix + b"\0" + canonical_identity_bytes(value)).hexdigest()


def _exact_keys(value: Any, expected: Sequence[str], label: str) -> Dict[str, Any]:
    if type(value) is not dict or set(value) != set(expected):
        raise TasksetIdentityError(
            f"{label} does not have its exact canonical field set"
        )
    return value


@dataclass(frozen=True)
class GenerationRequest:
    """Every normalized input which can affect the generated C/T/base-P skeleton."""

    formal_master_seed: int
    formal_generation_id: str
    processors: int
    task_count: int
    target_normalized_utilization: Fraction
    replicate_index: int
    period_min: int
    period_max: int
    utilization_allocation_mode: str
    min_task_utilization: Fraction
    max_task_utilization: Fraction
    utilization_tolerance: Fraction
    wcet_rounding_mode: str
    generator_version: str
    power_generation_mode: str
    power_generation_contract_identity: str
    workload_candidate_identity: str
    priority_policy: str
    dag_generation_mode: str
    energy_aware_generation: bool

    def __post_init__(self) -> None:
        _plain_int(self.formal_master_seed, "formal_master_seed", 0)
        _sha256(self.formal_generation_id, "formal_generation_id")
        _plain_int(self.processors, "processors", 1)
        _plain_int(self.task_count, "task_count", 1)
        _exact_fraction(
            self.target_normalized_utilization,
            "target_normalized_utilization",
            minimum=Fraction(0),
            maximum=Fraction(1),
        )
        if self.target_normalized_utilization <= 0:
            raise TasksetIdentityError(
                "target_normalized_utilization must be positive"
            )
        _plain_int(self.replicate_index, "replicate_index", 0)
        _plain_int(self.period_min, "period_min", 1)
        _plain_int(self.period_max, "period_max", 1)
        if self.period_min > self.period_max:
            raise TasksetIdentityError("period_min must not exceed period_max")
        _canonical_string(
            self.utilization_allocation_mode, "utilization_allocation_mode"
        )
        minimum = _exact_fraction(
            self.min_task_utilization,
            "min_task_utilization",
            minimum=Fraction(0),
            maximum=Fraction(1),
        )
        maximum = _exact_fraction(
            self.max_task_utilization,
            "max_task_utilization",
            minimum=Fraction(0),
            maximum=Fraction(1),
        )
        if minimum <= 0 or maximum <= 0 or minimum > maximum:
            raise TasksetIdentityError(
                "task utilization bounds must satisfy 0 < min <= max <= 1"
            )
        _exact_fraction(
            self.utilization_tolerance,
            "utilization_tolerance",
            minimum=Fraction(0),
        )
        for label in (
            "wcet_rounding_mode",
            "generator_version",
            "power_generation_mode",
            "priority_policy",
            "dag_generation_mode",
        ):
            _canonical_string(getattr(self, label), label)
        _sha256(
            self.power_generation_contract_identity,
            "power_generation_contract_identity",
        )
        _sha256(
            self.workload_candidate_identity,
            "workload_candidate_identity",
        )
        if type(self.energy_aware_generation) is not bool:
            raise TasksetIdentityError("energy_aware_generation must be bool")

    @property
    def generator_seed(self) -> int:
        """Return the sole formal generator seed derived by the frozen helper."""

        return derive_seed(
            self.formal_master_seed,
            self.formal_generation_id,
            self.replicate_index,
            seed_mode=GENERATION_DIMENSIONS_SEED_MODE,
        )

    def identity_material(self) -> Dict[str, Any]:
        return {
            "generation_contract_version": GENERATION_REQUEST_CONTRACT_VERSION,
            "formal_master_seed": self.formal_master_seed,
            "formal_generation_id": self.formal_generation_id,
            "seed_derivation_contract": TASKSET_SEED_DERIVATION_DOMAIN,
            "seed_derivation_mode": GENERATION_DIMENSIONS_SEED_MODE,
            "generator_seed": self.generator_seed,
            "processor_count": self.processors,
            "task_count": self.task_count,
            "target_normalized_utilization": canonical_fraction_material(
                self.target_normalized_utilization
            ),
            "replicate_index": self.replicate_index,
            "period_range": {
                "minimum": self.period_min,
                "maximum": self.period_max,
            },
            "utilization_allocation": {
                "mode": self.utilization_allocation_mode,
                "minimum_task_utilization": canonical_fraction_material(
                    self.min_task_utilization
                ),
                "maximum_task_utilization": canonical_fraction_material(
                    self.max_task_utilization
                ),
                "total_tolerance": canonical_fraction_material(
                    self.utilization_tolerance
                ),
            },
            "wcet_rounding_mode": self.wcet_rounding_mode,
            "generator_version": self.generator_version,
            "power_generation": {
                "mode": self.power_generation_mode,
                "contract_identity": self.power_generation_contract_identity,
                "workload_candidate_identity": self.workload_candidate_identity,
            },
            "priority_policy": self.priority_policy,
            "dag_generation_mode": self.dag_generation_mode,
            "energy_aware_generation": self.energy_aware_generation,
        }

    @classmethod
    def from_material(cls, value: Any) -> "GenerationRequest":
        material = _exact_keys(
            value,
            (
                "generation_contract_version",
                "formal_master_seed",
                "formal_generation_id",
                "seed_derivation_contract",
                "seed_derivation_mode",
                "generator_seed",
                "processor_count",
                "task_count",
                "target_normalized_utilization",
                "replicate_index",
                "period_range",
                "utilization_allocation",
                "wcet_rounding_mode",
                "generator_version",
                "power_generation",
                "priority_policy",
                "dag_generation_mode",
                "energy_aware_generation",
            ),
            "generation request",
        )
        if material["generation_contract_version"] != GENERATION_REQUEST_CONTRACT_VERSION:
            raise TasksetIdentityError("generation contract version mismatch")
        if material["seed_derivation_contract"] != TASKSET_SEED_DERIVATION_DOMAIN:
            raise TasksetIdentityError("seed derivation contract mismatch")
        if material["seed_derivation_mode"] != GENERATION_DIMENSIONS_SEED_MODE:
            raise TasksetIdentityError("seed derivation mode mismatch")
        period_range = _exact_keys(
            material["period_range"], ("minimum", "maximum"), "period range"
        )
        utilization = _exact_keys(
            material["utilization_allocation"],
            (
                "mode",
                "minimum_task_utilization",
                "maximum_task_utilization",
                "total_tolerance",
            ),
            "utilization allocation",
        )
        power = _exact_keys(
            material["power_generation"],
            ("mode", "contract_identity", "workload_candidate_identity"),
            "power generation",
        )
        request = cls(
            material["formal_master_seed"],
            material["formal_generation_id"],
            material["processor_count"],
            material["task_count"],
            fraction_from_canonical_material(
                material["target_normalized_utilization"],
                "target_normalized_utilization",
            ),
            material["replicate_index"],
            period_range["minimum"],
            period_range["maximum"],
            utilization["mode"],
            fraction_from_canonical_material(
                utilization["minimum_task_utilization"],
                "minimum_task_utilization",
            ),
            fraction_from_canonical_material(
                utilization["maximum_task_utilization"],
                "maximum_task_utilization",
            ),
            fraction_from_canonical_material(
                utilization["total_tolerance"], "total_tolerance"
            ),
            material["wcet_rounding_mode"],
            material["generator_version"],
            power["mode"],
            power["contract_identity"],
            power["workload_candidate_identity"],
            material["priority_policy"],
            material["dag_generation_mode"],
            material["energy_aware_generation"],
        )
        serialized_seed = _plain_int(
            material["generator_seed"], "generator_seed", 0
        )
        if serialized_seed != request.generator_seed:
            raise TasksetIdentityError("derived generator_seed mismatch")
        return request


def generation_request_id(request: GenerationRequest) -> str:
    if type(request) is not GenerationRequest:
        raise TasksetIdentityError("request must be a GenerationRequest")
    return _identity_hash(GENERATION_REQUEST_DOMAIN, request.identity_material())


@dataclass(frozen=True)
class SkeletonTask:
    task_id: str
    priority_rank: int
    wcet: int
    period: int
    base_power: Fraction

    def __post_init__(self) -> None:
        _canonical_string(self.task_id, "task_id")
        _plain_int(self.priority_rank, "priority_rank", 0)
        _plain_int(self.wcet, "wcet", 1)
        _plain_int(self.period, "period", 1)
        if self.wcet > self.period:
            raise TasksetIdentityError("skeleton task must satisfy C <= T")
        _exact_fraction(self.base_power, "base_power", minimum=Fraction(0))

    def material(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "priority_rank": self.priority_rank,
            "wcet": self.wcet,
            "period": self.period,
            "base_power": canonical_fraction_material(self.base_power),
        }

    @classmethod
    def from_material(cls, value: Any) -> "SkeletonTask":
        material = _exact_keys(
            value,
            ("task_id", "priority_rank", "wcet", "period", "base_power"),
            "skeleton task",
        )
        return cls(
            material["task_id"],
            material["priority_rank"],
            material["wcet"],
            material["period"],
            fraction_from_canonical_material(
                material["base_power"], "base_power"
            ),
        )


@dataclass(frozen=True)
class TasksetTask:
    task_id: str
    priority_rank: int
    wcet: int
    period: int
    relative_deadline: int
    actual_power: Fraction
    deadline_generation_mode: str

    def __post_init__(self) -> None:
        _canonical_string(self.task_id, "task_id")
        _plain_int(self.priority_rank, "priority_rank", 0)
        _plain_int(self.wcet, "wcet", 1)
        _plain_int(self.period, "period", 1)
        _plain_int(self.relative_deadline, "relative_deadline", 1)
        if not self.wcet <= self.relative_deadline <= self.period:
            raise TasksetIdentityError("task must satisfy 1 <= C <= D <= T")
        _exact_fraction(self.actual_power, "actual_power", minimum=Fraction(0))
        _canonical_string(
            self.deadline_generation_mode, "deadline_generation_mode"
        )

    @property
    def deadline_to_period_ratio(self) -> Fraction:
        return Fraction(self.relative_deadline, self.period)

    @property
    def deadline_slack_fraction(self) -> Fraction:
        if self.period == self.wcet:
            return Fraction(1)
        return Fraction(
            self.relative_deadline - self.wcet,
            self.period - self.wcet,
        )

    def material(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "priority_rank": self.priority_rank,
            "wcet": self.wcet,
            "period": self.period,
            "relative_deadline": self.relative_deadline,
            "actual_power": canonical_fraction_material(self.actual_power),
            "deadline_generation_mode": self.deadline_generation_mode,
            "deadline_to_period_ratio": canonical_fraction_material(
                self.deadline_to_period_ratio
            ),
            "deadline_slack_fraction": canonical_fraction_material(
                self.deadline_slack_fraction
            ),
        }

    def content_material(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "priority_rank": self.priority_rank,
            "wcet": self.wcet,
            "period": self.period,
            "relative_deadline": self.relative_deadline,
            "actual_power": canonical_fraction_material(self.actual_power),
        }

    @classmethod
    def from_material(cls, value: Any) -> "TasksetTask":
        material = _exact_keys(
            value,
            (
                "task_id",
                "priority_rank",
                "wcet",
                "period",
                "relative_deadline",
                "actual_power",
                "deadline_generation_mode",
                "deadline_to_period_ratio",
                "deadline_slack_fraction",
            ),
            "taskset task",
        )
        task = cls(
            material["task_id"],
            material["priority_rank"],
            material["wcet"],
            material["period"],
            material["relative_deadline"],
            fraction_from_canonical_material(
                material["actual_power"], "actual_power"
            ),
            material["deadline_generation_mode"],
        )
        if fraction_from_canonical_material(
            material["deadline_to_period_ratio"],
            "deadline_to_period_ratio",
        ) != task.deadline_to_period_ratio:
            raise TasksetIdentityError("deadline_to_period_ratio mismatch")
        if fraction_from_canonical_material(
            material["deadline_slack_fraction"],
            "deadline_slack_fraction",
        ) != task.deadline_slack_fraction:
            raise TasksetIdentityError("deadline_slack_fraction mismatch")
        return task


def _validate_task_order(
    tasks: Sequence[Any],
    expected_count: int,
    label: str,
    expected_type: Any,
) -> None:
    if type(tasks) is not tuple:
        raise TasksetIdentityError(f"{label} must be an immutable tuple")
    _plain_int(expected_count, f"{label} expected count", 1)
    if len(tasks) != expected_count:
        raise TasksetIdentityError(f"{label} count mismatch")
    if any(type(task) is not expected_type for task in tasks):
        raise TasksetIdentityError(f"{label} contains an invalid record type")
    task_ids = [task.task_id for task in tasks]
    ranks = [task.priority_rank for task in tasks]
    if len(set(task_ids)) != len(task_ids):
        raise TasksetIdentityError(f"{label} contains duplicate task IDs")
    if len(set(ranks)) != len(ranks):
        raise TasksetIdentityError(f"{label} contains duplicate priority ranks")
    if ranks != list(range(expected_count)):
        raise TasksetIdentityError(
            f"{label} is not in canonical priority-rank order"
        )


def compute_taskset_skeleton_id(
    generation_id: str,
    processors: int,
    tasks: Tuple[SkeletonTask, ...],
) -> str:
    _sha256(generation_id, "generation_request_id")
    _plain_int(processors, "processors", 1)
    if not tasks:
        raise TasksetIdentityError("skeleton tasks must not be empty")
    _validate_task_order(tasks, len(tasks), "skeleton tasks", SkeletonTask)
    material = {
        "taskset_skeleton_contract_version": TASKSET_SKELETON_CONTRACT_VERSION,
        "generation_request_id": generation_id,
        "processor_count": processors,
        "task_count": len(tasks),
        "tasks": [task.material() for task in tasks],
    }
    return _identity_hash(TASKSET_SKELETON_DOMAIN, material)


def compute_taskset_hash(
    processors: int,
    tasks: Tuple[TasksetTask, ...],
) -> str:
    _plain_int(processors, "processors", 1)
    if not tasks:
        raise TasksetIdentityError("taskset tasks must not be empty")
    _validate_task_order(tasks, len(tasks), "taskset tasks", TasksetTask)
    material = {
        "taskset_content_contract_version": TASKSET_CONTENT_CONTRACT_VERSION,
        "processor_count": processors,
        "task_count": len(tasks),
        "canonical_priority_order": [
            task.content_material() for task in tasks
        ],
    }
    return _identity_hash(TASKSET_CONTENT_DOMAIN, material)


def power_vector_hash(tasks: Tuple[TasksetTask, ...]) -> str:
    """Reference the existing canonical v9.3 power-vector identity."""

    if not tasks:
        raise TasksetIdentityError("power-vector tasks must not be empty")
    _validate_task_order(
        tasks, len(tasks), "power-vector tasks", TasksetTask
    )
    return legacy_domain_hash(
        POWER_VECTOR_DOMAIN,
        [
            {"task_id": task.task_id, "P": fraction_text(task.actual_power)}
            for task in tasks
        ],
    )


def deadline_from_slack_fraction(
    wcet: int,
    period: int,
    lambda_num: int,
    lambda_den: int,
) -> int:
    """Compute C + floor(num * (T-C) / den) using integers only."""

    c_value = _plain_int(wcet, "wcet", 1)
    t_value = _plain_int(period, "period", 1)
    numerator = _plain_int(lambda_num, "lambda_num", 0)
    denominator = _plain_int(lambda_den, "lambda_den", 1)
    if c_value > t_value:
        raise TasksetIdentityError("deadline inputs must satisfy C <= T")
    if numerator > denominator:
        raise TasksetIdentityError("slack fraction must satisfy 0 <= num <= den")
    return c_value + (numerator * (t_value - c_value)) // denominator


def fixed_slack_deadline(
    wcet: int, period: int, slack_fraction: Fraction
) -> int:
    """Exact helper for sensitivity variants; shares the sole floor formula."""

    exact = _exact_fraction(
        slack_fraction,
        "slack_fraction",
        minimum=Fraction(0),
        maximum=Fraction(1),
    )
    return deadline_from_slack_fraction(
        wcet, period, exact.numerator, exact.denominator
    )


def derive_deadline_lambda_numerator(
    *,
    formal_master_seed: int,
    generation_id: str,
    task_id: str,
    priority_rank: int,
) -> int:
    """Derive one closed-interval [0,1] 64-bit slack draw by SHA-256."""

    _plain_int(formal_master_seed, "formal_master_seed", 0)
    _sha256(generation_id, "generation_request_id")
    _canonical_string(task_id, "task_id")
    _plain_int(priority_rank, "priority_rank", 0)
    material = {
        "deadline_contract_version": DEADLINE_CONTRACT_VERSION,
        "formal_master_seed": formal_master_seed,
        "generation_request_id": generation_id,
        "canonical_task_id": task_id,
        "priority_rank": priority_rank,
        "deadline_generation_mode": CONSTRAINED_UNIFORM_SLACK_MODE,
    }
    digest = hashlib.sha256(
        DEADLINE_DRAW_DOMAIN.encode("ascii")
        + b"\0"
        + canonical_identity_bytes(material)
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


@dataclass(frozen=True)
class DeadlineVariant:
    mode: str
    lambda_numerators: Tuple[int, ...] = ()
    fixed_slack_fraction: Optional[Fraction] = None

    def __post_init__(self) -> None:
        _canonical_string(self.mode, "deadline variant mode")
        if type(self.lambda_numerators) is not tuple:
            raise TasksetIdentityError("lambda_numerators must be a tuple")
        if self.mode == IMPLICIT_DEADLINE_MODE:
            if self.lambda_numerators or self.fixed_slack_fraction is not None:
                raise TasksetIdentityError(
                    "implicit deadline variant must not carry slack parameters"
                )
        elif self.mode == CONSTRAINED_UNIFORM_SLACK_MODE:
            if not self.lambda_numerators or self.fixed_slack_fraction is not None:
                raise TasksetIdentityError(
                    "uniform-slack variant requires only per-task draws"
                )
            for index, value in enumerate(self.lambda_numerators):
                _plain_int(value, f"lambda_numerators[{index}]", 0)
                if value > UINT64_MAX:
                    raise TasksetIdentityError("deadline draw exceeds uint64")
        elif self.mode == FIXED_SLACK_FRACTION_VARIANT:
            if self.lambda_numerators or self.fixed_slack_fraction is None:
                raise TasksetIdentityError(
                    "fixed-slack variant requires one exact fraction"
                )
            _exact_fraction(
                self.fixed_slack_fraction,
                "fixed_slack_fraction",
                minimum=Fraction(0),
                maximum=Fraction(1),
            )
        else:
            raise TasksetIdentityError("unknown deadline variant mode")

    def material(self, tasks: Tuple[SkeletonTask, ...]) -> Dict[str, Any]:
        if self.mode == IMPLICIT_DEADLINE_MODE:
            return {
                "deadline_contract_version": DEADLINE_CONTRACT_VERSION,
                "mode": self.mode,
            }
        if self.mode == CONSTRAINED_UNIFORM_SLACK_MODE:
            if len(self.lambda_numerators) != len(tasks):
                raise TasksetIdentityError("deadline draw count mismatch")
            return {
                "deadline_contract_version": DEADLINE_CONTRACT_VERSION,
                "mode": self.mode,
                "lambda_denominator": UINT64_MAX,
                "per_task_lambda_numerators": [
                    {
                        "task_id": task.task_id,
                        "priority_rank": task.priority_rank,
                        "numerator": numerator,
                    }
                    for task, numerator in zip(tasks, self.lambda_numerators)
                ],
            }
        return {
            "deadline_contract_version": DEADLINE_CONTRACT_VERSION,
            "mode": self.mode,
            "slack_fraction": canonical_fraction_material(
                self.fixed_slack_fraction  # type: ignore[arg-type]
            ),
        }

    @classmethod
    def from_material(
        cls, value: Any, tasks: Tuple[SkeletonTask, ...]
    ) -> "DeadlineVariant":
        if type(value) is not dict:
            raise TasksetIdentityError("deadline variant must be a mapping")
        if value.get("deadline_contract_version") != DEADLINE_CONTRACT_VERSION:
            raise TasksetIdentityError("deadline contract version mismatch")
        mode = value.get("mode")
        if mode == IMPLICIT_DEADLINE_MODE:
            _exact_keys(
                value,
                ("deadline_contract_version", "mode"),
                "implicit deadline variant",
            )
            return cls(mode)
        if mode == CONSTRAINED_UNIFORM_SLACK_MODE:
            material = _exact_keys(
                value,
                (
                    "deadline_contract_version",
                    "mode",
                    "lambda_denominator",
                    "per_task_lambda_numerators",
                ),
                "uniform deadline variant",
            )
            if material["lambda_denominator"] != UINT64_MAX:
                raise TasksetIdentityError("deadline lambda denominator mismatch")
            rows = material["per_task_lambda_numerators"]
            if type(rows) is not list or len(rows) != len(tasks):
                raise TasksetIdentityError("deadline draw count mismatch")
            numerators = []
            for index, (row, task) in enumerate(zip(rows, tasks)):
                draw = _exact_keys(
                    row,
                    ("task_id", "priority_rank", "numerator"),
                    f"deadline draw {index}",
                )
                if (
                    draw["task_id"] != task.task_id
                    or draw["priority_rank"] != task.priority_rank
                ):
                    raise TasksetIdentityError("deadline draw task binding mismatch")
                numerators.append(draw["numerator"])
            return cls(mode, tuple(numerators))
        if mode == FIXED_SLACK_FRACTION_VARIANT:
            material = _exact_keys(
                value,
                ("deadline_contract_version", "mode", "slack_fraction"),
                "fixed deadline variant",
            )
            return cls(
                mode,
                (),
                fraction_from_canonical_material(
                    material["slack_fraction"], "slack_fraction"
                ),
            )
        raise TasksetIdentityError("unknown deadline variant mode")


@dataclass(frozen=True)
class PowerVariant:
    mode: str
    scale: Fraction

    def __post_init__(self) -> None:
        _canonical_string(self.mode, "power variant mode")
        scale = _exact_fraction(
            self.scale, "power scale", minimum=Fraction(0)
        )
        if scale <= 0:
            raise TasksetIdentityError("power scale must be positive")
        if self.mode == BASE_POWER_VARIANT:
            if scale != 1:
                raise TasksetIdentityError("base power variant must have scale 1")
        elif self.mode == SCALED_POWER_VARIANT:
            if scale == 1:
                raise TasksetIdentityError(
                    "scale 1 has the unique base_power_v1 representation"
                )
        else:
            raise TasksetIdentityError("unknown power variant mode")

    def material(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "scale": canonical_fraction_material(self.scale),
        }

    @classmethod
    def from_material(cls, value: Any) -> "PowerVariant":
        material = _exact_keys(value, ("mode", "scale"), "power variant")
        return cls(
            material["mode"],
            fraction_from_canonical_material(material["scale"], "power scale"),
        )


def compute_taskset_id(
    skeleton_id: str,
    content_hash: str,
    deadline_variant: DeadlineVariant,
    power_variant: PowerVariant,
    skeleton_tasks: Tuple[SkeletonTask, ...],
) -> str:
    _sha256(skeleton_id, "taskset_skeleton_id")
    _sha256(content_hash, "taskset_hash")
    material = {
        "taskset_identity_contract_version": TASKSET_IDENTITY_CONTRACT_VERSION,
        "taskset_skeleton_id": skeleton_id,
        "taskset_hash": content_hash,
        "deadline_variant": deadline_variant.material(skeleton_tasks),
        "power_variant": power_variant.material(),
    }
    return _identity_hash(TASKSET_ID_DOMAIN, material)


@dataclass(frozen=True)
class TasksetIdentityCertificate:
    generation_request: GenerationRequest
    generation_request_id: str
    processors: int
    skeleton_tasks: Tuple[SkeletonTask, ...]
    taskset_skeleton_id: str
    deadline_variant: DeadlineVariant
    power_variant: PowerVariant
    tasks: Tuple[TasksetTask, ...]
    power_vector_hash: str
    taskset_hash: str
    taskset_id: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if type(self.generation_request) is not GenerationRequest:
            raise TasksetIdentityError(
                "generation_request must be a GenerationRequest"
            )
        _sha256(self.generation_request_id, "generation_request_id")
        expected_generation_id = generation_request_id(self.generation_request)
        if self.generation_request_id != expected_generation_id:
            raise TasksetIdentityError("generation_request_id mismatch")
        _plain_int(self.processors, "processors", 1)
        if self.processors != self.generation_request.processors:
            raise TasksetIdentityError("processor count/request mismatch")
        count = self.generation_request.task_count
        _validate_task_order(
            self.skeleton_tasks, count, "skeleton tasks", SkeletonTask
        )
        _validate_task_order(self.tasks, count, "taskset tasks", TasksetTask)

        if type(self.deadline_variant) is not DeadlineVariant:
            raise TasksetIdentityError("invalid deadline variant")
        if type(self.power_variant) is not PowerVariant:
            raise TasksetIdentityError("invalid power variant")

        if self.deadline_variant.mode == CONSTRAINED_UNIFORM_SLACK_MODE:
            if len(self.deadline_variant.lambda_numerators) != count:
                raise TasksetIdentityError("deadline draw count mismatch")
            expected_draws = tuple(
                derive_deadline_lambda_numerator(
                    formal_master_seed=(
                        self.generation_request.formal_master_seed
                    ),
                    generation_id=self.generation_request_id,
                    task_id=task.task_id,
                    priority_rank=task.priority_rank,
                )
                for task in self.skeleton_tasks
            )
            if self.deadline_variant.lambda_numerators != expected_draws:
                raise TasksetIdentityError("deadline draw derivation mismatch")

        for index, (skeleton, task) in enumerate(
            zip(self.skeleton_tasks, self.tasks)
        ):
            if (
                task.task_id != skeleton.task_id
                or task.priority_rank != skeleton.priority_rank
                or task.wcet != skeleton.wcet
                or task.period != skeleton.period
            ):
                raise TasksetIdentityError(
                    f"task {index} does not match its skeleton"
                )
            if task.deadline_generation_mode != self.deadline_variant.mode:
                raise TasksetIdentityError(
                    f"task {index} deadline mode/variant mismatch"
                )
            if self.deadline_variant.mode == IMPLICIT_DEADLINE_MODE:
                expected_deadline = skeleton.period
            elif self.deadline_variant.mode == CONSTRAINED_UNIFORM_SLACK_MODE:
                expected_deadline = deadline_from_slack_fraction(
                    skeleton.wcet,
                    skeleton.period,
                    self.deadline_variant.lambda_numerators[index],
                    UINT64_MAX,
                )
            else:
                exact = self.deadline_variant.fixed_slack_fraction
                if exact is None:
                    raise TasksetIdentityError("missing fixed slack fraction")
                expected_deadline = fixed_slack_deadline(
                    skeleton.wcet, skeleton.period, exact
                )
            if task.relative_deadline != expected_deadline:
                raise TasksetIdentityError(
                    f"task {index} deadline variant/D mismatch"
                )
            expected_power = skeleton.base_power * self.power_variant.scale
            if task.actual_power != expected_power:
                raise TasksetIdentityError(
                    f"task {index} actual power/power variant mismatch"
                )

        _sha256(self.taskset_skeleton_id, "taskset_skeleton_id")
        expected_skeleton = compute_taskset_skeleton_id(
            self.generation_request_id, self.processors, self.skeleton_tasks
        )
        if self.taskset_skeleton_id != expected_skeleton:
            raise TasksetIdentityError("taskset_skeleton_id mismatch")

        _sha256(self.power_vector_hash, "power_vector_hash")
        if self.power_vector_hash != power_vector_hash(self.tasks):
            raise TasksetIdentityError("power_vector_hash mismatch")

        _sha256(self.taskset_hash, "taskset_hash")
        expected_hash = compute_taskset_hash(self.processors, self.tasks)
        if self.taskset_hash != expected_hash:
            raise TasksetIdentityError("taskset_hash mismatch")

        _sha256(self.taskset_id, "taskset_id")
        expected_taskset_id = compute_taskset_id(
            self.taskset_skeleton_id,
            self.taskset_hash,
            self.deadline_variant,
            self.power_variant,
            self.skeleton_tasks,
        )
        if self.taskset_id != expected_taskset_id:
            raise TasksetIdentityError("taskset_id mismatch")

    def material(self) -> Dict[str, Any]:
        return {
            "schema": CERTIFICATE_SCHEMA,
            "generation_request": self.generation_request.identity_material(),
            "generation_request_id": self.generation_request_id,
            "processor_count": self.processors,
            "task_count": len(self.tasks),
            "skeleton_tasks": [task.material() for task in self.skeleton_tasks],
            "taskset_skeleton_id": self.taskset_skeleton_id,
            "deadline_variant": self.deadline_variant.material(
                self.skeleton_tasks
            ),
            "power_variant": self.power_variant.material(),
            "tasks": [task.material() for task in self.tasks],
            "power_vector_hash": self.power_vector_hash,
            "taskset_hash": self.taskset_hash,
            "taskset_id": self.taskset_id,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_identity_bytes(self.material())

    @classmethod
    def from_material(cls, value: Any) -> "TasksetIdentityCertificate":
        material = _exact_keys(
            value,
            (
                "schema",
                "generation_request",
                "generation_request_id",
                "processor_count",
                "task_count",
                "skeleton_tasks",
                "taskset_skeleton_id",
                "deadline_variant",
                "power_variant",
                "tasks",
                "power_vector_hash",
                "taskset_hash",
                "taskset_id",
            ),
            "taskset identity certificate",
        )
        if material["schema"] != CERTIFICATE_SCHEMA:
            raise TasksetIdentityError("certificate schema mismatch")
        skeleton_rows = material["skeleton_tasks"]
        task_rows = material["tasks"]
        if type(skeleton_rows) is not list or type(task_rows) is not list:
            raise TasksetIdentityError("certificate task records must be lists")
        skeleton = tuple(
            SkeletonTask.from_material(row) for row in skeleton_rows
        )
        tasks = tuple(TasksetTask.from_material(row) for row in task_rows)
        _plain_int(material["task_count"], "certificate task_count", 1)
        if material["task_count"] != len(tasks):
            raise TasksetIdentityError("certificate task_count mismatch")
        return cls(
            GenerationRequest.from_material(material["generation_request"]),
            material["generation_request_id"],
            material["processor_count"],
            skeleton,
            material["taskset_skeleton_id"],
            DeadlineVariant.from_material(material["deadline_variant"], skeleton),
            PowerVariant.from_material(material["power_variant"]),
            tasks,
            material["power_vector_hash"],
            material["taskset_hash"],
            material["taskset_id"],
        )

    @classmethod
    def from_canonical_bytes(cls, value: bytes) -> "TasksetIdentityCertificate":
        if type(value) is not bytes:
            raise TasksetIdentityError("certificate encoding must be bytes")

        def reject_constant(token: str) -> None:
            raise TasksetIdentityError(f"non-finite JSON token: {token}")

        def unique_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
            result: Dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise TasksetIdentityError(f"duplicate JSON key: {key}")
                result[key] = item
            return result

        try:
            text = value.decode("utf-8")
            material = json.loads(
                text,
                parse_constant=reject_constant,
                object_pairs_hook=unique_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TasksetIdentityError("invalid certificate JSON") from exc
        if canonical_identity_bytes(material) != value:
            raise TasksetIdentityError("certificate JSON is not canonical")
        return cls.from_material(material)


def build_taskset_identity_certificate(
    request: GenerationRequest,
    skeleton_tasks: Tuple[SkeletonTask, ...],
    *,
    deadline_mode: str,
    fixed_slack_fraction: Optional[Fraction] = None,
    power_scale: Fraction = Fraction(1),
) -> TasksetIdentityCertificate:
    """Build and self-validate one provenance-bearing task-set version."""

    if type(request) is not GenerationRequest:
        raise TasksetIdentityError("request must be a GenerationRequest")
    _validate_task_order(
        skeleton_tasks, request.task_count, "skeleton tasks", SkeletonTask
    )
    generation_id = generation_request_id(request)
    skeleton_id = compute_taskset_skeleton_id(
        generation_id, request.processors, skeleton_tasks
    )

    if deadline_mode == IMPLICIT_DEADLINE_MODE:
        if fixed_slack_fraction is not None:
            raise TasksetIdentityError(
                "implicit mode does not accept a fixed slack fraction"
            )
        deadline_variant = DeadlineVariant(deadline_mode)
    elif deadline_mode == CONSTRAINED_UNIFORM_SLACK_MODE:
        if fixed_slack_fraction is not None:
            raise TasksetIdentityError(
                "uniform-slack mode does not accept a fixed slack fraction"
            )
        draws = tuple(
            derive_deadline_lambda_numerator(
                formal_master_seed=request.formal_master_seed,
                generation_id=generation_id,
                task_id=task.task_id,
                priority_rank=task.priority_rank,
            )
            for task in skeleton_tasks
        )
        deadline_variant = DeadlineVariant(deadline_mode, draws)
    elif deadline_mode == FIXED_SLACK_FRACTION_VARIANT:
        if fixed_slack_fraction is None:
            raise TasksetIdentityError(
                "fixed-slack variant requires an exact fraction"
            )
        deadline_variant = DeadlineVariant(
            deadline_mode, (), fixed_slack_fraction
        )
    else:
        raise TasksetIdentityError("unknown deadline generation mode")

    scale = _exact_fraction(power_scale, "power_scale", minimum=Fraction(0))
    if scale <= 0:
        raise TasksetIdentityError("power_scale must be positive")
    power_variant = PowerVariant(
        BASE_POWER_VARIANT if scale == 1 else SCALED_POWER_VARIANT,
        scale,
    )

    tasks = []
    for index, skeleton in enumerate(skeleton_tasks):
        if deadline_variant.mode == IMPLICIT_DEADLINE_MODE:
            deadline = skeleton.period
        elif deadline_variant.mode == CONSTRAINED_UNIFORM_SLACK_MODE:
            deadline = deadline_from_slack_fraction(
                skeleton.wcet,
                skeleton.period,
                deadline_variant.lambda_numerators[index],
                UINT64_MAX,
            )
        else:
            exact = deadline_variant.fixed_slack_fraction
            if exact is None:
                raise TasksetIdentityError("missing fixed slack fraction")
            deadline = fixed_slack_deadline(
                skeleton.wcet, skeleton.period, exact
            )
        tasks.append(TasksetTask(
            skeleton.task_id,
            skeleton.priority_rank,
            skeleton.wcet,
            skeleton.period,
            deadline,
            skeleton.base_power * scale,
            deadline_variant.mode,
        ))
    task_tuple = tuple(tasks)
    content_hash = compute_taskset_hash(request.processors, task_tuple)
    taskset_id_value = compute_taskset_id(
        skeleton_id,
        content_hash,
        deadline_variant,
        power_variant,
        skeleton_tasks,
    )
    return TasksetIdentityCertificate(
        request,
        generation_id,
        request.processors,
        skeleton_tasks,
        skeleton_id,
        deadline_variant,
        power_variant,
        task_tuple,
        power_vector_hash(task_tuple),
        content_hash,
        taskset_id_value,
    )


__all__ = [
    "BASE_POWER_VARIANT",
    "CERTIFICATE_SCHEMA",
    "CONSTRAINED_UNIFORM_SLACK_MODE",
    "DEADLINE_CONTRACT_VERSION",
    "DEADLINE_DRAW_DOMAIN",
    "DeadlineVariant",
    "FIXED_SLACK_FRACTION_VARIANT",
    "GENERATION_REQUEST_CONTRACT_VERSION",
    "GENERATION_REQUEST_DOMAIN",
    "GenerationRequest",
    "IMPLICIT_DEADLINE_MODE",
    "POWER_VECTOR_DOMAIN",
    "PRIMARY_DEADLINE_GENERATION_MODES",
    "PowerVariant",
    "SCALED_POWER_VARIANT",
    "SkeletonTask",
    "TASKSET_CONTENT_CONTRACT_VERSION",
    "TASKSET_CONTENT_DOMAIN",
    "TASKSET_IDENTITY_CONTRACT_VERSION",
    "TASKSET_ID_DOMAIN",
    "TASKSET_SKELETON_CONTRACT_VERSION",
    "TASKSET_SKELETON_DOMAIN",
    "TasksetIdentityCertificate",
    "TasksetIdentityError",
    "TasksetTask",
    "UINT64_MAX",
    "build_taskset_identity_certificate",
    "canonical_fraction_material",
    "canonical_identity_bytes",
    "compute_taskset_hash",
    "compute_taskset_id",
    "compute_taskset_skeleton_id",
    "deadline_from_slack_fraction",
    "derive_deadline_lambda_numerator",
    "fixed_slack_deadline",
    "fraction_from_canonical_material",
    "generation_request_id",
    "power_vector_hash",
]
