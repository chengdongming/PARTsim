"""Generation compatibility, full-load material constraints, and opt-in wiring.

The small synthetic service fixture accelerates material tests; it is never
used as experimental energy data or as evidence of scheduler superiority.
"""

import csv
import json
from concurrent.futures import Future
from fractions import Fraction
from pathlib import Path

import pytest

from experiments.v9_3 import priority_aligned as profile
from experiments.v9_3 import scheduler_load_cross as experiment
from experiments.v9_3.cell_model import expand_cells
from experiments.v9_3.simulation_engine import render_system_projection
from experiments.v9_3 import simulation_engine
from experiments.v9_3.taskset_store import ServiceCurveMaterial
from experiments.v9_3.taskset_store import TasksetStore, TasksetStoreError
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


def test_retry_default_is_larger_only_for_constrained_dm():
    for mode in ("constrained", "implicit"):
        for policy in ("RM", "DM"):
            material = profile.profile_material("priority-aligned", deadline_mode=mode, priority_policy=policy)
            expected = 512 if (mode, policy) == ("constrained", "DM") else 64
            assert material["parameters"]["max_attempts"] == expected
            explicit = profile.profile_material("priority-aligned", {"max_attempts": 2},
                                                deadline_mode=mode, priority_policy=policy)
            assert explicit["parameters"]["max_attempts"] == 2


def test_constrained_dm_high_uc_regression_preserves_constraints(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "prepare_service_curve", small_service)
    args = dict(seed=20261003, utilizations=[Fraction(4, 5)], count=111,
                processors=4, tasks=10, period_min=40, period_max=200,
                min_task_util=Fraction(1, 100), max_task_util=Fraction(4, 5),
                tolerance=Fraction(1, 100), deadline_mode="constrained", priority_policy="DM",
                taskset_profile="priority-aligned", initial_energy_rule="zero")
    old_config = experiment._config(**args, taskset_profile_options={"max_attempts": 64})
    old = TasksetStore(tmp_path / "old", old_config, small_service(old_config, tmp_path / "old_service"))
    with pytest.raises(TasksetStoreError, match="exhausted 64 candidates.*taskset_index=110.*no fallback"):
        old.get_or_create(expand_cells(old_config)[0], 110)
    fixed_config = experiment._config(**args)
    fixed = TasksetStore(tmp_path / "fixed", fixed_config, small_service(fixed_config, tmp_path / "fixed_service"))
    row = fixed.get_or_create(expand_cells(fixed_config)[0], 110)
    assert row.taskset_profile["parameters"]["max_attempts"] == 512
    assert row.generation_id != expand_cells(old_config)[0].generation_id
    assert all(0 < t["C"] <= t["D"] < t["T"] for t in row.task_payload)
    cfg = {"taskset_profile": row.taskset_profile, "processors": 4, "priority_policy": "DM",
           "min_task_util": "1/100", "max_task_util": "4/5", "util_tolerance_total": "1/100"}
    analyzer._validate_taskset_profile(cfg, row.generated_row())


def test_profile_preparation_failure_checkpoints_and_reuses_validated_material(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "prepare_service_curve", small_service)
    original_prepare = experiment.run_prepare_jobs
    root = tmp_path / "resumed"
    args = dict(utilizations=[Fraction(7, 10)], count=2, prepare_workers=2,
                taskset_profile="priority-aligned")

    def interrupted(jobs, worker, **kwargs):
        kwargs["on_result"](worker(jobs[0]))
        raise TasksetStoreError("injected preparation interruption")

    monkeypatch.setattr(experiment, "run_prepare_jobs", interrupted)
    with pytest.raises(TasksetStoreError, match="injected preparation interruption"):
        materialize(root, **args)
    files = list((root / "tasksets").rglob("taskset_*.json"))
    assert len(files) == 1
    first_path, first_bytes = files[0], files[0].read_bytes()
    retried = []

    def resumed(jobs, worker, **kwargs):
        retried.extend(job["taskset_index"] for job in jobs)
        return original_prepare(jobs, worker, **kwargs)

    monkeypatch.setattr(experiment, "run_prepare_jobs", resumed)
    rows, _ = materialize(root, **args)
    assert len(rows) == 2 and retried == [1]
    assert first_path.read_bytes() == first_bytes
    serial, _ = materialize(tmp_path / "serial", **dict(args, prepare_workers=1))
    assert [r.semantic_hash for r in rows] == [r.semantic_hash for r in serial]


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


def test_profile_is_opt_in_and_v6_scope_is_rejected(tmp_path):
    args = runner.make_parser().parse_args(["--output", str(tmp_path), "--seed", "1"])
    assert args.taskset_profile == "ordinary"
    assert args.taskset_profile_config is None
    with pytest.raises(SystemExit, match="A-implicit"):
        runner.main(["--output", str(tmp_path), "--seed", "1", "--taskset-profile", "priority-aligned"])
    assert not (tmp_path / "run_config.json").exists()


@pytest.fixture(scope="module")
def material_matrix(tmp_path_factory):
    root = tmp_path_factory.mktemp("profile_deadline_priority_matrix")
    matrix = {}
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(experiment, "prepare_service_curve", small_service)
        for mode in ("constrained", "implicit"):
            for policy in ("RM", "DM"):
                for e0 in ("zero", "battery_capacity/2"):
                    key = mode, policy, e0
                    matrix[key] = materialize(root / mode / policy / ("zero" if e0 == "zero" else "half"),
                                              deadline_mode=mode, priority_policy=policy,
                                              initial_energy_rule=e0, prepare_workers=2,
                                              taskset_profile="priority-aligned")
    return matrix


@pytest.mark.parametrize("mode", ["constrained", "implicit"])
@pytest.mark.parametrize("policy", ["RM", "DM"])
@pytest.mark.parametrize("e0", ["zero", "battery_capacity/2"])
def test_full_uc_material_matrix_respects_runtime_order(material_matrix, mode, policy, e0):
    rows, _ = material_matrix[mode, policy, e0]
    assert len(rows) == 18
    for row in rows:
        tasks = row.task_payload
        order = (list(range(10)) if policy == "RM" else
                 sorted(range(10), key=lambda i: (tasks[i]["D"], tasks[i]["T"], tasks[i]["priority_rank"])))
        high, low = order[:4], order[4:]
        document = json.loads(row.canonical_path.read_text())
        features = document["generation_parameters"]["priority_aligned_material"]["features"]
        anchor = order[features["anchor_rank"] - 1]
        assert anchor in high
        assert min(tasks[i]["C"] for i in low) >= 2
        assert sum(Fraction(tasks[i]["C"], tasks[i]["T"]) for i in low) >= row.actual_utilization / 5
        assert all(tasks[i]["C"] < tasks[anchor]["C"] for i in low)
        assert all(tasks[i]["D"] - tasks[i]["C"] > tasks[anchor]["D"] - tasks[anchor]["C"] for i in low)
        assert all(0 < t["C"] <= t["D"] <= t["T"] for t in tasks)
        assert all(t["D"] == t["T"] if mode == "implicit" else t["D"] < t["T"] for t in tasks)
        config = {"taskset_profile": row.taskset_profile, "processors": 4, "priority_policy": policy,
                  "min_task_util": "1/100", "max_task_util": "4/5", "util_tolerance_total": "1/100"}
        analyzer._validate_taskset_profile(config, row.generated_row())


def test_implicit_policies_and_initial_energy_share_material(material_matrix):
    for mode in ("constrained", "implicit"):
        for policy in ("RM", "DM"):
            zero, _ = material_matrix[mode, policy, "zero"]
            half, _ = material_matrix[mode, policy, "battery_capacity/2"]
            assert [r.semantic_hash for r in zero] == [r.semantic_hash for r in half]
    rm, _ = material_matrix["implicit", "RM", "zero"]
    dm, _ = material_matrix["implicit", "DM", "zero"]
    assert [r.semantic_hash for r in rm] == [r.semantic_hash for r in dm]
    rm, _ = material_matrix["constrained", "RM", "zero"]
    dm, _ = material_matrix["constrained", "DM", "zero"]
    assert {r.generation_id for r in rm}.isdisjoint(r.generation_id for r in dm)


@pytest.mark.parametrize("mode", ["constrained", "implicit"])
@pytest.mark.parametrize("policy", ["RM", "DM"])
def test_transform_preserves_source_deadlines_workloads_and_power(material_matrix, tmp_path, mode, policy):
    rows, service = material_matrix[mode, policy, "zero"]
    row = next(r for r in rows if r.target_utilization == 2 and r.taskset_index == 0)
    config = experiment._config(seed=20261003, utilizations=[Fraction(i, 10) for i in range(1, 10)],
                                count=2, processors=4, tasks=10, period_min=40, period_max=200,
                                min_task_util=Fraction(1, 100), max_task_util=Fraction(4, 5),
                                tolerance=Fraction(1, 100), deadline_mode=mode, priority_policy=policy,
                                taskset_profile="priority-aligned", initial_energy_rule="zero")
    store = TasksetStore(tmp_path / "store", config, service)
    cell = next(c for c in expand_cells(config) if c.generation_id == row.generation_id)
    details = json.loads(row.canonical_path.read_text())["generation_parameters"]["priority_aligned_material"]
    _, source = store._generate_payload(cell, details["candidate_seed"])
    assert details["source_wcets"] == [t["C"] for t in source]
    for before, after in zip(source, row.task_payload):
        assert all(before[k] == after[k] for k in ("D", "T", "task_id", "workload", "priority_rank", "arrival_offset"))
        assert abs(Fraction(after["P"]) / Fraction(before["P"]) - 1) <= Fraction(1, 10**12)


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


@pytest.mark.parametrize("mode", ["constrained", "implicit"])
@pytest.mark.parametrize("policy", ["RM", "DM"])
@pytest.mark.parametrize("scan", ["uc", "ue"])
def test_runner_and_analyzer_matrix_uses_compact_results(material_matrix, tmp_path, monkeypatch, mode, policy, scan):
    """Real task/energy/request/report wiring with synthetic simulation outcomes."""
    rows, service = material_matrix[mode, policy, "zero"]
    campaign = ("a-implicit-" if mode == "implicit" else "") + (
        "uc-fixed-supply" if scan == "uc" else "ue-service-scaling")
    version = "a-implicit" if mode == "implicit" else "v8"
    spec = experiment.campaign_spec(version, campaign)
    ucs = {uc for uc, _ in spec["cells"]}
    selected = [r for r in rows if r.taskset_index == 0 and r.target_utilization / 4 in ucs]
    calls = []

    def prepared(*args, **kwargs):
        assert kwargs["deadline_mode"] == mode and kwargs["priority_policy"] == policy
        return selected, service

    generic = mode == "constrained" or policy == "DM"

    def simulated(**kwargs):
        calls.append(kwargs)
        assert kwargs["generic_wholepass_fast"] is generic
        assert kwargs["implicit_wholepass_fast"] is not generic
        assert kwargs["simulation_config"]["trace_mode"] == "none"
        assert kwargs["simulation_config"]["priority_policy"] == policy
        return simulation_engine.WholePassFastExecution(
            {"taskset_pass": True, "completion_reason": "unit_test_only",
             "fast_mode": "generic_hardrt_wholepass" if generic else "a_implicit_rm_hardrt_wholepass"},
            0.0, tmp_path / "unit_test_compact.json")

    class InlineExecutor:
        def __init__(self, **kwargs):
            self._processes = {}

        def submit(self, function, job):
            future = Future()
            future.set_result(function(job))
            return future

        def shutdown(self, **kwargs):
            pass

    monkeypatch.setattr(experiment, "materialize_tasksets", prepared)
    monkeypatch.setattr(experiment, "construct_paired_harvest_trace", lambda path, horizon: (Fraction(1),) * horizon)
    monkeypatch.setattr(runner, "run_paired_simulation", simulated)
    monkeypatch.setattr(runner, "ProcessPoolExecutor", InlineExecutor)
    assert runner.main(["--output", str(tmp_path), "--seed", "20261003", "--workers", "1",
                        "--samples-per-cell", "1", "--experiment-version", version, "--campaign", campaign,
                        "--priority-policy", policy, "--initial-energy-rule", "zero",
                        "--taskset-profile", "priority-aligned"]) == 0
    assert len(calls) == 243
    plotted = []
    monkeypatch.setattr(analyzer, "plot_composite_scan", lambda *args, **kwargs: plotted.append(args))
    report = analyzer.analyze(tmp_path)
    summaries = list(csv.DictReader((tmp_path / "summary.csv").open()))
    assert len(summaries) == 243
    assert {r["deadline_mode"] for r in summaries} == {mode}
    assert {r["priority_policy"] for r in summaries} == {policy}
    assert report["deadline_modes"] == [mode] and report["priority_policy"] == policy
    assert report["wholepass_only"] is True and report["dmr_available"] is False
    assert len(plotted) == 1 and plotted[0][2] == f"figure_scheduler_{scan}_slices.png"
    assert "priority-aligned tasksets" in plotted[0][6]
    assert not list(tmp_path.rglob("*trace*.json"))


@pytest.mark.skipif(not Path("build/rtsim/rtsim").is_file(), reason="build simulator for native equivalence")
@pytest.mark.parametrize("mode", ["constrained", "implicit"])
@pytest.mark.parametrize("policy", ["RM", "DM"])
@pytest.mark.parametrize("scheduler", experiment.ALL_SCHEDULERS)
def test_native_profile_compact_matches_full_trace(material_matrix, tmp_path, mode, policy, scheduler):
    rows, service = material_matrix[mode, policy, "zero"]
    taskset = next(r for r in rows if r.target_utilization == 2 and r.taskset_index == 0)
    results = []
    for compact in (False, True):
        execution = simulation_engine.run_paired_simulation(
            simulation_id_value=f"profile-native-{compact}", base_system_path=service.system_path,
            run_root=tmp_path / str(compact), task_payload=taskset.task_payload,
            taskset_hash=taskset.semantic_hash, processors=4, exact_e0=Fraction(0),
            energy_config={"simulation_initial_battery": "0", "battery_capacity": "1",
                           "allow_harvest_clipping": True,
                           "service_curve": {"solar_scale": "1", "use_real_solar_data": False}},
            simulation_config={"simulator_bin": str(Path("build/rtsim/rtsim").resolve()),
                               "horizon": 400, "maximum_horizon": 400, "horizon_extension_policy": "none",
                               "priority_policy": policy, "deadline_mode": mode,
                               "campaign": "deadline-profile-sensitivity-v1", "wholepass_mode": "hard-rt",
                               "warmup": 0, "minimum_jobs_per_task": 1,
                               "trace_mode": "none" if compact else "semantic", "retain_trace": False,
                               "trace_on_failure": False, "timeout_seconds": 30,
                               "cleanup_transient_artifacts": True},
            scheduler_id=experiment.perf_g.SCHEDULER_CLI[scheduler], generic_wholepass_fast=compact)
        results.append(execution)
    full, compact = results
    assert isinstance(compact, simulation_engine.WholePassFastExecution)
    assert full.result.status.value in {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}
    assert compact.result["taskset_pass"] == (full.result.status.value == "SIM_PASS_OBSERVED")
    if full.result.status.value == "SIM_DEADLINE_MISS":
        # Trace observations are grouped by task, not ordered by miss time.
        missed = min((job for job in full.result.jobs if job.deadline_miss),
                     key=lambda job: job.absolute_deadline)
        first = compact.result["first_deadline_miss"]
        assert first["task_id"] == (missed.task_name or missed.task_id)
        assert first["release"] == missed.release
        assert first["absolute_deadline"] == missed.absolute_deadline
    assert not list(compact.output_path.parent.glob("*trace*.json"))
