"""PR-B certificate-backed taskset store for the opt-in RTA4 profile."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from .constrained_taskset_identity import (
    FIXED_SLACK_FRACTION_VARIANT, GenerationRequest, SkeletonTask,
    TasksetIdentityCertificate, TasksetIdentityError,
    build_taskset_identity_certificate,
)
from .result_writer import atomic_write_json, atomic_write_text
from .rta4_formal_config import (
    RTA4_FORMAL_STORE_VERSION, canonical_json, domain_hash, fraction_text,
)


FORMAL_TASKSET_STORE_MANIFEST = "formal_taskset_store_manifest.json"
FORMAL_TASKSET_STORE_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_FORMAL_TASKSET_STORE:v1"
PRIORITY_IDENTITY_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PRIORITY_VECTOR:v1"


class RTA4FormalTasksetStoreError(RuntimeError):
    """Raised when certificate provenance or store namespace is invalid."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _store_manifest() -> Dict[str, Any]:
    material = {
        "store_version": RTA4_FORMAL_STORE_VERSION,
        "certificate_format": "PR_B_CANONICAL_TASKSET_IDENTITY_CERTIFICATE",
        "certificate_directory": "certificates",
        "identity_axes_excluded": [
            "method", "E0", "release_mode", "battery", "worker_count",
            "timeout", "output_path",
        ],
    }
    return {
        **material,
        "store_identity": domain_hash(FORMAL_TASKSET_STORE_DOMAIN, material),
    }


def formal_taskset_store_identity() -> str:
    return _store_manifest()["store_identity"]


def build_ofat_taskset_variant(
    certificate: TasksetIdentityCertificate, *,
    deadline_slack_fraction: Fraction = Fraction(3, 4),
    power_scale: Fraction = Fraction(1),
) -> TasksetIdentityCertificate:
    """Use the sole PR-B exact deadline/power variant implementation."""

    if type(certificate) is not TasksetIdentityCertificate:
        raise RTA4FormalTasksetStoreError("OFAT base must be a PR-B certificate")
    if type(deadline_slack_fraction) is not Fraction or type(power_scale) is not Fraction:
        raise RTA4FormalTasksetStoreError("OFAT scales must be exact Fractions")
    return build_taskset_identity_certificate(
        certificate.generation_request, certificate.skeleton_tasks,
        deadline_mode=FIXED_SLACK_FRACTION_VARIANT,
        fixed_slack_fraction=deadline_slack_fraction,
        power_scale=power_scale,
    )


def scale_taskset_time_exact(
    certificate: TasksetIdentityCertificate, *, scale: int,
    scaled_request: GenerationRequest,
) -> TasksetIdentityCertificate:
    """Scale C/D/T together while preserving P, utilization and priority."""

    if type(certificate) is not TasksetIdentityCertificate:
        raise RTA4FormalTasksetStoreError("time-scale base must be a PR-B certificate")
    if type(scale) is not int or isinstance(scale, bool) or scale < 1:
        raise RTA4FormalTasksetStoreError("integer time scale must be positive")
    if type(scaled_request) is not GenerationRequest:
        raise RTA4FormalTasksetStoreError("scaled_request must be a GenerationRequest")
    if (
        scaled_request.processors != certificate.processors
        or scaled_request.task_count != len(certificate.tasks)
    ):
        raise RTA4FormalTasksetStoreError("scaled request/taskset dimensions mismatch")
    if certificate.power_variant.scale != 1:
        raise RTA4FormalTasksetStoreError("time-scale base must use base power representation")
    slack = {task.deadline_slack_fraction for task in certificate.tasks}
    if len(slack) != 1:
        raise RTA4FormalTasksetStoreError(
            "CORE-5A time scaling requires one fixed deadline slack fraction"
        )
    scaled_skeleton = tuple(
        SkeletonTask(
            task.task_id, task.priority_rank, task.wcet * scale,
            task.period * scale, task.actual_power,
        )
        for task in certificate.tasks
    )
    result = build_taskset_identity_certificate(
        scaled_request, scaled_skeleton,
        deadline_mode=FIXED_SLACK_FRACTION_VARIANT,
        fixed_slack_fraction=next(iter(slack)), power_scale=Fraction(1),
    )
    for before, after in zip(certificate.tasks, result.tasks):
        if (
            after.wcet != before.wcet * scale
            or after.relative_deadline != before.relative_deadline * scale
            or after.period != before.period * scale
            or after.actual_power != before.actual_power
            or after.priority_rank != before.priority_rank
        ):
            raise RTA4FormalTasksetStoreError("exact time-scale invariant mismatch")
    return result


@dataclass(frozen=True)
class FormalTasksetRows:
    skeleton: Mapping[str, Any]
    taskset: Mapping[str, Any]
    tasks: Tuple[Mapping[str, Any], ...]


def certificate_rows(
    certificate: TasksetIdentityCertificate, *, certificate_path: str,
    certificate_sha256: str,
) -> FormalTasksetRows:
    if type(certificate) is not TasksetIdentityCertificate:
        raise RTA4FormalTasksetStoreError(
            "formal tasksets require a PR-B TasksetIdentityCertificate"
        )
    try:
        certificate.validate()
    except TasksetIdentityError as exc:
        raise RTA4FormalTasksetStoreError("taskset certificate is invalid") from exc
    total_utilization = sum(
        (Fraction(task.wcet, task.period) for task in certificate.tasks),
        Fraction(0),
    )
    total_density = sum(
        (Fraction(task.wcet, task.relative_deadline) for task in certificate.tasks),
        Fraction(0),
    )
    normalized_utilization = total_utilization / certificate.processors
    normalized_density = total_density / certificate.processors
    priority_identity = domain_hash(PRIORITY_IDENTITY_DOMAIN, [
        {"task_id": task.task_id, "priority_rank": task.priority_rank}
        for task in certificate.tasks
    ])
    deadline_variant = canonical_json(
        certificate.deadline_variant.material(certificate.skeleton_tasks)
    )
    power_variant = canonical_json(certificate.power_variant.material())
    request = certificate.generation_request
    skeleton = {
        "generation_request_id": certificate.generation_request_id,
        "taskset_skeleton_id": certificate.taskset_skeleton_id,
        "formal_seed": request.generator_seed,
        "processor_count": certificate.processors,
        "task_count": len(certificate.tasks),
        "target_normalized_utilization": fraction_text(
            request.target_normalized_utilization
        ),
        "actual_normalized_utilization": fraction_text(normalized_utilization),
        "generation_status": "GENERATED_AND_CERTIFIED",
        "priority_identity": priority_identity,
        "base_power_vector_identity": domain_hash(
            "ASAP_BLOCK:V9.3:RTA4_BASE_POWER_VECTOR:v1",
            [task.material() for task in certificate.skeleton_tasks],
        ),
        "certificate_path": certificate_path,
        "certificate_sha256": certificate_sha256,
    }
    taskset = {
        "generation_request_id": certificate.generation_request_id,
        "taskset_skeleton_id": certificate.taskset_skeleton_id,
        "taskset_id": certificate.taskset_id,
        "taskset_hash": certificate.taskset_hash,
        "power_vector_hash": certificate.power_vector_hash,
        "priority_identity": priority_identity,
        "deadline_variant": deadline_variant,
        "power_variant": power_variant,
        "normalized_utilization": fraction_text(normalized_utilization),
        "normalized_density": fraction_text(normalized_density),
        "generation_status": "GENERATED_AND_CERTIFIED",
        "certificate_path": certificate_path,
        "certificate_sha256": certificate_sha256,
    }
    tasks = tuple({
        "taskset_skeleton_id": certificate.taskset_skeleton_id,
        "taskset_id": certificate.taskset_id,
        "task_id": task.task_id,
        "priority_rank": task.priority_rank,
        "C": task.wcet,
        "D": task.relative_deadline,
        "T": task.period,
        "P_exact": fraction_text(task.actual_power),
        "D_over_T": fraction_text(task.deadline_to_period_ratio),
        "deadline_slack_fraction": fraction_text(task.deadline_slack_fraction),
        "deadline_variant": deadline_variant,
        "power_variant": power_variant,
    } for task in certificate.tasks)
    return FormalTasksetRows(skeleton, taskset, tasks)


class RTA4FormalTasksetStore:
    """Persist only canonical PR-B certificates in an isolated namespace."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.certificates = self.root / "certificates"
        marker = self.root / FORMAL_TASKSET_STORE_MANIFEST
        expected = _store_manifest()
        if self.root.exists() and any(self.root.iterdir()) and not marker.is_file():
            raise RTA4FormalTasksetStoreError(
                "refusing to open a non-RTA4 or legacy taskset store"
            )
        if marker.is_file():
            try:
                import json
                observed = json.loads(marker.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RTA4FormalTasksetStoreError("cannot read taskset store marker") from exc
            if observed != expected:
                raise RTA4FormalTasksetStoreError("taskset store identity mismatch")
        self.certificates.mkdir(parents=True, exist_ok=True)
        if not marker.is_file():
            atomic_write_json(marker, expected)

    def put(self, certificate: TasksetIdentityCertificate) -> FormalTasksetRows:
        if type(certificate) is not TasksetIdentityCertificate:
            raise RTA4FormalTasksetStoreError(
                "formal tasksets require a PR-B TasksetIdentityCertificate"
            )
        try:
            certificate.validate()
        except TasksetIdentityError as exc:
            raise RTA4FormalTasksetStoreError("taskset certificate is invalid") from exc
        payload = certificate.canonical_bytes()
        path = self.certificates / f"{certificate.taskset_id}.json"
        if path.is_file():
            if path.read_bytes() != payload:
                raise RTA4FormalTasksetStoreError("taskset certificate conflict")
        else:
            atomic_write_text(path, payload.decode("utf-8"))
        relative = path.relative_to(self.root).as_posix()
        return certificate_rows(
            certificate, certificate_path=relative,
            certificate_sha256=_sha256_bytes(payload),
        )

    def load(self, taskset_id: str) -> TasksetIdentityCertificate:
        path = self.certificates / f"{taskset_id}.json"
        try:
            payload = path.read_bytes()
            certificate = TasksetIdentityCertificate.from_canonical_bytes(payload)
        except Exception as exc:
            raise RTA4FormalTasksetStoreError(
                f"cannot load validated taskset certificate: {taskset_id}"
            ) from exc
        if certificate.taskset_id != taskset_id:
            raise RTA4FormalTasksetStoreError("certificate filename/identity mismatch")
        return certificate


__all__ = [
    "FORMAL_TASKSET_STORE_MANIFEST", "FormalTasksetRows",
    "RTA4FormalTasksetStore", "RTA4FormalTasksetStoreError",
    "build_ofat_taskset_variant", "build_taskset_identity_certificate",
    "certificate_rows", "scale_taskset_time_exact",
    "formal_taskset_store_identity",
]
