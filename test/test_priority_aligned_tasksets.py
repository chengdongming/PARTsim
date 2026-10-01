"""Generation compatibility, full-load material constraints, and opt-in wiring.

The small synthetic service fixture accelerates material tests; it is never
used as experimental energy data or as evidence of scheduler superiority.
"""

import csv
import json
from fractions import Fraction
from pathlib import Path

import pytest

from experiments.v9_3 import priority_aligned as profile
from experiments.v9_3 import scheduler_load_cross as experiment
from experiments.v9_3.cell_model import expand_cells
from experiments.v9_3.simulation_engine import render_system_projection
from experiments.v9_3.taskset_store import ServiceCurveMaterial
from experiments.v9_3.taskset_store import TasksetStoreError
from scripts import analyze_scheduler_load_cross as analyzer
from scripts import run_scheduler_load_cross as runner
from scripts import stitch_a_implicit_standardized as stitcher


def small_service(config, root):
    root.mkdir(parents=True, exist_ok=True)
    system = root / "system_config.yaml"
    spec = config["energy"]["service_curve"]
    system.write_text(render_system_projection(
        Path(__file__).resolve().parents[1] / spec["system_template"],
        processors=4, initial_battery=Fraction(config["energy"]["simulation_initial_battery"]),
        battery_capacity=Fraction(config["energy"]["battery_capacity"]), service_curve=spec,
    ))
    return ServiceCurveMaterial(tuple(Fraction(i, 1000) for i in range(200)),
                                "material-unit-test-service", "material test only", system)


def materialize(root, **overrides):
    args = dict(seed=20261003, utilizations=[Fraction(i, 10) for i in range(1, 10)],
                count=2, processors=4, tasks=10, period_min=40, period_max=200,
                min_task_util=Fraction(1, 100), max_task_util=Fraction(4, 5),
                tolerance=Fraction(1, 100), deadline_mode="implicit", initial_energy_rule="zero")
    args.update(overrides)
    return experiment.materialize_tasksets(root, **args)


@pytest.fixture
def generated(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "prepare_service_curve", small_service)
    return materialize(tmp_path / "new", taskset_profile="priority-aligned")[0]


def test_ordinary_generation_dimensions_and_seeds_remain_identical():
    args = dict(seed=20260906, utilizations=[Fraction(1, 2)], count=1,
                processors=4, tasks=10, period_min=40, period_max=200,
                min_task_util=Fraction(1, 100), max_task_util=Fraction(4, 5),
                tolerance=Fraction(1, 100), deadline_mode="implicit")
    default = experiment._config(**args)
    explicit = experiment._config(**args, taskset_profile="ordinary")
    assert default == explicit
    assert "taskset_profile" not in default["generation"]
    assert expand_cells(default) == expand_cells(explicit)


def test_ordinary_material_matches_prechange_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "prepare_service_curve", small_service)
    expected = json.loads((Path(__file__).parent / "fixtures" / "ordinary_profile_golden.json").read_text())
    for mode in ("constrained", "implicit"):
        rows, _ = materialize(tmp_path / mode, seed=20260906, count=1,
                              utilizations=[Fraction(1, 10), Fraction(1, 2), Fraction(9, 10)],
                              deadline_mode=mode)
        for row in rows:
            document = json.loads(row.canonical_path.read_text())
            key = mode + ":" + document["generation_parameters"]["utilization"]
            assert document["generation_id"] == expected[key]["generation_id"]
            assert document["seed"] == expected[key]["seed"]
            assert document["tasks"] == expected[key]["tasks"]
            assert "taskset_profile" not in row.generated_row()


def test_all_uc_points_have_real_low_priority_work(generated):
    assert len(generated) == 18
    for row in generated:
        document = json.loads(row.canonical_path.read_text())
        detail = document["generation_parameters"]["priority_aligned_material"]
        features = detail["features"]
        tasks = document["tasks"]
        assert min(t["C"] for t in tasks[4:]) >= 2
        assert min(Fraction(t["C"], t["T"]) for t in tasks) >= Fraction(1, 100)
        assert Fraction(features["low_group_share"]) >= Fraction(1, 5)
        assert abs(row.actual_utilization - row.target_utilization) <= Fraction(1, 100)
        assert abs(row.actual_utilization - Fraction(features["source_total_utilization"])) <= Fraction(1, 10000)
        assert detail["accepted_attempt"] < profile.DEFAULT_PARAMETERS["max_attempts"]
        assert len(detail["rejected_material_reasons"]) == detail["accepted_attempt"]


def test_same_seed_material_is_stable_across_parallel_preparation(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "prepare_service_curve", small_service)
    args = dict(utilizations=[Fraction(1, 10), Fraction(7, 10), Fraction(9, 10)], count=1,
                taskset_profile="priority-aligned")
    serial, _ = materialize(tmp_path / "serial", **args)
    parallel, _ = materialize(tmp_path / "parallel", prepare_workers=2, **args)
    assert [t.semantic_hash for t in serial] == [t.semantic_hash for t in parallel]


def test_custom_parameters_propagate_into_actual_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "prepare_service_curve", small_service)
    options = {"min_low_wcet": 3, "low_group_share_min": "1/4"}
    rows, _ = materialize(tmp_path, utilizations=[Fraction(7, 10)], count=1,
                          taskset_profile="priority-aligned", taskset_profile_options=options)
    row = rows[0]
    assert min(t["C"] for t in row.task_payload[4:]) >= 3
    doc = json.loads(row.canonical_path.read_text())
    assert Fraction(doc["generation_parameters"]["priority_aligned_material"]["features"]["low_group_share"]) >= Fraction(1, 4)
    assert row.taskset_profile["parameters"]["min_low_wcet"] == 3


def test_infeasible_material_stops_without_ordinary_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "prepare_service_curve", small_service)
    with pytest.raises(TasksetStoreError, match="exhausted 2 candidates.*no fallback"):
        materialize(tmp_path, utilizations=[Fraction(1, 10)], count=1,
                    taskset_profile="priority-aligned",
                    taskset_profile_options={"min_low_wcet": 100, "max_attempts": 2})
    assert not list((tmp_path / "tasksets").rglob("taskset_*.json"))


def test_parameters_change_population_but_initial_energy_does_not():
    args = dict(seed=20261003, utilizations=[Fraction(7, 10)], count=1,
                processors=4, tasks=10, period_min=40, period_max=200,
                min_task_util=Fraction(1, 100), max_task_util=Fraction(4, 5),
                tolerance=Fraction(1, 100), deadline_mode="implicit",
                taskset_profile="priority-aligned")
    zero = experiment._config(**args, initial_energy_rule="zero")
    half = experiment._config(**args, initial_energy_rule="battery_capacity/2")
    raised_floor = experiment._config(**args, taskset_profile_options={"min_low_wcet": 3})
    assert expand_cells(zero)[0].generation_id == expand_cells(half)[0].generation_id
    assert expand_cells(zero)[0].generation_id != expand_cells(raised_floor)[0].generation_id


@pytest.mark.parametrize("campaign", [experiment.A_IMPLICIT_UC_FIXED_SUPPLY_CAMPAIGN,
                                      experiment.A_IMPLICIT_UE_SERVICE_SCALING_CAMPAIGN])
def test_cli_json_parameters_reach_both_campaigns_before_simulation(tmp_path, monkeypatch, campaign):
    parameter_file = tmp_path / "parameters.json"
    parameter_file.write_text(json.dumps({"min_low_wcet": 3, "low_group_share_min": "1/4"}))
    output = tmp_path / "run"
    calls = []

    class PreparationReached(Exception):
        pass

    def inspect_materializer(*args, **kwargs):
        calls.append(kwargs)
        raise PreparationReached

    monkeypatch.setattr(experiment, "materialize_tasksets", inspect_materializer)
    with pytest.raises(PreparationReached):
        runner.main(["--output", str(output), "--seed", "20261003",
                     "--experiment-version", "a-implicit", "--campaign", campaign,
                     "--initial-energy-rule", "zero", "--samples-per-cell", "1",
                     "--taskset-profile", "priority-aligned",
                     "--taskset-profile-config", str(parameter_file)])
    config = json.loads((output / "run_config.json").read_text())
    parameters = config["taskset_profile"]["parameters"]
    assert parameters["min_low_wcet"] == 3
    assert parameters["low_group_share_min"] == "1/4"
    assert calls[0]["taskset_profile_options"] == parameters
    assert calls[0]["initial_energy_rule"] == "zero"
    assert config["wholepass_fast_path"] is True
    assert config["full_trace_default"] is False
    assert config["run_identity"] == experiment.run_identity(config)
    ordinary = {k: v for k, v in config.items() if k not in {"taskset_profile", "taskset_population", "run_identity"}}
    assert not runner._resume_configs_match(config, ordinary)
    assert not (output / "results.jsonl").exists()


@pytest.mark.parametrize("parameters", [{"min_low_wcet": 1}, {"low_group_share_min": 0.2},
                                          {"unknown_option": 1}, {"max_attempts": 0}])
def test_invalid_profile_parameters_are_rejected(parameters):
    with pytest.raises(ValueError):
        profile.profile_material("priority-aligned", parameters)


def test_profile_is_opt_in_and_constrained_scope_is_rejected(tmp_path):
    args = runner.make_parser().parse_args(["--output", str(tmp_path), "--seed", "1"])
    assert args.taskset_profile == "ordinary"
    assert args.taskset_profile_config is None
    with pytest.raises(SystemExit, match="A-implicit"):
        runner.main(["--output", str(tmp_path), "--seed", "1", "--taskset-profile", "priority-aligned"])
    assert not (tmp_path / "run_config.json").exists()


def test_new_population_cannot_enter_historical_composite(tmp_path):
    with pytest.raises(SystemExit, match="requires ordinary tasksets"):
        stitcher._common_config_checks({"taskset_profile": profile.profile_material("priority-aligned")}, "source")
    with pytest.raises(SystemExit, match="canonical A-implicit V2"):
        runner.main(["--output", str(tmp_path), "--seed", "1",
                     "--experiment-version", "a-implicit-v1",
                     "--campaign", experiment.A_IMPLICIT_UC_FIXED_SUPPLY_CAMPAIGN,
                     "--taskset-profile", "priority-aligned"])
    assert not (tmp_path / "run_config.json").exists()


def test_analyzer_checks_population_and_actual_material(generated):
    for row in generated:
        config = {"taskset_profile": row.taskset_profile, "processors": 4,
                  "min_task_util": "1/100", "max_task_util": "4/5", "util_tolerance_total": "1/100"}
        analyzer._validate_taskset_profile(config, row.generated_row())
        with pytest.raises(SystemExit, match="differs"):
            analyzer._validate_taskset_profile({}, row.generated_row())


def test_analyzer_rejects_modified_payload(generated):
    row = generated[0]
    config = {"taskset_profile": row.taskset_profile, "processors": 4,
              "min_task_util": "1/100", "max_task_util": "4/5", "util_tolerance_total": "1/100"}
    altered = row.generated_row()
    tasks = json.loads(altered["task_input_json"])
    tasks[4]["C"] = 1
    altered["task_input_json"] = json.dumps(tasks)
    with pytest.raises(SystemExit, match="invalid priority-aligned material"):
        analyzer._validate_taskset_profile(config, altered)


@pytest.mark.parametrize("campaign", [experiment.A_IMPLICIT_UC_FIXED_SUPPLY_CAMPAIGN,
                                      experiment.A_IMPLICIT_UE_SERVICE_SCALING_CAMPAIGN])
def test_analyzer_preserves_profile_in_summary_and_plot(generated, tmp_path, monkeypatch, campaign):
    """Synthetic outcomes exercise reporting only, never scheduler performance."""
    class PreparationReached(Exception):
        pass

    def stop_preparation(*args, **kwargs):
        raise PreparationReached

    monkeypatch.setattr(experiment, "materialize_tasksets", stop_preparation)
    with pytest.raises(PreparationReached):
        runner.main(["--output", str(tmp_path), "--seed", "20261003",
                     "--experiment-version", "a-implicit", "--campaign", campaign,
                     "--initial-energy-rule", "zero", "--samples-per-cell", "1",
                     "--taskset-profile", "priority-aligned"])
    config = json.loads((tmp_path / "run_config.json").read_text())
    spec = experiment.a_implicit_standardized_campaign_spec(campaign)
    ucs = {uc for uc, _ in spec["cells"]}
    tasksets = [r for r in generated if r.taskset_index == 0 and r.target_utilization / 4 in ucs]
    requests = experiment.request_rows(tasksets, spec["cells"], experiment.ALL_SCHEDULERS, 60000,
                                         priority_policy="RM", deadline_mode="implicit",
                                         experiment_name=config["experiment"], campaign=campaign,
                                         energy_control=config["energy_control"],
                                         campaign_contract=config["campaign_contract"])
    results = [{**request, "wholepass": True, "technical_error": None,
                "simulation_status": "SIM_PASS_OBSERVED", "fast_mode": "a_implicit_rm_hardrt_wholepass",
                "outcome": {"outcome_status": "AVAILABLE", "wholepass": True, "taskset_pass": True}}
               for request in requests]
    for filename, rows in (("tasksets.jsonl", [r.generated_row() for r in tasksets]),
                           ("requests.jsonl", requests), ("results.jsonl", results)):
        (tmp_path / filename).write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(analyzer, "_v7_validate_energy", lambda *args, **kwargs: None)
    plotted = []
    monkeypatch.setattr(analyzer, "plot_composite_scan", lambda *args, **kwargs: plotted.append(args))
    report = analyzer._analyze_a_implicit(tmp_path)
    summaries = list(csv.DictReader((tmp_path / "summary.csv").open()))
    assert len(summaries) == 243
    assert {r["taskset_profile"] for r in summaries} == {"priority-aligned"}
    assert report["taskset_profile"] == config["taskset_profile"]
    assert len(plotted) == 1
    assert "priority-aligned tasksets" in plotted[0][6]
