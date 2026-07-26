from __future__ import annotations

from copy import deepcopy
import hashlib
import json
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
    RTA4_PILOT_REPORT, build_pilot_manifest, build_pilot_observations,
    build_pilot_report,
)
from experiments.v9_3.rta4_formal_plan import (
    iter_core1_plan, iter_core2_plan, iter_core3_plan, iter_core4_plan,
    iter_core5b_plan,
)
from experiments.v9_3.rta4_formal_validation import (
    RTA4FormalValidationError, validate_formal_run_closure,
)
from experiments.v9_3.rta4_pilot_execution import (
    PilotExecutionRunner, PilotTasksetProvider, RTA4_PILOT_CHECKPOINT,
    RTA4_PILOT_EXECUTION_CONFIG, RTA4_PILOT_EXECUTION_MANIFEST,
    RTA4_PILOT_TERMINAL_DIRECTORY, RTA4_PILOT_TEST_EXECUTION_CLASS,
    RTA4PilotExecutionError, RTA4PilotExecutionInterrupted,
    _execution_batches,
    audit_pilot_namespace, build_pilot_execution_config,
    runtime_ci_engineering_warnings, validate_pilot_execution_config,
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
        core_record_counts={core: counts for core in RTA4_CORES},
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
        CONFIGS, core_record_counts={core: 2 for core in RTA4_CORES},
        selection_seed="RTA4-PLAN-ONLY-V1",
        output_root=tmp_path / "output", taskset_store=tmp_path / "store",
        config_paths=CONFIG_PATHS,
    )
    right = build_pilot_manifest(
        CONFIGS, core_record_counts={core: 2 for core in RTA4_CORES},
        selection_seed="RTA4-PLAN-ONLY-V1",
        output_root=tmp_path / "output", taskset_store=tmp_path / "store",
        config_paths=CONFIG_PATHS,
    )
    assert left == right
    for core in RTA4_CORES:
        assert len(left["selected_records"][core]) == 2
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
        (context["output"] / RTA4_PILOT_TERMINAL_DIRECTORY).glob("*.json")
    )
    assert len(terminals) == 1
    assert not (context["output"] / RTA4_PILOT_OBSERVATIONS).exists()
    assert not (context["output"] / RTA4_PILOT_REPORT).exists()
    checkpoint = json.loads(
        (context["output"] / RTA4_PILOT_CHECKPOINT).read_text()
    )
    assert checkpoint["state"] == "INCOMPLETE_PILOT"
    assert checkpoint["completed_record_count"] == 1
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
            context["output"] / RTA4_PILOT_TERMINAL_DIRECTORY
        ).glob("*.json")
    )
    terminal = json.loads(terminal_path.read_text())
    assert terminal["engineering_error"] is True
    assert terminal["attempt_count"] == 0


def test_all_core_callbacks_resume_and_engineering_only_report(
    completed_context,
):
    output = completed_context["output"]
    assert completed_context["callback_count"] == 6
    audit = audit_pilot_namespace(output, CONFIGS)
    assert audit["checkpoint_state"] == "PILOT_COMPLETE"
    assert audit["execution_class"] == RTA4_PILOT_TEST_EXECUTION_CLASS
    assert audit["freeze_eligible"] is False
    assert not (output / ".rta4_pilot_worker_traces").exists()
    observations = json.loads(
        (output / RTA4_PILOT_OBSERVATIONS).read_text()
    )
    assert observations["observation_count"] == 6
    assert {row["core"] for row in observations["observations"]} == set(
        RTA4_CORES
    )
    assert all(
        row["ci_width_engineering_warning"]
        for row in observations["observations"]
    )
    report = json.loads((output / RTA4_PILOT_REPORT).read_text())
    assert report["scientific_results_included"] is False
    assert len(report["engineering_metrics"]["strata"]) == 6
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
        "--execution-config", str(
            completed_context["output"] / RTA4_PILOT_EXECUTION_CONFIG
        ),
        "--simulator-binary", str(
            completed_context["root"] / "fake-simulator"
        ),
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
            payload["completed_record_count"] -= 1
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
    assert resumed.complete and resumed.processed_count == 4


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
