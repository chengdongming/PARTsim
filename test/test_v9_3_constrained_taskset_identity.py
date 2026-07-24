from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import random
import subprocess
import sys

import pytest

import asap_block_v9_3_runner as production_runner
from experiments.v9_3.cell_model import expand_cells
from experiments.v9_3.config import config_hash, domain_hash, fraction_text, load_config
from experiments.v9_3.constrained_taskset_identity import (
    BASE_POWER_VARIANT,
    CONSTRAINED_UNIFORM_SLACK_MODE,
    DEADLINE_CONTRACT_VERSION,
    DEADLINE_DRAW_DOMAIN,
    DeadlineVariant,
    FIXED_SLACK_FRACTION_VARIANT,
    GENERATION_REQUEST_CONTRACT_VERSION,
    GENERATION_REQUEST_DOMAIN,
    GenerationRequest,
    IMPLICIT_DEADLINE_MODE,
    POWER_VECTOR_DOMAIN,
    PRIMARY_DEADLINE_GENERATION_MODES,
    SCALED_POWER_VARIANT,
    SkeletonTask,
    TASKSET_CONTENT_DOMAIN,
    TASKSET_ID_DOMAIN,
    TASKSET_SKELETON_DOMAIN,
    TasksetIdentityCertificate,
    TasksetIdentityError,
    TasksetTask,
    UINT64_MAX,
    build_taskset_identity_certificate,
    canonical_identity_bytes,
    compute_taskset_hash,
    compute_taskset_id,
    deadline_from_slack_fraction,
    fixed_slack_deadline,
    fraction_from_canonical_material,
    power_vector_hash,
)
from experiments.v9_3.execution_engine import ExecutionEngine
from global_task_generator import EnergyAwareTaskGenerator


ROOT = Path(__file__).resolve().parents[1]


def _request(replicate_index=7, **changes):
    values = {
        "formal_master_seed": 930700,
        "generator_seed": 117,
        "processors": 4,
        "task_count": 3,
        "target_normalized_utilization": Fraction(1, 2),
        "replicate_index": replicate_index,
        "period_min": 40,
        "period_max": 200,
        "utilization_allocation_mode": "uunifast_discard_v1",
        "min_task_utilization": Fraction(1, 100),
        "max_task_utilization": Fraction(4, 5),
        "utilization_tolerance": Fraction(1, 100),
        "wcet_rounding_mode": "compensated",
        "generator_version": "global_task_generator_frozen_v1",
        "power_generation_mode": "generator_default_heterogeneous",
        "power_generation_contract_identity": "1" * 64,
        "workload_candidate_identity": "2" * 64,
        "priority_policy": "RM",
        "dag_generation_mode": "disabled",
        "arrival_offset_generation_mode": "disabled",
        "energy_aware_generation": False,
    }
    values.update(changes)
    return GenerationRequest(**values)


def _skeleton():
    return (
        SkeletonTask("tau-a", 0, 3, 11, Fraction(1, 3)),
        SkeletonTask("tau-b", 1, 5, 13, Fraction(2, 5)),
        SkeletonTask("tau-c", 2, 7, 17, Fraction(3, 7)),
    )


def _certificate(
    deadline_mode=CONSTRAINED_UNIFORM_SLACK_MODE,
    *,
    request=None,
    skeleton=None,
    fixed=None,
    power_scale=Fraction(1),
):
    return build_taskset_identity_certificate(
        request or _request(),
        skeleton or _skeleton(),
        deadline_mode=deadline_mode,
        fixed_slack_fraction=fixed,
        power_scale=power_scale,
    )


def _process_build(_index):
    return _certificate().canonical_bytes()


@pytest.mark.parametrize(
    "numerator,denominator,expected",
    [
        (0, 1, 4),
        (1, 1, 20),
        (1, 4, 8),
        (1, 2, 12),
        (3, 4, 16),
    ],
)
def test_exact_deadline_formula_boundaries(numerator, denominator, expected):
    assert deadline_from_slack_fraction(
        4, 20, numerator, denominator
    ) == expected


def test_exact_deadline_floor_large_integer_and_equal_period_boundary():
    assert deadline_from_slack_fraction(3, 10, 1, 3) == 5
    huge = 10**100
    assert deadline_from_slack_fraction(huge, huge + 11, 2, 3) == huge + 7
    assert deadline_from_slack_fraction(huge, huge, 0, 1) == huge
    assert deadline_from_slack_fraction(huge, huge, 1, 1) == huge


@pytest.mark.parametrize("fraction", [Fraction(1, 4), Fraction(1, 2), Fraction(3, 4), Fraction(1)])
def test_fixed_slack_helper_uses_exact_common_formula(fraction):
    assert fixed_slack_deadline(4, 20, fraction) == deadline_from_slack_fraction(
        4, 20, fraction.numerator, fraction.denominator
    )


def test_task_deadline_metrics_are_exact_and_equal_period_has_unit_slack():
    ordinary = TasksetTask(
        "a", 0, 4, 20, 8, Fraction(1, 3), FIXED_SLACK_FRACTION_VARIANT
    )
    boundary = TasksetTask(
        "b", 0, 9, 9, 9, Fraction(2, 3), IMPLICIT_DEADLINE_MODE
    )
    assert ordinary.deadline_to_period_ratio == Fraction(2, 5)
    assert ordinary.deadline_slack_fraction == Fraction(1, 4)
    assert boundary.deadline_to_period_ratio == 1
    assert boundary.deadline_slack_fraction == 1
    assert all(
        type(value) is int
        for ratio in (
            ordinary.deadline_to_period_ratio,
            ordinary.deadline_slack_fraction,
            boundary.deadline_slack_fraction,
        )
        for value in (ratio.numerator, ratio.denominator)
    )


def test_primary_modes_and_identity_domains_are_unique_and_frozen():
    assert PRIMARY_DEADLINE_GENERATION_MODES == {
        "implicit", "constrained_uniform_slack_v1",
    }
    assert len({
        GENERATION_REQUEST_DOMAIN,
        TASKSET_SKELETON_DOMAIN,
        TASKSET_CONTENT_DOMAIN,
        TASKSET_ID_DOMAIN,
        DEADLINE_DRAW_DOMAIN,
    }) == 5
    assert GENERATION_REQUEST_CONTRACT_VERSION == (
        "ASAP_BLOCK_V9_3_GENERATION_REQUEST_V1"
    )
    assert DEADLINE_CONTRACT_VERSION == "ASAP_BLOCK_V9_3_DEADLINE_V1"


def test_implicit_and_uniform_modes_share_the_same_skeleton():
    implicit = _certificate(IMPLICIT_DEADLINE_MODE)
    constrained = _certificate(CONSTRAINED_UNIFORM_SLACK_MODE)
    assert implicit.generation_request_id == constrained.generation_request_id
    assert implicit.taskset_skeleton_id == constrained.taskset_skeleton_id
    assert [task.relative_deadline for task in implicit.tasks] == [
        task.period for task in implicit.tasks
    ]
    assert all(
        1 <= task.wcet <= task.relative_deadline <= task.period
        for task in constrained.tasks
    )
    assert [
        (task.task_id, task.priority_rank, task.wcet, task.period, task.actual_power)
        for task in implicit.tasks
    ] == [
        (task.task_id, task.priority_rank, task.wcet, task.period, task.actual_power)
        for task in constrained.tasks
    ]


def test_repeated_calls_and_unrelated_call_order_are_byte_deterministic():
    expected = _certificate().canonical_bytes()
    assert _certificate().canonical_bytes() == expected
    _certificate(
        FIXED_SLACK_FRACTION_VARIANT,
        request=_request(replicate_index=99),
        fixed=Fraction(3, 4),
        power_scale=Fraction(5, 4),
    )
    assert _certificate().canonical_bytes() == expected
    assert _certificate(IMPLICIT_DEADLINE_MODE).canonical_bytes() != expected
    assert _certificate().canonical_bytes() == expected


@pytest.mark.parametrize("workers", [2, 4])
def test_process_count_does_not_change_any_identity_or_bytes(workers):
    expected = _certificate().canonical_bytes()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        observed = list(pool.map(_process_build, range(workers * 2)))
    assert observed == [expected] * (workers * 2)


def test_fresh_python_process_produces_identical_certificate_bytes():
    script = """
from fractions import Fraction
from experiments.v9_3.constrained_taskset_identity import *
r = GenerationRequest(930700,117,4,3,Fraction(1,2),7,40,200,
    'uunifast_discard_v1',Fraction(1,100),Fraction(4,5),Fraction(1,100),
    'compensated','global_task_generator_frozen_v1',
    'generator_default_heterogeneous','1'*64,'2'*64,'RM','disabled',
    'disabled',False)
s = (SkeletonTask('tau-a',0,3,11,Fraction(1,3)),
     SkeletonTask('tau-b',1,5,13,Fraction(2,5)),
     SkeletonTask('tau-c',2,7,17,Fraction(3,7)))
c = build_taskset_identity_certificate(
    r,s,deadline_mode=CONSTRAINED_UNIFORM_SLACK_MODE)
print(c.canonical_bytes().hex())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT), capture_output=True, text=True, check=True,
    )
    assert bytes.fromhex(completed.stdout.strip()) == _certificate().canonical_bytes()


def test_canonical_round_trip_is_byte_exact():
    certificate = _certificate()
    restored = TasksetIdentityCertificate.from_canonical_bytes(
        certificate.canonical_bytes()
    )
    assert restored == certificate
    assert restored.canonical_bytes() == certificate.canonical_bytes()


def _generator_output(seed, *, interleave_identity):
    generator = EnergyAwareTaskGenerator(seed=seed, energy_manager=None)
    if interleave_identity:
        _certificate()
    tasks, resources, dag, energy = generator.generate_taskset(
        n=5,
        total_utilization=2.0,
        min_period=40,
        max_period=80,
        num_cpus=4,
        implicit_deadline=True,
        dag_enabled=False,
        energy_aware=False,
        arrival_offset=False,
    )
    return tasks, resources, dag, energy


def test_deadline_and_identity_logic_do_not_consume_shared_rng_across_many_seeds():
    for seed in range(930700, 930716):
        assert _generator_output(seed, interleave_identity=False) == _generator_output(
            seed, interleave_identity=True
        )


def test_deadline_logic_does_not_touch_python_random_state():
    random.seed(121)
    before = random.getstate()
    _certificate()
    after = random.getstate()
    assert after == before
    source = (ROOT / "experiments/v9_3/constrained_taskset_identity.py").read_text(
        encoding="utf-8"
    )
    assert "import random" not in source
    assert "random.Random" not in source


def test_generation_request_id_binds_every_skeleton_generation_input():
    baseline = _certificate()
    changes = {
        "formal_master_seed": 930701,
        "generator_seed": 118,
        "processors": 5,
        "task_count": 4,
        "target_normalized_utilization": Fraction(3, 5),
        "replicate_index": 8,
        "period_min": 41,
        "period_max": 201,
        "utilization_allocation_mode": "different_uunifast",
        "min_task_utilization": Fraction(1, 50),
        "max_task_utilization": Fraction(3, 4),
        "utilization_tolerance": Fraction(1, 50),
        "wcet_rounding_mode": "ceil",
        "generator_version": "generator_v2",
        "power_generation_mode": "power_v2",
        "power_generation_contract_identity": "3" * 64,
        "workload_candidate_identity": "4" * 64,
        "priority_policy": "different_priority",
        "dag_generation_mode": "enabled_v1",
        "arrival_offset_generation_mode": "period_fraction_v1",
        "energy_aware_generation": True,
    }
    for field, value in changes.items():
        request = _request(**{field: value})
        assert request.identity_material() != baseline.generation_request.identity_material()
        assert request != baseline.generation_request
        # task_count/processors changes cannot build against the fixture, but
        # generation identity itself remains directly auditable.
        from experiments.v9_3.constrained_taskset_identity import generation_request_id
        assert generation_request_id(request) != baseline.generation_request_id


def test_identity_relationships_for_deadline_and_power_changes():
    deadline_a = _certificate(
        FIXED_SLACK_FRACTION_VARIANT, fixed=Fraction(1, 2)
    )
    deadline_b = _certificate(
        FIXED_SLACK_FRACTION_VARIANT, fixed=Fraction(3, 4)
    )
    power_b = _certificate(
        FIXED_SLACK_FRACTION_VARIANT,
        fixed=Fraction(1, 2),
        power_scale=Fraction(3, 2),
    )
    assert deadline_a.taskset_skeleton_id == deadline_b.taskset_skeleton_id
    assert deadline_a.taskset_hash != deadline_b.taskset_hash
    assert deadline_a.taskset_id != deadline_b.taskset_id
    assert deadline_a.taskset_skeleton_id == power_b.taskset_skeleton_id
    assert deadline_a.taskset_hash != power_b.taskset_hash
    assert deadline_a.taskset_id != power_b.taskset_id
    assert deadline_a.power_vector_hash != power_b.power_vector_hash
    assert deadline_a.power_variant.mode == BASE_POWER_VARIANT
    assert power_b.power_variant.mode == SCALED_POWER_VARIANT


def test_identity_relationships_for_c_t_and_priority_changes():
    baseline = _certificate(IMPLICIT_DEADLINE_MODE)
    changed_c = (
        replace(_skeleton()[0], wcet=4), *_skeleton()[1:],
    )
    changed_t = (
        replace(_skeleton()[0], period=12), *_skeleton()[1:],
    )
    changed_priority = (
        replace(_skeleton()[1], priority_rank=0),
        replace(_skeleton()[0], priority_rank=1),
        _skeleton()[2],
    )
    for changed in (changed_c, changed_t, changed_priority):
        observed = _certificate(IMPLICIT_DEADLINE_MODE, skeleton=changed)
        assert observed.taskset_skeleton_id != baseline.taskset_skeleton_id
        assert observed.taskset_hash != baseline.taskset_hash
        assert observed.taskset_id != baseline.taskset_id


def test_non_taskset_axes_are_absent_from_all_taskset_identity_material():
    certificate = _certificate()
    encoded = certificate.canonical_bytes().decode("utf-8")
    forbidden = (
        "E0", "service_curve", "rta_method", "release_mode",
        "release_offset", "battery_capacity", "worker_count", "timeout",
        "plotting", "simulation_horizon",
    )
    assert all(value not in encoded for value in forbidden)


def test_same_content_different_replicate_preserves_content_hash_not_provenance_ids():
    first = _certificate(IMPLICIT_DEADLINE_MODE, request=_request(7))
    second = _certificate(IMPLICIT_DEADLINE_MODE, request=_request(8))
    assert first.generation_request_id != second.generation_request_id
    assert first.taskset_skeleton_id != second.taskset_skeleton_id
    assert first.taskset_hash == second.taskset_hash
    assert first.taskset_id != second.taskset_id


def test_power_vector_hash_is_exact_existing_identity_thin_wrapper():
    certificate = _certificate()
    expected = domain_hash(
        POWER_VECTOR_DOMAIN,
        [
            {"task_id": task.task_id, "P": fraction_text(task.actual_power)}
            for task in certificate.tasks
        ],
    )
    assert power_vector_hash(certificate.tasks) == expected
    assert certificate.power_vector_hash == expected


@pytest.mark.parametrize(
    "mutation",
    [
        "skeleton_c", "task_c", "task_t", "task_d", "task_p",
        "task_order", "priority", "generation_request", "generation_id",
        "skeleton_id", "taskset_hash", "taskset_id", "deadline_mode",
        "deadline_fraction", "power_identity", "power_vector_hash",
    ],
)
def test_serialized_tampering_fails_closed(mutation):
    certificate = _certificate(
        FIXED_SLACK_FRACTION_VARIANT, fixed=Fraction(1, 2)
    )
    material = deepcopy(certificate.material())
    if mutation == "skeleton_c":
        material["skeleton_tasks"][0]["wcet"] += 1
    elif mutation == "task_c":
        material["tasks"][0]["wcet"] += 1
    elif mutation == "task_t":
        material["tasks"][0]["period"] += 1
    elif mutation == "task_d":
        material["tasks"][0]["relative_deadline"] += 1
    elif mutation == "task_p":
        material["tasks"][0]["actual_power"] = {
            "numerator": 9, "denominator": 10,
        }
    elif mutation == "task_order":
        material["tasks"][0], material["tasks"][1] = (
            material["tasks"][1], material["tasks"][0]
        )
    elif mutation == "priority":
        material["tasks"][1]["priority_rank"] = 0
    elif mutation == "generation_request":
        material["generation_request"]["formal_master_seed"] += 1
    elif mutation == "generation_id":
        material["generation_request_id"] = "0" * 64
    elif mutation == "skeleton_id":
        material["taskset_skeleton_id"] = "0" * 64
    elif mutation == "taskset_hash":
        material["taskset_hash"] = "0" * 64
    elif mutation == "taskset_id":
        material["taskset_id"] = "0" * 64
    elif mutation == "deadline_mode":
        material["tasks"][0]["deadline_generation_mode"] = "implicit"
    elif mutation == "deadline_fraction":
        material["deadline_variant"]["slack_fraction"] = {
            "numerator": 3, "denominator": 4,
        }
    elif mutation == "power_identity":
        material["generation_request"]["power_generation"][
            "contract_identity"
        ] = "3" * 64
    else:
        material["power_vector_hash"] = "0" * 64
    with pytest.raises(TasksetIdentityError):
        TasksetIdentityCertificate.from_material(material)


def test_deadline_tamper_is_rejected_even_after_rehashing_content_and_taskset_id():
    certificate = _certificate(
        FIXED_SLACK_FRACTION_VARIANT, fixed=Fraction(1, 2)
    )
    tasks = list(certificate.tasks)
    tasks[0] = replace(tasks[0], relative_deadline=tasks[0].relative_deadline + 1)
    task_tuple = tuple(tasks)
    content_hash = compute_taskset_hash(certificate.processors, task_tuple)
    identity = compute_taskset_id(
        certificate.taskset_skeleton_id,
        content_hash,
        certificate.deadline_variant,
        certificate.power_variant,
        certificate.skeleton_tasks,
    )
    material = certificate.material()
    material["tasks"] = [task.material() for task in task_tuple]
    material["taskset_hash"] = content_hash
    material["taskset_id"] = identity
    with pytest.raises(TasksetIdentityError, match="deadline variant/D mismatch"):
        TasksetIdentityCertificate.from_material(material)


def test_power_tamper_is_rejected_even_after_rehashing_all_content_identities():
    certificate = _certificate()
    tasks = list(certificate.tasks)
    tasks[0] = replace(tasks[0], actual_power=Fraction(99, 100))
    task_tuple = tuple(tasks)
    content_hash = compute_taskset_hash(certificate.processors, task_tuple)
    identity = compute_taskset_id(
        certificate.taskset_skeleton_id,
        content_hash,
        certificate.deadline_variant,
        certificate.power_variant,
        certificate.skeleton_tasks,
    )
    material = certificate.material()
    material["tasks"] = [task.material() for task in task_tuple]
    material["power_vector_hash"] = power_vector_hash(task_tuple)
    material["taskset_hash"] = content_hash
    material["taskset_id"] = identity
    with pytest.raises(TasksetIdentityError, match="power variant mismatch"):
        TasksetIdentityCertificate.from_material(material)


def test_uniform_draw_tamper_is_rejected_even_with_self_consistent_d_and_hashes():
    certificate = _certificate()
    draws = list(certificate.deadline_variant.lambda_numerators)
    draws[0] = 0 if draws[0] != 0 else UINT64_MAX
    variant = DeadlineVariant(CONSTRAINED_UNIFORM_SLACK_MODE, tuple(draws))
    tasks = list(certificate.tasks)
    skeleton = certificate.skeleton_tasks[0]
    tasks[0] = replace(
        tasks[0],
        relative_deadline=deadline_from_slack_fraction(
            skeleton.wcet, skeleton.period, draws[0], UINT64_MAX
        ),
    )
    task_tuple = tuple(tasks)
    content_hash = compute_taskset_hash(certificate.processors, task_tuple)
    identity = compute_taskset_id(
        certificate.taskset_skeleton_id,
        content_hash,
        variant,
        certificate.power_variant,
        certificate.skeleton_tasks,
    )
    material = certificate.material()
    material["deadline_variant"] = variant.material(certificate.skeleton_tasks)
    material["tasks"] = [task.material() for task in task_tuple]
    material["taskset_hash"] = content_hash
    material["taskset_id"] = identity
    with pytest.raises(TasksetIdentityError, match="draw derivation mismatch"):
        TasksetIdentityCertificate.from_material(material)


@pytest.mark.parametrize(
    "value",
    [
        {"numerator": 1, "denominator": 0},
        {"numerator": 1, "denominator": -2},
        {"numerator": 2, "denominator": 4},
        {"numerator": True, "denominator": 1},
    ],
)
def test_noncanonical_rational_material_is_rejected(value):
    with pytest.raises(TasksetIdentityError):
        fraction_from_canonical_material(value, "ratio")


@pytest.mark.parametrize("value", [0.5, float("nan"), float("inf"), None, {1, 2}])
def test_float_nonfinite_null_and_set_cannot_enter_identity_material(value):
    with pytest.raises(TasksetIdentityError):
        canonical_identity_bytes({"bad": value})


@pytest.mark.parametrize(
    "call",
    [
        lambda: deadline_from_slack_fraction(True, 10, 1, 2),
        lambda: deadline_from_slack_fraction(1, True, 1, 2),
        lambda: deadline_from_slack_fraction(1, 10, True, 2),
        lambda: deadline_from_slack_fraction(1, 10, 1, True),
        lambda: deadline_from_slack_fraction(1, 10, 1, 0),
        lambda: deadline_from_slack_fraction(1, 10, 3, 2),
        lambda: SkeletonTask("a", True, 1, 2, Fraction(1)),
        lambda: SkeletonTask("a", 0, 1, 2, 0.5),
        lambda: replace(_request(), processors=True),
    ],
)
def test_bool_float_and_illegal_integer_inputs_fail_closed(call):
    with pytest.raises(TasksetIdentityError):
        call()


def test_duplicate_ids_duplicate_ranks_and_noncanonical_order_are_rejected():
    duplicate_ids = (
        _skeleton()[0], replace(_skeleton()[1], task_id="tau-a"), _skeleton()[2]
    )
    duplicate_ranks = (
        _skeleton()[0], replace(_skeleton()[1], priority_rank=0), _skeleton()[2]
    )
    bad_order = (_skeleton()[1], _skeleton()[0], _skeleton()[2])
    for tasks in (duplicate_ids, duplicate_ranks, bad_order):
        with pytest.raises(TasksetIdentityError):
            _certificate(skeleton=tasks)


def test_noncanonical_sha_and_noncanonical_json_are_rejected():
    material = _certificate().material()
    material["taskset_hash"] = material["taskset_hash"].upper()
    with pytest.raises(TasksetIdentityError, match="lowercase SHA-256"):
        TasksetIdentityCertificate.from_material(material)
    encoded = _certificate().canonical_bytes()
    with pytest.raises(TasksetIdentityError, match="not canonical"):
        TasksetIdentityCertificate.from_canonical_bytes(b" " + encoded)


def test_certificate_task_count_bool_and_empty_public_vectors_are_rejected():
    material = _certificate().material()
    material["task_count"] = True
    with pytest.raises(TasksetIdentityError, match="plain integer"):
        TasksetIdentityCertificate.from_material(material)
    with pytest.raises(TasksetIdentityError, match="must not be empty"):
        compute_taskset_hash(4, ())
    with pytest.raises(TasksetIdentityError, match="must not be empty"):
        power_vector_hash(())


def test_legacy_formal_dry_run_contract_and_configuration_hashes_are_unchanged():
    core1 = load_config(ROOT / "configs/v9_3_core1_formal.yaml")
    core2 = load_config(ROOT / "configs/v9_3_core2_formal.yaml")
    assert config_hash(core1) == (
        "e0fe1259d2f23a9883b6f8635bed12e2b13211bd91f670e562bb573cd1bbd183"
    )
    assert config_hash(core2) == (
        "5e3c7333ba619f31b074b9fd5df495993c436900d8ca201d5cd0c272bbd30b6e"
    )
    assert tuple(item.name for item in production_runner.VARIANT_ORDER) == (
        "CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC",
    )
    descriptions = (ExecutionEngine(core1).describe(), ExecutionEngine(core2).describe())
    assert [item["cell_count"] for item in descriptions] == [24, 24]
    assert [item["request_count"] for item in descriptions] == [9600, 24000]
    assert [item["unique_taskset_count"] for item in descriptions] == [1600, 1600]
    assert len(expand_cells(core1)) == len(expand_cells(core2)) == 24
