import csv
import subprocess
from collections import defaultdict
from pathlib import Path
from unittest import mock

import pytest
import yaml

from scripts import run_rta_parameter_sensitivity as runner


def base_args(output_root, name, sweep):
    arguments = [
        "--output-root", str(output_root),
        "--experiment-name", name,
        "--sweep", sweep,
        "--task-n", "2",
        "--M", "2",
        "--num-tasksets", "1",
        "--task-p-min", "40",
        "--task-p-max", "80",
        "--rta-horizon-ms", "100",
        "--rta-timeout", "7",
        "--seed", "12345",
        "--max-workers", "1",
        "--rta-assume-no-overflow",
    ]
    if sweep == "utilization":
        arguments.extend(["--utilizations", "0.2", "--fixed-e0", "0"])
    else:
        arguments.extend(["--e0-values", "0,5", "--fixed-utilization", "0.6"])
    return arguments


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


def test_utilization_sweep_records_normalized_and_total_targets(tmp_path):
    args = parse_and_validate(
        base_args(tmp_path, "e4-target-util", "utilization") + [
            "--utilizations", "0.5",
            "--M", "4",
            "--wcet-rounding", "compensated",
            "--actual-utilization-tolerance-total", "0.01",
        ]
    )
    spec = runner.build_specs(args, tmp_path / "planned")[0]

    assert spec["normalized_utilization"] == 0.5
    assert spec["total_utilization"] == 2.0
    assert spec["target_normalized_utilization"] == 0.5
    assert spec["target_total_utilization"] == 2.0
    assert spec["task_util_min"] == 0.01
    assert spec["task_util_max"] == 0.8
    assert spec["wcet_rounding"] == "compensated"
    assert spec["deadline_mode"] == "implicit"
    assert spec["actual_utilization_tolerance_total"] == 0.01


def successful_rta_result(profile=False):
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


def timeout_rta_result(profile=False):
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
        "rta_profile_enabled": profile,
        "rta_profile_task_time_sum_sec": None,
        "rta_profile_task_count": 0,
    }


def write_mock_task(spec):
    path = Path(spec["task_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "tasks:\n  - name: {}\n".format(spec["taskset_family_id"]),
        encoding="utf-8",
    )
    return ""


def test_cli_parses_lists_and_rejects_invalid_sweep_values(tmp_path):
    utilization = parse_and_validate(
        base_args(tmp_path, "e4-util-parse", "utilization")
        + ["--utilizations", "0.2,0.4,1.0"]
    )
    assert utilization.utilizations == [0.2, 0.4, 1.0]
    assert utilization.fixed_e0 == 0
    assert utilization.max_workers == 1
    assert not utilization.profile_rta

    e0 = parse_and_validate(
        base_args(tmp_path, "e4-e0-parse", "e0")
        + ["--e0-values", "0,5,10.5"]
    )
    assert e0.e0_values == [0.0, 5.0, 10.5]
    assert e0.fixed_utilization == 0.6

    for arguments in (
        base_args(tmp_path, "bad-zero", "utilization")
        + ["--utilizations", "0"],
        base_args(tmp_path, "bad-high", "utilization")
        + ["--utilizations", "1.01"],
        base_args(tmp_path, "bad-e0", "e0")
        + ["--e0-values=-1,0"],
        base_args(tmp_path, "bad-fixed-e0", "utilization")
        + ["--fixed-e0", "-1"],
    ):
        parser = runner.build_parser()
        args = parser.parse_args(arguments)
        with pytest.raises(SystemExit):
            runner.validate_args(parser, args)


def test_utilization_sweep_requires_explicit_fixed_e0(tmp_path):
    arguments = base_args(tmp_path, "missing-fixed-e0", "utilization")
    fixed_e0_index = arguments.index("--fixed-e0")
    del arguments[fixed_e0_index:fixed_e0_index + 2]

    parser = runner.build_parser()
    args = parser.parse_args(arguments)
    with pytest.raises(SystemExit):
        runner.validate_args(parser, args)

    explicit_zero = parse_and_validate(
        arguments + ["--fixed-e0", "0.0"]
    )
    assert explicit_zero.fixed_e0 == 0.0


def test_utilization_dry_run_writes_manifest_and_empty_results(tmp_path):
    arguments = base_args(tmp_path, "e4-util-dry", "utilization") + [
        "--utilizations", "0.2,0.4",
        "--num-tasksets", "2",
        "--dry-run",
    ]
    with mock.patch.object(
        runner, "_generate_taskset"
    ) as generation_mock, mock.patch.object(
        runner, "write_system_config"
    ) as config_mock, mock.patch.object(
        runner.acceptance, "run_asap_block_rta"
    ) as rta_mock:
        results_path = runner.main(arguments)

    generation_mock.assert_not_called()
    config_mock.assert_not_called()
    rta_mock.assert_not_called()
    run_dir = tmp_path / "e4-util-dry"
    manifest_rows = read_rows(run_dir / runner.MANIFEST_FILENAME)
    assert len(manifest_rows) == 4
    assert {row["status"] for row in manifest_rows} == {"dry_run"}
    assert {row["harvesting_profile"] for row in manifest_rows} == {
        runner.HARVESTING_PROFILE
    }
    assert {row["harvesting_profile_fixed"] for row in manifest_rows} == {
        "True"
    }
    assert {row["use_real_solar_data"] for row in manifest_rows} == {"False"}
    assert {row["harvesting_scale"] for row in manifest_rows} == {"1.0"}
    assert {row["actual_utilization_tolerance_total"] for row in manifest_rows} == {""}
    assert {
        row["rta_initial_energy_semantics"] for row in manifest_rows
    } == {runner.E0_SEMANTICS}
    assert {
        (
            float(row["normalized_utilization"]),
            float(row["total_utilization"]),
        )
        for row in manifest_rows
    } == {(0.2, 0.4), (0.4, 0.8)}
    assert read_rows(results_path) == []
    assert "use_real_solar_data" in read_header(
        run_dir / runner.MANIFEST_FILENAME
    )
    assert "harvesting_scale" in read_header(
        run_dir / runner.MANIFEST_FILENAME
    )
    assert read_header(results_path) == runner.RESULT_FIELDS
    assert "use_real_solar_data" in read_header(results_path)
    assert "harvesting_scale" in read_header(results_path)
    assert not (run_dir / "configs").exists()
    assert not (run_dir / "tasks").exists()


def test_e0_dry_run_reuses_family_identity_and_is_deterministic(tmp_path):
    arguments = base_args(tmp_path, "e4-e0-dry", "e0") + [
        "--e0-values", "0,5,10",
        "--num-tasksets", "2",
        "--dry-run",
    ]
    args = parse_and_validate(arguments)
    first = runner.build_specs(args, tmp_path / "planned")
    second = runner.build_specs(args, tmp_path / "planned")
    assert [
        (
            row["seed"],
            row["config_id"],
            row["taskset_family_id"],
            row["task_file"],
        )
        for row in first
    ] == [
        (
            row["seed"],
            row["config_id"],
            row["taskset_family_id"],
            row["task_file"],
        )
        for row in second
    ]

    results_path = runner.main(arguments)
    rows = read_rows(tmp_path / "e4-e0-dry" / runner.MANIFEST_FILENAME)
    assert len(rows) == 3 * 2
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["taskset_family_id"]].append(row)
    assert len(grouped) == 2
    for family_rows in grouped.values():
        assert len(family_rows) == 3
        assert len({row["taskset_id"] for row in family_rows}) == 1
        assert len({row["seed"] for row in family_rows}) == 1
        assert len({row["task_file"] for row in family_rows}) == 1
        assert len({row["config_id"] for row in family_rows}) == 3
        assert {row["task_file_sha256"] for row in family_rows} == {""}
    assert read_rows(results_path) == []


def test_e0_run_generates_each_family_once_and_reuses_task_hash(tmp_path):
    arguments = base_args(tmp_path, "e4-e0-run", "e0") + [
        "--e0-values", "0,5,10",
        "--num-tasksets", "2",
    ]
    with mock.patch.object(
        runner, "_generate_taskset", side_effect=write_mock_task
    ) as generation_mock, mock.patch.object(
        runner.acceptance,
        "run_asap_block_rta",
        return_value=successful_rta_result(),
    ) as rta_mock, mock.patch.object(
        runner.acceptance, "run_single_simulation_worker"
    ) as simulation_mock:
        results_path = runner.main(arguments)

    assert generation_mock.call_count == 2
    assert rta_mock.call_count == 6
    simulation_mock.assert_not_called()
    rows = read_rows(results_path)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["taskset_family_id"]].append(row)
    assert len(grouped) == 2
    for family_rows in grouped.values():
        assert len(family_rows) == 3
        assert len({row["seed"] for row in family_rows}) == 1
        assert len({row["task_file"] for row in family_rows}) == 1
        assert len({row["task_file_sha256"] for row in family_rows}) == 1
        assert next(iter({
            row["task_file_sha256"] for row in family_rows
        }))
        assert len({row["config_file"] for row in family_rows}) == 3
        assert len({row["config_id"] for row in family_rows}) == 3
    assert {
        call.kwargs["initial_energy"] for call in rta_mock.call_args_list
    } == {0.0, 5.0, 10.0}


def test_utilization_run_passes_normalized_times_m_and_writes_real_m(
    tmp_path,
):
    arguments = base_args(tmp_path, "e4-util-run", "utilization") + [
        "--M", "3",
        "--utilizations", "0.2,0.4",
        "--profile-rta",
    ]
    generator_commands = []

    def fake_generator(command, **_kwargs):
        generator_commands.append(command)
        output = Path(command[command.index("-o") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("tasks: []\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with mock.patch.object(
        runner.subprocess, "run", side_effect=fake_generator
    ), mock.patch.object(
        runner.acceptance,
        "run_asap_block_rta",
        side_effect=[
            successful_rta_result(profile=True),
            timeout_rta_result(profile=True),
        ],
    ) as rta_mock, mock.patch.object(
        runner.acceptance, "run_single_simulation_worker"
    ) as simulation_mock:
        results_path = runner.main(arguments)

    simulation_mock.assert_not_called()
    assert len(generator_commands) == 2
    assert {
        command[command.index("-u") + 1] for command in generator_commands
    } == {"0.6", "1.2"}
    assert {
        command[command.index("-c") + 1] for command in generator_commands
    } == {"3"}

    rows = read_rows(results_path)
    assert [
        (
            float(row["normalized_utilization"]),
            float(row["total_utilization"]),
        )
        for row in rows
    ] == [(0.2, 0.6), (0.4, 1.2)]
    assert len({row["seed"] for row in rows}) == 2
    assert rows[0]["rta_proven"] == "True"
    assert rows[0]["rta_schedulable"] == "True"
    assert float(rows[0]["rta_response_bound"]) == 9.0
    assert float(rows[0]["rta_runtime_sec"]) == 0.25
    assert float(rows[0]["rta_profile_task_time_sum_sec"]) == 0.2
    assert rows[1]["rta_timed_out"] == "True"
    assert rows[1]["result_status"] == "rta_timeout"
    assert float(rows[1]["rta_runtime_sec"]) == 7.01
    assert rows[1]["rta_profile_task_time_sum_sec"] == ""
    assert {row["use_real_solar_data"] for row in rows} == {"False"}
    assert {row["harvesting_scale"] for row in rows} == {"1.0"}
    assert {
        row["rta_initial_energy_semantics"] for row in rows
    } == {runner.E0_SEMANTICS}

    for call in rta_mock.call_args_list:
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


def test_task_generation_failure_retains_manifest_and_result_row(tmp_path):
    arguments = base_args(tmp_path, "e4-generation-error", "utilization")
    with mock.patch.object(
        runner, "_generate_taskset", return_value="generator failed"
    ), mock.patch.object(
        runner.acceptance, "run_asap_block_rta"
    ) as rta_mock:
        results_path = runner.main(arguments)

    rta_mock.assert_not_called()
    result = read_rows(results_path)[0]
    manifest = read_rows(
        tmp_path / "e4-generation-error" / runner.MANIFEST_FILENAME
    )[0]
    assert result["result_status"] == "task_generation_error"
    assert result["result_error"] == "generator failed"
    assert result["rta_attempted"] == "False"
    assert result["rta_proven"] == "False"
    assert manifest["status"] == "task_generation_error"
    assert manifest["error"] == "generator failed"


def test_runner_is_v20p4_rta_only_without_pessimism_or_acceptance_fields():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert runner.RTA_VERSION == "v20.4"
    assert runner.RTA_TOOL == "asap_block_rta.py"
    assert Path(runner.acceptance.RTA_TOOL).name == "asap_block_rta.py"
    assert "asap_block_rta_" + "v21" not in source
    assert "v21_" + "local" not in source
    assert "pessimism" not in runner.RESULT_FIELDS
    assert "acceptance_ratio" not in runner.RESULT_FIELDS
    assert "observed_max_response_time" not in runner.RESULT_FIELDS
