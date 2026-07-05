import json
from pathlib import Path

import pytest
import yaml

import acceptance_ratio_test as acceptance
import asap_block_rta as v20
import asap_block_rta_ablation_variants as ablation
from scripts import run_rta_ablation


def make_task(name, period, wcet, index, deadline=None, energy=0.1):
    return v20.RTATask(
        name,
        period,
        wcet,
        period if deadline is None else deadline,
        "low",
        index,
        energy,
    )


def write_inputs(tmp_path, task_specs):
    system_path = tmp_path / "system.yml"
    system_path.write_text(
        yaml.safe_dump(
            {
                "cpu_islands": [
                    {
                        "name": "island0",
                        "numcpus": 1,
                        "base_freq": 8100,
                    }
                ],
                "energy_management": {
                    "initial_energy": 100.0,
                    "max_energy": 100.0,
                    "use_real_solar_data": False,
                    "base_harvesting_rate": 0.0,
                    "scheduler_energy_model": {
                        "base_power": 1.0,
                        "workload_coefficients": {"low": 1.0},
                        "frequency_power_ratios": {8100: 1.0},
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    tasks_path = tmp_path / "tasks.yml"
    tasks_path.write_text(
        yaml.safe_dump({"taskset": task_specs}, sort_keys=False),
        encoding="utf-8",
    )
    return system_path, tasks_path


def task_spec(name, period, wcet, deadline=None):
    deadline = period if deadline is None else deadline
    return {
        "name": name,
        "iat": period,
        "runtime": wcet,
        "deadline": deadline,
        "params": (
            "period={},wcet={},arrival_offset=0,workload=low".format(
                period, wcet
            )
        ),
        "code": ["fixed({}, low)".format(wcet)],
    }


def parser_args(variant):
    return [
        "--tasks",
        "tasks.yml",
        "--config",
        "system.yml",
        "--variant",
        variant,
        "--horizon-ms",
        "20",
    ]


def test_policy_registry_matches_safe_chain_definition():
    assert set(ablation.VARIANT_POLICIES) == {
        "baseline_safe",
        "carry_in_certified",
        "capacity_coupled",
    }

    a0 = ablation.VARIANT_POLICIES["baseline_safe"]
    assert a0.expected_rta_version == "e3-a0-baseline-safe"
    assert a0.certificate_policy == "none_deadline_workload"
    assert not a0.uses_certified_carry_in
    assert not a0.uses_processor_capacity_coupling
    assert not a0.uses_window_level_task_capacity
    assert not a0.uses_window_level_u_capacity

    a1 = ablation.VARIANT_POLICIES["carry_in_certified"]
    assert a1.expected_rta_version == "e3-a1-carry-in-certified"
    assert a1.certificate_policy == "strict_variant_specific"
    assert a1.uses_certified_carry_in
    assert not a1.uses_processor_capacity_coupling
    assert not a1.uses_window_level_task_capacity
    assert not a1.uses_window_level_u_capacity

    a2 = ablation.VARIANT_POLICIES["capacity_coupled"]
    assert a2.expected_rta_version == "e3-a2-capacity-coupled"
    assert a2.certificate_policy == "strict_variant_specific"
    assert a2.uses_certified_carry_in
    assert a2.uses_processor_capacity_coupling
    assert not a2.uses_window_level_task_capacity
    assert not a2.uses_window_level_u_capacity

    for policy in ablation.VARIANT_POLICIES.values():
        assert policy.variant_group == "safe_chain"
        assert policy.variant_safety_label == "safe_under_v20p4_assumptions"
        assert policy.proof_claim_eligible
        assert not policy.diagnostic_only
        assert not policy.uses_local_window


@pytest.mark.parametrize(
    "variant",
    ["baseline_safe", "carry_in_certified", "capacity_coupled"],
)
def test_cli_accepts_only_a0_a2_variants(variant):
    args = ablation.build_parser().parse_args(parser_args(variant))
    assert args.variant == variant


@pytest.mark.parametrize(
    "variant",
    ["unknown", "A3", "A4", "v20p4_full", "v21_experimental"],
)
def test_cli_rejects_variants_not_implemented_by_this_module(variant):
    with pytest.raises(SystemExit):
        ablation.build_parser().parse_args(parser_args(variant))


def test_official_v20p4_and_existing_endpoint_runner_are_unchanged():
    assert acceptance.RTA_VERSION == "v20.4"
    assert Path(acceptance.RTA_TOOL).name == "asap_block_rta.py"
    assert v20.RTA_VERSION == "v20.4"
    assert set(run_rta_ablation.VARIANT_REGISTRY) == {
        "baseline_safe",
        "carry_in_certified",
        "capacity_coupled",
        "v20p4_full",
        "v21_local_window_closure",
    }
    assert run_rta_ablation.VARIANT_ALIASES["v21_experimental"] == (
        "v21_local_window_closure"
    )


def test_a0_does_not_require_higher_priority_certificates():
    high = make_task("high", 5, 1, 0)
    target = make_task("target", 20, 1, 1)
    result = ablation.response_time_bound_variant(
        ablation.VARIANT_POLICIES["baseline_safe"],
        target,
        [high, target],
        M=1,
        beta=[0.0],
        E0=100.0,
        assume_no_overflow=True,
        certified_bounds={},
    )
    assert result.certificate_status == "not_required"
    assert result.proof_claim_allowed
    assert result.rta_status != "certificate_missing"


def test_a0_analyzer_does_not_create_or_fill_certificate_map(
    tmp_path, monkeypatch
):
    system_path, tasks_path = write_inputs(
        tmp_path,
        [task_spec("high", 5, 1), task_spec("target", 20, 1)],
    )
    observed_maps = []

    def fake_response(
        policy,
        target,
        tasks,
        M,
        beta,
        E0=0,
        max_iterations=1000,
        assume_no_overflow=False,
        profile=False,
        certified_bounds=None,
    ):
        observed_maps.append(certified_bounds)
        return ablation.AblationTaskResult(
            task_name=target.name,
            period=target.period,
            wcet=target.wcet,
            deadline=target.deadline,
            workload=target.workload,
            energy_per_tick=target.energy_per_tick,
            schedulable=True,
            response_time_bound=target.wcet,
            rta_status="proven_under_assumptions",
            certificate_status="not_required",
            proof_claim_allowed=True,
            proof_claim_succeeded=True,
        )

    monkeypatch.setattr(ablation, "response_time_bound_variant", fake_response)
    report = ablation.analyze_taskset_variant(
        str(system_path),
        str(tasks_path),
        "baseline_safe",
        horizon_ms=20,
        initial_energy=100.0,
        assume_no_overflow=True,
        harvest_trace=[0.0] * 20,
    )

    assert observed_maps == [None, None]
    assert all(task.certificate_status == "not_required" for task in report.tasks)
    assert all(task.proof_claim_allowed for task in report.tasks)


@pytest.mark.parametrize("variant", ["carry_in_certified", "capacity_coupled"])
def test_a1_a2_analyzers_keep_separate_variant_certificate_maps(
    tmp_path, monkeypatch, variant
):
    system_path, tasks_path = write_inputs(
        tmp_path,
        [task_spec("high", 5, 1), task_spec("target", 20, 1)],
    )
    observed_maps = []

    def fake_response(
        policy,
        target,
        tasks,
        M,
        beta,
        E0=0,
        max_iterations=1000,
        assume_no_overflow=False,
        profile=False,
        certified_bounds=None,
    ):
        observed_maps.append(dict(certified_bounds))
        return ablation.AblationTaskResult(
            task_name=target.name,
            period=target.period,
            wcet=target.wcet,
            deadline=target.deadline,
            workload=target.workload,
            energy_per_tick=target.energy_per_tick,
            schedulable=True,
            response_time_bound=target.wcet,
            rta_status="proven_under_assumptions",
            certificate_status="available",
            proof_claim_allowed=True,
            proof_claim_succeeded=True,
        )

    monkeypatch.setattr(ablation, "response_time_bound_variant", fake_response)
    ablation.analyze_taskset_variant(
        str(system_path),
        str(tasks_path),
        variant,
        horizon_ms=20,
        initial_energy=100.0,
        assume_no_overflow=True,
        harvest_trace=[0.0] * 20,
    )

    assert observed_maps == [{}, {"high": 1}]


@pytest.mark.parametrize("variant", ["carry_in_certified", "capacity_coupled"])
def test_a1_a2_missing_certificate_blocks_proof_claim(variant):
    high = make_task("high", 5, 1, 0)
    target = make_task("target", 20, 1, 1)
    result = ablation.response_time_bound_variant(
        ablation.VARIANT_POLICIES[variant],
        target,
        [high, target],
        M=1,
        beta=[0.0],
        E0=100.0,
        assume_no_overflow=True,
        certified_bounds={},
    )
    assert result.rta_status == "certificate_missing"
    assert result.certificate_status == "certificate_missing"
    assert not result.proof_claim_allowed
    assert not result.proof_claim_succeeded
    assert not result.schedulable
    assert result.response_time_bound is None
    assert "high" in result.failure_reason


@pytest.mark.parametrize("variant", ["carry_in_certified", "capacity_coupled"])
def test_failed_hp_analysis_is_not_reused_as_variant_certificate(
    tmp_path, variant
):
    system_path, tasks_path = write_inputs(
        tmp_path,
        [task_spec("high", 5, 1), task_spec("target", 20, 1)],
    )
    report = ablation.analyze_taskset_variant(
        str(system_path),
        str(tasks_path),
        variant,
        horizon_ms=20,
        initial_energy=0.0,
        assume_no_overflow=True,
        harvest_trace=[0.0] * 20,
    )
    assert not report.tasks[0].proof_claim_succeeded
    assert report.tasks[1].certificate_status == "certificate_missing"
    assert not report.tasks[1].proof_claim_allowed
    assert not report.tasks[1].proof_claim_succeeded


def test_a0_a1_omit_processor_coupling_while_a2_enforces_it():
    high = make_task("high", 10, 4, 0)
    target = make_task("target", 20, 1, 1)
    tasks = [high, target]
    certified = {"high": 4}

    a1_states = ablation.energy_states_for_z(
        ablation.VARIANT_POLICIES["carry_in_certified"],
        target,
        tasks,
        w=5,
        x=2,
        z=1,
        M=2,
        certified_bounds=certified,
    )
    a2_states = ablation.energy_states_for_z(
        ablation.VARIANT_POLICIES["capacity_coupled"],
        target,
        tasks,
        w=5,
        x=2,
        z=1,
        M=2,
        certified_bounds=certified,
    )

    assert a1_states
    assert not a2_states


def test_a2_does_not_apply_window_level_u_capacity():
    high1 = make_task("high1", 10, 4, 0)
    high2 = make_task("high2", 11, 4, 1)
    target = make_task("target", 20, 1, 2)
    tasks = [high1, high2, target]
    states = ablation.energy_states_for_z(
        ablation.VARIANT_POLICIES["capacity_coupled"],
        target,
        tasks,
        w=2,
        x=2,
        z=1,
        M=2,
        certified_bounds={"high1": 4, "high2": 4},
    )
    assert states
    max_u, _max_energy = states[0]
    assert max_u > 2 * (2 - 2)


def test_a2_does_not_apply_single_task_window_capacity():
    high = make_task("high", 10, 4, 0, deadline=10)
    target = make_task("target", 20, 1, 1)
    policy = ablation.VARIANT_POLICIES["capacity_coupled"]
    window = 1
    high_workload = v20.workload_bound(high, window, theta=10)
    assert high_workload == 4
    assert high_workload > window

    states = ablation.energy_states_for_z(
        policy,
        target,
        [high, target],
        w=window,
        x=1,
        z=1,
        M=2,
        certified_bounds={"high": 10},
    )

    assert states
    max_u, _max_energy = states[0]
    assert max_u == high_workload
    assert not policy.uses_window_level_task_capacity
    assert not policy.uses_window_level_u_capacity


def test_empty_energy_state_set_is_unproven(monkeypatch):
    target = make_task("target", 10, 1, 0)
    monkeypatch.setattr(
        ablation,
        "energy_states_for_z",
        lambda *args, **kwargs: [],
    )

    result = ablation.response_time_bound_variant(
        ablation.VARIANT_POLICIES["baseline_safe"],
        target,
        [target],
        M=1,
        beta=[0.0] * 11,
        E0=100.0,
        assume_no_overflow=True,
    )

    assert result.rta_status == "rta_unproven"
    assert not result.schedulable
    assert not result.proof_claim_succeeded
    assert result.response_time_bound is None
    assert result.failure_reason == (
        "energy-state set is empty for every reference prefix"
    )


def test_json_schema_and_profile_for_completed_a0_analysis(tmp_path):
    system_path, tasks_path = write_inputs(
        tmp_path, [task_spec("only", 10, 1)]
    )
    report = ablation.analyze_taskset_variant(
        str(system_path),
        str(tasks_path),
        "baseline_safe",
        horizon_ms=20,
        initial_energy=100.0,
        assume_no_overflow=True,
        harvest_trace=[0.0] * 20,
        profile=True,
    ).to_dict()

    assert report["rta_version"] == "e3-a0-baseline-safe"
    assert report["certificate_status"] == "not_required"
    assert report["proof_claim_allowed"]
    assert report["proof_claim_succeeded"]
    assert report["schedulable"]
    for field in (
        "variant_name",
        "variant_label",
        "variant_group",
        "variant_safety_label",
        "proof_claim_eligible",
        "diagnostic_only",
        "uses_certified_carry_in",
        "uses_processor_capacity_coupling",
        "uses_window_level_task_capacity",
        "uses_window_level_u_capacity",
        "uses_local_window",
        "certificate_policy",
    ):
        assert field in report

    task = report["tasks"][0]
    for field in (
        "task_name",
        "schedulable",
        "response_time_bound",
        "rta_status",
        "certificate_status",
        "proof_claim_allowed",
        "proof_claim_succeeded",
        "rta_profile",
    ):
        assert field in task
    assert task["rta_profile"]["total_time_sec"] >= 0


def test_cli_emits_json_with_variant_version_and_profile(
    tmp_path, capsys
):
    system_path, tasks_path = write_inputs(
        tmp_path, [task_spec("only", 10, 1)]
    )
    exit_code = ablation.main(
        [
            "--tasks",
            str(tasks_path),
            "--config",
            str(system_path),
            "--variant",
            "baseline_safe",
            "--horizon-ms",
            "20",
            "--initial-energy",
            "100.0",
            "--assume-no-overflow",
            "--profile",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["rta_version"] == "e3-a0-baseline-safe"
    assert payload["tasks"][0]["rta_profile"]["total_time_sec"] >= 0
