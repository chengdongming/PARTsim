import csv
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock

import pytest
import yaml

from scripts import run_rta_ablation as runner


def base_args(output_root, name):
    return [
        "--output-root", str(output_root),
        "--experiment-name", name,
        "--variants", "v20p4_full,v21_experimental",
        "--utilizations", "0.2",
        "--task-n", "2",
        "--M", "2",
        "--num-tasksets", "1",
        "--task-p-min", "40",
        "--task-p-max", "80",
        "--rta-horizon-ms", "100",
        "--rta-timeout", "7",
        "--rta-initial-energy", "0",
        "--seed", "12345",
        "--max-workers", "1",
        "--rta-assume-no-overflow",
    ]


def parse_and_validate(arguments):
    parser = runner.build_parser()
    args = parser.parse_args(arguments)
    runner.validate_args(parser, args)
    return args


def read_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_header(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


def assert_no_duplicates(fields):
    duplicates = [name for name, count in Counter(fields).items() if count > 1]
    assert duplicates == []


def successful_v20_result(profile=False):
    return {
        "rta_version": "v20.4",
        "rta_status": "proven_under_assumptions",
        "rta_error": None,
        "rta_proven_under_assumptions": True,
        "rta_bound": 9.0,
        "rta_attempted": True,
        "rta_runtime_sec": 0.25,
        "rta_runtime_source": "subprocess_wall_clock_perf_counter",
        "rta_timed_out": False,
        "rta_timeout_sec": 7.0,
        "rta_profile_enabled": profile,
        "rta_profile_task_time_sum_sec": 0.2 if profile else None,
        "rta_profile_task_count": 2 if profile else 0,
    }


def timeout_v20_result():
    return {
        "rta_version": "v20.4",
        "rta_status": "rta_error",
        "rta_error": "RTA timed out after 7.0 seconds",
        "rta_proven_under_assumptions": False,
        "rta_bound": None,
        "rta_attempted": True,
        "rta_runtime_sec": 7.01,
        "rta_runtime_source": "subprocess_wall_clock_perf_counter",
        "rta_timed_out": True,
        "rta_timeout_sec": 7.0,
        "rta_profile_enabled": False,
        "rta_profile_task_time_sum_sec": None,
        "rta_profile_task_count": 0,
    }


def v21_payload(proven=True, bound=8):
    return {
        "rta_version": "v21-local-window",
        "proven_under_assumptions": proven,
        "conditional": True,
        "tasks": [
            {
                "task_name": "task0",
                "proven_under_assumptions": proven,
                "response_time_bound": bound,
            }
        ],
    }


def ablation_payload(variant, proven=True, bound=11):
    version = runner.VARIANT_REGISTRY[variant]["expected_rta_version"]
    payload = {
        "rta_version": version,
        "schedulable": proven,
        "proven_under_assumptions": proven,
        "response_time_bound": bound,
        "certificate_status": "not_required" if variant == "baseline_safe" else "available",
        "proof_claim_allowed": True,
        "proof_claim_succeeded": proven,
        "tasks": [
            {
                "task_name": "task0",
                "proven_under_assumptions": proven,
                "response_time_bound": bound,
                "certificate_status": "not_required" if variant == "baseline_safe" else "available",
                "proof_claim_allowed": True,
                "proof_claim_succeeded": proven,
                "rta_profile": {"total_time_sec": 0.01},
            }
        ],
    }
    payload.update({
        key: runner.VARIANT_REGISTRY[variant][key]
        for key in (
            "variant_name",
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
        )
    })
    return payload


def write_mock_task(spec):
    path = Path(spec["task_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "tasks:\n  - name: {}\n".format(spec["taskset_family_id"]),
        encoding="utf-8",
    )
    return ""


def test_variant_registry_aliases_and_all_expansion(tmp_path):
    args = parse_and_validate(base_args(tmp_path, "e3-parse"))
    assert args.variants == ["v20p4_full", "v21_experimental"]
    assert args.utilizations == [0.2]
    assert [entry["canonical"] for entry in args.variant_requests] == [
        "v20p4_full",
        "v21_local_window_closure",
    ]

    all_args = parse_and_validate(
        base_args(tmp_path, "e3-all") + ["--variants", "all"]
    )
    assert [entry["canonical"] for entry in all_args.variant_requests] == [
        "baseline_safe",
        "carry_in_certified",
        "capacity_coupled",
        "v20p4_full",
        "v21_local_window_closure",
    ]

    alias_args = parse_and_validate(
        base_args(tmp_path, "e3-alias") + ["--variants", "A0,A1,A2,A3,A4"]
    )
    assert [entry["canonical"] for entry in alias_args.variant_requests] == [
        "baseline_safe",
        "carry_in_certified",
        "capacity_coupled",
        "v20p4_full",
        "v21_local_window_closure",
    ]
    assert runner.VARIANT_REGISTRY["v21_local_window_closure"]["proof_claim_eligible"] is True
    assert runner.VARIANT_REGISTRY["v21_local_window_closure"]["variant_is_experimental"] is False
    assert runner.VARIANT_REGISTRY["v21_local_window_closure"]["diagnostic_only"] is False

    for bad in ("unknown", "A5"):
        parser = runner.build_parser()
        args = parser.parse_args(
            base_args(tmp_path, "bad-{}".format(bad.lower()))
            + ["--variants", bad]
        )
        with pytest.raises(SystemExit):
            runner.validate_args(parser, args)


def test_manifest_and_result_fields_have_no_duplicates():
    assert_no_duplicates(runner.MANIFEST_FIELDS)
    assert_no_duplicates(runner.RESULT_FIELDS)


@pytest.mark.parametrize(
    "variant", ["baseline_safe", "carry_in_certified", "capacity_coupled"]
)
def test_a0_a2_payload_metadata_overrides_registry_fallback(tmp_path, variant):
    args = parse_and_validate(
        base_args(tmp_path, "e3-payload-{}".format(variant))
        + ["--variants", variant]
    )
    spec = runner.build_specs(args, tmp_path / "planned")[0]
    payload = ablation_payload(variant, proven=True, bound=11)
    payload.update({
        "variant_label": "payload label {}".format(variant),
        "variant_safety_label": "payload_safety_label",
        "theory_family": "payload_complete_window_component",
        "closure_method": "payload_method",
        "uses_certified_carry_in": not bool(
            runner.VARIANT_REGISTRY[variant]["uses_certified_carry_in"]
        ),
        "uses_processor_capacity_coupling": not bool(
            runner.VARIANT_REGISTRY[variant][
                "uses_processor_capacity_coupling"
            ]
        ),
        "uses_window_level_task_capacity": True,
        "uses_window_level_u_capacity": True,
        "uses_local_window": True,
        "uses_delta_closure": True,
        "uses_parallel_u_compression": True,
        "certificate_policy": "payload_certificate_policy",
        "certificate_status": "payload_certificate_status",
        "proof_claim_eligible": False,
        "proof_claim_allowed": False,
        "proof_claim_succeeded": False,
        "diagnostic_only": True,
        "fallback_used": True,
        "fallback_reason": "payload fallback",
        "empty_state_guard": False,
        "empty_set_guard": True,
        "fallback_guard": False,
        "consistency_guard": False,
    })

    parsed = runner._parse_rta_payload(
        payload, spec["_expected_rta_version"], assume_no_overflow=True
    )
    row = runner._result_row(spec, parsed)

    assert row["variant_name"] == variant
    assert row["variant_label"] == "payload label {}".format(variant)
    assert row["variant_safety_label"] == "payload_safety_label"
    assert row["theory_family"] == "payload_complete_window_component"
    assert row["closure_method"] == "payload_method"
    assert row["uses_certified_carry_in"] is (
        not runner.VARIANT_REGISTRY[variant]["uses_certified_carry_in"]
    )
    assert row["uses_processor_capacity_coupling"] is (
        not runner.VARIANT_REGISTRY[variant][
            "uses_processor_capacity_coupling"
        ]
    )
    assert row["uses_window_level_task_capacity"] is True
    assert row["uses_window_level_u_capacity"] is True
    assert row["uses_local_window"] is True
    assert row["uses_delta_closure"] is True
    assert row["uses_parallel_u_compression"] is True
    assert row["certificate_policy"] == "payload_certificate_policy"
    assert row["certificate_status"] == "payload_certificate_status"
    assert row["proof_claim_eligible"] is False
    assert row["proof_claim_allowed"] is False
    assert row["proof_claim_succeeded"] is False
    assert row["diagnostic_only"] is True
    assert row["fallback_used"] is True
    assert row["fallback_reason"] == "payload fallback"
    assert row["empty_state_guard"] is False
    assert row["empty_set_guard"] is True
    assert row["fallback_guard"] is False
    assert row["consistency_guard"] is False


def test_a4_v21_payload_metadata_and_counters_override_registry_fallback(tmp_path):
    args = parse_and_validate(
        base_args(tmp_path, "e3-v21-payload")
        + ["--variants", "v21_experimental"]
    )
    spec = runner.build_specs(args, tmp_path / "planned")[0]
    payload = v21_payload(proven=True, bound=8)
    payload.update({
        "theory_family": "payload_local_window_closure",
        "closure_method": "payload_delta_closure",
        "empty_set_guard": False,
        "fallback_guard": False,
        "consistency_guard": False,
        "certified_carry_in_source": "payload_v21_recursive_certification",
        "uses_local_window": True,
        "uses_delta_closure": True,
        "uses_parallel_u_compression": False,
        "diagnostic_only": False,
        "proof_claim_eligible": True,
        "proof_claim_allowed": True,
        "proof_claim_succeeded": True,
        "certificate_status": "payload_certificate_status",
        "failure_reason": "payload_failure_reason",
        "fallback_used": True,
        "fallback_reason": "payload_fallback_reason",
    })
    payload["tasks"] = [
        {
            "task_name": "task0",
            "proven_under_assumptions": True,
            "response_time_bound": 8,
            "rta_profile": {
                "delta_iterations": 2,
                "g_loc_calls": 3,
                "omega_feasibility_calls": 4,
                "empty_omega_count": 1,
                "no_closure_count": 0,
                "closed_prefix_count": 2,
                "delta_cap_exceeded_count": 0,
                "max_delta_cap": 7,
                "max_delta_seen": 5,
                "delta_jump_count": 1,
            },
        },
        {
            "task_name": "task1",
            "proven_under_assumptions": True,
            "response_time_bound": 6,
            "rta_profile": {
                "delta_iterations": 5,
                "g_loc_calls": 7,
                "omega_feasibility_calls": 11,
                "empty_omega_count": 2,
                "no_closure_count": 1,
                "closed_prefix_count": 3,
                "delta_cap_exceeded_count": 4,
                "max_delta_cap": 13,
                "max_delta_seen": 17,
                "delta_jump_count": 6,
            },
        },
    ]

    parsed = runner._parse_rta_payload(
        payload, spec["_expected_rta_version"], assume_no_overflow=True
    )
    row = runner._result_row(spec, parsed)

    assert row["input_variant"] == "v21_experimental"
    assert row["variant_name"] == "v21_local_window_closure"
    assert row["variant_canonical"] == "v21_local_window_closure"
    assert row["variant_stage"] == "A4"
    assert row["variant_group"] == "local_window_refinement"
    assert row["theory_family"] == "payload_local_window_closure"
    assert row["closure_method"] == "payload_delta_closure"
    assert row["certified_carry_in_source"] == (
        "payload_v21_recursive_certification"
    )
    assert row["uses_local_window"] is True
    assert row["uses_delta_closure"] is True
    assert row["uses_parallel_u_compression"] is False
    assert row["proof_claim_eligible"] is True
    assert row["diagnostic_only"] is False
    assert row["failure_reason"] == "payload_failure_reason"
    assert row["fallback_used"] is True
    assert row["fallback_reason"] == "payload_fallback_reason"
    assert int(row["v21_delta_iterations"]) == 7
    assert int(row["v21_g_loc_calls"]) == 10
    assert int(row["v21_omega_feasibility_calls"]) == 15
    assert int(row["v21_empty_omega_count"]) == 3
    assert int(row["v21_no_closure_count"]) == 1
    assert int(row["v21_closed_prefix_count"]) == 5
    assert int(row["v21_delta_cap_exceeded_count"]) == 4
    assert int(row["v21_delta_jump_count"]) == 7
    assert int(row["v21_max_delta_cap"]) == 13
    assert int(row["v21_max_delta_seen"]) == 17


def test_a3_partial_payload_metadata_uses_v20_safe_fallback(tmp_path):
    args = parse_and_validate(
        base_args(tmp_path, "e3-v20-payload") + ["--variants", "v20p4_full"]
    )
    spec = runner.build_specs(args, tmp_path / "planned")[0]
    payload = successful_v20_result()
    payload["theory_family"] = "payload_complete_window_override"

    row = runner._result_row(spec, runner._normalize_version(spec, payload))

    assert row["variant_name"] == "v20p4_full"
    assert row["variant_stage"] == "A3"
    assert row["variant_group"] == "safe_chain"
    assert row["theory_family"] == "payload_complete_window_override"
    assert row["closure_method"] == "fixed_point_complete_window"
    assert row["variant_safety_label"] == "safe_under_v20p4_assumptions"
    assert row["proof_claim_eligible"] is True
    assert row["diagnostic_only"] is False
    assert row["empty_state_guard"] is True
    assert row["uses_local_window"] is False
    assert row["uses_delta_closure"] is False
    assert row["uses_parallel_u_compression"] is False


def test_dry_run_writes_manifest_and_empty_results_without_calls(tmp_path):
    arguments = base_args(tmp_path, "e3-dry") + [
        "--utilizations", "0.2,0.4",
        "--num-tasksets", "2",
        "--dry-run",
    ]
    with mock.patch.object(
        runner, "_generate_taskset"
    ) as generation_mock, mock.patch.object(
        runner, "write_system_config"
    ) as config_mock, mock.patch.object(
        runner, "_run_rta"
    ) as rta_mock, mock.patch.object(
        runner.acceptance, "run_single_simulation_worker"
    ) as simulation_mock:
        results_path = runner.main(arguments)

    generation_mock.assert_not_called()
    config_mock.assert_not_called()
    rta_mock.assert_not_called()
    simulation_mock.assert_not_called()

    run_dir = tmp_path / "e3-dry"
    manifest_rows = read_rows(run_dir / runner.MANIFEST_FILENAME)
    assert len(manifest_rows) == 2 * 2 * 2
    assert {row["status"] for row in manifest_rows} == {"dry_run"}
    assert read_rows(results_path) == []
    assert read_header(results_path) == runner.RESULT_FIELDS
    assert not (run_dir / "configs").exists()
    assert not (run_dir / "tasks").exists()


def test_variant_metadata_and_fixed_energy_metadata_are_recorded(tmp_path):
    results_path = runner.main(
        base_args(tmp_path, "e3-metadata") + ["--dry-run"]
    )
    rows = read_rows(tmp_path / "e3-metadata" / runner.MANIFEST_FILENAME)
    by_variant = {row["variant_name"]: row for row in rows}

    v20 = by_variant["v20p4_full"]
    assert v20["variant_safety_label"] == "safe_under_v20p4_assumptions"
    assert v20["proof_claim_eligible"] == "True"
    assert v20["variant_is_default"] == "True"
    assert v20["variant_is_experimental"] == "False"

    v21 = by_variant["v21_local_window_closure"]
    assert v21["input_variant"] == "v21_experimental"
    assert v21["variant_stage"] == "A4"
    assert v21["variant_group"] == "local_window_refinement"
    assert v21["formal_variant"] == "True"
    assert v21["variant_safety_label"] == "safe_under_v21_local_window_assumptions"
    assert v21["proof_claim_eligible"] == "True"
    assert v21["variant_is_default"] == "False"
    assert v21["variant_is_experimental"] == "False"
    assert v21["diagnostic_only"] == "False"

    assert {row["diagnostic_only"] for row in rows} == {"False"}
    assert {row["harvesting_profile"] for row in rows} == {
        runner.HARVESTING_PROFILE
    }
    assert {row["harvesting_profile_fixed"] for row in rows} == {"True"}
    assert {row["use_real_solar_data"] for row in rows} == {"False"}
    assert {row["harvesting_scale"] for row in rows} == {"1.0"}
    assert {row["rta_initial_energy_semantics"] for row in rows} == {
        runner.E0_SEMANTICS
    }
    assert read_rows(results_path) == []


def test_same_taskset_is_reused_across_variants_and_is_deterministic(tmp_path):
    args = parse_and_validate(
        base_args(tmp_path, "e3-deterministic")
        + ["--utilizations", "0.2,0.4", "--num-tasksets", "2"]
    )
    first = runner.build_specs(args, tmp_path / "planned")
    second = runner.build_specs(args, tmp_path / "planned")
    assert [
        (
            row["seed"],
            row["config_id"],
            row["taskset_family_id"],
            row["task_file"],
            row["variant_name"],
        )
        for row in first
    ] == [
        (
            row["seed"],
            row["config_id"],
            row["taskset_family_id"],
            row["task_file"],
            row["variant_name"],
        )
        for row in second
    ]

    grouped = defaultdict(list)
    for row in first:
        grouped[(row["normalized_utilization"], row["taskset_id"])].append(row)
    assert len(grouped) == 4
    for family_rows in grouped.values():
        assert {row["variant_name"] for row in family_rows} == {
            "v20p4_full",
            "v21_local_window_closure",
        }
        assert len({row["seed"] for row in family_rows}) == 1
        assert len({row["taskset_family_id"] for row in family_rows}) == 1
        assert len({row["task_file"] for row in family_rows}) == 1


def test_run_uses_normalized_times_m_and_propagates_v20_v21_results(tmp_path):
    arguments = base_args(tmp_path, "e3-run") + [
        "--variants", "all",
        "--M", "3",
        "--utilizations", "0.2",
        "--profile-rta",
    ]
    generated = []

    def fake_generation(spec):
        generated.append(spec)
        return write_mock_task(spec)

    def fake_rta_subprocess(command, **_kwargs):
        if runner.ABLATION_TOOL in command[1]:
            variant = command[command.index("--variant") + 1]
            assert variant in {
                "baseline_safe",
                "carry_in_certified",
                "capacity_coupled",
            }
            assert "--profile-rta" in command
            output = json.dumps(ablation_payload(variant, proven=True, bound=11))
        else:
            assert "asap_block_rta_v21_local_window.py" in command[1]
            assert "--json" in command
            assert "--profile-rta" in command
            payload = v21_payload(proven=True, bound=8)
            payload.update({
                "theory_family": "local_window_closure",
                "closure_method": "delta_closure",
                "empty_set_guard": True,
                "fallback_guard": True,
                "consistency_guard": True,
                "uses_local_window": True,
                "uses_delta_closure": True,
                "uses_parallel_u_compression": False,
            })
            payload["tasks"][0]["rta_profile"] = {
                "delta_iterations": 2,
                "g_loc_calls": 3,
                "omega_feasibility_calls": 4,
                "empty_omega_count": 1,
                "no_closure_count": 0,
                "closed_prefix_count": 2,
                "delta_cap_exceeded_count": 0,
                "max_delta_cap": 7,
                "max_delta_seen": 5,
                "delta_jump_count": 1,
            }
            output = json.dumps(payload)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    with mock.patch.object(
        runner, "_generate_taskset", side_effect=fake_generation
    ) as generation_mock, mock.patch.object(
        runner.acceptance,
        "run_asap_block_rta",
        return_value=successful_v20_result(profile=True),
    ) as v20_mock, mock.patch.object(
        runner.subprocess, "run", side_effect=fake_rta_subprocess
    ) as subprocess_mock, mock.patch.object(
        runner.acceptance, "run_single_simulation_worker"
    ) as simulation_mock:
        results_path = runner.main(arguments)

    assert generation_mock.call_count == 1
    assert len(generated) == 1
    assert float(generated[0]["total_utilization"]) == 0.6
    assert v20_mock.call_count == 1
    assert subprocess_mock.call_count == 4
    simulation_mock.assert_not_called()

    rows = read_rows(results_path)
    assert [row["variant_name"] for row in rows] == [
        "baseline_safe",
        "carry_in_certified",
        "capacity_coupled",
        "v20p4_full",
        "v21_local_window_closure",
    ]
    assert {row["seed"] for row in rows} and len({row["seed"] for row in rows}) == 1
    assert len({row["taskset_family_id"] for row in rows}) == 1
    assert len({row["task_file"] for row in rows}) == 1
    assert len({row["task_file_sha256"] for row in rows}) == 1
    assert next(iter({row["task_file_sha256"] for row in rows}))
    assert {
        (
            float(row["normalized_utilization"]),
            float(row["total_utilization"]),
        )
        for row in rows
    } == {(0.2, 0.6)}

    by_variant = {row["variant_name"]: row for row in rows}
    assert by_variant["baseline_safe"]["rta_tool"] == runner.ABLATION_TOOL
    assert by_variant["baseline_safe"]["variant_stage"] == "A0"
    assert by_variant["baseline_safe"]["certificate_policy"] == "none_deadline_workload"
    assert by_variant["carry_in_certified"]["variant_stage"] == "A1"
    assert by_variant["capacity_coupled"]["variant_stage"] == "A2"
    assert {
        by_variant[name]["rta_version"]
        for name in ("baseline_safe", "carry_in_certified", "capacity_coupled")
    } == {
        "e3-a0-baseline-safe",
        "e3-a1-carry-in-certified",
        "e3-a2-capacity-coupled",
    }
    v20 = by_variant["v20p4_full"]
    assert v20["rta_tool"] == "asap_block_rta.py"
    assert v20["expected_rta_version"] == "v20.4"
    assert v20["rta_version"] == "v20.4"
    assert v20["rta_proven"] == "True"
    assert v20["rta_schedulable"] == "True"
    assert float(v20["rta_response_bound"]) == 9.0
    assert float(v20["rta_runtime_sec"]) == 0.25
    assert v20["rta_profile_enabled"] == "True"
    assert float(v20["rta_profile_task_time_sum_sec"]) == 0.2

    v21 = by_variant["v21_local_window_closure"]
    assert v21["rta_tool"] == "asap_block_rta_v21_local_window.py"
    assert v21["expected_rta_version"] == "v21-local-window"
    assert v21["rta_version"] == "v21-local-window"
    assert v21["rta_proven"] == "True"
    assert v21["variant_stage"] == "A4"
    assert v21["formal_variant"] == "True"
    assert v21["proof_claim_eligible"] == "True"
    assert v21["diagnostic_only"] == "False"
    assert v21["rta_profile_enabled"] == "True"
    assert float(v21["rta_response_bound"]) == 8.0
    assert float(v21["rta_runtime_sec"]) >= 0.0
    assert int(v21["v21_delta_iterations"]) == 2

    for call in v20_mock.call_args_list:
        assert call.kwargs["algorithm"] == runner.acceptance.ASAP_BLOCK_ALGORITHM
        assert call.kwargs["timeout"] == 7.0
        assert call.kwargs["profile_rta"] is True
        with Path(call.kwargs["system_config"]).open(
            "r", encoding="utf-8"
        ) as handle:
            config = yaml.safe_load(handle)
        assert config["cpu_islands"][0]["numcpus"] == 3
        assert (
            config["cpu_islands"][0]["kernel"]["scheduler"]
            == runner.acceptance.ASAP_BLOCK_ALGORITHM
        )
        assert config["energy_management"]["use_real_solar_data"] is False
        assert config["energy_management"]["harvesting_scale"] == 1.0


def test_timeout_and_error_rows_are_retained(tmp_path):
    arguments = base_args(tmp_path, "e3-timeout")

    def fake_v21_timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command, timeout=kwargs.get("timeout", 7.0)
        )

    with mock.patch.object(
        runner, "_generate_taskset", side_effect=write_mock_task
    ), mock.patch.object(
        runner.acceptance,
        "run_asap_block_rta",
        return_value=timeout_v20_result(),
    ), mock.patch.object(
        runner.subprocess, "run", side_effect=fake_v21_timeout
    ):
        rows = read_rows(runner.main(arguments))

    assert len(rows) == 2
    by_variant = {row["variant_name"]: row for row in rows}
    assert by_variant["v20p4_full"]["result_status"] == "rta_timeout"
    assert by_variant["v20p4_full"]["rta_timed_out"] == "True"
    assert by_variant["v20p4_full"]["rta_proven"] == "False"
    assert by_variant["v21_local_window_closure"]["result_status"] == "rta_timeout"
    assert by_variant["v21_local_window_closure"]["rta_timed_out"] == "True"
    assert by_variant["v21_local_window_closure"]["rta_error"].startswith(
        "RTA timed out after"
    )


def test_version_mismatch_is_recorded_as_error_result(tmp_path):
    args = parse_and_validate(
        base_args(tmp_path, "e3-version-mismatch")
        + ["--variants", "v20p4_full"]
    )
    spec = runner.build_specs(args, tmp_path / "planned")[0]
    mismatched = successful_v20_result()
    mismatched["rta_version"] = "v21-local-window"

    normalized = runner._normalize_version(spec, mismatched)
    row = runner._result_row(spec, normalized)

    assert row["rta_version"] == "v21-local-window"
    assert row["expected_rta_version"] == "v20.4"
    assert "expected v20.4 report" in row["rta_error"]
    assert row["rta_status"] == "rta_error"
    assert row["result_status"] == "rta_error"
    assert row["rta_proven"] is False
    assert row["rta_schedulable"] is False
    assert row["rta_response_bound"] == ""


def test_version_isolation_and_no_simulation_derived_fields():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    acceptance_source = Path(runner.acceptance.__file__).read_text(
        encoding="utf-8"
    )
    assert runner.V20_TOOL == "asap_block_rta.py"
    assert runner.V20_VERSION == "v20.4"
    assert runner.V21_TOOL == "asap_block_rta_v21_local_window.py"
    assert runner.V21_VERSION == "v21-local-window"
    assert Path(runner.acceptance.RTA_TOOL).name == "asap_block_rta.py"
    assert runner.acceptance.RTA_VERSION == "v20.4"
    assert "asap_block_rta_v21" in source
    assert "asap_block_rta_v21" not in acceptance_source
    assert "acceptance_ratio" not in runner.RESULT_FIELDS
    assert "pessimism" not in runner.RESULT_FIELDS
    assert "observed_max_response_time" not in runner.RESULT_FIELDS
