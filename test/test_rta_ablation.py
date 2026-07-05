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


def write_mock_task(spec):
    path = Path(spec["task_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "tasks:\n  - name: {}\n".format(spec["taskset_family_id"]),
        encoding="utf-8",
    )
    return ""


def test_variant_parsing_and_disabled_component_variants(tmp_path):
    args = parse_and_validate(base_args(tmp_path, "e3-parse"))
    assert args.variants == ["v20p4_full", "v21_experimental"]
    assert args.utilizations == [0.2]

    v20_only = parse_and_validate(
        base_args(tmp_path, "e3-v20-only")
        + ["--variants", "v20p4_full"]
    )
    assert v20_only.variants == ["v20p4_full"]

    for bad in ("unknown", "baseline_safe", "A0", "A1", "A2"):
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

    v21 = by_variant["v21_experimental"]
    assert v21["variant_safety_label"] == "experimental_sufficient_candidate"
    assert v21["proof_claim_eligible"] == "False"
    assert v21["variant_is_default"] == "False"
    assert v21["variant_is_experimental"] == "True"

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
            "v21_experimental",
        }
        assert len({row["seed"] for row in family_rows}) == 1
        assert len({row["taskset_family_id"] for row in family_rows}) == 1
        assert len({row["task_file"] for row in family_rows}) == 1


def test_run_uses_normalized_times_m_and_propagates_v20_v21_results(tmp_path):
    arguments = base_args(tmp_path, "e3-run") + [
        "--M", "3",
        "--utilizations", "0.2",
        "--profile-rta",
    ]
    generated = []

    def fake_generation(spec):
        generated.append(spec)
        return write_mock_task(spec)

    def fake_v21_subprocess(command, **_kwargs):
        assert "asap_block_rta_v21_local_window.py" in command[1]
        assert "--json" in command
        output = json.dumps(v21_payload(proven=True, bound=8))
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    with mock.patch.object(
        runner, "_generate_taskset", side_effect=fake_generation
    ) as generation_mock, mock.patch.object(
        runner.acceptance,
        "run_asap_block_rta",
        return_value=successful_v20_result(profile=True),
    ) as v20_mock, mock.patch.object(
        runner.subprocess, "run", side_effect=fake_v21_subprocess
    ) as subprocess_mock, mock.patch.object(
        runner.acceptance, "run_single_simulation_worker"
    ) as simulation_mock:
        results_path = runner.main(arguments)

    assert generation_mock.call_count == 1
    assert len(generated) == 1
    assert float(generated[0]["total_utilization"]) == 0.6
    assert v20_mock.call_count == 1
    assert subprocess_mock.call_count == 1
    simulation_mock.assert_not_called()

    rows = read_rows(results_path)
    assert [row["variant_name"] for row in rows] == [
        "v20p4_full",
        "v21_experimental",
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

    v21 = by_variant["v21_experimental"]
    assert v21["rta_tool"] == "asap_block_rta_v21_local_window.py"
    assert v21["expected_rta_version"] == "v21-local-window"
    assert v21["rta_version"] == "v21-local-window"
    assert v21["rta_proven"] == "True"
    assert v21["proof_claim_eligible"] == "False"
    assert v21["rta_profile_enabled"] == "False"
    assert float(v21["rta_response_bound"]) == 8.0
    assert float(v21["rta_runtime_sec"]) >= 0.0

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
    assert by_variant["v21_experimental"]["result_status"] == "rta_timeout"
    assert by_variant["v21_experimental"]["rta_timed_out"] == "True"
    assert by_variant["v21_experimental"]["rta_error"].startswith(
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
