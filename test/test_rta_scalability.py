import csv
import subprocess
from pathlib import Path
from unittest import mock

import pytest
import yaml

from scripts import run_rta_scalability as runner


def cli_args(output_root, name="e5-test"):
    return [
        "--output-root", str(output_root),
        "--experiment-name", name,
        "--task-n-values", "2",
        "--m-values", "1",
        "--utilizations", "0.2",
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


def read_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_header(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


def parse_and_validate(arguments):
    parser = runner.build_parser()
    args = parser.parse_args(arguments)
    runner.validate_args(parser, args)
    return args


def test_parser_accepts_comma_lists_and_rejects_invalid_values(tmp_path):
    args = parse_and_validate([
        "--output-root", str(tmp_path),
        "--experiment-name", "e5-parse",
        "--task-n-values", "4,8,16",
        "--m-values", "2,4",
        "--utilizations", "0.2,0.4,1.5",
        "--utilization-mode", "total",
        "--rta-horizon-ms", "100",
    ])
    assert args.task_n_values == [4, 8, 16]
    assert args.m_values == [2, 4]
    assert args.utilizations == [0.2, 0.4, 1.5]
    assert args.utilization_mode == "total"
    assert args.max_workers == 1
    assert not args.profile_rta

    parser = runner.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--experiment-name", "bad",
            "--task-n-values", "4,0",
            "--rta-horizon-ms", "100",
        ])
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--experiment-name", "bad",
            "--m-values", "0",
            "--rta-horizon-ms", "100",
        ])
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--experiment-name", "bad",
            "--utilizations", "0",
            "--rta-horizon-ms", "100",
        ])


def test_dry_run_writes_full_manifest_and_header_only_results(tmp_path):
    arguments = cli_args(tmp_path, "e5-dry") + [
        "--task-n-values", "2,4",
        "--m-values", "1,2",
        "--utilizations", "0.2,0.4",
        "--num-tasksets", "2",
        "--dry-run",
    ]
    with mock.patch.object(
        runner.acceptance, "run_asap_block_rta"
    ) as rta_mock, mock.patch.object(
        runner, "_generate_taskset"
    ) as generation_mock:
        results_path = runner.main(arguments)

    rta_mock.assert_not_called()
    generation_mock.assert_not_called()
    run_dir = tmp_path / "e5-dry"
    manifest = run_dir / runner.MANIFEST_FILENAME
    assert results_path == run_dir / runner.RESULTS_FILENAME
    rows = read_rows(manifest)
    assert len(rows) == 2 * 2 * 2 * 2
    assert {row["status"] for row in rows} == {"dry_run"}
    assert {row["dry_run"] for row in rows} == {"True"}
    assert {row["utilization_mode"] for row in rows} == {"normalized"}
    assert {row["task_util_min"] for row in rows} == {"0.01"}
    assert {row["task_util_max"] for row in rows} == {"0.8"}
    assert {row["wcet_rounding"] for row in rows} == {"floor"}
    assert {row["deadline_mode"] for row in rows} == {"implicit"}
    assert {row["actual_utilization_tolerance_total"] for row in rows} == {""}
    assert read_rows(results_path) == []
    assert read_header(results_path) == runner.RESULT_FIELDS
    assert not (run_dir / "configs").exists()
    assert not (run_dir / "tasks").exists()


def test_config_ids_and_seeds_are_deterministic_and_distinguishable(tmp_path):
    args = parse_and_validate(
        cli_args(tmp_path) + [
            "--task-n-values", "2,4",
            "--m-values", "1,2",
            "--utilizations", "0.2,0.4",
            "--num-tasksets", "2",
        ]
    )
    first = runner.build_specs(args, tmp_path / "run")
    second = runner.build_specs(args, tmp_path / "run")

    identity = [
        (row["config_id"], row["taskset_id"], row["seed"])
        for row in first
    ]
    assert identity == [
        (row["config_id"], row["taskset_id"], row["seed"])
        for row in second
    ]
    assert len(identity) == len(set(identity))
    assert len({row["config_id"] for row in first}) == 2 * 2 * 2
    assert len({row["seed"] for row in first}) == len(first)


def test_default_utilization_mode_is_normalized(tmp_path):
    args = parse_and_validate(
        cli_args(tmp_path) + [
            "--task-n-values", "2",
            "--m-values", "4",
            "--utilizations", "0.5",
            "--wcet-rounding", "compensated",
            "--actual-utilization-tolerance-total", "0.01",
        ]
    )
    spec = runner.build_specs(args, tmp_path / "run")[0]

    assert spec["utilization_mode"] == "normalized"
    assert spec["utilization"] == 0.5
    assert spec["target_normalized_utilization"] == 0.5
    assert spec["target_total_utilization"] == 2.0
    assert spec["wcet_rounding"] == "compensated"
    assert spec["actual_utilization_tolerance_total"] == 0.01


def test_total_utilization_mode_preserves_legacy_semantics(tmp_path):
    args = parse_and_validate(
        cli_args(tmp_path) + [
            "--task-n-values", "2",
            "--m-values", "4",
            "--utilizations", "2.0",
            "--utilization-mode", "total",
        ]
    )
    spec = runner.build_specs(args, tmp_path / "run")[0]

    assert spec["utilization_mode"] == "total"
    assert spec["utilization"] == 2.0
    assert spec["target_total_utilization"] == 2.0
    assert spec["target_normalized_utilization"] == 0.5


def test_write_system_config_sets_real_numcpus_and_asap_block(tmp_path):
    output = tmp_path / "system_m3.yml"
    runner.write_system_config(runner.DEFAULT_SYSTEM_TEMPLATE, output, 3)

    with output.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    assert config["cpu_islands"][0]["numcpus"] == 3
    assert (
        config["cpu_islands"][0]["kernel"]["scheduler"]
        == runner.acceptance.ASAP_BLOCK_ALGORITHM
    )


def test_task_generator_receives_real_m_utilization_and_seed(tmp_path):
    args = parse_and_validate(
        cli_args(tmp_path) + [
            "--task-n-values", "4",
            "--m-values", "3",
            "--utilizations", "1.5",
            "--utilization-mode", "total",
            "--wcet-rounding", "compensated",
            "--actual-utilization-tolerance-total", "0.01",
        ]
    )
    spec = runner.build_specs(args, tmp_path / "run")[0]

    def fake_run(command, **_kwargs):
        Path(spec["task_file"]).parent.mkdir(parents=True, exist_ok=True)
        Path(spec["task_file"]).write_text("tasks: []\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with mock.patch.object(
        runner.subprocess, "run", side_effect=fake_run
    ) as run_mock:
        error = runner._generate_taskset(spec)

    assert error == ""
    command = run_mock.call_args.args[0]
    assert command[command.index("-c") + 1] == "3"
    assert command[command.index("-u") + 1] == "1.5"
    assert command[command.index("--seed") + 1] == str(spec["seed"])
    assert command[command.index("--min-task-util") + 1] == "0.01"
    assert command[command.index("--max-task-util") + 1] == "0.8"
    assert command[command.index("--wcet-rounding") + 1] == "compensated"
    assert (
        command[command.index("--actual-utilization-tolerance-total") + 1]
        == "0.01"
    )


def test_mocked_run_propagates_success_timeout_profile_and_skips_simulation(
    tmp_path,
):
    success = {
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
        "rta_profile_enabled": True,
        "rta_profile_task_time_sum_sec": 0.2,
        "rta_profile_task_count": 2,
    }
    timeout = {
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
        "rta_profile_enabled": True,
        "rta_profile_task_time_sum_sec": None,
        "rta_profile_task_count": 0,
    }
    arguments = cli_args(tmp_path, "e5-mocked") + [
        "--num-tasksets", "2",
        "--profile-rta",
    ]
    with mock.patch.object(
        runner, "_generate_taskset", return_value=""
    ), mock.patch.object(
        runner.acceptance,
        "run_asap_block_rta",
        side_effect=[success, timeout],
    ) as rta_mock, mock.patch.object(
        runner.acceptance, "run_single_simulation_worker"
    ) as simulation_mock:
        results_path = runner.main(arguments)

    simulation_mock.assert_not_called()
    assert rta_mock.call_count == 2
    for call in rta_mock.call_args_list:
        assert call.kwargs["algorithm"] == runner.acceptance.ASAP_BLOCK_ALGORITHM
        assert call.kwargs["timeout"] == 7.0
        assert call.kwargs["profile_rta"] is True
        with Path(call.kwargs["system_config"]).open(
            "r", encoding="utf-8"
        ) as handle:
            assert yaml.safe_load(handle)["cpu_islands"][0]["numcpus"] == 1

    rows = read_rows(results_path)
    assert len(rows) == 2
    assert rows[0]["rta_version"] == "v20.4"
    assert rows[0]["rta_tool"] == "asap_block_rta.py"
    assert rows[0]["rta_proven"] == "True"
    assert rows[0]["rta_schedulable"] == "True"
    assert float(rows[0]["rta_response_bound"]) == 9.0
    assert float(rows[0]["rta_runtime_sec"]) == 0.25
    assert float(rows[0]["rta_profile_task_time_sum_sec"]) == 0.2
    assert int(rows[0]["rta_profile_task_count"]) == 2
    assert rows[1]["rta_status"] == "rta_error"
    assert rows[1]["rta_proven"] == "False"
    assert rows[1]["rta_timed_out"] == "True"
    assert float(rows[1]["rta_runtime_sec"]) == 7.01
    assert rows[1]["rta_profile_task_time_sum_sec"] == ""
    assert int(rows[1]["rta_profile_task_count"]) == 0


def test_runner_is_pinned_to_default_v20p4_without_v21_tool_reference():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert runner.RTA_VERSION == "v20.4"
    assert runner.RTA_TOOL == "asap_block_rta.py"
    assert runner.acceptance.RTA_VERSION == "v20.4"
    assert Path(runner.acceptance.RTA_TOOL).name == "asap_block_rta.py"
    assert "asap_block_rta_" + "v21" not in source
    assert "v21_" + "local" not in source
