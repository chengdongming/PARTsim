from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from experiments.v9_3.result_writer import atomic_write_json
from experiments.v9_3.rta4_formal_config import (
    RTA4_CORES, load_rta4_formal_config,
)
from experiments.v9_3.rta4_formal_environment import (
    build_simulator_manifest, build_source_manifest,
)
from experiments.v9_3.rta4_formal_pilot import (
    RTA4_PILOT_OBSERVATIONS, RTA4_PILOT_OUTPUT_MARKER,
    RTA4_PILOT_REPORT, RTA4PilotError, build_pilot_manifest,
    build_pilot_observations, build_pilot_report,
)
from experiments.v9_3.rta4_formal_plan import (
    iter_core1_plan, iter_core2_plan, iter_core3_plan, iter_core4_plan,
    iter_core5b_plan,
)
from experiments.v9_3.rta4_formal_validation import (
    RTA4FormalValidationError, validate_formal_run_closure,
)
from experiments.v9_3.rta4_formal_store import RTA4FormalTasksetStore
import experiments.v9_3.rta4_pilot_execution as pilot_execution
from experiments.v9_3.rta4_pilot_execution import (
    PilotExecutionRunner, PilotTasksetProvider, RTA4_PILOT_CHECKPOINT,
    RTA4_PILOT_AUDIT, RTA4_PILOT_COMPLETION_SEAL,
    RTA4_PILOT_EXECUTION_CONFIG, RTA4_PILOT_EXECUTION_MANIFEST,
    RTA4_PILOT_FINAL_TERMINAL_DIRECTORY,
    RTA4_PILOT_RAW_TERMINAL_DIRECTORY,
    RTA4_PILOT_TERMINAL_DIRECTORY, RTA4_PILOT_TEST_EXECUTION_CLASS,
    RTA4PilotExecutionError, RTA4PilotExecutionInterrupted,
    _execution_batches,
    audit_pilot_namespace, build_pilot_execution_config,
    build_pilot_final_terminal, compute_pilot_output_io_bytes,
    pilot_final_terminal_preimage, runtime_ci_engineering_warnings,
    validate_pilot_phase_inventory,
    validate_pilot_execution_config,
)
TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from test_v9_3_rta4_formal_authorization import (
    _synthetic_certificate, _synthetic_rta, _synthetic_simulator,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATHS = {
    core: (
        ROOT / "configs"
        / f"v9_3_rta4_{core.lower().replace('-', '')}_unauthorized_pre_pilot_v1.yaml"
    ).resolve()
    for core in RTA4_CORES
}
CONFIGS = {
    core: load_rta4_formal_config(path, expected_core=core)
    for core, path in CONFIG_PATHS.items()
}


def _source_repo(root: Path) -> dict:
    repo = root / "source"
    repo.mkdir()
    source = repo / "entry.txt"
    source.write_text("pilot-source-v1\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(("git", "add", "entry.txt"), cwd=repo, check=True)
    subprocess.run(
        (
            "git", "-c", "user.name=RTA4 Test",
            "-c", "user.email=rta4@example.invalid",
            "commit", "-qm", "fixture",
        ),
        cwd=repo, check=True,
    )
    return build_source_manifest(repo, (source,))


def _context(root: Path, *, counts: int = 1, workers: int = 2):
    output = root / "pilot"
    store = root / "store"
    manifest = build_pilot_manifest(
        CONFIGS,
        core_record_counts={
            core: (counts * 4 if core == "CORE-5B" else counts)
            for core in RTA4_CORES
        },
        selection_seed="RTA4-PILOT-EXECUTION-TEST-V1",
        output_root=output, taskset_store=store,
        config_paths=CONFIG_PATHS,
    )
    output.mkdir(parents=True)
    manifest_path = output / RTA4_PILOT_OUTPUT_MARKER
    atomic_write_json(manifest_path, manifest)
    simulator = root / "fake-simulator"
    simulator.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    simulator.chmod(0o755)
    execution = build_pilot_execution_config(
        manifest_path, manifest,
        source_manifest=_source_repo(root),
        output_root=output, taskset_store=store,
        simulator_manifest=build_simulator_manifest(simulator),
        default_worker_count=workers, max_in_flight=max(3, workers),
        provisional_rta_attempt_timeout_seconds=2,
        provisional_simulation_timeout_seconds=2,
        memory_soft_limit_bytes=1 << 60,
        checkpoint_interval_records=2, maximum_attempts=2,
        execution_class=RTA4_PILOT_TEST_EXECUTION_CLASS,
    )
    atomic_write_json(output / RTA4_PILOT_EXECUTION_CONFIG, execution)
    return {
        "root": root, "output": output, "store": store,
        "manifest": manifest, "execution": execution,
    }


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _hard_exit_rta(_record, _certificate):
    os._exit(37)


@pytest.fixture(scope="module")
def completed_context(tmp_path_factory):
    context = _context(tmp_path_factory.mktemp("rta4-pilot-complete"))
    rta_calls = []
    simulation_calls = []

    def rta(record, certificate):
        rta_calls.append(record.execution_id)
        return _synthetic_rta(record, certificate)

    simulator = _synthetic_simulator(
        context["root"] / "fake-traces"
    )

    def simulation(*args):
        simulation_calls.append(args[0].execution_id)
        return simulator(*args)

    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    first = runner.run(
        certificate_provider=_synthetic_certificate,
        rta_callback=rta, simulation_callback=simulation,
        use_processes=False,
    )
    assert first.complete
    callback_count = len(rta_calls) + len(simulation_calls)
    terminal_before = {
        path.name: path.read_bytes()
        for path in (
            context["output"] / RTA4_PILOT_TERMINAL_DIRECTORY
        ).glob("*.json")
    }
    second = runner.run(
        resume=True, certificate_provider=_synthetic_certificate,
        rta_callback=rta, simulation_callback=simulation,
        use_processes=False,
    )
    assert second.complete and second.processed_count == 0
    assert len(rta_calls) + len(simulation_calls) == callback_count
    assert terminal_before == {
        path.name: path.read_bytes()
        for path in (
            context["output"] / RTA4_PILOT_TERMINAL_DIRECTORY
        ).glob("*.json")
    }
    context.update({
        "summary": first, "audit": first.audit,
        "callback_count": callback_count,
    })
    return context


def test_plan_only_selection_is_deterministic_and_exact(tmp_path):
    left = build_pilot_manifest(
        CONFIGS, core_record_counts={
            core: (8 if core == "CORE-5B" else 2)
            for core in RTA4_CORES
        },
        selection_seed="RTA4-PLAN-ONLY-V1",
        output_root=tmp_path / "output", taskset_store=tmp_path / "store",
        config_paths=CONFIG_PATHS,
    )
    right = build_pilot_manifest(
        CONFIGS, core_record_counts={
            core: (8 if core == "CORE-5B" else 2)
            for core in RTA4_CORES
        },
        selection_seed="RTA4-PLAN-ONLY-V1",
        output_root=tmp_path / "output", taskset_store=tmp_path / "store",
        config_paths=CONFIG_PATHS,
    )
    assert left == right
    for core in RTA4_CORES:
        assert len(left["selected_records"][core]) == (
            8 if core == "CORE-5B" else 2
        )
        for row in left["selected_records"][core]:
            assert set(row) == {
                "ordinal", "plan_record_id", "kind",
                "mathematical_request_id", "execution_id", "method",
                "taskset_skeleton_slot_id", "taskset_slot_id",
                "worker_count", "selection_key",
            }


def test_shared_provider_reuses_cross_core_slot_certificates_without_generation():
    provider = PilotTasksetProvider(CONFIGS)
    core1 = next(iter_core1_plan())
    core2 = next(iter_core2_plan())
    core3 = next(iter_core3_plan())
    assert core1.taskset_slot_id == core2.taskset_slot_id
    assert core1.taskset_slot_id == core3.taskset_slot_id
    certificate = _synthetic_certificate(core1)
    provider._provider._tasksets[str(core1.taskset_slot_id)] = certificate
    assert provider(core1) is certificate
    assert provider(core2) is certificate
    assert provider(core3) is certificate

    core5b = next(iter_core5b_plan())
    core4 = next(
        row for row in iter_core4_plan()
        if row.mathematical_request_id == core5b.mathematical_request_id
    )
    certificate4 = _synthetic_certificate(core4)
    provider._provider._tasksets[str(core4.taskset_slot_id)] = certificate4
    assert provider(core4) is certificate4
    assert provider(core5b) is certificate4

    dependent_first = PilotTasksetProvider(CONFIGS)
    generated_from_dependent = dependent_first(core2)
    assert dependent_first(core1) is generated_from_dependent
    independently_generated = PilotTasksetProvider(CONFIGS)(core1)
    assert generated_from_dependent.canonical_bytes() == (
        independently_generated.canonical_bytes()
    )

    core5b_dependent_first = PilotTasksetProvider(CONFIGS)
    generated_from_core5b = core5b_dependent_first(core5b)
    assert core5b_dependent_first(core4) is generated_from_core5b
    independently_generated_core4 = PilotTasksetProvider(CONFIGS)(core4)
    assert generated_from_core5b.canonical_bytes() == (
        independently_generated_core4.canonical_bytes()
    )


def test_core5b_batches_honor_manifest_worker_conditions_in_order():
    records = list(iter_core5b_plan())[:4]
    assert [record.material["worker_count"] for record in records] == [
        1, 2, 4, 8,
    ]
    batches = list(_execution_batches(
        records, max_in_flight=4, default_workers=3,
    ))
    assert [workers for workers, _batch in batches] == [1, 2, 4, 8]
    assert [
        record.execution_id
        for _workers, batch in batches for record in batch
    ] == [record.execution_id for record in records]


def test_process_worker_and_parent_only_partial_persistence(tmp_path):
    context = _context(tmp_path, workers=1)
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    summary = runner.run(
        max_records=1, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta, use_processes=True,
    )
    assert not summary.complete
    terminals = tuple(
        (
            context["output"] / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
        ).glob("*.json")
    )
    assert len(terminals) == 1
    assert not (context["output"] / RTA4_PILOT_OBSERVATIONS).exists()
    assert not (context["output"] / RTA4_PILOT_REPORT).exists()
    checkpoint = json.loads(
        (context["output"] / RTA4_PILOT_CHECKPOINT).read_text()
    )
    assert checkpoint["state"] == "INCOMPLETE_PILOT"
    generation = json.loads(
        (
            context["output"] / "rta4_pilot_checkpoints"
            / checkpoint["checkpoint_filename"]
        ).read_text()
    )
    assert generation["completed_raw_count"] == 1
    assert audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
    )["freeze_eligible"] is False


def test_process_transport_failure_becomes_engineering_error(tmp_path):
    context = _context(tmp_path, workers=1)
    summary = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    ).run(
        max_records=1, certificate_provider=_synthetic_certificate,
        rta_callback=lambda _record, _certificate: {},
        use_processes=True,
    )
    assert not summary.complete and summary.processed_count == 1
    terminal_path = next(
        (
            context["output"] / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
        ).glob("*.json")
    )
    terminal = json.loads(terminal_path.read_text())
    assert terminal["engineering_error"] is True
    assert terminal["attempt_count"] == 0


def test_all_core_callbacks_resume_and_engineering_only_report(
    completed_context,
):
    output = completed_context["output"]
    assert completed_context["callback_count"] == 9
    audit = audit_pilot_namespace(output, CONFIGS)
    assert audit["checkpoint_state"] == "PILOT_COMPLETE"
    assert audit["execution_class"] == RTA4_PILOT_TEST_EXECUTION_CLASS
    assert audit["freeze_eligible"] is False
    assert not (output / ".rta4_pilot_worker_traces").exists()
    observations = json.loads(
        (output / RTA4_PILOT_OBSERVATIONS).read_text()
    )
    assert observations["observation_count"] == 9
    assert {row["core"] for row in observations["observations"]} == set(
        RTA4_CORES
    )
    assert all(
        row["ci_width_engineering_warning"]
        for row in observations["observations"]
    )
    report = json.loads((output / RTA4_PILOT_REPORT).read_text())
    assert report["scientific_results_included"] is False
    assert len(report["engineering_metrics"]["strata"]) == 9
    forbidden = {
        "schedulability", "candidate", "witness", "response_time",
        "proven_count", "method_rank", "scientific_result",
    }
    serialized_keys = {
        key
        for mapping in _walk_mappings(report)
        for key in mapping
    }
    assert not forbidden.intersection(serialized_keys)


def _walk_mappings(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_mappings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_mappings(item)


def test_validate_only_is_byte_for_byte_read_only(completed_context):
    before = _file_bytes(completed_context["output"])
    runner = PilotExecutionRunner(
        CONFIGS, completed_context["manifest"],
        completed_context["execution"],
    )
    summary = runner.run(resume=True, validate_only=True)
    assert summary.complete and summary.processed_count == 0
    assert _file_bytes(completed_context["output"]) == before
    command = [
        sys.executable, str(ROOT / "scripts" / "run_v9_3_rta4_pilot.py"),
        "--validate-only",
        "--output-root", str(completed_context["output"]),
    ]
    for core in RTA4_CORES:
        command.extend(("--config", f"{core}={CONFIG_PATHS[core]}"))
    result = subprocess.run(
        command, cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mode"] == "validate-only"
    assert _file_bytes(completed_context["output"]) == before


@pytest.mark.parametrize("damage", ["missing", "extra", "terminal", "checkpoint"])
def test_audit_rejects_missing_extra_or_damaged_evidence(
    completed_context, damage,
):
    output = completed_context["output"]
    terminals = sorted(
        (output / RTA4_PILOT_TERMINAL_DIRECTORY).glob("*.json")
    )
    checkpoint = output / RTA4_PILOT_CHECKPOINT
    saved = {
        path: path.read_bytes() for path in (*terminals, checkpoint)
    }
    extra = (
        output / RTA4_PILOT_TERMINAL_DIRECTORY / f"{'f' * 64}.json"
    )
    try:
        if damage == "missing":
            terminals[0].unlink()
        elif damage == "extra":
            shutil.copyfile(terminals[0], extra)
        elif damage == "terminal":
            payload = json.loads(terminals[0].read_text())
            payload["runtime_wall_milliseconds"] += 1
            atomic_write_json(terminals[0], payload)
        else:
            payload = json.loads(checkpoint.read_text())
            payload["checkpoint_pointer_sha256"] = "0" * 64
            atomic_write_json(checkpoint, payload)
        with pytest.raises(RTA4PilotExecutionError):
            audit_pilot_namespace(output, CONFIGS)
    finally:
        extra.unlink(missing_ok=True)
        for path, payload in saved.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)


@pytest.mark.parametrize(
    "drift", ["extra-certificate", "worker-temporary", "root-extra"],
)
def test_audit_rejects_extra_persistence_namespace(
    completed_context, drift,
):
    output = completed_context["output"]
    if drift == "extra-certificate":
        certificate_root = completed_context["store"] / "certificates"
        target = certificate_root / f"{'f' * 64}.json"
        source = next(certificate_root.glob("*.json"))
        target.write_bytes(source.read_bytes())
    elif drift == "worker-temporary":
        temporary = output / ".rta4_pilot_worker_traces"
        temporary.mkdir()
        target = temporary / "stale.json"
        target.write_text("{}\n", encoding="utf-8")
    else:
        target = output / "unexpected-pilot-evidence.json"
        target.write_text("{}\n", encoding="utf-8")
    try:
        with pytest.raises(RTA4PilotExecutionError):
            audit_pilot_namespace(output, CONFIGS)
    finally:
        target.unlink(missing_ok=True)
        if drift == "worker-temporary":
            target.parent.rmdir()


def test_simulator_sha_and_source_drift_fail_before_execution(tmp_path):
    context = _context(tmp_path)
    simulator = Path(
        context["execution"]["simulator_manifest"]["absolute_path"]
    )
    simulator.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    simulator.chmod(0o755)
    with pytest.raises(RTA4PilotExecutionError, match="simulator"):
        validate_pilot_execution_config(
            context["execution"], context["manifest"],
        )

    simulator.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    simulator.chmod(0o755)
    source = (
        Path(context["execution"]["source_manifest"]["repository_root"])
        / "entry.txt"
    )
    source.write_text("drift\n", encoding="utf-8")
    with pytest.raises(RTA4PilotExecutionError, match="source|config"):
        validate_pilot_execution_config(
            context["execution"], context["manifest"],
        )


def test_runtime_ci_rule_is_fixed_order_independent_and_runtime_only():
    rows = [
        {
            "plan_record_id": hashlib.sha256(str(index).encode()).hexdigest(),
            "core": "CORE-1", "method": "CW_THETA_CW",
            "worker_count": 1, "runtime_wall_milliseconds": 100,
            "scientific_noise": index % 2,
        }
        for index in range(8)
    ]
    forward = runtime_ci_engineering_warnings("a" * 64, rows)
    reverse = runtime_ci_engineering_warnings(
        "a" * 64, list(reversed(rows)),
    )
    assert forward == reverse
    assert set(forward.values()) == {False}
    changed = deepcopy(rows)
    for row in changed:
        row["scientific_noise"] = 1000 - row["scientific_noise"]
    assert runtime_ci_engineering_warnings("a" * 64, changed) == forward


def test_observation_and_report_digest_ignore_input_order(
    completed_context,
):
    manifest = completed_context["manifest"]
    observed = json.loads(
        (
            completed_context["output"] / RTA4_PILOT_OBSERVATIONS
        ).read_text()
    )
    wrapper = {
        "observation_id", "pilot_manifest_id", "selection_key", "core",
        "method", "taskset_skeleton_slot_id", "taskset_slot_id",
    }
    raw = [
        {key: value for key, value in row.items() if key not in wrapper}
        for row in observed["observations"]
    ]
    rebuilt = build_pilot_observations(manifest, list(reversed(raw)))
    assert rebuilt == observed
    assert build_pilot_report(manifest, rebuilt) == json.loads(
        (completed_context["output"] / RTA4_PILOT_REPORT).read_text()
    )


def test_interruption_checkpoint_is_resumable(tmp_path):
    context = _context(tmp_path)
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    with pytest.raises(RTA4PilotExecutionInterrupted):
        runner.run(
            certificate_provider=_synthetic_certificate,
            rta_callback=_synthetic_rta,
            simulation_callback=_synthetic_simulator(
                context["root"] / "fake-traces"
            ),
            use_processes=False, interrupt_after=2,
        )
    audit = audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
    )
    assert audit["checkpoint_state"] == "INCOMPLETE_PILOT"
    resumed = runner.run(
        resume=True, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta,
        simulation_callback=_synthetic_simulator(
            context["root"] / "fake-traces-resume"
        ),
        use_processes=False,
    )
    assert resumed.complete and resumed.processed_count == 7


def test_imported_or_test_observations_are_not_freeze_eligible(
    completed_context,
):
    assert completed_context["audit"]["freeze_eligible"] is False
    assert completed_context["audit"]["execution_class"] == (
        RTA4_PILOT_TEST_EXECUTION_CLASS
    )


def test_formal_closure_rejects_pilot_terminal_namespace(
    completed_context,
):
    with pytest.raises((RTA4FormalValidationError, FileNotFoundError)):
        validate_formal_run_closure(
            completed_context["output"], require_complete=True,
        )


def test_execution_config_has_no_hidden_operational_defaults(tmp_path):
    context = _context(tmp_path)
    document = context["execution"]
    for field in (
        "pilot_manifest", "source_manifest", "output_root",
        "taskset_store", "simulator_manifest", "default_worker_count",
        "max_in_flight", "provisional_rta_attempt_timeout_seconds",
        "provisional_simulation_timeout_seconds", "memory_soft_limit_bytes",
        "checkpoint_interval_records", "maximum_attempts",
        "runtime_ci_rule_version", "resume_policy", "environment_manifest",
        "dependency_manifest", "hardware_manifest",
    ):
        assert field in document
    damaged = deepcopy(document)
    damaged.pop("maximum_attempts")
    with pytest.raises(RTA4PilotExecutionError, match="field set"):
        validate_pilot_execution_config(damaged, context["manifest"])


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_core5b_selection_rejects_incomplete_execution_groups(
    tmp_path, count,
):
    scale = {core: 1 for core in RTA4_CORES}
    scale["CORE-5B"] = count
    with pytest.raises(RTA4PilotError, match="multiple of four"):
        build_pilot_manifest(
            CONFIGS, core_record_counts=scale,
            selection_seed="RTA4-CORE5B-GROUP-REJECT-V1",
            output_root=tmp_path / "output",
            taskset_store=tmp_path / "store",
            config_paths=CONFIG_PATHS,
        )


@pytest.mark.parametrize("count,group_count", [(4, 1), (8, 2)])
def test_core5b_selection_expands_complete_mathematical_groups(
    tmp_path, count, group_count,
):
    scale = {core: 1 for core in RTA4_CORES}
    scale["CORE-5B"] = count
    manifest = build_pilot_manifest(
        CONFIGS, core_record_counts=scale,
        selection_seed="RTA4-CORE5B-GROUP-V1",
        output_root=tmp_path / "output",
        taskset_store=tmp_path / "store",
        config_paths=CONFIG_PATHS,
    )
    rows = manifest["selected_records"]["CORE-5B"]
    assert manifest["scale_unit"] == "EXECUTION_RECORDS"
    assert manifest["selection_unit"]["CORE-5B"] == (
        "MATHEMATICAL_REQUEST_GROUP"
    )
    assert manifest["required_group_workers"]["CORE-5B"] == [1, 2, 4, 8]
    assert len(rows) == group_count * 4
    for offset in range(0, len(rows), 4):
        group = rows[offset:offset + 4]
        assert [row["worker_count"] for row in group] == [1, 2, 4, 8]
        assert len({row["mathematical_request_id"] for row in group}) == 1
        assert len({row["selection_key"] for row in group}) == 1
        assert len({row["execution_id"] for row in group}) == 4


def test_resume_hydrates_complete_store_without_provider_calls(tmp_path):
    context = _context(tmp_path)
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    runner.run(
        max_records=1, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta, use_processes=False,
    )
    calls = []

    def forbidden_provider(record):
        calls.append(record.execution_id)
        raise AssertionError("resume regenerated a pre-materialized slot")

    resumed = runner.run(
        resume=True, certificate_provider=forbidden_provider,
        rta_callback=_synthetic_rta,
        simulation_callback=_synthetic_simulator(
            context["root"] / "resume-hydration-traces"
        ),
        use_processes=False,
    )
    assert resumed.complete
    assert calls == []


@pytest.mark.parametrize("damage", ["missing", "extra", "conflict"])
def test_partial_store_inventory_is_exact_before_resume(
    tmp_path, damage,
):
    context = _context(tmp_path)
    PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    ).run(
        max_records=1, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta, use_processes=False,
    )
    certificate_root = context["store"] / "certificates"
    target = next(certificate_root.glob("*.json"))
    saved = target.read_bytes()
    extra = certificate_root / f"{'f' * 64}.json"
    try:
        if damage == "missing":
            target.unlink()
        elif damage == "extra":
            extra.write_bytes(saved)
        else:
            target.write_bytes(saved + b" ")
        with pytest.raises(RTA4PilotExecutionError, match="certificate|store"):
            audit_pilot_namespace(
                context["output"], CONFIGS, require_complete=False,
            )
    finally:
        extra.unlink(missing_ok=True)
        target.write_bytes(saved)


def test_runner_rejects_foreign_config_before_namespace_write(tmp_path):
    context = _context(tmp_path)
    before = _file_bytes(context["output"])
    foreign = deepcopy(context["execution"])
    foreign["provisional_rta_attempt_timeout_seconds"] += 1
    material = dict(foreign)
    material.pop("execution_config_id")
    from experiments.v9_3.rta4_pilot_execution import (
        RTA4_PILOT_EXECUTION_CONFIG_DOMAIN,
    )
    from experiments.v9_3.rta4_formal_config import domain_hash
    foreign["execution_config_id"] = domain_hash(
        RTA4_PILOT_EXECUTION_CONFIG_DOMAIN, material,
    )
    with pytest.raises(RTA4PilotExecutionError, match="canonical root"):
        PilotExecutionRunner(CONFIGS, context["manifest"], foreign)
    assert _file_bytes(context["output"]) == before


@pytest.mark.parametrize(
    "lower,upper", [(9, 10), (99, 100), (999, 1000), (9999, 10000)],
)
def test_output_io_preimage_is_unique_across_decimal_boundaries(
    completed_context, lower, upper,
):
    raw_path = next(
        (
            completed_context["output"]
            / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
        ).glob("*.json")
    )
    raw = json.loads(raw_path.read_text())
    lower_preimage = pilot_final_terminal_preimage(
        raw, checkpoint_overhead_milliseconds=lower,
        resume_overhead_milliseconds=0,
        ci_width_engineering_warning=True,
    )
    upper_preimage = pilot_final_terminal_preimage(
        raw, checkpoint_overhead_milliseconds=upper,
        resume_overhead_milliseconds=0,
        ci_width_engineering_warning=True,
    )
    lower_bytes = pilot_execution.canonical_json(
        lower_preimage,
    ).encode("utf-8")
    upper_bytes = pilot_execution.canonical_json(
        upper_preimage,
    ).encode("utf-8")
    assert len(upper_bytes) == len(lower_bytes) + 1
    reordered = dict(reversed(tuple(upper_preimage.items())))
    assert compute_pilot_output_io_bytes(
        reordered, raw["trace_size_bytes"],
    ) == compute_pilot_output_io_bytes(
        upper_preimage, raw["trace_size_bytes"],
    )
    first = build_pilot_final_terminal(
        raw, checkpoint_overhead_milliseconds=upper,
        resume_overhead_milliseconds=0,
        ci_width_engineering_warning=True,
    )
    second = build_pilot_final_terminal(
        raw, checkpoint_overhead_milliseconds=upper,
        resume_overhead_milliseconds=0,
        ci_width_engineering_warning=True,
    )
    assert pilot_execution._canonical_json_bytes(first) == (
        pilot_execution._canonical_json_bytes(second)
    )
    assert compute_pilot_output_io_bytes(
        upper_preimage, raw["trace_size_bytes"] + 6,
    ) == first["output_io_bytes"] + 6


@pytest.mark.parametrize(
    "stage,occurrence",
    [
        ("after_raw_terminal", 1),
        ("after_checkpoint_generation", 2),
        ("after_checkpoint_event", 2),
        ("after_checkpoint_pointer", 2),
        ("during_finalization", 1),
    ],
)
def test_transaction_crash_windows_resume_without_duplicate_raw_execution(
    tmp_path, stage, occurrence,
):
    context = _context(tmp_path)
    calls = []

    def rta(record, certificate):
        calls.append(record.execution_id)
        return _synthetic_rta(record, certificate)

    simulator_impl = _synthetic_simulator(
        context["root"] / "transaction-traces"
    )

    def simulation(*args):
        calls.append(args[0].execution_id)
        return simulator_impl(*args)

    hits = 0

    def hook(observed):
        nonlocal hits
        if observed == stage:
            hits += 1
            if hits == occurrence:
                raise RTA4PilotExecutionInterrupted(stage)

    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    with pytest.raises(RTA4PilotExecutionInterrupted):
        runner.run(
            certificate_provider=_synthetic_certificate,
            rta_callback=rta, simulation_callback=simulation,
            use_processes=False, transaction_hook=hook,
        )
    audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
        allow_recovery_artifacts=True,
    )
    resumed = runner.run(
        resume=True, certificate_provider=_synthetic_certificate,
        rta_callback=rta, simulation_callback=simulation,
        use_processes=False,
    )
    assert resumed.complete
    assert len(calls) == len(set(calls)) == 9
    assert audit_pilot_namespace(
        context["output"], CONFIGS,
    )["recovery_orphan_count"] == 0


def test_same_size_trace_replacement_and_damaged_store_marker_fail_audit(
    completed_context,
):
    output = completed_context["output"]
    trace = next((output / "rta4_pilot_traces").glob("*.json"))
    marker = (
        completed_context["store"] / "formal_taskset_store_manifest.json"
    )
    trace_bytes = trace.read_bytes()
    marker_bytes = marker.read_bytes()
    try:
        replacement = bytearray(trace_bytes)
        replacement[-2] = ord(" ")
        trace.write_bytes(bytes(replacement))
        assert trace.stat().st_size == len(trace_bytes)
        with pytest.raises(RTA4PilotExecutionError, match="trace"):
            audit_pilot_namespace(output, CONFIGS)
        trace.write_bytes(trace_bytes)
        marker.write_text("{}\n", encoding="utf-8")
        with pytest.raises(RTA4PilotExecutionError, match="marker"):
            audit_pilot_namespace(output, CONFIGS)
    finally:
        trace.write_bytes(trace_bytes)
        marker.write_bytes(marker_bytes)


def test_hard_child_exit_is_parent_canonicalized_and_temp_is_cleaned(
    tmp_path,
):
    context = _context(tmp_path, workers=1)
    summary = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    ).run(
        max_records=1, certificate_provider=_synthetic_certificate,
        rta_callback=_hard_exit_rta, use_processes=True,
    )
    assert not summary.complete and summary.processed_count == 1
    worker_root = (
        context["output"] / "rta4_pilot_worker_tmp"
    )
    assert list(worker_root.iterdir()) == []
    audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
    )
    resumed = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    ).run(
        resume=True, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta,
        simulation_callback=_synthetic_simulator(
            context["root"] / "hard-exit-resume-traces"
        ),
        use_processes=False,
    )
    assert resumed.complete
    assert audit_pilot_namespace(
        context["output"], CONFIGS,
    )["checkpoint_state"] == "PILOT_COMPLETE"


def test_cli_plan_resume_validate_and_error_modes(
    tmp_path, completed_context,
):
    plan_root = tmp_path / "cli-plan"
    command = [
        sys.executable, str(ROOT / "scripts" / "run_v9_3_rta4_pilot.py"),
        "--plan-only", "--output-root", str(plan_root),
        "--taskset-store", str(tmp_path / "cli-store"),
        "--selection-seed", "RTA4-CLI-PLAN-V1",
    ]
    for core in RTA4_CORES:
        command.extend(("--config", f"{core}={CONFIG_PATHS[core]}"))
        command.extend((
            "--scale", f"{core}={'4' if core == 'CORE-5B' else '1'}",
        ))
    planned = subprocess.run(
        command, cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert planned.returncode == 0, planned.stderr
    assert json.loads(planned.stdout)["mode"] == "plan-only"

    before = _file_bytes(completed_context["output"])
    common = [
        "--output-root", str(completed_context["output"]),
    ]
    for core in RTA4_CORES:
        common.extend(("--config", f"{core}={CONFIG_PATHS[core]}"))
    for mode in ("--resume", "--validate-only"):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "run_v9_3_rta4_pilot.py"),
                mode, *common,
            ],
            cwd=ROOT, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert result.returncode == 0, result.stderr
    rejected = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_v9_3_rta4_pilot.py"),
            "--resume", *common, "--execution-config",
            str(
                completed_context["output"]
                / RTA4_PILOT_EXECUTION_CONFIG
            ),
        ],
        cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert rejected.returncode != 0
    assert _file_bytes(completed_context["output"]) == before


@pytest.mark.parametrize(
    "phase,overrides",
    [
        ("EXECUTING", {
            "raw_execution_order": ["e1", "e2"],
            "final_execution_order": ["e1"],
        }),
        ("FINALIZING", {
            "raw_execution_order": ["e1"],
        }),
        ("FINALIZING", {
            "trace_execution_ids": [],
            "required_trace_execution_ids": ["e2"],
        }),
        ("FINALIZING", {
            "final_execution_order": ["e2"],
        }),
        ("PILOT_COMPLETE", {
            "audit_present": False,
        }),
        ("PILOT_COMPLETE", {
            "completion_seal_present": False,
        }),
        ("PILOT_COMPLETE", {
            "worker_active_count": 1,
        }),
        ("PREPARING_STORE", {
            "completed_store_slot_order": [],
            "store_manifest_present": False,
            "raw_execution_order": ["e1"],
            "final_execution_order": [],
            "trace_execution_ids": [],
            "required_trace_execution_ids": [],
            "observations_present": False,
            "report_present": False,
            "audit_present": False,
            "completion_seal_present": False,
        }),
    ],
)
def test_phase_inventory_negative_combinations_fail_closed(
    phase, overrides,
):
    values = {
        "phase": phase,
        "expected_store_slot_order": ["s1", "s2"],
        "completed_store_slot_order": ["s1", "s2"],
        "store_manifest_present": True,
        "selected_execution_order": ["e1", "e2"],
        "raw_execution_order": ["e1", "e2"],
        "final_execution_order": (
            ["e1", "e2"] if phase in {"FINALIZING", "PILOT_COMPLETE"}
            else []
        ),
        "trace_execution_ids": ["e2"],
        "required_trace_execution_ids": ["e2"],
        "observations_present": phase == "PILOT_COMPLETE",
        "report_present": phase == "PILOT_COMPLETE",
        "audit_present": phase == "PILOT_COMPLETE",
        "completion_seal_present": phase == "PILOT_COMPLETE",
        "worker_active_count": 0,
        "recovery_artifact_count": 0,
    }
    values.update(overrides)
    with pytest.raises(RTA4PilotExecutionError):
        validate_pilot_phase_inventory(**values)


@pytest.mark.parametrize(
    "stage,expected_extra_generation",
    [
        ("after_preparing_store_pointer", 0),
        ("after_store_certificate", 1),
        ("after_store_slot_checkpoint", 0),
        ("after_store_manifest", 0),
    ],
)
def test_preparing_store_crash_windows_resume_without_manual_cleanup(
    tmp_path, stage, expected_extra_generation,
):
    context = _context(tmp_path)
    calls = []

    def provider(record):
        calls.append(str(record.taskset_slot_id))
        return _synthetic_certificate(record)

    fired = False

    def hook(observed):
        nonlocal fired
        if observed == stage and not fired:
            fired = True
            raise RTA4PilotExecutionInterrupted(stage)

    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    with pytest.raises(RTA4PilotExecutionInterrupted, match=stage):
        runner.run(
            max_records=0, certificate_provider=provider,
            use_processes=False, transaction_hook=hook,
        )
    partial = audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
        reconstruct_store=False, allow_recovery_artifacts=True,
    )
    assert partial["audited_checkpoint_phase"] == "PREPARING_STORE"
    resumed = runner.run(
        resume=True, max_records=0, certificate_provider=provider,
        use_processes=False,
    )
    assert not resumed.complete
    audit = audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
        reconstruct_store=False,
    )
    assert audit["audited_checkpoint_phase"] == "EXECUTING"
    expected_slots = len({
        row["taskset_slot_id"]
        for core in RTA4_CORES
        for row in context["manifest"]["selected_records"][core]
    })
    assert len(calls) == expected_slots + expected_extra_generation


def test_preparing_store_unknown_certificate_fails_without_deletion(
    tmp_path,
):
    context = _context(tmp_path)
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )

    def stop(stage):
        if stage == "after_preparing_store_pointer":
            raise RTA4PilotExecutionInterrupted(stage)

    with pytest.raises(RTA4PilotExecutionInterrupted):
        runner.run(
            max_records=0,
            certificate_provider=_synthetic_certificate,
            use_processes=False, transaction_hook=stop,
        )
    store = RTA4FormalTasksetStore(context["store"])
    foreign_record = runner.records[-1]
    store.put(_synthetic_certificate(foreign_record))
    certificate_path = next(
        (context["store"] / "certificates").iterdir()
    )
    before = certificate_path.read_bytes()
    with pytest.raises(
        RTA4PilotExecutionError, match="next-slot|unknown",
    ):
        runner.run(
            resume=True, max_records=0,
            certificate_provider=_synthetic_certificate,
            use_processes=False,
        )
    assert certificate_path.read_bytes() == before


@pytest.mark.parametrize(
    "stage",
    [
        "after_completion_audit",
        "after_completion_seal",
        "after_pilot_complete_checkpoint_generation",
        "after_pilot_complete_checkpoint_event",
        "after_pilot_complete_checkpoint_pointer",
    ],
)
def test_completion_transaction_crash_windows_are_resumable(
    tmp_path, stage,
):
    context = _context(tmp_path)
    calls = []

    def rta(record, certificate):
        calls.append(record.execution_id)
        return _synthetic_rta(record, certificate)

    simulator_impl = _synthetic_simulator(
        context["root"] / "completion-traces"
    )

    def simulation(*args):
        calls.append(args[0].execution_id)
        return simulator_impl(*args)

    fired = False

    def hook(observed):
        nonlocal fired
        if observed == stage and not fired:
            fired = True
            raise RTA4PilotExecutionInterrupted(stage)

    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    with pytest.raises(RTA4PilotExecutionInterrupted, match=stage):
        runner.run(
            certificate_provider=_synthetic_certificate,
            rta_callback=rta, simulation_callback=simulation,
            use_processes=False, transaction_hook=hook,
        )
    final_before = {
        path.name: path.read_bytes()
        for path in (
            context["output"] / RTA4_PILOT_FINAL_TERMINAL_DIRECTORY
        ).glob("*.json")
    }
    audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
        reconstruct_store=False, allow_recovery_artifacts=True,
    )
    resumed = runner.run(
        resume=True, certificate_provider=_synthetic_certificate,
        rta_callback=rta, simulation_callback=simulation,
        use_processes=False,
    )
    assert resumed.complete
    assert len(calls) == len(set(calls)) == 9
    assert final_before == {
        path.name: path.read_bytes()
        for path in (
            context["output"] / RTA4_PILOT_FINAL_TERMINAL_DIRECTORY
        ).glob("*.json")
    }
    assert (context["output"] / RTA4_PILOT_AUDIT).is_file()
    assert (context["output"] / RTA4_PILOT_COMPLETION_SEAL).is_file()
    audit_pilot_namespace(context["output"], CONFIGS)


@pytest.mark.parametrize(
    "damage",
    [
        "generation", "event", "gap", "previous_link",
        "phase_rollback", "unknown_filename",
    ],
)
def test_historical_checkpoint_damage_and_gaps_fail_closed(
    tmp_path, damage,
):
    context = _context(tmp_path)
    PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    ).run(
        max_records=1, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta, use_processes=False,
    )
    generation_root = context["output"] / "rta4_pilot_checkpoints"
    event_root = context["output"] / "rta4_pilot_checkpoint_events"
    if damage == "generation":
        target = generation_root / "00000000.json"
        document = json.loads(target.read_text())
        document["unexpected"] = True
        atomic_write_json(target, document)
    elif damage == "event":
        target = event_root / "00000000.json"
        document = json.loads(target.read_text())
        document["unexpected"] = True
        atomic_write_json(target, document)
    elif damage == "gap":
        target = generation_root / "00000001.json"
        target.unlink()
    elif damage == "unknown_filename":
        target = generation_root / "0000000a.json"
        target.write_text("{}\n", encoding="utf-8")
    else:
        target = max(generation_root.glob("*.json"))
        document = json.loads(target.read_text())
        if damage == "previous_link":
            document["previous_checkpoint_id"] = "0" * 64
        else:
            empty_digest = hashlib.sha256(
                pilot_execution.canonical_json({}).encode("utf-8")
            ).hexdigest()
            document.update({
                "phase": "PREPARING_STORE",
                "store_manifest_id": None,
                "completed_raw_count": 0,
                "completed_raw_execution_order": [],
                "completed_raw_terminal_digests": {},
                "completed_raw_ordered_digest": empty_digest,
                "final_terminal_execution_order": [],
                "final_terminal_digests": {},
                "final_terminal_ordered_digest": empty_digest,
                "trace_digests": {},
                "trace_ordered_digest": empty_digest,
            })
        material = deepcopy(document)
        material.pop("checkpoint_id")
        document["checkpoint_id"] = pilot_execution.domain_hash(
            pilot_execution.RTA4_PILOT_CHECKPOINT_DOMAIN, material,
        )
        atomic_write_json(target, document)
    before = target.read_bytes() if target.exists() else None
    with pytest.raises(RTA4PilotExecutionError, match="checkpoint|event"):
        audit_pilot_namespace(
            context["output"], CONFIGS, require_complete=False,
        )
    if before is not None:
        assert target.read_bytes() == before


@pytest.mark.parametrize(
    "stage,event_only",
    [
        ("after_executing_checkpoint_generation", False),
        ("after_executing_checkpoint_event", False),
        ("after_executing_checkpoint_event", True),
    ],
)
def test_known_next_checkpoint_orphans_recover_only_after_preflight(
    tmp_path, stage, event_only,
):
    context = _context(tmp_path)
    hits = 0

    def hook(observed):
        nonlocal hits
        if observed == stage:
            hits += 1
            if hits == 2:
                raise RTA4PilotExecutionInterrupted(stage)

    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    with pytest.raises(RTA4PilotExecutionInterrupted):
        runner.run(
            max_records=1,
            certificate_provider=_synthetic_certificate,
            rta_callback=_synthetic_rta, use_processes=False,
            transaction_hook=hook,
        )
    pointer = json.loads(
        (context["output"] / RTA4_PILOT_CHECKPOINT).read_text()
    )
    orphan_generation = pointer["checkpoint_generation"] + 1
    generation_path = (
        context["output"] / "rta4_pilot_checkpoints"
        / f"{orphan_generation:08d}.json"
    )
    event_path = (
        context["output"] / "rta4_pilot_checkpoint_events"
        / f"{orphan_generation:08d}.json"
    )
    if event_only:
        generation_path.unlink()
    before = {
        path: path.read_bytes()
        for path in (generation_path, event_path) if path.exists()
    }
    audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
        reconstruct_store=False, allow_recovery_artifacts=True,
    )
    assert {
        path: path.read_bytes()
        for path in before
    } == before
    resumed = runner.run(
        resume=True, max_records=0,
        certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta, use_processes=False,
    )
    assert resumed.processed_count == 0
    audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
    )


def test_unknown_checkpoint_artifact_is_not_deleted(
    tmp_path,
):
    context = _context(tmp_path)
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    runner.run(
        max_records=1, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta, use_processes=False,
    )
    pointer = json.loads(
        (context["output"] / RTA4_PILOT_CHECKPOINT).read_text()
    )
    unknown_generation = (
        context["output"] / "rta4_pilot_checkpoints"
        / f"{pointer['checkpoint_generation'] + 2:08d}.json"
    )
    unknown_generation.write_text("{}\n", encoding="utf-8")
    before_generation = unknown_generation.read_bytes()
    with pytest.raises(RTA4PilotExecutionError):
        runner.run(
            resume=True, max_records=0,
            certificate_provider=_synthetic_certificate,
            use_processes=False,
        )
    assert unknown_generation.read_bytes() == before_generation


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_unregistered_worker_child_is_rejected_without_deletion(
    tmp_path, kind,
):
    context = _context(tmp_path)
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    runner.run(
        max_records=0, certificate_provider=_synthetic_certificate,
        use_processes=False,
    )
    external = tmp_path / "external-worker-evidence"
    external.mkdir()
    unknown = (
        context["output"] / "rta4_pilot_worker_tmp" / "unregistered"
    )
    if kind == "directory":
        unknown.mkdir()
    else:
        unknown.symlink_to(external, target_is_directory=True)
    with pytest.raises(RTA4PilotExecutionError, match="worker temporary"):
        runner.run(
            resume=True, max_records=0,
            certificate_provider=_synthetic_certificate,
            use_processes=False,
        )
    assert unknown.exists()
    assert external.is_dir()


def test_batch_parent_persistence_exception_cleans_all_registered_roots(
    tmp_path, monkeypatch,
):
    context = _context(tmp_path, counts=2, workers=2)
    original_write = pilot_execution._write_json_once
    failed = False

    def fail_first_raw_write(path, document):
        nonlocal failed
        if (
            path.parent.name == RTA4_PILOT_RAW_TERMINAL_DIRECTORY
            and not failed
        ):
            failed = True
            raise RTA4PilotExecutionError(
                "injected parent raw persistence failure"
            )
        return original_write(path, document)

    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    monkeypatch.setattr(
        pilot_execution, "_write_json_once", fail_first_raw_write,
    )
    with pytest.raises(
        RTA4PilotExecutionError, match="parent raw persistence",
    ):
        runner.run(
            max_records=2,
            certificate_provider=_synthetic_certificate,
            rta_callback=_synthetic_rta, use_processes=False,
        )
    assert list(
        (context["output"] / "rta4_pilot_worker_tmp").iterdir()
    ) == []
    registry = json.loads(
        (
            context["output"]
            / "rta4_pilot_worker_temp_registry.json"
        ).read_text()
    )
    assert all(
        batch["batch_status"] == "CLEANED"
        for batch in registry["batches"]
    )


def test_worker_registry_rejects_path_escape_and_cleanup_failure(
    tmp_path, monkeypatch,
):
    context = _context(tmp_path)
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    runner.run(
        max_records=0,
        certificate_provider=_synthetic_certificate,
        use_processes=False,
    )
    batch_id, (worker_root,) = runner._register_worker_batch(
        (runner.records[0],),
    )
    monkeypatch.setattr(
        pilot_execution.shutil, "rmtree",
        lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
    )
    with pytest.raises(
        RTA4PilotExecutionError, match="could not clean",
    ):
        runner._cleanup_worker_batch(batch_id)
    assert worker_root.is_dir()
    monkeypatch.undo()
    runner._cleanup_worker_batch(batch_id)

    registry_path = (
        context["output"] / "rta4_pilot_worker_temp_registry.json"
    )
    registry = json.loads(registry_path.read_text())
    batch = deepcopy(registry["batches"][-1])
    batch["batch_status"] = "ACTIVE"
    batch["entries"][0]["cleanup_status"] = "PENDING"
    escape = tmp_path / "must-not-delete"
    escape.mkdir()
    batch["entries"][0]["temp_root"] = str(escape)
    damaged = pilot_execution._build_worker_temp_registry(
        context["execution"], runner.execution_manifest,
        [*registry["batches"][:-1], batch],
    )
    atomic_write_json(registry_path, damaged)
    with pytest.raises(RTA4PilotExecutionError, match="worker temporary"):
        runner.run(
            resume=True, max_records=0,
            certificate_provider=_synthetic_certificate,
            use_processes=False,
        )
    assert escape.is_dir()


def test_cli_test_execute_partial_resume_second_resume_and_errors(
    tmp_path,
):
    context = _context(tmp_path / "execute")
    script = str(ROOT / "scripts" / "run_v9_3_rta4_pilot.py")
    common = [
        "--output-root", str(context["output"]),
    ]
    for core in RTA4_CORES:
        common.extend(("--config", f"{core}={CONFIG_PATHS[core]}"))

    executed = subprocess.run(
        [
            sys.executable, script, "--execute", *common,
            "--execution-config",
            str(context["output"] / RTA4_PILOT_EXECUTION_CONFIG),
            "--simulator-binary", str(context["root"] / "fake-simulator"),
            "--max-records", "1",
        ],
        cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert executed.returncode == 0, executed.stderr
    first = json.loads(executed.stdout)
    assert first["processed_count"] == 1
    assert first["complete"] is False

    resumed = subprocess.run(
        [sys.executable, script, "--resume", *common],
        cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["complete"] is True
    before = _file_bytes(context["output"])
    for mode in ("--resume", "--validate-only"):
        repeated = subprocess.run(
            [sys.executable, script, mode, *common],
            cwd=ROOT, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert repeated.returncode == 0, repeated.stderr
        assert _file_bytes(context["output"]) == before

    missing_context = _context(tmp_path / "missing-simulator")
    missing_common = [
        "--output-root", str(missing_context["output"]),
    ]
    for core in RTA4_CORES:
        missing_common.extend(
            ("--config", f"{core}={CONFIG_PATHS[core]}")
        )
    missing_before = _file_bytes(missing_context["output"])
    missing = subprocess.run(
        [
            sys.executable, script, "--execute", *missing_common,
            "--execution-config",
            str(
                missing_context["output"]
                / RTA4_PILOT_EXECUTION_CONFIG
            ),
        ],
        cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert missing.returncode != 0
    assert _file_bytes(missing_context["output"]) == missing_before

    foreign = deepcopy(missing_context["execution"])
    foreign["provisional_rta_attempt_timeout_seconds"] += 1
    foreign_material = deepcopy(foreign)
    foreign_material.pop("execution_config_id")
    foreign["execution_config_id"] = pilot_execution.domain_hash(
        pilot_execution.RTA4_PILOT_EXECUTION_CONFIG_DOMAIN,
        foreign_material,
    )
    foreign_path = tmp_path / "foreign-execution-config.json"
    atomic_write_json(foreign_path, foreign)
    mismatch_before = _file_bytes(missing_context["output"])
    mismatch = subprocess.run(
        [
            sys.executable, script, "--execute", *missing_common,
            "--execution-config", str(foreign_path),
            "--simulator-binary",
            str(missing_context["root"] / "fake-simulator"),
        ],
        cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert mismatch.returncode != 0
    assert _file_bytes(missing_context["output"]) == mismatch_before
