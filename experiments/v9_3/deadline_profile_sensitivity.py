"""Paired deadline projections for exploratory sensitivity runs.

This module deliberately does not generate tasksets or alter the frozen
LOAD-CROSS path.  It projects one already-generated canonical taskset into
several deadline profiles while preserving every non-deadline task field.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Mapping, Sequence

from .config import domain_hash, fraction_text
from .simulation_engine import derive_fixed_priority_ranks


DOMAIN = "ASAP_BLOCK:V9.3:DEADLINE_PROFILE_SENSITIVITY:v1"
RELAX_ORIGINAL_DOMAIN = "ASAP_BLOCK:V9.3:DEADLINE_RELAX_SENSITIVITY:v2"
PROFILE_LAMBDAS = {
    "TIGHT": Fraction(1, 4),
    "MEDIUM": Fraction(1, 2),
    "LOOSE": Fraction(3, 4),
    "IMPLICIT": Fraction(1),
}
PROFILE_ORDER = tuple(PROFILE_LAMBDAS)


class DeadlineProfileError(ValueError):
    """Raised when a deadline projection would violate pairing invariants."""


@dataclass(frozen=True)
class ProjectedTaskset:
    base_taskset_id: str
    base_taskset_hash: str
    projected_taskset_id: str
    projected_taskset_hash: str
    deadline_profile: str
    deadline_lambda: Fraction | None
    taskset_index: int
    seed: int
    processors: int
    task_count: int
    target_utilization: Fraction
    actual_utilization: Fraction
    task_payload: tuple[Mapping[str, Any], ...]
    projection_mode: str = "fixed-common-lambda"
    deadline_alpha: Fraction | None = None

    def row(self) -> dict[str, Any]:
        return {
            "base_taskset_id": self.base_taskset_id,
            "base_taskset_hash": self.base_taskset_hash,
            "projected_taskset_id": self.projected_taskset_id,
            "projected_taskset_hash": self.projected_taskset_hash,
            "deadline_profile": self.deadline_profile,
            "projection_mode": self.projection_mode,
            "deadline_alpha": (
                fraction_text(self.deadline_alpha)
                if self.deadline_alpha is not None else None
            ),
            "deadline_lambda": (
                fraction_text(self.deadline_lambda)
                if self.deadline_lambda is not None else None
            ),
            "taskset_index": self.taskset_index,
            "generation_seed": self.seed,
            "M": self.processors,
            "task_n": self.task_count,
            "target_total_utilization": fraction_text(self.target_utilization),
            "actual_total_utilization": fraction_text(self.actual_utilization),
            "task_payload": list(self.task_payload),
        }


def _exact_lambda(value: Any) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise DeadlineProfileError("deadline lambda must be exact")
    try:
        result = value if isinstance(value, Fraction) else Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise DeadlineProfileError("deadline lambda must be exact") from exc
    if not 0 < result <= 1:
        raise DeadlineProfileError("deadline lambda must be in (0, 1]")
    return result


def _exact_alpha(value: Any) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise DeadlineProfileError("deadline alpha must be exact")
    try:
        result = value if isinstance(value, Fraction) else Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise DeadlineProfileError("deadline alpha must be exact") from exc
    if not 0 <= result <= 1:
        raise DeadlineProfileError("deadline alpha must be in [0, 1]")
    return result


def deadline_lambda(profile: str) -> Fraction:
    try:
        return PROFILE_LAMBDAS[str(profile).upper()]
    except KeyError as exc:
        raise DeadlineProfileError(f"unknown deadline profile: {profile}") from exc


def project_deadline(c: int, t: int, lam: Fraction) -> int:
    """Compute D = C + floor(lambda * (T-C)) with exact integer arithmetic."""

    if isinstance(c, bool) or isinstance(t, bool) or not isinstance(c, int) or not isinstance(t, int):
        raise DeadlineProfileError("C and T must be integers")
    if not 0 < c <= t:
        raise DeadlineProfileError("deadline projection requires 0 < C <= T")
    lam = _exact_lambda(lam)
    if c == t:
        return t
    return c + (lam.numerator * (t - c)) // lam.denominator


def project_relaxed_deadline(
    c: int, d_original: int, t: int, alpha: Fraction,
) -> int:
    """Compute D_orig + floor(alpha * (T-D_orig)) exactly."""

    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (c, d_original, t)
    ):
        raise DeadlineProfileError("C, D_orig and T must be integers")
    if not 0 < c <= d_original <= t:
        raise DeadlineProfileError(
            "deadline relaxation requires 0 < C <= D_orig <= T"
        )
    alpha = _exact_alpha(alpha)
    return d_original + (
        alpha.numerator * (t - d_original) // alpha.denominator
    )


def profile_name_for_alpha(alpha: Fraction) -> str:
    alpha = _exact_alpha(alpha)
    if alpha == 0:
        return "ORIGINAL"
    if alpha == 1:
        return "IMPLICIT"
    return f"RELAX_{alpha.numerator}_{alpha.denominator}"


def _task_value(taskset: Any, key: str) -> Any:
    if isinstance(taskset, Mapping):
        return taskset[key]
    return getattr(taskset, key)


def _non_deadline_material(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in row.items()
        if key not in {"D", "D_over_T"}
    }


def project_task_payload(
    task_payload: Sequence[Mapping[str, Any]],
    profile: str,
) -> tuple[Mapping[str, Any], ...]:
    """Project one canonical payload; no input row is mutated."""

    profile_name = str(profile).upper()
    lam = deadline_lambda(profile_name)
    projected: list[Mapping[str, Any]] = []
    for row in task_payload:
        if not isinstance(row, Mapping):
            raise DeadlineProfileError("task payload row must be a mapping")
        try:
            c = row["C"]
            t = row["T"]
        except KeyError as exc:
            raise DeadlineProfileError("task payload row lacks C or T") from exc
        d = project_deadline(c, t, lam)
        if not c <= d <= t:
            raise DeadlineProfileError("projected deadline violates C <= D <= T")
        item = dict(row)
        item["D"] = d
        item["D_over_T"] = fraction_text(Fraction(d, t))
        projected.append(item)
    return tuple(projected)


def project_taskset(taskset: Any, profile: str) -> ProjectedTaskset:
    """Create one identity-bearing profile from one canonical base taskset."""

    profile_name = str(profile).upper()
    lam = deadline_lambda(profile_name)
    base_id = str(_task_value(taskset, "taskset_id"))
    base_hash = str(_task_value(taskset, "semantic_hash"))
    payload = project_task_payload(_task_value(taskset, "task_payload"), profile_name)
    task_count = int(_task_value(taskset, "task_count"))
    if len(payload) != task_count:
        raise DeadlineProfileError("projected payload length does not match task count")
    identity_material = {
        "base_taskset_id": base_id,
        "base_taskset_hash": base_hash,
        "deadline_profile": profile_name,
        "deadline_lambda": fraction_text(lam),
        "task_payload": list(payload),
    }
    projected_hash = domain_hash(DOMAIN + ":PROJECTED_TASKSET", identity_material)
    return ProjectedTaskset(
        base_taskset_id=base_id,
        base_taskset_hash=base_hash,
        projected_taskset_id="deadline-profile-" + projected_hash[:32],
        projected_taskset_hash=projected_hash,
        deadline_profile=profile_name,
        deadline_lambda=lam,
        taskset_index=int(_task_value(taskset, "taskset_index")),
        seed=int(_task_value(taskset, "seed")),
        processors=int(_task_value(taskset, "processors")),
        task_count=task_count,
        target_utilization=Fraction(_task_value(taskset, "target_utilization")),
        actual_utilization=Fraction(_task_value(taskset, "actual_utilization")),
        task_payload=payload,
    )


def fixed_profile_name_for_lambda(lam: Fraction) -> str:
    lam = _exact_lambda(lam)
    for name, known in PROFILE_LAMBDAS.items():
        if lam == known:
            return name
    return f"LAMBDA_{lam.numerator}_{lam.denominator}"


def project_task_payload_with_lambda(
    task_payload: Sequence[Mapping[str, Any]], lam: Fraction,
) -> tuple[Mapping[str, Any], ...]:
    lam = _exact_lambda(lam)
    projected: list[Mapping[str, Any]] = []
    for row in task_payload:
        if not isinstance(row, Mapping):
            raise DeadlineProfileError("task payload row must be a mapping")
        try:
            c, t = row["C"], row["T"]
        except KeyError as exc:
            raise DeadlineProfileError("task payload row lacks C or T") from exc
        d = project_deadline(c, t, lam)
        item = dict(row)
        item["D"] = d
        item["D_over_T"] = fraction_text(Fraction(d, t))
        projected.append(item)
    return tuple(projected)


def project_taskset_with_lambda(taskset: Any, lam: Fraction) -> ProjectedTaskset:
    """Project with a caller-selected legacy common lambda."""

    lam = _exact_lambda(lam)
    profile_name = fixed_profile_name_for_lambda(lam)
    if profile_name in PROFILE_LAMBDAS:
        return project_taskset(taskset, profile_name)
    base_id = str(_task_value(taskset, "taskset_id"))
    base_hash = str(_task_value(taskset, "semantic_hash"))
    payload = project_task_payload_with_lambda(_task_value(taskset, "task_payload"), lam)
    task_count = int(_task_value(taskset, "task_count"))
    if len(payload) != task_count:
        raise DeadlineProfileError("projected payload length does not match task count")
    identity_material = {
        "projection_mode": "fixed-common-lambda",
        "base_taskset_id": base_id,
        "base_taskset_hash": base_hash,
        "deadline_profile": profile_name,
        "deadline_lambda": fraction_text(lam),
        "task_payload": list(payload),
    }
    projected_hash = domain_hash(DOMAIN + ":PROJECTED_TASKSET", identity_material)
    return ProjectedTaskset(
        base_taskset_id=base_id,
        base_taskset_hash=base_hash,
        projected_taskset_id="deadline-profile-" + projected_hash[:32],
        projected_taskset_hash=projected_hash,
        deadline_profile=profile_name,
        deadline_lambda=lam,
        taskset_index=int(_task_value(taskset, "taskset_index")),
        seed=int(_task_value(taskset, "seed")),
        processors=int(_task_value(taskset, "processors")),
        task_count=task_count,
        target_utilization=Fraction(_task_value(taskset, "target_utilization")),
        actual_utilization=Fraction(_task_value(taskset, "actual_utilization")),
        task_payload=payload,
        projection_mode="fixed-common-lambda",
    )


def project_profiles_with_fixed_lambdas(
    taskset: Any, lambdas: Sequence[Fraction],
) -> tuple[ProjectedTaskset, ...]:
    values = tuple(_exact_lambda(value) for value in lambdas)
    if not values or values != tuple(sorted(set(values))):
        raise DeadlineProfileError("fixed lambdas must be unique and strictly increasing")
    profiles = tuple(project_taskset_with_lambda(taskset, value) for value in values)
    validate_profile_pairing_material(profiles)
    for rows in zip(*(item.task_payload for item in profiles)):
        if [row["D"] for row in rows] != sorted(row["D"] for row in rows):
            raise DeadlineProfileError("deadlines are not monotone by lambda")
    implicit = next((item for item in profiles if item.deadline_lambda == 1), None)
    if implicit is not None:
        validate_implicit_priority_order(implicit)
    return profiles


def project_task_payload_from_original_deadline(
    task_payload: Sequence[Mapping[str, Any]], alpha: Fraction,
) -> tuple[Mapping[str, Any], ...]:
    """Relax each task's original deadline toward its period exactly."""

    alpha = _exact_alpha(alpha)
    if alpha == 0:
        # Preserve the canonical payload byte-for-byte at the original point;
        # in particular, do not reformat D_over_T.
        for row in task_payload:
            if not isinstance(row, Mapping):
                raise DeadlineProfileError("task payload row must be a mapping")
            try:
                project_relaxed_deadline(row["C"], row["D"], row["T"], alpha)
            except KeyError as exc:
                raise DeadlineProfileError(
                    "task payload row lacks C, D or T"
                ) from exc
        return tuple(task_payload)
    projected: list[Mapping[str, Any]] = []
    for row in task_payload:
        if not isinstance(row, Mapping):
            raise DeadlineProfileError("task payload row must be a mapping")
        try:
            c, d_original, t = row["C"], row["D"], row["T"]
        except KeyError as exc:
            raise DeadlineProfileError(
                "task payload row lacks C, D or T"
            ) from exc
        d_new = project_relaxed_deadline(c, d_original, t, alpha)
        item = dict(row)
        item["D"] = d_new
        item["D_over_T"] = fraction_text(Fraction(d_new, t))
        projected.append(item)
    return tuple(projected)


def project_taskset_from_original_deadline(
    taskset: Any, alpha: Fraction,
) -> ProjectedTaskset:
    """Create one identity-bearing original-deadline relaxation profile."""

    alpha = _exact_alpha(alpha)
    profile_name = profile_name_for_alpha(alpha)
    base_id = str(_task_value(taskset, "taskset_id"))
    base_hash = str(_task_value(taskset, "semantic_hash"))
    base_payload = _task_value(taskset, "task_payload")
    payload = project_task_payload_from_original_deadline(base_payload, alpha)
    task_count = int(_task_value(taskset, "task_count"))
    if len(payload) != task_count:
        raise DeadlineProfileError("projected payload length does not match task count")
    identity_material = {
        "projection_mode": "relax-original",
        "base_taskset_id": base_id,
        "base_taskset_hash": base_hash,
        "deadline_alpha": fraction_text(alpha),
        "task_payload": list(payload),
    }
    projected_hash = domain_hash(
        RELAX_ORIGINAL_DOMAIN + ":PROJECTED_TASKSET", identity_material,
    )
    return ProjectedTaskset(
        base_taskset_id=base_id,
        base_taskset_hash=base_hash,
        projected_taskset_id="deadline-relax-original-" + projected_hash[:32],
        projected_taskset_hash=projected_hash,
        deadline_profile=profile_name,
        deadline_lambda=None,
        taskset_index=int(_task_value(taskset, "taskset_index")),
        seed=int(_task_value(taskset, "seed")),
        processors=int(_task_value(taskset, "processors")),
        task_count=task_count,
        target_utilization=Fraction(_task_value(taskset, "target_utilization")),
        actual_utilization=Fraction(_task_value(taskset, "actual_utilization")),
        task_payload=payload,
        projection_mode="relax-original",
        deadline_alpha=alpha,
    )


def project_profiles(taskset: Any) -> tuple[ProjectedTaskset, ...]:
    profiles = tuple(project_taskset(taskset, name) for name in PROFILE_ORDER)
    validate_profile_pairing(profiles)
    validate_implicit_priority_order(profiles[-1])
    return profiles


def validate_relaxed_profile_pairing(
    profiles: Sequence[ProjectedTaskset],
    base_taskset: Any,
) -> None:
    """Validate ordered relax-original profiles and endpoint semantics."""

    if not profiles:
        raise DeadlineProfileError("at least one profile is required")
    alphas = [item.deadline_alpha for item in profiles]
    if any(alpha is None for alpha in alphas):
        raise DeadlineProfileError("relax-original profiles require alpha")
    if alphas != sorted(set(alphas)):
        raise DeadlineProfileError("alpha values must be unique and strictly increasing")
    if any(item.projection_mode != "relax-original" for item in profiles):
        raise DeadlineProfileError("mixed projection modes are not allowed")
    expected_names = tuple(profile_name_for_alpha(alpha) for alpha in alphas)
    if tuple(item.deadline_profile for item in profiles) != expected_names:
        raise DeadlineProfileError("profile names do not match alpha values")
    validate_profile_pairing_material(profiles)
    base_payload = tuple(_task_value(base_taskset, "task_payload"))
    for item in profiles:
        if item.deadline_alpha == 0 and item.task_payload != base_payload:
            raise DeadlineProfileError("ORIGINAL payload differs from canonical base")
        if item.deadline_alpha == 1 and any(
            row["D"] != row["T"] for row in item.task_payload
        ):
            raise DeadlineProfileError("IMPLICIT profile does not have D=T")
    for rows in zip(*(item.task_payload for item in profiles)):
        deadlines = [row["D"] for row in rows]
        if deadlines != sorted(deadlines):
            raise DeadlineProfileError("deadlines are not monotone by alpha")


def validate_profile_pairing_material(
    profiles: Sequence[ProjectedTaskset],
) -> None:
    """Validate shared base provenance and non-deadline task material."""

    if len({item.projected_taskset_hash for item in profiles}) != len(profiles):
        raise DeadlineProfileError("projected profile identities are not unique")
    first = profiles[0]
    if len(first.task_payload) != first.task_count:
        raise DeadlineProfileError("profile payload length does not match task count")
    for item in profiles[1:]:
        if len(item.task_payload) != len(first.task_payload):
            raise DeadlineProfileError("profiles have different task counts")
    for item in profiles:
        if (
            item.base_taskset_id != first.base_taskset_id
            or item.base_taskset_hash != first.base_taskset_hash
            or item.taskset_index != first.taskset_index
            or item.seed != first.seed
            or item.processors != first.processors
            or item.task_count != first.task_count
            or item.target_utilization != first.target_utilization
            or item.actual_utilization != first.actual_utilization
        ):
            raise DeadlineProfileError("profiles do not share one base taskset")
    for rows in zip(*(item.task_payload for item in profiles)):
        invariant = _non_deadline_material(rows[0])
        if any(_non_deadline_material(row) != invariant for row in rows[1:]):
            raise DeadlineProfileError("profile changed non-deadline task material")
        if any(not row["C"] <= row["D"] <= row["T"] for row in rows):
            raise DeadlineProfileError("profile violates C <= D <= T")


def project_profiles_from_original_deadline(
    taskset: Any, alphas: Sequence[Fraction],
) -> tuple[ProjectedTaskset, ...]:
    profiles = tuple(
        project_taskset_from_original_deadline(taskset, alpha)
        for alpha in alphas
    )
    validate_relaxed_profile_pairing(profiles, taskset)
    implicit = next((item for item in profiles if item.deadline_alpha == 1), None)
    if implicit is not None:
        validate_implicit_priority_order(implicit)
    return profiles


def validate_profile_pairing(profiles: Sequence[ProjectedTaskset]) -> None:
    """Fail closed unless profiles differ only in deadline-derived material."""

    if tuple(item.deadline_profile for item in profiles) != PROFILE_ORDER:
        raise DeadlineProfileError("profile order must be TIGHT, MEDIUM, LOOSE, IMPLICIT")
    if len({item.projected_taskset_hash for item in profiles}) != len(profiles):
        raise DeadlineProfileError("projected profile identities are not unique")
    if not profiles:
        raise DeadlineProfileError("at least one profile is required")
    first = profiles[0]
    if len(first.task_payload) != first.task_count:
        raise DeadlineProfileError("profile payload length does not match task count")
    if any(len(item.task_payload) != len(first.task_payload) for item in profiles[1:]):
        raise DeadlineProfileError("profiles have different task counts")
    for item in profiles:
        if (
            item.base_taskset_id != first.base_taskset_id
            or item.base_taskset_hash != first.base_taskset_hash
            or item.taskset_index != first.taskset_index
            or item.seed != first.seed
            or item.processors != first.processors
            or item.task_count != first.task_count
            or item.target_utilization != first.target_utilization
            or item.actual_utilization != first.actual_utilization
        ):
            raise DeadlineProfileError("profiles do not share one base taskset")
    for rows in zip(*(item.task_payload for item in profiles)):
        invariant = _non_deadline_material(rows[0])
        deadlines = [row["D"] for row in rows]
        if any(_non_deadline_material(row) != invariant for row in rows[1:]):
            raise DeadlineProfileError("profile changed non-deadline task material")
        if deadlines != sorted(deadlines):
            raise DeadlineProfileError("deadlines are not monotone by profile")
        if any(not row["C"] <= row["D"] <= row["T"] for row in rows):
            raise DeadlineProfileError("profile violates C <= D <= T")


def validate_implicit_priority_order(profile: ProjectedTaskset) -> None:
    """Require DM to be exactly RM when the projected profile has D=T."""

    if profile.deadline_profile != "IMPLICIT":
        raise DeadlineProfileError("implicit priority check requires IMPLICIT profile")
    if any(row["D"] != row["T"] for row in profile.task_payload):
        raise DeadlineProfileError("IMPLICIT profile does not have D=T")
    rm_order = {
        str(row["task_id"]): int(row["priority_rank"])
        for row in profile.task_payload
    }
    dm_order = derive_fixed_priority_ranks(profile.task_payload, "DM")
    if dm_order != rm_order:
        raise DeadlineProfileError("IMPLICIT RM and DM priority orders differ")


def validate_same_projected_material(
    rm: ProjectedTaskset, dm: ProjectedTaskset,
) -> None:
    if (
        rm.deadline_profile != dm.deadline_profile
        or rm.projected_taskset_hash != dm.projected_taskset_hash
        or rm.task_payload != dm.task_payload
    ):
        raise DeadlineProfileError("RM and DM do not share projected task material")
