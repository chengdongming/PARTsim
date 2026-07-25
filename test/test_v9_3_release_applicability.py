from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path

import pytest

import asap_block_rta_v9_3 as rta_core
import asap_block_rta_v9_3_methods as rta_methods
import asap_block_rta_v9_3_taskset as rta_taskset
from experiments.v9_3 import exact_energy
from experiments.v9_3.constrained_taskset_identity import (
    CONSTRAINED_UNIFORM_SLACK_MODE,
    GenerationRequest,
    IMPLICIT_DEADLINE_MODE,
    SkeletonTask,
    build_taskset_identity_certificate,
)
from experiments.v9_3.release_applicability import (
    ASYNC_HASH_PHASE_V1,
    E0Evaluation,
    E0_CONDITION_NOT_SATISFIED,
    E0_CONDITION_SATISFIED,
    FINITE_BATTERY_EMPIRICAL,
    RELEASE_HORIZON,
    RELEASE_SNAPSHOT_STAGE,
    RTA_FAIL,
    RTA_PASS,
    SIM_DEADLINE_MISS,
    SIM_NO_DEADLINE_MISS,
    SIMULATOR_TRACE_CONTRACT_VERSION,
    SYNC_V1,
    THEOREM_ALIGNED,
    ReleaseApplicabilityError,
    ReleaseObservationWindow,
    ReleaseProjection,
    ReleaseTraceAudit,
    apply_release_projection,
    assess_applicability,
    build_no_overflow_evidence,
    build_release_projection,
    evaluate_e0_condition,
    evaluate_e0_grid,
    parse_release_trace,
    simulation_applicability_identity,
    validate_simulation_evidence,
)
from experiments.v9_3.simulation_engine import (
    SimulationConfigurationError,
    _render_taskset_yaml,
    shared_e0_simulation_identity,
    simulation_identity,
)
from experiments.v9_3.task_identity import runtime_task_name_for_source_id


def _request() -> GenerationRequest:
    return GenerationRequest(
        formal_master_seed=930700,
        formal_generation_id="3" * 64,
        processors=4,
        task_count=3,
        target_normalized_utilization=Fraction(1, 2),
        replicate_index=7,
        period_min=40,
        period_max=200,
        utilization_allocation_mode="uunifast_discard_v1",
        min_task_utilization=Fraction(1, 100),
        max_task_utilization=Fraction(4, 5),
        utilization_tolerance=Fraction(1, 100),
        wcet_rounding_mode="compensated",
        generator_version="global_task_generator_frozen_v1",
        power_generation_mode="generator_default_heterogeneous",
        power_generation_contract_identity="1" * 64,
        workload_candidate_identity="2" * 64,
        priority_policy="RM",
        dag_generation_mode="disabled",
        energy_aware_generation=False,
    )


def _skeleton() -> tuple[SkeletonTask, ...]:
    return (
        SkeletonTask("tau-a", 0, 3, 11, Fraction(1, 3)),
        SkeletonTask("tau-b", 1, 5, 13, Fraction(2, 5)),
        SkeletonTask("tau-c", 2, 7, 17, Fraction(3, 7)),
    )


def _certificate(deadline_mode=CONSTRAINED_UNIFORM_SLACK_MODE):
    return build_taskset_identity_certificate(
        _request(), _skeleton(), deadline_mode=deadline_mode
    )


def _base_payload(certificate):
    return tuple(
        {
            "task_id": task.task_id,
            "priority_rank": task.priority_rank,
            "C": task.wcet,
            "D": task.relative_deadline,
            "T": task.period,
            "P": str(task.actual_power),
            "workload": "control",
        }
        for task in certificate.tasks
    )


def _process_projection(mode: str) -> bytes:
    return build_release_projection(
        _certificate(), release_mode=mode
    ).canonical_bytes()


SERVICE_ID = "d" * 64
INITIAL_BATTERY = Fraction(1)
BATTERY_CAPACITY = Fraction(10)


def _trace_document(
    certificate,
    window,
    projection,
    *,
    track=THEOREM_ALIGNED,
    scheduler="gpfp_asap_block",
    deadline_miss=False,
):
    payload = apply_release_projection(
        certificate, projection, _base_payload(certificate)
    )
    expected = sorted(
        (
            release,
            row["priority_rank"],
            row["task_id"],
        )
        for row in payload
        for release in range(
            row["arrival_offset"], RELEASE_HORIZON, row["T"]
        )
    )
    releases = {}
    for row in expected:
        releases.setdefault(row[0], []).append(row)
    events = []
    for release, rows in sorted(releases.items()):
        snapshot_energy = 50 if release == 0 else 1000
        for _release, _rank, task_id in rows:
            events.append({
                "time": str(release),
                "event_type": "arrival",
                "task_name": runtime_task_name_for_source_id(task_id),
                "arrival_time": str(release),
                # Deliberately not the E0 evidence source.
                "current_energy_mJ": -999,
            })
        for _release, _rank, task_id in rows:
            events.append({
                "time": str(release),
                "event_type": "release_energy_snapshot",
                "task_name": runtime_task_name_for_source_id(task_id),
                "arrival_time": str(release),
                "available_energy_mJ": snapshot_energy,
                "sampling_stage": RELEASE_SNAPSHOT_STAGE,
                "scheduler": scheduler,
                "trace_contract_version": (
                    SIMULATOR_TRACE_CONTRACT_VERSION
                ),
            })
    if deadline_miss:
        task = payload[0]
        release = task["arrival_offset"]
        deadline = release + task["D"]
        events.append({
            "time": str(deadline),
            "event_type": "dline_miss",
            "task_name": runtime_task_name_for_source_id(task["task_id"]),
            "job_id": (
                f"{runtime_task_name_for_source_id(task['task_id'])}@"
                f"{release}"
            ),
            "arrival_time": str(release),
            "deadline": str(deadline),
            "remaining_execution_ms": 1,
        })
    simulation_id = simulation_applicability_identity(
        taskset_id=certificate.taskset_id,
        release_projection_id=projection.release_projection_id,
        scheduler=scheduler,
        service_identity=SERVICE_ID,
        initial_battery=INITIAL_BATTERY,
        battery_capacity=BATTERY_CAPACITY,
        window=window,
        applicability_track=track,
    )
    return payload, {
        "events": events,
        "trace_schema_version": 2,
        "run_id": simulation_id,
        "taskset_semantic_hash": certificate.taskset_hash,
        "configured_scheduler": scheduler,
        "expected_simulation_horizon_ms": window.observation_horizon,
        "observed_simulation_end_ms": window.observation_horizon,
        "simulation_completed": True,
        "simulator_trace_contract_version": (
            SIMULATOR_TRACE_CONTRACT_VERSION
        ),
        "release_horizon_ms": window.release_horizon,
        "observation_horizon_ms": window.observation_horizon,
        "release_cutoff_enabled": True,
        "observation_horizon_reached": True,
        "simulation_completion_reason": "reached_horizon",
    }


def _write_trace(path: Path, value) -> Path:
    path.write_text(
        json.dumps(value, separators=(",", ":")), encoding="utf-8"
    )
    return path


def _parse_synthetic_trace(
    tmp_path,
    *,
    track=THEOREM_ALIGNED,
    scheduler="gpfp_asap_block",
    deadline_miss=False,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    certificate = _certificate()
    projection = build_release_projection(
        certificate, release_mode=SYNC_V1
    )
    window = ReleaseObservationWindow.for_certificate(certificate)
    payload, document = _trace_document(
        certificate,
        window,
        projection,
        track=track,
        scheduler=scheduler,
        deadline_miss=deadline_miss,
    )
    audit = parse_release_trace(
        _write_trace(tmp_path / "trace.json", document),
        payload,
        expected_simulation_id=document["run_id"],
        expected_taskset_hash=certificate.taskset_hash,
        expected_certificate=certificate,
        expected_projection=projection,
        window=window,
        expected_scheduler=scheduler,
    )
    return certificate, projection, window, payload, document, audit


def _small_certificate():
    request = replace(
        _request(),
        replicate_index=8,
        period_max=20_000,
    )
    skeleton = (
        SkeletonTask("tau-a", 0, 3, 10_000, Fraction(1, 3)),
        SkeletonTask("tau-b", 1, 5, 10_000, Fraction(2, 5)),
        SkeletonTask("tau-c", 2, 7, 15_000, Fraction(3, 7)),
    )
    return build_taskset_identity_certificate(
        request,
        skeleton,
        deadline_mode=CONSTRAINED_UNIFORM_SLACK_MODE,
    )


def _trace_fixture(
    tmp_path,
    *,
    certificate=None,
    release_mode=SYNC_V1,
    track=THEOREM_ALIGNED,
    scheduler="gpfp_asap_block",
    deadline_miss=False,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    certificate = certificate or _small_certificate()
    projection = build_release_projection(
        certificate, release_mode=release_mode
    )
    window = ReleaseObservationWindow.for_certificate(certificate)
    payload, document = _trace_document(
        certificate,
        window,
        projection,
        track=track,
        scheduler=scheduler,
        deadline_miss=deadline_miss,
    )
    return certificate, projection, window, payload, document


def _parse_fixture(
    tmp_path,
    *,
    certificate=None,
    release_mode=SYNC_V1,
    track=THEOREM_ALIGNED,
    scheduler="gpfp_asap_block",
    deadline_miss=False,
):
    values = _trace_fixture(
        tmp_path,
        certificate=certificate,
        release_mode=release_mode,
        track=track,
        scheduler=scheduler,
        deadline_miss=deadline_miss,
    )
    cert, projection, window, payload, document = values
    audit = parse_release_trace(
        _write_trace(tmp_path / "trace.json", document),
        payload,
        expected_simulation_id=document["run_id"],
        expected_taskset_hash=cert.taskset_hash,
        expected_certificate=cert,
        expected_projection=projection,
        window=window,
        expected_scheduler=scheduler,
    )
    return (*values, audit)


def test_sync_offsets_are_zero_and_async_is_deterministic_and_in_range():
    certificate = _certificate()
    sync = build_release_projection(certificate, release_mode=SYNC_V1)
    first = build_release_projection(
        certificate, release_mode=ASYNC_HASH_PHASE_V1
    )
    second = build_release_projection(
        certificate, release_mode=ASYNC_HASH_PHASE_V1
    )
    assert [row.arrival_offset for row in sync.offsets] == [0, 0, 0]
    assert first.canonical_bytes() == second.canonical_bytes()
    assert all(
        0 <= row.arrival_offset < row.period for row in first.offsets
    )
    assert ReleaseProjection.from_canonical_bytes(
        first.canonical_bytes()
    ) == first


def test_deadline_variants_share_vector_but_have_distinct_projection_ids():
    implicit = _certificate(IMPLICIT_DEADLINE_MODE)
    constrained = _certificate(CONSTRAINED_UNIFORM_SLACK_MODE)
    first = build_release_projection(
        implicit, release_mode=ASYNC_HASH_PHASE_V1
    )
    second = build_release_projection(
        constrained, release_mode=ASYNC_HASH_PHASE_V1
    )
    assert implicit.taskset_skeleton_id == constrained.taskset_skeleton_id
    assert implicit.taskset_id != constrained.taskset_id
    assert first.offsets == second.offsets
    assert first.release_vector_hash == second.release_vector_hash
    assert first.release_projection_id != second.release_projection_id


def test_unrelated_rta_and_simulation_axes_cannot_change_offsets():
    certificate = _certificate()
    expected = build_release_projection(
        certificate, release_mode=ASYNC_HASH_PHASE_V1
    ).release_vector_hash
    irrelevant_axes = (
        {"E0": "0", "method": "CW", "service": "raw", "capacity": "1"},
        {"E0": "1", "method": "SEQ", "service": "scaled", "capacity": "100"},
    )
    assert all(
        build_release_projection(
            certificate, release_mode=ASYNC_HASH_PHASE_V1
        ).release_vector_hash
        == expected
        for _axis in irrelevant_axes
    )


def test_projection_changes_only_payload_release_fields_and_yaml_is_consistent():
    certificate = _certificate()
    before = certificate.canonical_bytes()
    projection = build_release_projection(
        certificate, release_mode=ASYNC_HASH_PHASE_V1
    )
    source = _base_payload(certificate)
    projected = apply_release_projection(
        certificate, projection, source
    )
    assert certificate.canonical_bytes() == before
    assert all("arrival_offset" not in row and "ph" not in row for row in source)
    assert all(
        row["arrival_offset"] == row["ph"] == offset.arrival_offset
        for row, offset in zip(projected, projection.offsets)
    )
    rendered = _render_taskset_yaml(
        projected, release_horizon=RELEASE_HORIZON
    )
    assert rendered.startswith("release_horizon: 30000\ntaskset:\n")
    for offset in projection.offsets:
        assert f"    ph: {offset.arrival_offset}\n" in rendered
        assert f"arrival_offset={offset.arrival_offset}," in rendered
    malformed = [dict(row) for row in projected]
    malformed[0]["ph"] += 1
    with pytest.raises(
        SimulationConfigurationError, match="projection mismatch"
    ):
        _render_taskset_yaml(malformed, release_horizon=RELEASE_HORIZON)


def test_release_and_observation_horizons_cover_all_pre_cutoff_deadlines():
    certificate = _certificate()
    window = ReleaseObservationWindow.for_certificate(certificate)
    maximum = max(task.relative_deadline for task in certificate.tasks)
    assert window.release_horizon == 30_000
    assert window.observation_horizon == 30_000 + maximum
    assert 29_999 + maximum <= window.observation_horizon


def test_trace_energy_uses_exact_binary64_materialization_and_e0_grid(tmp_path):
    *_unused, audit = _parse_synthetic_trace(tmp_path)
    assert audit.minimum_release_energy_exact == Fraction(1, 20)
    evaluations = evaluate_e0_grid(
        audit, (Fraction(0), Fraction(1, 20), Fraction(1))
    )
    above, equal, below = evaluations
    assert above.e0_condition_satisfied
    assert above.status == E0_CONDITION_SATISFIED
    assert equal.e0_condition_satisfied
    assert equal.status == E0_CONDITION_SATISFIED
    assert not below.e0_condition_satisfied
    assert below.status == E0_CONDITION_NOT_SATISFIED
    assert below.first_violating_task_id == "tau-a"
    assert below.first_violating_release == 0
    assert all(row.evaluated_release_count == 6801 for row in evaluations)


def test_applicability_tracks_and_e0_failure_precedence(tmp_path):
    def evidence(audit, track, *, overflow=True):
        simulation = validate_simulation_evidence(
            audit,
            service_identity=SERVICE_ID,
            initial_battery=INITIAL_BATTERY,
            battery_capacity=BATTERY_CAPACITY,
            applicability_track=track,
        )
        no_overflow = build_no_overflow_evidence(
            initial_battery=INITIAL_BATTERY,
            battery_capacity=BATTERY_CAPACITY,
            offered_harvest=Fraction(1 if overflow else 10),
            required_margin=Fraction(0),
            service_identity=SERVICE_ID,
            observation_horizon=audit.observation_horizon,
        )
        return simulation, no_overflow

    def classify(
        audit,
        track,
        requested_e0,
        *,
        overflow=True,
        rta_outcome=RTA_PASS,
    ):
        simulation, no_overflow = evidence(
            audit, track, overflow=overflow
        )
        evaluation = evaluate_e0_condition(audit, requested_e0)
        return assess_applicability(
            requested_track=track,
            release_trace_audit=audit,
            requested_e0=requested_e0,
            e0_evaluation=evaluation,
            no_overflow_evidence=no_overflow,
            simulation_evidence=simulation,
            expected_taskset_id=audit.taskset_id,
            expected_taskset_hash=audit.taskset_hash,
            expected_release_projection_id=(
                audit.release_projection_id
            ),
            expected_simulation_id=audit.simulation_id,
            rta_outcome=rta_outcome,
            simulation_outcome=audit.simulation_outcome,
        )

    *_unused, theorem_audit = _parse_synthetic_trace(tmp_path / "theorem")
    theorem = classify(
        theorem_audit, THEOREM_ALIGNED, Fraction(1, 20)
    )
    assert theorem.theorem_comparison_eligible
    assert not theorem.theorem_applicable_soundness_counterexample

    *_unused, miss_audit = _parse_synthetic_trace(
        tmp_path / "miss", deadline_miss=True
    )
    counterexample = classify(
        miss_audit, THEOREM_ALIGNED, Fraction(1, 20)
    )
    assert counterexample.theorem_applicable_soundness_counterexample
    assert not counterexample.empirical_difference
    rta_failure = classify(
        miss_audit,
        THEOREM_ALIGNED,
        Fraction(1, 20),
        rta_outcome=RTA_FAIL,
    )
    assert not rta_failure.theorem_applicable_soundness_counterexample

    *_unused, empirical_audit = _parse_synthetic_trace(
        tmp_path / "empirical",
        track=FINITE_BATTERY_EMPIRICAL,
        deadline_miss=True,
    )
    empirical = classify(
        empirical_audit,
        FINITE_BATTERY_EMPIRICAL,
        Fraction(1, 20),
        overflow=False,
    )
    assert empirical.category == FINITE_BATTERY_EMPIRICAL
    assert not empirical.theorem_applicable_soundness_counterexample
    assert empirical.empirical_difference

    inapplicable = classify(
        theorem_audit, THEOREM_ALIGNED, Fraction(1)
    )
    assert inapplicable.category == E0_CONDITION_NOT_SATISFIED
    assert not inapplicable.theorem_comparison_eligible
    assert not inapplicable.theorem_applicable_soundness_counterexample

    with pytest.raises(
        ReleaseApplicabilityError, match="no_overflow"
    ):
        classify(
            theorem_audit,
            THEOREM_ALIGNED,
            Fraction(1, 20),
            overflow=False,
        )


@pytest.mark.parametrize(
    "mutation,error",
    [
        (
            lambda value: value.update(release_horizon_ms=29_999),
            "release horizon mismatch",
        ),
        (
            lambda value: value.update(release_cutoff_enabled=False),
            "release cutoff is not enabled",
        ),
        (
            lambda value: value.update(run_id="b" * 64),
            "simulation identity mismatch",
        ),
        (
            lambda value: value["events"].append(
                {
                    "time": "30000",
                    "event_type": "arrival",
                    "task_name": runtime_task_name_for_source_id("tau-a"),
                    "arrival_time": "30000",
                    "current_energy_mJ": 50,
                }
            ),
            "release at/after release horizon",
        ),
    ],
)
def test_trace_horizon_cutoff_and_identity_fail_closed(
    tmp_path, mutation, error
):
    certificate = _certificate()
    projection = build_release_projection(
        certificate, release_mode=SYNC_V1
    )
    window = ReleaseObservationWindow.for_certificate(certificate)
    payload, document = _trace_document(
        certificate, window, projection
    )
    expected_simulation_id = document["run_id"]
    mutation(document)
    with pytest.raises(ReleaseApplicabilityError, match=error):
        parse_release_trace(
            _write_trace(tmp_path / "malformed.json", document),
            payload,
            expected_simulation_id=expected_simulation_id,
            expected_taskset_hash=certificate.taskset_hash,
            expected_certificate=certificate,
            expected_projection=projection,
            window=window,
        )


def test_projection_is_deterministic_with_two_and_four_processes():
    expected = _process_projection(ASYNC_HASH_PHASE_V1)
    for workers in (2, 4):
        with ProcessPoolExecutor(max_workers=workers) as executor:
            observed = tuple(
                executor.map(
                    _process_projection,
                    (ASYNC_HASH_PHASE_V1,) * 8,
                )
            )
        assert observed == (expected,) * 8


def test_simulation_identity_binds_release_capacity_and_not_method_or_e0():
    certificate = _certificate()
    window = ReleaseObservationWindow.for_certificate(certificate)
    sync = build_release_projection(certificate, release_mode=SYNC_V1)
    async_projection = build_release_projection(
        certificate, release_mode=ASYNC_HASH_PHASE_V1
    )
    common = {
        "taskset_id": certificate.taskset_id,
        "scheduler": "gpfp_asap_block",
        "service_identity": "d" * 64,
        "initial_battery": Fraction(1),
        "window": window,
        "applicability_track": THEOREM_ALIGNED,
    }
    first = simulation_applicability_identity(
        **common,
        release_projection_id=sync.release_projection_id,
        battery_capacity=Fraction(10),
    )
    assert first == simulation_applicability_identity(
        **common,
        release_projection_id=sync.release_projection_id,
        battery_capacity=Fraction(10),
    )
    assert first != simulation_applicability_identity(
        **common,
        release_projection_id=async_projection.release_projection_id,
        battery_capacity=Fraction(10),
    )
    assert first != simulation_applicability_identity(
        **common,
        release_projection_id=sync.release_projection_id,
        battery_capacity=Fraction(20),
    )


def test_sync_and_async_reuse_identical_rta_input_and_method_result():
    certificate = _certificate()
    build_release_projection(certificate, release_mode=SYNC_V1)
    build_release_projection(
        certificate, release_mode=ASYNC_HASH_PHASE_V1
    )
    tasks = tuple(
        rta_core.V93Task(
            task.task_id,
            task.wcet,
            task.relative_deadline,
            task.period,
            task.actual_power,
        )
        for task in certificate.tasks
    )
    e0 = Fraction(1000)
    beta = tuple(
        Fraction(0)
        for _ in range(max(task.deadline for task in tasks))
    )
    exact_identity = exact_energy.exact_input_identity(
        task_powers=((task.name, task.power) for task in tasks),
        e0=e0,
        service_prefix=beta,
    )
    context = rta_taskset.DependencyContext(
        taskset_identity=certificate.taskset_id,
        task_definitions_identity=certificate.taskset_hash,
        priority_order_identity=certificate.taskset_skeleton_id,
        e0_canonical_identity="e0-shared",
        service_curve_identity="service-shared",
        power_vector_identity=certificate.power_vector_hash,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=rta_taskset.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=(
            rta_taskset.FIXED_CARRY_IN_INTERFACE_SHA256
        ),
        formal_contract_identity="formal-shared",
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity=exact_identity,
        float_decision_path=False,
    )
    analysis_input = rta_taskset.TasksetAnalysisInput(
        tasks=tasks,
        processors=certificate.processors,
        e0=e0,
        beta=beta,
        dependency_context=context,
    )
    results = tuple(
        rta_taskset.analyze_method_taskset_v9_3(
            analysis_id="release-independent-rta",
            method_spec=rta_methods.V93MethodId.CW_THETA_CW,
            analysis_input=analysis_input,
        )
        for _projection in (SYNC_V1, ASYNC_HASH_PHASE_V1)
    )
    assert (
        results[0].method_id,
        results[0].solver_status,
        results[0].analysis_certification_status,
        results[0].taskset_proven,
        results[0].first_failed_task,
        results[0].failure_reason,
        results[0].carry_trace,
    ) == (
        results[1].method_id,
        results[1].solver_status,
        results[1].analysis_certification_status,
        results[1].taskset_proven,
        results[1].first_failed_task,
        results[1].failure_reason,
        results[1].carry_trace,
    )
    assert tuple(
        (
            row.task_id,
            row.solver_status,
            row.certification_status,
            row.candidate_response_time,
            row.carry_in_values_used,
            row.closing_w,
            row.witness_h,
            row.failure_reason,
        )
        for row in results[0].task_results
    ) == tuple(
        (
            row.task_id,
            row.solver_status,
            row.certification_status,
            row.candidate_response_time,
            row.carry_in_values_used,
            row.closing_w,
            row.witness_h,
            row.failure_reason,
        )
        for row in results[1].task_results
    )
    assert results[0].exact_input_identity == exact_identity


def test_legacy_simulation_identity_and_yaml_rendering_are_unchanged():
    config = {"horizon": 10, "trace_mode": "semantic"}
    assert simulation_identity(
        "cell", "a" * 64, Fraction(1, 20), config
    ) == "0186f316c38dfb1a80528f8e2f26f48ceb5b5e0f9fc21f499db284fb77e61a49"
    assert shared_e0_simulation_identity(
        "generation", "a" * 64, config
    ) == "0ad81710aa5ac592d5b3a7ab769552dae5fcc40bf751fb19f09573e662f0d06c"
    payload = ({
        "task_id": "0",
        "priority_rank": 0,
        "C": 2,
        "D": 5,
        "T": 10,
        "P": "1",
        "workload": "control",
        "arrival_offset": 3,
    },)
    assert _render_taskset_yaml(payload) == (
        "taskset:\n"
        "  - name: v93_task_0\n"
        "    iat: 10\n"
        "    runtime: 2\n"
        "    startcpu: 0\n"
        "    deadline: 5\n"
        "    ph: 3\n"
        "    params: \"period=10,wcet=2,arrival_offset=3,"
        "workload=control\"\n"
        "    code:\n"
        "      - fixed(2, control)\n"
    )


def test_projection_and_trace_material_reject_tampering(tmp_path):
    certificate = _certificate()
    projection = build_release_projection(
        certificate, release_mode=ASYNC_HASH_PHASE_V1
    )
    material = deepcopy(projection.material())
    material["offsets"][0]["arrival_offset"] = (
        material["offsets"][0]["arrival_offset"] + 1
    ) % material["offsets"][0]["period"]
    with pytest.raises(
        ReleaseApplicabilityError, match="derivation mismatch"
    ):
        ReleaseProjection.from_material(material)
    (
        _certificate_value,
        _projection_value,
        window,
        payload,
        document,
        _audit,
    ) = _parse_synthetic_trace(tmp_path)
    document["simulator_trace_contract_version"] = "wrong"
    with pytest.raises(
        ReleaseApplicabilityError, match="contract version mismatch"
    ):
        parse_release_trace(
            _write_trace(tmp_path / "wrong-contract.json", document),
            payload,
            expected_simulation_id=document["run_id"],
            expected_taskset_hash=certificate.taskset_hash,
            expected_certificate=certificate,
            expected_projection=_projection_value,
            window=window,
        )


def test_complete_expected_set_and_v2_snapshots_are_bound(tmp_path):
    *_unused, audit = _parse_fixture(tmp_path)
    assert audit.expected_release_count == 8
    assert audit.evaluated_release_count == 8
    assert len(audit.samples) == 8
    at_zero = [
        row.energy_exact for row in audit.samples if row.release == 0
    ]
    assert len(at_zero) == 3
    assert at_zero == [Fraction(1, 20)] * 3
    assert audit.minimum_release_energy_exact == Fraction(1, 20)
    assert audit.trace_contract_version == (
        "ASAP_BLOCK_V9_3_RELEASE_CUTOFF_TRACE_V2"
    )
    assert audit.expected_arrival_set_digest
    assert audit.observed_arrival_set_digest
    assert audit.samples_digest
    assert audit.release_trace_audit_id


def test_frozen_6801_release_case_rejects_one_or_two_arrivals(tmp_path):
    (
        certificate,
        projection,
        window,
        payload,
        document,
        audit,
    ) = _parse_synthetic_trace(tmp_path / "complete")
    assert audit.expected_release_count == 6801
    assert audit.evaluated_release_count == 6801

    for retained in (1, 2):
        candidate = deepcopy(document)
        arrivals = [
            event for event in candidate["events"]
            if event.get("event_type") == "arrival"
        ][:retained]
        snapshots = [
            event for event in candidate["events"]
            if event.get("event_type") == "release_energy_snapshot"
        ][:retained]
        candidate["events"] = arrivals + snapshots
        with pytest.raises(
            ReleaseApplicabilityError, match="missing arrivals"
        ):
            parse_release_trace(
                _write_trace(
                    tmp_path / f"incomplete-{retained}.json",
                    candidate,
                ),
                payload,
                expected_simulation_id=document["run_id"],
                expected_taskset_hash=certificate.taskset_hash,
                expected_certificate=certificate,
                expected_projection=projection,
                window=window,
            )


def test_arrival_and_snapshot_counterexamples_fail_closed(tmp_path):
    (
        certificate,
        projection,
        window,
        payload,
        document,
    ) = _trace_fixture(tmp_path)

    def parse_candidate(name, candidate, candidate_payload=None):
        return parse_release_trace(
            _write_trace(tmp_path / f"{name}.json", candidate),
            payload if candidate_payload is None else candidate_payload,
            expected_simulation_id=document["run_id"],
            expected_taskset_hash=certificate.taskset_hash,
            expected_certificate=certificate,
            expected_projection=projection,
            window=window,
        )

    def first_index(candidate, event_type, *, release=None):
        return next(
            index for index, event in enumerate(candidate["events"])
            if event.get("event_type") == event_type
            and (
                release is None
                or int(event.get("arrival_time")) == release
            )
        )

    candidate = deepcopy(document)
    candidate["events"].pop(first_index(candidate, "arrival"))
    with pytest.raises(ReleaseApplicabilityError, match="missing arrivals"):
        parse_candidate("missing-arrival", candidate)

    candidate = deepcopy(document)
    arrival = deepcopy(
        candidate["events"][first_index(candidate, "arrival")]
    )
    candidate["events"].append(arrival)
    with pytest.raises(ReleaseApplicabilityError, match="duplicate arrivals"):
        parse_candidate("duplicate-arrival", candidate)

    candidate = deepcopy(document)
    off_grid = deepcopy(
        candidate["events"][first_index(candidate, "arrival")]
    )
    off_grid.update(time="1", arrival_time="1")
    candidate["events"].append(off_grid)
    with pytest.raises(ReleaseApplicabilityError, match="off-grid"):
        parse_candidate("off-grid-arrival", candidate)

    candidate = deepcopy(document)
    at_cutoff = deepcopy(
        candidate["events"][first_index(candidate, "arrival")]
    )
    at_cutoff.update(time="30000", arrival_time="30000")
    candidate["events"].append(at_cutoff)
    with pytest.raises(
        ReleaseApplicabilityError, match="at/after release horizon"
    ):
        parse_candidate("at-cutoff-arrival", candidate)

    candidate = deepcopy(document)
    candidate["events"][first_index(candidate, "arrival")][
        "task_name"
    ] = "unknown-runtime-task"
    with pytest.raises(
        ReleaseApplicabilityError, match="unknown runtime task"
    ):
        parse_candidate("unknown-arrival", candidate)

    candidate = deepcopy(document)
    candidate["events"][first_index(candidate, "arrival")][
        "arrival_time"
    ] = "1"
    with pytest.raises(
        ReleaseApplicabilityError, match="time/release mismatch"
    ):
        parse_candidate("arrival-time-mismatch", candidate)

    malformed_payload = [dict(row) for row in payload]
    malformed_payload[0]["ph"] = 1
    with pytest.raises(
        ReleaseApplicabilityError, match="offset/ph mismatch"
    ):
        parse_candidate(
            "payload-ph-mismatch", deepcopy(document), malformed_payload
        )

    malformed_payload = [dict(row) for row in payload]
    malformed_payload[0]["arrival_offset"] = 1
    malformed_payload[0]["ph"] = 1
    with pytest.raises(
        ReleaseApplicabilityError, match="projection mismatch"
    ):
        parse_candidate(
            "payload-projection-mismatch",
            deepcopy(document),
            malformed_payload,
        )

    malformed_payload = [dict(row) for row in payload]
    malformed_payload[0]["C"] += 1
    with pytest.raises(
        ReleaseApplicabilityError, match="taskset certificate mismatch"
    ):
        parse_candidate(
            "payload-certificate-mismatch",
            deepcopy(document),
            malformed_payload,
        )

    candidate = deepcopy(document)
    candidate["events"].pop(
        first_index(candidate, "release_energy_snapshot")
    )
    with pytest.raises(
        ReleaseApplicabilityError, match="missing release energy snapshots"
    ):
        parse_candidate("missing-snapshot", candidate)

    candidate = deepcopy(document)
    snapshot = deepcopy(
        candidate["events"][
            first_index(candidate, "release_energy_snapshot")
        ]
    )
    candidate["events"].append(snapshot)
    with pytest.raises(
        ReleaseApplicabilityError, match="duplicate release energy"
    ):
        parse_candidate("duplicate-snapshot", candidate)

    candidate = deepcopy(document)
    snapshot = deepcopy(
        candidate["events"][
            first_index(candidate, "release_energy_snapshot")
        ]
    )
    snapshot.update(time="1", arrival_time="1")
    candidate["events"].append(snapshot)
    with pytest.raises(
        ReleaseApplicabilityError, match="snapshot without arrival"
    ):
        parse_candidate("snapshot-without-arrival", candidate)

    for field, value, error in (
        ("sampling_stage", "post_consumption", "stage mismatch"),
        ("scheduler", "gpfp_asap_sync", "scheduler mismatch"),
        ("trace_contract_version", "V1", "contract mismatch"),
    ):
        candidate = deepcopy(document)
        candidate["events"][
            first_index(candidate, "release_energy_snapshot")
        ][field] = value
        with pytest.raises(ReleaseApplicabilityError, match=error):
            parse_candidate(f"snapshot-{field}", candidate)

    candidate = deepcopy(document)
    candidate["events"][
        first_index(candidate, "release_energy_snapshot")
    ]["time"] = "1"
    with pytest.raises(
        ReleaseApplicabilityError, match="time/release mismatch"
    ):
        parse_candidate("snapshot-time", candidate)

    candidate = deepcopy(document)
    second_snapshot = [
        index for index, event in enumerate(candidate["events"])
        if event.get("event_type") == "release_energy_snapshot"
        and event.get("arrival_time") == "0"
    ][1]
    candidate["events"][second_snapshot]["available_energy_mJ"] = 51
    with pytest.raises(
        ReleaseApplicabilityError, match="same-tick"
    ):
        parse_candidate("same-tick-energy", candidate)

    candidate = deepcopy(document)
    snapshot_position = first_index(
        candidate, "release_energy_snapshot", release=0
    )
    candidate["events"].insert(snapshot_position, {
        "time": "0",
        "event_type": "scheduled",
        "task_name": runtime_task_name_for_source_id("tau-a"),
        "arrival_time": "0",
    })
    with pytest.raises(
        ReleaseApplicabilityError, match="after consumption"
    ):
        parse_candidate("snapshot-after-consumption", candidate)

    candidate = deepcopy(document)
    for event in candidate["events"]:
        if event.get("event_type") == "arrival":
            event["current_energy_mJ"] = "ignored-not-energy"
    parsed = parse_candidate("legacy-arrival-energy-ignored", candidate)
    assert parsed.minimum_release_energy_exact == Fraction(1, 20)

    candidate = deepcopy(document)
    candidate["simulator_trace_contract_version"] = (
        "ASAP_BLOCK_V9_3_RELEASE_CUTOFF_TRACE_V1"
    )
    with pytest.raises(
        ReleaseApplicabilityError, match="contract version mismatch"
    ):
        parse_candidate("v1-trace", candidate)

    candidate = deepcopy(document)
    candidate["simulation_completed"] = False
    with pytest.raises(
        ReleaseApplicabilityError, match="horizon is incomplete"
    ):
        parse_candidate("invalid-status", candidate)


def test_release_audit_and_evidence_reject_direct_tampering(tmp_path):
    *_unused, audit = _parse_fixture(tmp_path)
    mutations = (
        {"expected_release_count": 999},
        {"expected_arrival_set_digest": "0" * 64},
        {"samples": audit.samples[1:]},
        {
            "samples": (
                replace(audit.samples[0], release=1),
                *audit.samples[1:],
            )
        },
        {
            "minimum_release_energy_exact":
                audit.minimum_release_energy_exact + 1
        },
    )
    for mutation in mutations:
        with pytest.raises(
            ReleaseApplicabilityError,
            match="validated trace parsing",
        ):
            replace(audit, **mutation)

    no_overflow = build_no_overflow_evidence(
        initial_battery=INITIAL_BATTERY,
        battery_capacity=BATTERY_CAPACITY,
        offered_harvest=Fraction(1),
        required_margin=Fraction(0),
        service_identity=SERVICE_ID,
        observation_horizon=audit.observation_horizon,
    )
    with pytest.raises(
        ReleaseApplicabilityError, match="derivation mismatch"
    ):
        replace(no_overflow, valid=False)

    simulation = validate_simulation_evidence(
        audit,
        service_identity=SERVICE_ID,
        initial_battery=INITIAL_BATTERY,
        battery_capacity=BATTERY_CAPACITY,
        applicability_track=THEOREM_ALIGNED,
    )
    with pytest.raises(
        ReleaseApplicabilityError, match="validated trace"
    ):
        replace(simulation, release_projection_id="f" * 64)


def test_forged_or_cross_chain_evidence_cannot_be_theorem_aligned(
    tmp_path,
):
    (
        certificate,
        projection,
        window,
        payload,
        document,
        audit,
    ) = _parse_fixture(tmp_path / "primary")
    evaluation = evaluate_e0_condition(audit, Fraction(1, 20))
    no_overflow = build_no_overflow_evidence(
        initial_battery=INITIAL_BATTERY,
        battery_capacity=BATTERY_CAPACITY,
        offered_harvest=Fraction(1),
        required_margin=Fraction(0),
        service_identity=SERVICE_ID,
        observation_horizon=audit.observation_horizon,
    )
    simulation = validate_simulation_evidence(
        audit,
        service_identity=SERVICE_ID,
        initial_battery=INITIAL_BATTERY,
        battery_capacity=BATTERY_CAPACITY,
        applicability_track=THEOREM_ALIGNED,
    )
    base = {
        "requested_track": THEOREM_ALIGNED,
        "release_trace_audit": audit,
        "requested_e0": Fraction(1, 20),
        "e0_evaluation": evaluation,
        "no_overflow_evidence": no_overflow,
        "simulation_evidence": simulation,
        "expected_taskset_id": audit.taskset_id,
        "expected_taskset_hash": audit.taskset_hash,
        "expected_release_projection_id": (
            audit.release_projection_id
        ),
        "expected_simulation_id": audit.simulation_id,
        "rta_outcome": RTA_PASS,
        "simulation_outcome": audit.simulation_outcome,
    }
    result = assess_applicability(**base)
    assert result.category == THEOREM_ALIGNED

    with pytest.raises(TypeError):
        assess_applicability(
            requested_track=THEOREM_ALIGNED,
            e0_evaluation=evaluation,
            release_cutoff_valid=True,
            observation_horizon_complete=True,
            no_overflow_valid=True,
            identity_match=True,
            scheduler_is_target=True,
            rta_pass=True,
            simulation_deadline_miss=True,
        )

    forged = object.__new__(E0Evaluation)
    for name in evaluation.__dataclass_fields__:
        object.__setattr__(forged, name, getattr(evaluation, name))
    object.__setattr__(
        forged, "minimum_release_energy_exact", Fraction(10**9)
    )
    object.__setattr__(forged, "expected_release_count", 999)
    object.__setattr__(
        forged, "first_violating_task_id", "not-in-trace"
    )
    object.__setattr__(forged, "first_violating_release", 29_999)
    object.__setattr__(forged, "e0_condition_satisfied", False)
    object.__setattr__(
        forged, "status", E0_CONDITION_NOT_SATISFIED
    )
    forged_values = dict(base)
    forged_values["e0_evaluation"] = forged
    with pytest.raises(
        ReleaseApplicabilityError, match="E0 evaluation/release audit"
    ):
        assess_applicability(**forged_values)

    other_document = deepcopy(document)
    for event in other_document["events"]:
        if (
            event.get("event_type") == "release_energy_snapshot"
            and event.get("arrival_time") == "0"
        ):
            event["available_energy_mJ"] = 60
    other_audit = parse_release_trace(
        _write_trace(tmp_path / "other-audit.json", other_document),
        payload,
        expected_simulation_id=document["run_id"],
        expected_taskset_hash=certificate.taskset_hash,
        expected_certificate=certificate,
        expected_projection=projection,
        window=window,
    )
    other_evaluation = evaluate_e0_condition(
        other_audit, Fraction(1, 20)
    )
    cross_e0 = dict(base)
    cross_e0["e0_evaluation"] = other_evaluation
    with pytest.raises(
        ReleaseApplicabilityError, match="E0 evaluation/release audit"
    ):
        assess_applicability(**cross_e0)

    wrong_service = build_no_overflow_evidence(
        initial_battery=INITIAL_BATTERY,
        battery_capacity=BATTERY_CAPACITY,
        offered_harvest=Fraction(1),
        required_margin=Fraction(0),
        service_identity="e" * 64,
        observation_horizon=audit.observation_horizon,
    )
    cross_service = dict(base)
    cross_service["no_overflow_evidence"] = wrong_service
    with pytest.raises(
        ReleaseApplicabilityError,
        match="no-overflow/simulation evidence mismatch",
    ):
        assess_applicability(**cross_service)

    *_values, other_projection_audit = _parse_fixture(
        tmp_path / "other-projection",
        release_mode=ASYNC_HASH_PHASE_V1,
    )
    other_simulation = validate_simulation_evidence(
        other_projection_audit,
        service_identity=SERVICE_ID,
        initial_battery=INITIAL_BATTERY,
        battery_capacity=BATTERY_CAPACITY,
        applicability_track=THEOREM_ALIGNED,
    )
    cross_projection = dict(base)
    cross_projection["simulation_evidence"] = other_simulation
    with pytest.raises(
        ReleaseApplicabilityError,
        match="simulation evidence/release audit mismatch",
    ):
        assess_applicability(**cross_projection)

    identity_mismatch = dict(base)
    identity_mismatch["expected_taskset_id"] = "f" * 64
    with pytest.raises(
        ReleaseApplicabilityError,
        match="expected applicability identity mismatch",
    ):
        assess_applicability(**identity_mismatch)

    *_values, wrong_scheduler_audit = _parse_fixture(
        tmp_path / "wrong-scheduler",
        scheduler="gpfp_asap_sync",
    )
    wrong_scheduler_simulation = validate_simulation_evidence(
        wrong_scheduler_audit,
        service_identity=SERVICE_ID,
        initial_battery=INITIAL_BATTERY,
        battery_capacity=BATTERY_CAPACITY,
        applicability_track=THEOREM_ALIGNED,
    )
    wrong_scheduler_overflow = build_no_overflow_evidence(
        initial_battery=INITIAL_BATTERY,
        battery_capacity=BATTERY_CAPACITY,
        offered_harvest=Fraction(1),
        required_margin=Fraction(0),
        service_identity=SERVICE_ID,
        observation_horizon=(
            wrong_scheduler_audit.observation_horizon
        ),
    )
    wrong_scheduler_e0 = evaluate_e0_condition(
        wrong_scheduler_audit, Fraction(1, 20)
    )
    with pytest.raises(
        ReleaseApplicabilityError, match="scheduler mismatch"
    ):
        assess_applicability(
            requested_track=THEOREM_ALIGNED,
            release_trace_audit=wrong_scheduler_audit,
            requested_e0=Fraction(1, 20),
            e0_evaluation=wrong_scheduler_e0,
            no_overflow_evidence=wrong_scheduler_overflow,
            simulation_evidence=wrong_scheduler_simulation,
            expected_taskset_id=wrong_scheduler_audit.taskset_id,
            expected_taskset_hash=wrong_scheduler_audit.taskset_hash,
            expected_release_projection_id=(
                wrong_scheduler_audit.release_projection_id
            ),
            expected_simulation_id=wrong_scheduler_audit.simulation_id,
            rta_outcome=RTA_PASS,
            simulation_outcome=(
                wrong_scheduler_audit.simulation_outcome
            ),
        )
