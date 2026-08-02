"""Versioned, exact task-source inputs for RTA4 formal V4.

The module has no runner or method-specific behavior.  Both explicit manifests
and registered deterministic families normalize to :class:`TaskSourceV4`.
Scientific floating-point values are rejected before identities are computed.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random
import re
from typing import Any, Callable, Mapping, Sequence

import yaml

from .rta4_formal_config import canonical_json, domain_hash, fraction_text


EXPLICIT_TASKSET_MANIFEST = "EXPLICIT_TASKSET_MANIFEST"
GENERATED_FAMILY = "GENERATED_FAMILY"
GENERAL_RANDOM_CONSTRAINED_V1 = "GENERAL_RANDOM_CONSTRAINED_V1"
T10_BALANCED_V1 = "T10_BALANCED_V1"
EXPLICIT_MANIFEST_SCHEMA_V1 = (
    "ASAP_BLOCK_V9_3_RTA4_EXPLICIT_TASKSET_MANIFEST_V1"
)
TASK_SOURCE_SCHEMA_V4 = "ASAP_BLOCK_V9_3_RTA4_TASK_SOURCE_V4"
TASK_SOURCE_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:TASK_SOURCE:v4"
TASKSET_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:TASKSET:v4"
TASK_ORDER_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:TASK_ORDER:v4"
MANIFEST_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:EXPLICIT_MANIFEST:v4"
CONTENT_CERTIFICATE_DOMAIN_V4 = (
    "ASAP_BLOCK:V9.3:RTA4:TASK_SOURCE_CONTENT_CERTIFICATE:v4"
)
PRIORITY_POLICY_RM = "RM_STRICT_PERIOD_ASCENDING"


class RTA4TaskSourceV4Error(ValueError):
    """Raised when task-source science or content identity is ambiguous."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _plain_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RTA4TaskSourceV4Error(
            f"{label} must be a plain integer >= {minimum}"
        )
    return value


def _positive_int(value: Any, label: str) -> int:
    return _plain_int(value, label, minimum=1)


def _exact_text(
    value: Any, label: str, *, nonnegative: bool = True,
) -> str:
    if type(value) is not str or not value:
        raise RTA4TaskSourceV4Error(
            f"{label} must be an exact rational string"
        )
    try:
        exact = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise RTA4TaskSourceV4Error(f"{label} is not an exact rational") from exc
    if nonnegative and exact < 0:
        raise RTA4TaskSourceV4Error(f"{label} must be nonnegative")
    canonical = fraction_text(exact)
    if value != canonical:
        raise RTA4TaskSourceV4Error(f"{label} must be canonical: {canonical}")
    return canonical


def _field_set(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise RTA4TaskSourceV4Error(
            f"{label} field set mismatch; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    return value


def _stable_id(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value)
    ):
        raise RTA4TaskSourceV4Error(f"{label} is not a stable identifier")
    return value


@dataclass(frozen=True)
class TaskV4:
    name: str
    C: int
    D: int
    T: int
    power: str

    def material(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "C": self.C,
            "D": self.D,
            "T": self.T,
            "power": self.power,
        }


@dataclass(frozen=True)
class TasksetV4:
    taskset_id: str
    source_seed: int | None
    tasks: tuple[TaskV4, ...]
    task_order: tuple[str, ...]
    task_order_sha256: str
    content_sha256: str
    identity: str

    def material(self, *, include_identity: bool = True) -> dict[str, Any]:
        material = {
            "taskset_id": self.taskset_id,
            "source_seed": self.source_seed,
            "task_order": list(self.task_order),
            "task_order_sha256": self.task_order_sha256,
            "tasks": [task.material() for task in self.tasks],
            "content_sha256": self.content_sha256,
        }
        if include_identity:
            material["taskset_identity"] = self.identity
        return material

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.material()).encode("utf-8")


@dataclass(frozen=True)
class TaskSourceV4:
    mode: str
    processors: int
    priority_policy: str
    task_count: int
    taskset_count: int
    tasksets: tuple[TasksetV4, ...]
    normalized_config: Mapping[str, Any]
    identity: str
    content_certificate: Mapping[str, Any]
    manifest_path: str | None = None
    manifest_file_sha256: str | None = None
    manifest_semantic_sha256: str | None = None

    def taskset(self, index: int) -> TasksetV4:
        if type(index) is not int or not 0 <= index < len(self.tasksets):
            raise RTA4TaskSourceV4Error("taskset index is outside source")
        return self.tasksets[index]


def _normalize_task(raw: Any, label: str) -> TaskV4:
    row = _field_set(raw, {"name", "C", "D", "T", "power"}, label)
    name = _stable_id(row["name"], f"{label}.name")
    c = _positive_int(row["C"], f"{label}.C")
    d = _positive_int(row["D"], f"{label}.D")
    period = _positive_int(row["T"], f"{label}.T")
    if not c <= d <= period:
        raise RTA4TaskSourceV4Error(f"{label} violates C<=D<=T")
    power = _exact_text(row["power"], f"{label}.power")
    return TaskV4(name, c, d, period, power)


def _validate_task_sequence(
    tasks: Sequence[TaskV4], task_order: Sequence[str], *,
    task_count: int, label: str,
) -> tuple[TaskV4, ...]:
    if len(tasks) != task_count:
        raise RTA4TaskSourceV4Error(f"{label} task count mismatch")
    names = tuple(task.name for task in tasks)
    if len(set(names)) != len(names):
        raise RTA4TaskSourceV4Error(f"{label} task names are not unique")
    if tuple(task_order) != names:
        raise RTA4TaskSourceV4Error(f"{label} task order differs from tasks")
    periods = tuple(task.T for task in tasks)
    if any(left >= right for left, right in zip(periods, periods[1:])):
        raise RTA4TaskSourceV4Error(
            f"{label} violates strict RM period order"
        )
    return tuple(tasks)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise RTA4TaskSourceV4Error(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping,
)


def _parse_manifest(path: Path) -> tuple[bytes, Mapping[str, Any]]:
    payload = path.read_bytes()
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise RTA4TaskSourceV4Error(
                            f"duplicate JSON key: {key!r}"
                        )
                    result[key] = value
                return result

            raw = json.loads(payload, object_pairs_hook=unique_pairs)
        elif suffix in {".yaml", ".yml"}:
            raw = yaml.load(payload, Loader=_UniqueKeyLoader)
        else:
            raise RTA4TaskSourceV4Error(
                "explicit manifest extension must be .json/.yaml/.yml"
            )
    except RTA4TaskSourceV4Error:
        raise
    except Exception as exc:
        raise RTA4TaskSourceV4Error(
            f"cannot parse explicit taskset manifest: {path}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise RTA4TaskSourceV4Error("explicit manifest root must be a mapping")
    return payload, raw


def load_explicit_taskset_manifest_v4(path: Path | str) -> TaskSourceV4:
    manifest_path = Path(path).expanduser().resolve(strict=True)
    payload, raw = _parse_manifest(manifest_path)
    row = _field_set(raw, {
        "schema", "processors", "priority_policy", "task_count",
        "taskset_count", "task_order", "tasksets",
    }, "manifest")
    if row["schema"] != EXPLICIT_MANIFEST_SCHEMA_V1:
        raise RTA4TaskSourceV4Error("unknown explicit manifest schema")
    processors = _positive_int(row["processors"], "manifest.processors")
    if row["priority_policy"] != PRIORITY_POLICY_RM:
        raise RTA4TaskSourceV4Error("unsupported manifest priority policy")
    task_count = _positive_int(row["task_count"], "manifest.task_count")
    taskset_count = _positive_int(row["taskset_count"], "manifest.taskset_count")
    order_raw = row["task_order"]
    if (
        type(order_raw) is not list
        or len(order_raw) != task_count
        or any(type(item) is not str for item in order_raw)
        or len(set(order_raw)) != len(order_raw)
    ):
        raise RTA4TaskSourceV4Error("manifest.task_order is invalid")
    order = tuple(_stable_id(item, "manifest.task_order") for item in order_raw)
    tasksets_raw = row["tasksets"]
    if type(tasksets_raw) is not list or len(tasksets_raw) != taskset_count:
        raise RTA4TaskSourceV4Error("manifest.tasksets count mismatch")

    normalized_rows = []
    taskset_ids = set()
    for index, raw_taskset in enumerate(tasksets_raw):
        taskset_row = _field_set(
            raw_taskset, {"taskset_id", "source_seed", "tasks"},
            f"manifest.tasksets[{index}]",
        )
        taskset_id = _stable_id(
            taskset_row["taskset_id"], f"manifest.tasksets[{index}].taskset_id",
        )
        if taskset_id in taskset_ids:
            raise RTA4TaskSourceV4Error("manifest taskset IDs are not unique")
        taskset_ids.add(taskset_id)
        seed = taskset_row["source_seed"]
        if seed is not None:
            seed = _plain_int(seed, f"manifest.tasksets[{index}].source_seed")
        raw_tasks = taskset_row["tasks"]
        if type(raw_tasks) is not list:
            raise RTA4TaskSourceV4Error("manifest taskset tasks must be a list")
        tasks = tuple(
            _normalize_task(value, f"manifest.tasksets[{index}].tasks[{task_index}]")
            for task_index, value in enumerate(raw_tasks)
        )
        _validate_task_sequence(
            tasks, order, task_count=task_count,
            label=f"manifest.tasksets[{index}]",
        )
        normalized_rows.append({
            "taskset_id": taskset_id,
            "source_seed": seed,
            "tasks": [task.material() for task in tasks],
        })
    semantic = {
        "schema": EXPLICIT_MANIFEST_SCHEMA_V1,
        "processors": processors,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_count": task_count,
        "taskset_count": taskset_count,
        "task_order": list(order),
        "tasksets": normalized_rows,
    }
    file_sha = _sha256(payload)
    semantic_sha = _sha256(canonical_json(semantic).encode("utf-8"))
    source_config = {
        "schema": TASK_SOURCE_SCHEMA_V4,
        "mode": EXPLICIT_TASKSET_MANIFEST,
        "manifest_file_sha256": file_sha,
        "manifest_semantic_sha256": semantic_sha,
        "processors": processors,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_count": task_count,
        "taskset_count": taskset_count,
        "task_order": list(order),
    }
    source_identity = domain_hash(MANIFEST_DOMAIN_V4, source_config)
    order_identity = domain_hash(TASK_ORDER_DOMAIN_V4, list(order))
    tasksets = []
    for row_value in normalized_rows:
        content = {
            "taskset_id": row_value["taskset_id"],
            "source_seed": row_value["source_seed"],
            "task_order": list(order),
            "tasks": row_value["tasks"],
        }
        content_sha = _sha256(canonical_json(content).encode("utf-8"))
        identity = domain_hash(TASKSET_DOMAIN_V4, {
            "task_source_identity": source_identity,
            "content_sha256": content_sha,
        })
        tasksets.append(TasksetV4(
            row_value["taskset_id"], row_value["source_seed"],
            tuple(TaskV4(**task) for task in row_value["tasks"]), order,
            order_identity, content_sha, identity,
        ))
    certificate_base = {
        "schema": "ASAP_BLOCK_V9_3_RTA4_TASK_SOURCE_CONTENT_CERTIFICATE_V4",
        "mode": EXPLICIT_TASKSET_MANIFEST,
        "task_source_identity": source_identity,
        "manifest_file_sha256": file_sha,
        "manifest_semantic_sha256": semantic_sha,
        "task_order_sha256": order_identity,
        "taskset_identities": [taskset.identity for taskset in tasksets],
    }
    certificate = {
        **certificate_base,
        "content_certificate_identity": domain_hash(
            CONTENT_CERTIFICATE_DOMAIN_V4, certificate_base,
        ),
    }
    return TaskSourceV4(
        EXPLICIT_TASKSET_MANIFEST, processors, PRIORITY_POLICY_RM,
        task_count, taskset_count, tuple(tasksets), source_config,
        source_identity, certificate, str(manifest_path), file_sha, semantic_sha,
    )


FROZEN_T10_CORE_DISTRIBUTION = {
    "tau_1": {"C": [1], "D": [4, 5], "T": [5, 6], "power": ["1/12", "1/10"]},
    "tau_2": {"C": [1], "D": [6, 7, 8], "T": [10, 11], "power": ["1/6", "1/5"]},
    "tau_3": {"C": [1], "D": [9, 10, 11], "T": [12], "power": ["1/5", "1/4"]},
    "tau_4": {"C": [1, 2], "D": [9, 10, 11], "T": [13, 14], "power": ["1/50", "1/40"]},
    "tau_5": {"C": [1], "D": [11, 12, 13], "T": [15, 16], "power": ["1/100", "1/80"]},
    "tau_6": {"C": [3, 4], "D": [12, 13], "T": [19, 20], "power": ["1/25", "1/20"]},
    "tau_7": {"C": [1], "D": [17, 18, 19], "T": [21, 22], "power": ["1/6", "1/5"]},
}
FROZEN_T10_CORE_GENERATOR_CONTRACT = {
    "contract_id": "T10_SEVEN_TASK_CORE_FROZEN_DISTRIBUTION_V2",
    "rng": "PYTHON_RANDOM_MT19937_CHOICE_ORDER_V1",
    "seed_formula": "base_seed_plus_generation_index",
    "task_order": [f"tau_{index}" for index in range(1, 8)],
    "distribution": FROZEN_T10_CORE_DISTRIBUTION,
}
FROZEN_T10_BACKGROUND_TASKS = [
    {"name": "tau_8", "C": 1, "D": 24, "T": 27, "power": "1/80"},
    {"name": "tau_9", "C": 1, "D": 28, "T": 36, "power": "1/80"},
    {"name": "tau_10", "C": 1, "D": 34, "T": 54, "power": "1/80"},
]


def _generation_indices(value: Any, count: int, label: str) -> list[int]:
    if type(value) is not list or len(value) != count:
        raise RTA4TaskSourceV4Error(f"{label} count mismatch")
    result = [
        _plain_int(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(set(result)) != len(result):
        raise RTA4TaskSourceV4Error(f"{label} contains duplicates")
    return result


def _normalize_choice_list(
    value: Any, label: str, *, rational: bool = False,
) -> list[Any]:
    if type(value) is not list or not value:
        raise RTA4TaskSourceV4Error(f"{label} must be a non-empty list")
    result = (
        [_exact_text(item, f"{label}[{index}]") for index, item in enumerate(value)]
        if rational else
        [_positive_int(item, f"{label}[{index}]") for index, item in enumerate(value)]
    )
    if len(set(result)) != len(result):
        raise RTA4TaskSourceV4Error(f"{label} contains duplicates")
    return result


def _normalize_templates(value: Any, task_count: int) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) != task_count:
        raise RTA4TaskSourceV4Error("task_templates count mismatch")
    templates = []
    for index, raw in enumerate(value):
        row = _field_set(raw, {"name", "C", "D", "T", "power"}, f"task_templates[{index}]")
        template = {
            "name": _stable_id(row["name"], f"task_templates[{index}].name"),
            "C": _normalize_choice_list(row["C"], f"task_templates[{index}].C"),
            "D": _normalize_choice_list(row["D"], f"task_templates[{index}].D"),
            "T": _normalize_choice_list(row["T"], f"task_templates[{index}].T"),
            "power": _normalize_choice_list(
                row["power"], f"task_templates[{index}].power", rational=True,
            ),
        }
        if max(template["C"]) > min(template["D"]) or max(template["D"]) > min(template["T"]):
            raise RTA4TaskSourceV4Error(
                f"task_templates[{index}] does not guarantee C<=D<=T"
            )
        templates.append(template)
    if len({row["name"] for row in templates}) != task_count:
        raise RTA4TaskSourceV4Error("task template names are not unique")
    for left, right in zip(templates, templates[1:]):
        if max(left["T"]) >= min(right["T"]):
            raise RTA4TaskSourceV4Error(
                "task templates do not guarantee strict RM order"
            )
    return templates


def _normalize_general_parameters(raw: Any) -> dict[str, Any]:
    row = _field_set(raw, {
        "processors", "priority_policy", "task_count", "taskset_count",
        "base_seed", "generation_indices", "task_templates",
    }, "GENERAL_RANDOM_CONSTRAINED_V1.parameters")
    processors = _positive_int(row["processors"], "parameters.processors")
    if row["priority_policy"] != PRIORITY_POLICY_RM:
        raise RTA4TaskSourceV4Error("general family priority policy mismatch")
    task_count = _positive_int(row["task_count"], "parameters.task_count")
    taskset_count = _positive_int(row["taskset_count"], "parameters.taskset_count")
    return {
        "processors": processors,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_count": task_count,
        "taskset_count": taskset_count,
        "base_seed": _plain_int(row["base_seed"], "parameters.base_seed"),
        "generation_indices": _generation_indices(
            row["generation_indices"], taskset_count,
            "parameters.generation_indices",
        ),
        "task_templates": _normalize_templates(row["task_templates"], task_count),
    }


def _normalize_t10_parameters(raw: Any) -> dict[str, Any]:
    row = _field_set(raw, {
        "processors", "priority_policy", "task_count",
        "mechanism_core_task_count", "background_utilization",
        "background_tasks", "taskset_count", "base_seed",
        "generation_indices", "core_generator_contract",
    }, "T10_BALANCED_V1.parameters")
    if _positive_int(row["processors"], "parameters.processors") != 4:
        raise RTA4TaskSourceV4Error("T10 processors must be 4")
    if row["priority_policy"] != PRIORITY_POLICY_RM:
        raise RTA4TaskSourceV4Error("T10 priority policy mismatch")
    if _positive_int(row["task_count"], "parameters.task_count") != 10:
        raise RTA4TaskSourceV4Error("T10 task_count must be 10")
    if _positive_int(row["mechanism_core_task_count"], "parameters.mechanism_core_task_count") != 7:
        raise RTA4TaskSourceV4Error("T10 mechanism core count must be 7")
    if _exact_text(row["background_utilization"], "parameters.background_utilization") != "1/12":
        raise RTA4TaskSourceV4Error("T10 background utilization must be 1/12")
    backgrounds = [
        _normalize_task(value, f"parameters.background_tasks[{index}]").material()
        for index, value in enumerate(row["background_tasks"])
    ] if type(row["background_tasks"]) is list else []
    if backgrounds != FROZEN_T10_BACKGROUND_TASKS:
        raise RTA4TaskSourceV4Error("T10 background task contract mismatch")
    generator = json.loads(canonical_json(row["core_generator_contract"]))
    if generator != FROZEN_T10_CORE_GENERATOR_CONTRACT:
        raise RTA4TaskSourceV4Error("T10 core generator contract mismatch")
    taskset_count = _positive_int(row["taskset_count"], "parameters.taskset_count")
    return {
        "processors": 4,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_count": 10,
        "mechanism_core_task_count": 7,
        "background_utilization": "1/12",
        "background_tasks": backgrounds,
        "taskset_count": taskset_count,
        "base_seed": _plain_int(row["base_seed"], "parameters.base_seed"),
        "generation_indices": _generation_indices(
            row["generation_indices"], taskset_count,
            "parameters.generation_indices",
        ),
        "core_generator_contract": generator,
    }


def _hash_choice(values: Sequence[Any], material: Mapping[str, Any]) -> Any:
    digest = hashlib.sha256(
        b"ASAP_BLOCK:V9.3:RTA4:GENERAL_RANDOM_CONSTRAINED_V1\0"
        + canonical_json(material).encode("utf-8")
    ).digest()
    return values[int.from_bytes(digest, "big") % len(values)]


def _generate_general(parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    generated = []
    for ordinal, generation_index in enumerate(parameters["generation_indices"]):
        seed = parameters["base_seed"] + generation_index
        tasks = []
        for task_index, template in enumerate(parameters["task_templates"]):
            task = {"name": template["name"]}
            for field in ("C", "D", "T", "power"):
                task[field] = _hash_choice(template[field], {
                    "seed": seed,
                    "generation_index": generation_index,
                    "task_index": task_index,
                    "field": field,
                    "choices": template[field],
                })
            tasks.append(task)
        generated.append({
            "taskset_id": f"general-{ordinal:08d}",
            "source_seed": seed,
            "tasks": tasks,
        })
    return generated


def _generate_t10(parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    distribution = parameters["core_generator_contract"]["distribution"]
    order = parameters["core_generator_contract"]["task_order"]
    generated = []
    for ordinal, generation_index in enumerate(parameters["generation_indices"]):
        seed = parameters["base_seed"] + generation_index
        rng = random.Random(seed)
        tasks = []
        for name in order:
            specification = distribution[name]
            period = int(rng.choice(specification["T"]))
            wcet = int(rng.choice(specification["C"]))
            deadlines = [
                int(value) for value in specification["D"]
                if wcet <= int(value) <= period
            ]
            if not deadlines:
                raise RTA4TaskSourceV4Error("T10 generated no valid deadline")
            tasks.append({
                "name": name,
                "C": wcet,
                "D": int(rng.choice(deadlines)),
                "T": period,
                "power": fraction_text(Fraction(rng.choice(specification["power"]))),
            })
        tasks.extend(parameters["background_tasks"])
        generated.append({
            "taskset_id": f"t10-balanced-{ordinal:08d}",
            "source_seed": seed,
            "tasks": tasks,
        })
    return generated


_FAMILY_REGISTRY: dict[
    str, tuple[str, Callable[[Any], dict[str, Any]], Callable[[Mapping[str, Any]], list[dict[str, Any]]]]
] = {
    GENERAL_RANDOM_CONSTRAINED_V1: (
        "ASAP_BLOCK_V9_3_RTA4_GENERAL_RANDOM_CONSTRAINED_FAMILY_SCHEMA_V1",
        _normalize_general_parameters, _generate_general,
    ),
    T10_BALANCED_V1: (
        "ASAP_BLOCK_V9_3_RTA4_T10_BALANCED_FAMILY_SCHEMA_V1",
        _normalize_t10_parameters, _generate_t10,
    ),
}


def registered_family_ids_v4() -> tuple[str, ...]:
    return tuple(_FAMILY_REGISTRY)


def normalize_generated_family_v4(raw: Any) -> TaskSourceV4:
    row = _field_set(raw, {"mode", "family_id", "parameters"}, "task_source")
    if row["mode"] != GENERATED_FAMILY:
        raise RTA4TaskSourceV4Error("task_source mode is not GENERATED_FAMILY")
    family_id = row["family_id"]
    if family_id not in _FAMILY_REGISTRY:
        raise RTA4TaskSourceV4Error(f"unknown generated family: {family_id!r}")
    family_schema, normalizer, generator = _FAMILY_REGISTRY[str(family_id)]
    parameters = normalizer(row["parameters"])
    source_config = {
        "schema": TASK_SOURCE_SCHEMA_V4,
        "mode": GENERATED_FAMILY,
        "family_id": family_id,
        "family_schema": family_schema,
        "family_version": "1",
        "parameters": parameters,
    }
    source_identity = domain_hash(TASK_SOURCE_DOMAIN_V4, source_config)
    raw_tasksets = generator(parameters)
    order = tuple(task["name"] for task in raw_tasksets[0]["tasks"])
    order_identity = domain_hash(TASK_ORDER_DOMAIN_V4, list(order))
    tasksets = []
    for index, raw_taskset in enumerate(raw_tasksets):
        tasks = tuple(
            _normalize_task(value, f"generated[{index}].tasks[{task_index}]")
            for task_index, value in enumerate(raw_taskset["tasks"])
        )
        _validate_task_sequence(
            tasks, order, task_count=parameters["task_count"],
            label=f"generated[{index}]",
        )
        content = {
            "taskset_id": raw_taskset["taskset_id"],
            "source_seed": raw_taskset["source_seed"],
            "task_order": list(order),
            "tasks": [task.material() for task in tasks],
        }
        content_sha = _sha256(canonical_json(content).encode("utf-8"))
        identity = domain_hash(TASKSET_DOMAIN_V4, {
            "task_source_identity": source_identity,
            "generation_ordinal": index,
            "content_sha256": content_sha,
        })
        tasksets.append(TasksetV4(
            raw_taskset["taskset_id"], raw_taskset["source_seed"], tasks,
            order, order_identity, content_sha, identity,
        ))
    certificate_base = {
        "schema": "ASAP_BLOCK_V9_3_RTA4_TASK_SOURCE_CONTENT_CERTIFICATE_V4",
        "mode": GENERATED_FAMILY,
        "task_source_identity": source_identity,
        "family_id": family_id,
        "family_schema": family_schema,
        "task_order_sha256": order_identity,
        "taskset_identities": [taskset.identity for taskset in tasksets],
    }
    certificate = {
        **certificate_base,
        "content_certificate_identity": domain_hash(
            CONTENT_CERTIFICATE_DOMAIN_V4, certificate_base,
        ),
    }
    return TaskSourceV4(
        GENERATED_FAMILY, parameters["processors"], PRIORITY_POLICY_RM,
        parameters["task_count"], parameters["taskset_count"],
        tuple(tasksets), source_config, source_identity, certificate,
    )


def load_task_source_v4(
    raw: Any, *, base_directory: Path | str | None = None,
) -> TaskSourceV4:
    if not isinstance(raw, Mapping):
        raise RTA4TaskSourceV4Error("task_source must be a mapping")
    mode = raw.get("mode")
    if mode == EXPLICIT_TASKSET_MANIFEST:
        row = _field_set(raw, {"mode", "manifest_path"}, "task_source")
        manifest = row["manifest_path"]
        if type(manifest) is not str or not manifest:
            raise RTA4TaskSourceV4Error("manifest_path must be non-empty")
        path = Path(manifest)
        if not path.is_absolute():
            if base_directory is None:
                raise RTA4TaskSourceV4Error(
                    "relative manifest requires a base directory"
                )
            path = Path(base_directory) / path
        return load_explicit_taskset_manifest_v4(path)
    if mode == GENERATED_FAMILY:
        return normalize_generated_family_v4(raw)
    raise RTA4TaskSourceV4Error("missing or unknown task_source mode")


def revalidate_task_source_v4(source: TaskSourceV4) -> TaskSourceV4:
    if type(source) is not TaskSourceV4:
        raise RTA4TaskSourceV4Error("task source has not been normalized")
    if source.mode == EXPLICIT_TASKSET_MANIFEST:
        observed = load_explicit_taskset_manifest_v4(str(source.manifest_path))
    elif source.mode == GENERATED_FAMILY:
        observed = normalize_generated_family_v4({
            "mode": GENERATED_FAMILY,
            "family_id": source.normalized_config["family_id"],
            "parameters": source.normalized_config["parameters"],
        })
    else:
        raise RTA4TaskSourceV4Error("unknown normalized task source mode")
    if (
        observed.identity != source.identity
        or observed.content_certificate != source.content_certificate
        or tuple(taskset.canonical_bytes() for taskset in observed.tasksets)
        != tuple(taskset.canonical_bytes() for taskset in source.tasksets)
    ):
        raise RTA4TaskSourceV4Error("task source changed after normalization")
    return observed


__all__ = [
    "CONTENT_CERTIFICATE_DOMAIN_V4", "EXPLICIT_MANIFEST_SCHEMA_V1",
    "EXPLICIT_TASKSET_MANIFEST", "FROZEN_T10_BACKGROUND_TASKS",
    "FROZEN_T10_CORE_DISTRIBUTION", "FROZEN_T10_CORE_GENERATOR_CONTRACT",
    "GENERAL_RANDOM_CONSTRAINED_V1", "GENERATED_FAMILY",
    "PRIORITY_POLICY_RM", "RTA4TaskSourceV4Error", "T10_BALANCED_V1",
    "TASK_SOURCE_SCHEMA_V4", "TaskSourceV4", "TaskV4", "TasksetV4",
    "load_explicit_taskset_manifest_v4", "load_task_source_v4",
    "normalize_generated_family_v4", "registered_family_ids_v4",
    "revalidate_task_source_v4",
]
