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
    RTA4_PILOT_EXECUTION_CLASS, RTA4_PILOT_OBSERVATIONS,
    RTA4_PILOT_OUTPUT_MARKER,
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
    build_pilot_final_terminal, build_simulation_support,
    compute_pilot_output_io_bytes,
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


def _source_repo(root: Path) -> tuple[dict, Path, Path]:
    repo = root / "source"
    repo.mkdir()
    source = repo / "entry.txt"
    base_system = repo / "base-system.yml"
    energy_config = repo / "energy.yml"
    source.write_text("pilot-source-v1\n", encoding="utf-8")
    base_system.write_text("system: synthetic-fixture\n", encoding="utf-8")
    energy_config.write_text("{}\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(
        ("git", "add", "entry.txt", "base-system.yml", "energy.yml"),
        cwd=repo, check=True,
    )
    subprocess.run(
        (
            "git", "-c", "user.name=RTA4 Test",
            "-c", "user.email=rta4@example.invalid",
            "commit", "-qm", "fixture",
        ),
        cwd=repo, check=True,
    )
    return (
        build_source_manifest(repo, (source, base_system, energy_config)),
        base_system, energy_config,
    )


def _context(
    root: Path, *, counts: int = 1, workers: int = 2,
    execution_class: str = RTA4_PILOT_TEST_EXECUTION_CLASS,
):
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
    source_manifest, base_system, energy_config = _source_repo(root)
    execution = build_pilot_execution_config(
        manifest_path, manifest,
        source_manifest=source_manifest,
        output_root=output, taskset_store=store,
        simulator_manifest=build_simulator_manifest(simulator),
        simulation_support=(
            build_simulation_support(
                base_system_path=base_system,
                energy_config_path=energy_config,
            )
            if execution_class == RTA4_PILOT_EXECUTION_CLASS else None
        ),
        default_worker_count=workers, max_in_flight=max(3, workers),
        provisional_rta_attempt_timeout_seconds=2,
        provisional_simulation_timeout_seconds=2,
        memory_soft_limit_bytes=1 << 60,
        checkpoint_interval_records=2, maximum_attempts=2,
        execution_class=execution_class,
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


def _rehash_checkpoint_document(document: dict) -> dict:
    document["completed_raw_count"] = len(
        document["completed_raw_execution_order"]
    )
    for field, ordered in (
        (
            "completed_raw_terminal_digests",
            "completed_raw_ordered_digest",
        ),
        ("checkpoint_event_digests", "checkpoint_event_ordered_digest"),
        ("resume_event_digests", "resume_event_ordered_digest"),
        ("final_terminal_digests", "final_terminal_ordered_digest"),
        ("trace_digests", "trace_ordered_digest"),
    ):
        document[ordered] = hashlib.sha256(
            pilot_execution.canonical_json({
                key: document[field][key]
                for key in sorted(document[field])
            }).encode("utf-8")
        ).hexdigest()
    document["expected_store_slot_set_sha256"] = hashlib.sha256(
        pilot_execution.canonical_json(sorted(
            document["expected_store_slot_order"]
        )).encode("utf-8")
    ).hexdigest()
    material = deepcopy(document)
    material.pop("checkpoint_id", None)
    document["checkpoint_id"] = pilot_execution.domain_hash(
        pilot_execution.RTA4_PILOT_CHECKPOINT_DOMAIN, material,
    )
    return document


def _rewrite_checkpoint_suffix(
    output: Path, target_generation: int, mutate,
    mutate_event=None,
) -> Path:
    """Keep a damaged historical suffix internally hash/link consistent."""

    generation_root = output / "rta4_pilot_checkpoints"
    event_root = output / "rta4_pilot_checkpoint_events"
    pointer_path = output / RTA4_PILOT_CHECKPOINT
    pointer = json.loads(pointer_path.read_text())
    current = pointer["checkpoint_generation"]
    prior_events = [
        json.loads((event_root / f"{generation:08d}.json").read_text())
        for generation in range(target_generation)
    ]
    previous = (
        None if target_generation == 0 else
        json.loads(
            (
                generation_root
                / f"{target_generation - 1:08d}.json"
            ).read_text()
        )
    )
    previous_path = (
        None if previous is None else
        generation_root / f"{target_generation - 1:08d}.json"
    )
    final_checkpoint = None
    final_event = None
    final_path = None
    final_event_path = None
    for generation in range(target_generation, current + 1):
        path = generation_root / f"{generation:08d}.json"
        event_path = event_root / f"{generation:08d}.json"
        document = json.loads(path.read_text())
        old_event = json.loads(event_path.read_text())
        if previous is not None:
            document["previous_checkpoint_id"] = previous["checkpoint_id"]
            document["previous_checkpoint_payload_sha256"] = (
                pilot_execution._sha256(previous_path)
            )
        if generation == target_generation:
            mutate(document)
        document["checkpoint_event_digests"] = {
            event["checkpoint_event_id"]: event["checkpoint_event_sha256"]
            for event in prior_events
        }
        document["checkpoint_event_digests"] = {
            key: document["checkpoint_event_digests"][key]
            for key in sorted(document["checkpoint_event_digests"])
        }
        document = _rehash_checkpoint_document(document)
        atomic_write_json(path, document)
        event_source = deepcopy(old_event)
        if generation == target_generation and mutate_event is not None:
            mutate_event(event_source)
        event = pilot_execution._build_checkpoint_event(
            document, path,
            event_kind=event_source["event_kind"],
            triggering_execution_id=event_source[
                "triggering_execution_id"
            ],
            measurement_origin=event_source["measurement_origin"],
            write_milliseconds=event_source[
                "checkpoint_write_milliseconds"
            ],
        )
        atomic_write_json(event_path, event)
        prior_events.append(event)
        previous = document
        previous_path = path
        final_checkpoint = document
        final_event = event
        final_path = path
        final_event_path = event_path
    atomic_write_json(
        pointer_path,
        pilot_execution._checkpoint_pointer(
            final_checkpoint, final_path, final_event, final_event_path,
        ),
    )
    return generation_root / f"{target_generation:08d}.json"


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
        max_records=3, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta,
        simulation_callback=_synthetic_simulator(
            context["root"] / "historical-damage-traces"
        ),
        use_processes=False,
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


def test_timeout_result_cleans_registered_worker_batch(tmp_path):
    context = _context(tmp_path, workers=1)

    def timeout(_record, _certificate):
        return {"solver_status": "TIMEOUT"}

    summary = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    ).run(
        max_records=1, certificate_provider=_synthetic_certificate,
        rta_callback=timeout, use_processes=False,
    )
    assert summary.processed_count == 1 and not summary.complete
    raw = json.loads(next(
        (
            context["output"] / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
        ).glob("*.json")
    ).read_text())
    assert raw["timed_out"] is True
    registry = json.loads(
        (
            context["output"]
            / "rta4_pilot_worker_temp_registry.json"
        ).read_text()
    )
    assert registry["batches"]
    assert all(
        batch["batch_status"] == "CLEANED"
        and all(
            entry["cleanup_status"] == "CLEANED"
            for entry in batch["entries"]
        )
        for batch in registry["batches"]
    )
    assert list(
        (context["output"] / "rta4_pilot_worker_tmp").iterdir()
    ) == []


def test_multiflight_checkpoint_exception_cleans_every_registry_entry(
    tmp_path,
):
    context = _context(tmp_path, counts=2, workers=2)
    hits = 0

    def hook(stage):
        nonlocal hits
        if stage == "after_executing_checkpoint_generation":
            hits += 1
            if hits == 2:
                raise RTA4PilotExecutionInterrupted(stage)

    with pytest.raises(RTA4PilotExecutionInterrupted):
        PilotExecutionRunner(
            CONFIGS, context["manifest"], context["execution"],
        ).run(
            max_records=3,
            certificate_provider=_synthetic_certificate,
            rta_callback=_synthetic_rta,
            simulation_callback=_synthetic_simulator(
                context["root"] / "checkpoint-cleanup-traces"
            ),
            use_processes=False, transaction_hook=hook,
        )
    registry = json.loads(
        (
            context["output"]
            / "rta4_pilot_worker_temp_registry.json"
        ).read_text()
    )
    assert registry["batches"]
    assert any(len(batch["entries"]) >= 2 for batch in registry["batches"])
    assert all(
        batch["batch_status"] == "CLEANED"
        and all(
            entry["cleanup_status"] == "CLEANED"
            for entry in batch["entries"]
        )
        for batch in registry["batches"]
    )
    assert list(
        (context["output"] / "rta4_pilot_worker_tmp").iterdir()
    ) == []
    resumed = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    ).run(
        resume=True,
        certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta,
        simulation_callback=_synthetic_simulator(
            context["root"] / "multiflight-resume-traces"
        ),
        use_processes=False,
    )
    assert resumed.complete


def test_active_all_cleaned_worker_registry_is_rejected(tmp_path):
    context = _context(tmp_path)
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    runner.run(
        max_records=0, certificate_provider=_synthetic_certificate,
        use_processes=False,
    )
    batch_id, _paths = runner._register_worker_batch(
        (runner.records[0],)
    )
    runner._cleanup_worker_batch(batch_id)
    registry_path = (
        context["output"] / "rta4_pilot_worker_temp_registry.json"
    )
    registry = json.loads(registry_path.read_text())
    batches = deepcopy(registry["batches"])
    batches[-1]["batch_status"] = "ACTIVE"
    damaged = pilot_execution._build_worker_temp_registry(
        context["execution"], runner.execution_manifest, batches,
    )
    atomic_write_json(registry_path, damaged)
    before = registry_path.read_bytes()
    with pytest.raises(
        RTA4PilotExecutionError,
        match="ACTIVE worker batch",
    ):
        runner._load_worker_registry()
    assert registry_path.read_bytes() == before


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


def test_phase_inventory_requires_context_and_rejects_observed_facts(
    completed_context,
):
    runner = PilotExecutionRunner(
        CONFIGS, completed_context["manifest"],
        completed_context["execution"],
    )
    canonical = runner.checkpoint_context
    raw_documents = [
        json.loads((
            completed_context["output"]
            / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
            / f"{execution_id}.json"
        ).read_text())
        for execution_id in canonical.selected_execution_order
    ]
    raw_order = list(canonical.selected_execution_order)
    final_order = list(canonical.selected_execution_order)
    slots = list(canonical.expected_store_slot_order)
    traces = {
        row["execution_id"] for row in raw_documents
        if row["trace_filename"] is not None
    }

    preparing = {
        "phase": "PREPARING_STORE",
        "completed_store_slot_order": [],
        "store_manifest_present": False,
        "raw_execution_order": [],
        "validated_raw_documents": [],
        "final_execution_order": [],
        "trace_execution_ids": [],
        "resume_event_ids": [],
        "observations_present": False,
        "report_present": False,
        "audit_present": False,
        "completion_seal_present": False,
        "worker_active_count": 0,
        "recovery_artifact_count": 0,
    }
    with pytest.raises(TypeError):
        validate_pilot_phase_inventory(**preparing)
    validate_pilot_phase_inventory(canonical, **preparing)

    executing = {
        **preparing,
        "phase": "EXECUTING",
        "completed_store_slot_order": slots,
        "store_manifest_present": True,
        "raw_execution_order": raw_order,
        "validated_raw_documents": raw_documents,
        "trace_execution_ids": traces,
    }
    finalizing = {
        **executing,
        "phase": "FINALIZING",
        "final_execution_order": final_order,
    }
    complete = {
        **finalizing,
        "phase": "PILOT_COMPLETE",
        "observations_present": True,
        "report_present": True,
        "audit_present": True,
        "completion_seal_present": True,
    }
    validate_pilot_phase_inventory(canonical, **executing)
    validate_pilot_phase_inventory(canonical, **finalizing)
    validate_pilot_phase_inventory(canonical, **complete)

    cases = []
    cases.append({**preparing, "completed_store_slot_order": ["foreign"]})
    cases.append({
        **executing,
        "raw_execution_order": list(reversed(raw_order)),
        "validated_raw_documents": list(reversed(raw_documents)),
    })
    cases.append({**executing, "final_execution_order": [raw_order[0]]})
    cases.append({
        **finalizing,
        "trace_execution_ids": traces - {
            next(iter(canonical.required_simulation_execution_ids))
        },
    })
    cases.append({**finalizing, "final_execution_order": [raw_order[1]]})
    cases.append({**complete, "audit_present": False})
    cases.append({**complete, "completion_seal_present": False})
    cases.append({**complete, "worker_active_count": 1})
    cases.append({
        **preparing,
        "raw_execution_order": [raw_order[0]],
        "validated_raw_documents": [raw_documents[0]],
    })
    cases.append({**preparing, "resume_event_ids": ["resume-event"]})
    for values in cases:
        with pytest.raises(RTA4PilotExecutionError):
            validate_pilot_phase_inventory(canonical, **values)


@pytest.mark.parametrize(
    "execution_class",
    [RTA4_PILOT_EXECUTION_CLASS, RTA4_PILOT_TEST_EXECUTION_CLASS],
)
def test_trace_exemption_is_raw_bound_and_domain_independent(
    tmp_path, execution_class,
):
    context = _context(
        tmp_path, execution_class=execution_class,
    )
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    record = next(row for row in runner.records if row.kind == "simulation")
    certificate = _synthetic_certificate(record)
    simulation_id = pilot_execution._simulation_identity(
        record, certificate,
    )[3]

    def raw(*, engineering_error, timed_out, with_trace=False):
        payload = b"{}" if with_trace else b""
        metrics = {
            "runtime_wall_milliseconds": 1,
            "runtime_cpu_milliseconds": 1,
            "peak_rss_bytes": 1,
            "timed_out": timed_out,
            "attempt_count": 1,
            "worker_throughput_milli_records_per_second": 1,
            "simulation_wall_milliseconds": 1,
            "trace_size_bytes": len(payload),
            "engineering_error": engineering_error,
        }
        return pilot_execution.build_pilot_raw_terminal(
            runner.selected[str(record.execution_id)],
            context["execution"], certificate, metrics,
            trace_sha256=(
                hashlib.sha256(payload).hexdigest() if with_trace else None
            ),
            simulation_id=simulation_id,
        )

    execution_id = str(record.execution_id)
    for document in (
        raw(engineering_error=True, timed_out=False),
        raw(engineering_error=False, timed_out=True),
        raw(engineering_error=True, timed_out=True),
    ):
        assert pilot_execution._trace_exemptions_from_validated_raw(
            runner.checkpoint_context, [document], (),
        ) == frozenset({execution_id})

    successful = raw(engineering_error=False, timed_out=False)
    with pytest.raises(
        RTA4PilotExecutionError, match="successful simulation",
    ):
        pilot_execution._trace_exemptions_from_validated_raw(
            runner.checkpoint_context, [successful], (),
        )

    trace_bound = raw(
        engineering_error=True, timed_out=True, with_trace=True,
    )
    assert pilot_execution._trace_exemptions_from_validated_raw(
        runner.checkpoint_context, [trace_bound], (execution_id,),
    ) == frozenset()
    with pytest.raises(RTA4PilotExecutionError, match="lacks observed trace"):
        pilot_execution._trace_exemptions_from_validated_raw(
            runner.checkpoint_context, [trace_bound], (),
        )

    foreign = deepcopy(successful)
    foreign["execution_id"] = "f" * 64
    material = deepcopy(foreign)
    material.pop("raw_terminal_sha256")
    foreign["raw_terminal_sha256"] = pilot_execution.domain_hash(
        pilot_execution.RTA4_PILOT_RAW_TERMINAL_DOMAIN, material,
    )
    with pytest.raises(RTA4PilotExecutionError, match="outside canonical"):
        pilot_execution._trace_exemptions_from_validated_raw(
            runner.checkpoint_context, [foreign], (),
        )


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


def test_preparing_store_resume_event_fails_closed_without_deletion(
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
    event = pilot_execution._build_resume_event(
        context["execution"], generation=1,
        preflight_started_ns=1, preflight_finished_ns=2,
        initialization_milliseconds=0,
        first_pending_execution_id=str(runner.records[0].execution_id),
    )
    path = (
        context["output"] / "rta4_pilot_resume_events"
        / "00000001.json"
    )
    atomic_write_json(path, event)
    before = path.read_bytes()
    with pytest.raises(
        RTA4PilotExecutionError,
        match="PREPARING_STORE|resume",
    ):
        audit_pilot_namespace(
            context["output"], CONFIGS, require_complete=False,
            reconstruct_store=False, allow_recovery_artifacts=True,
        )
    assert path.read_bytes() == before


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
        generation_paths = sorted(generation_root.glob("*.json"))
        target_generation = int(generation_paths[-2].stem)

        def mutate(document):
            if damage == "previous_link":
                document["previous_checkpoint_id"] = "0" * 64
                return
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
        target = _rewrite_checkpoint_suffix(
            context["output"], target_generation, mutate,
        )
    before = target.read_bytes() if target.exists() else None
    with pytest.raises(
        RTA4PilotExecutionError,
        match="checkpoint|event|transition",
    ):
        audit_pilot_namespace(
            context["output"], CONFIGS, require_complete=False,
        )
    if before is not None:
        assert target.read_bytes() == before


def test_noncurrent_canonical_static_and_monotonic_damage_is_rejected(
    tmp_path,
):
    context = _context(tmp_path)
    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    first = runner.run(
        max_records=1, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta, use_processes=False,
    )
    assert not first.complete
    resumed = runner.run(
        resume=True, certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta,
        simulation_callback=_synthetic_simulator(
            context["root"] / "historical-monotonic-traces"
        ),
        use_processes=False,
    )
    assert resumed.complete
    generation_root = context["output"] / "rta4_pilot_checkpoints"
    original = _file_bytes(context["output"])

    def documents():
        return [
            json.loads(path.read_text())
            for path in sorted(generation_root.glob("*.json"))
        ]

    def restore():
        for relative, payload in original.items():
            (context["output"] / relative).write_bytes(payload)

    static_cases = {
        "output_root": lambda row: row.__setitem__(
            "output_root", str(tmp_path / "foreign-output"),
        ),
        "taskset_store": lambda row: row.__setitem__(
            "taskset_store", str(tmp_path / "foreign-store"),
        ),
        "planned_record_count": lambda row: row.__setitem__(
            "planned_record_count", row["planned_record_count"] + 1,
        ),
        "expected_store_slot_order": lambda row: row[
            "expected_store_slot_order"
        ].append("foreign-store-slot"),
        "execution_class": lambda row: row.__setitem__(
            "execution_class", "ENGINEERING_PILOT",
        ),
        "execution_config_id": lambda row: row.__setitem__(
            "execution_config_id", "0" * 64,
        ),
    }
    for label, mutate in static_cases.items():
        rows = documents()
        target = next(
            row["checkpoint_generation"] for row in rows[1:-1]
            if row["phase"] == "PREPARING_STORE"
        )
        damaged = _rewrite_checkpoint_suffix(
            context["output"], target, mutate,
        )
        before = damaged.read_bytes()
        with pytest.raises(
            RTA4PilotExecutionError,
            match="canonical static|static binding",
        ):
            audit_pilot_namespace(context["output"], CONFIGS)
        assert damaged.read_bytes() == before, label
        restore()

    def drop_store(row):
        slot = row["completed_store_slot_order"].pop()
        row["completed_store_slot_digests"].pop(slot)

    def drop_raw(row):
        execution_id = row["completed_raw_execution_order"].pop()
        row["completed_raw_terminal_digests"].pop(execution_id)

    def reorder_raw(row):
        row["completed_raw_execution_order"][:2] = reversed(
            row["completed_raw_execution_order"][:2]
        )

    def drop_trace(row):
        row["trace_digests"].pop(next(iter(row["trace_digests"])))

    def drop_final(row):
        execution_id = row["final_terminal_execution_order"].pop()
        row["final_terminal_digests"].pop(execution_id)

    def drop_resume(row):
        row["resume_event_digests"].clear()

    rows = documents()
    current_generation = rows[-1]["checkpoint_generation"]
    monotonic_cases = (
        (
            "store",
            next(
                row["checkpoint_generation"] for row in rows
                if row["phase"] == "PREPARING_STORE"
                and len(row["completed_store_slot_order"]) >= 2
            ),
            drop_store,
        ),
        (
            "raw",
            next(
                row["checkpoint_generation"] for row in rows
                if row["phase"] == "EXECUTING"
                and len(row["completed_raw_execution_order"]) >= 1
                and row["checkpoint_generation"] < current_generation
            ),
            drop_raw,
        ),
        (
            "raw-order",
            next(
                row["checkpoint_generation"] for row in rows
                if len(row["completed_raw_execution_order"]) >= 2
                and row["checkpoint_generation"] < current_generation
            ),
            reorder_raw,
        ),
        (
            "trace",
            next(
                row["checkpoint_generation"] for row in rows
                if row["trace_digests"]
                and row["checkpoint_generation"] < current_generation
            ),
            drop_trace,
        ),
        (
            "final",
            next(
                row["checkpoint_generation"] for row in rows
                if len(row["final_terminal_execution_order"]) >= 2
                and row["checkpoint_generation"] < current_generation
            ),
            drop_final,
        ),
        (
            "resume",
            next(
                row["checkpoint_generation"] for row in rows
                if row["resume_event_digests"]
                and row["checkpoint_generation"] < current_generation
            ),
            drop_resume,
        ),
    )
    for label, target, mutate in monotonic_cases:
        damaged = _rewrite_checkpoint_suffix(
            context["output"], target, mutate,
        )
        before = damaged.read_bytes()
        with pytest.raises(RTA4PilotExecutionError):
            audit_pilot_namespace(context["output"], CONFIGS)
        assert damaged.read_bytes() == before, label
        restore()


def test_noncurrent_store_identity_and_raw_event_causality_reject_rehash(
    tmp_path,
):
    context = _context(tmp_path)
    PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    ).run(
        certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta,
        simulation_callback=_synthetic_simulator(
            context["root"] / "history-causality-traces"
        ),
        use_processes=False,
    )
    root = context["output"]
    generation_root = root / "rta4_pilot_checkpoints"
    original = _file_bytes(root)

    def rows():
        return [
            json.loads(path.read_text())
            for path in sorted(generation_root.glob("*.json"))
        ]

    def restore():
        for relative, payload in original.items():
            (root / relative).write_bytes(payload)

    documents = rows()
    store_target = next(
        row["checkpoint_generation"] for row in documents[1:-1]
        if row["phase"] == "EXECUTING"
        and row["completed_raw_count"] >= 1
    )
    damaged = _rewrite_checkpoint_suffix(
        root, store_target,
        lambda row: row.__setitem__("store_manifest_id", "f" * 64),
    )
    before = damaged.read_bytes()
    with pytest.raises(RTA4PilotExecutionError, match="store manifest"):
        audit_pilot_namespace(root, CONFIGS)
    assert damaged.read_bytes() == before
    restore()

    documents = rows()
    raw_target_row = next(
        row for row in documents[1:-1]
        if row["phase"] == "EXECUTING"
        and row["completed_raw_count"] == 1
    )
    next_execution = raw_target_row["completed_raw_execution_order"][0]
    canonical_next = next(
        execution_id
        for execution_id in PilotExecutionRunner(
            CONFIGS, context["manifest"], context["execution"],
        ).checkpoint_context.selected_execution_order
        if execution_id not in raw_target_row[
            "completed_raw_terminal_digests"
        ]
    )
    next_raw = json.loads((
        root / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
        / f"{canonical_next}.json"
    ).read_text())

    def append_two_raw(row):
        row["completed_raw_execution_order"].append(canonical_next)
        row["completed_raw_terminal_digests"][canonical_next] = next_raw[
            "raw_terminal_sha256"
        ]

    damaged = _rewrite_checkpoint_suffix(
        root, raw_target_row["checkpoint_generation"], append_two_raw,
    )
    before = damaged.read_bytes()
    with pytest.raises(RTA4PilotExecutionError, match="EXECUTING transition"):
        audit_pilot_namespace(root, CONFIGS)
    assert damaged.read_bytes() == before
    restore()

    documents = rows()
    trigger_target = next(
        row["checkpoint_generation"] for row in documents[1:-1]
        if row["phase"] == "EXECUTING"
        and row["completed_raw_count"] == 2
    )
    damaged = _rewrite_checkpoint_suffix(
        root, trigger_target, lambda _row: None,
        mutate_event=lambda event: event.__setitem__(
            "triggering_execution_id", next_execution,
        ),
    )
    before = damaged.read_bytes()
    with pytest.raises(RTA4PilotExecutionError, match="causality"):
        audit_pilot_namespace(root, CONFIGS)
    assert damaged.read_bytes() == before
    restore()

    damaged = _rewrite_checkpoint_suffix(
        root, trigger_target, lambda _row: None,
        mutate_event=lambda event: event.__setitem__(
            "event_kind", "PHASE_TRANSITION",
        ),
    )
    before = damaged.read_bytes()
    with pytest.raises(RTA4PilotExecutionError, match="causality"):
        audit_pilot_namespace(root, CONFIGS)
    assert damaged.read_bytes() == before
    restore()

    documents = rows()
    phase_target = next(
        row["checkpoint_generation"] for row in documents[1:-1]
        if row["phase"] == "FINALIZING"
        and row["final_terminal_execution_order"] == []
    )
    historical_trigger = next_execution
    damaged = _rewrite_checkpoint_suffix(
        root, phase_target, lambda _row: None,
        mutate_event=lambda event: event.update({
            "event_kind": "EXECUTION_RAW_COMMIT",
            "triggering_execution_id": historical_trigger,
        }),
    )
    before = damaged.read_bytes()
    with pytest.raises(RTA4PilotExecutionError, match="causality"):
        audit_pilot_namespace(root, CONFIGS)
    assert damaged.read_bytes() == before

@pytest.mark.parametrize(
    "stage,event_only",
    [
        ("after_executing_checkpoint_generation", False),
        ("after_executing_checkpoint_event", False),
        ("after_executing_checkpoint_event", True),
        ("after_executing_checkpoint_event", "malformed"),
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
        if event_only == "malformed":
            malformed = json.loads(event_path.read_text())
            malformed["unexpected"] = True
            atomic_write_json(event_path, malformed)
    before = {
        path: path.read_bytes()
        for path in (generation_path, event_path) if path.exists()
    }
    if event_only:
        with pytest.raises(
            RTA4PilotExecutionError,
            match="event-only|incomplete transaction",
        ):
            audit_pilot_namespace(
                context["output"], CONFIGS, require_complete=False,
                reconstruct_store=False,
                allow_recovery_artifacts=True,
            )
        with pytest.raises(RTA4PilotExecutionError):
            runner.run(
                resume=True, max_records=0,
                certificate_provider=_synthetic_certificate,
                rta_callback=_synthetic_rta, use_processes=False,
            )
        assert {
            path: path.read_bytes()
            for path in before
        } == before
        return
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
    assert generation_path.is_file()
    assert event_path.is_file()
    adopted_pointer = json.loads(
        (context["output"] / RTA4_PILOT_CHECKPOINT).read_text()
    )
    assert adopted_pointer["checkpoint_generation"] == orphan_generation
    adopted_event = json.loads(event_path.read_text())
    assert adopted_event["measurement_origin"] == (
        "RECOVERY_RECONSTRUCTED"
        if stage == "after_executing_checkpoint_generation"
        else "MEASURED_AT_WRITE"
    )
    if adopted_event["measurement_origin"] == "RECOVERY_RECONSTRUCTED":
        assert adopted_event["checkpoint_write_milliseconds"] == 0
    audit_pilot_namespace(
        context["output"], CONFIGS, require_complete=False,
    )


def test_current_plus_one_orphan_requires_full_canonical_transition(
    tmp_path,
):
    context = _context(tmp_path)
    hits = 0

    def hook(stage):
        nonlocal hits
        if stage == "after_executing_checkpoint_generation":
            hits += 1
            if hits == 4:
                raise RTA4PilotExecutionInterrupted(stage)

    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    with pytest.raises(RTA4PilotExecutionInterrupted):
        runner.run(
            max_records=3,
            certificate_provider=_synthetic_certificate,
            rta_callback=_synthetic_rta,
            simulation_callback=_synthetic_simulator(
                context["root"] / "orphan-canonical-traces"
            ),
            use_processes=False, transaction_hook=hook,
        )
    pointer = json.loads(
        (context["output"] / RTA4_PILOT_CHECKPOINT).read_text()
    )
    generation = pointer["checkpoint_generation"] + 1
    path = (
        context["output"] / "rta4_pilot_checkpoints"
        / f"{generation:08d}.json"
    )
    original = path.read_bytes()

    def foreign_output(row):
        row["output_root"] = str(tmp_path / "foreign-output")

    def foreign_count(row):
        row["planned_record_count"] += 1

    def foreign_store(row):
        row["taskset_store"] = str(tmp_path / "foreign-store")

    def foreign_config(row):
        row["execution_config_id"] = "d" * 64

    def foreign_slots(row):
        row["expected_store_slot_order"][-1] = "foreign-slot"

    def foreign_store_manifest(row):
        row["store_manifest_id"] = "c" * 64

    def store_rollback(row):
        slot = row["completed_store_slot_order"].pop()
        row["completed_store_slot_digests"].pop(slot)

    def raw_rollback(row):
        execution_id = row["completed_raw_execution_order"].pop()
        row["completed_raw_terminal_digests"].pop(execution_id)
        row["trace_digests"].pop(execution_id, None)

    def missing_trace(row):
        row["trace_digests"].clear()

    def foreign_trace(row):
        execution_id = next(iter(row["trace_digests"]))
        row["trace_digests"][execution_id] = "b" * 64

    def foreign_resume(row):
        row["resume_event_digests"] = {"f" * 64: "e" * 64}

    for label, mutate in (
        ("foreign-output", foreign_output),
        ("foreign-count", foreign_count),
        ("foreign-store", foreign_store),
        ("foreign-config", foreign_config),
        ("foreign-slots", foreign_slots),
        ("foreign-store-manifest", foreign_store_manifest),
        ("store-rollback", store_rollback),
        ("raw-rollback", raw_rollback),
        ("missing-trace", missing_trace),
        ("foreign-trace", foreign_trace),
        ("foreign-resume", foreign_resume),
    ):
        document = json.loads(original)
        mutate(document)
        atomic_write_json(path, _rehash_checkpoint_document(document))
        damaged = path.read_bytes()
        with pytest.raises(RTA4PilotExecutionError):
            audit_pilot_namespace(
                context["output"], CONFIGS, require_complete=False,
                reconstruct_store=False, allow_recovery_artifacts=True,
            )
        assert path.read_bytes() == damaged, label
        path.write_bytes(original)


@pytest.mark.parametrize("damage", ["next-trigger", "event-kind"])
def test_current_plus_one_event_pair_requires_canonical_causality(
    tmp_path, damage,
):
    context = _context(tmp_path)
    hits = 0

    def hook(stage):
        nonlocal hits
        if stage == "after_executing_checkpoint_event":
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
    generation = pointer["checkpoint_generation"] + 1
    generation_path = (
        context["output"] / "rta4_pilot_checkpoints"
        / f"{generation:08d}.json"
    )
    event_path = (
        context["output"] / "rta4_pilot_checkpoint_events"
        / f"{generation:08d}.json"
    )
    checkpoint = json.loads(generation_path.read_text())
    event = json.loads(event_path.read_text())
    if damage == "next-trigger":
        canonical_order = PilotExecutionRunner(
            CONFIGS, context["manifest"], context["execution"],
        ).checkpoint_context.selected_execution_order
        event["triggering_execution_id"] = canonical_order[
            len(checkpoint["completed_raw_execution_order"])
        ]
    else:
        event["event_kind"] = "PHASE_TRANSITION"
    damaged = pilot_execution._build_checkpoint_event(
        checkpoint, generation_path,
        event_kind=event["event_kind"],
        triggering_execution_id=event["triggering_execution_id"],
        measurement_origin=event["measurement_origin"],
        write_milliseconds=event["checkpoint_write_milliseconds"],
    )
    atomic_write_json(event_path, damaged)
    before = event_path.read_bytes()
    with pytest.raises(RTA4PilotExecutionError, match="causality"):
        audit_pilot_namespace(
            context["output"], CONFIGS, require_complete=False,
            reconstruct_store=False, allow_recovery_artifacts=True,
        )
    assert event_path.read_bytes() == before


@pytest.mark.parametrize("damage", ["generation", "event", "raw"])
def test_recovery_adoption_revalidates_authorized_bytes(
    tmp_path, damage,
):
    context = _context(tmp_path)
    hits = 0

    def hook(stage):
        nonlocal hits
        if stage == "after_executing_checkpoint_event":
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
    preflight = runner._resume_preflight(allow_recovery_artifacts=True)
    authorization = preflight.recovery_candidate
    assert isinstance(
        authorization, pilot_execution.AuthorizedRecoveryCandidate,
    )
    if damage == "generation":
        target = authorization.generation_path
    elif damage == "event":
        target = authorization.event_path
        assert target is not None
    else:
        target = next((
            context["output"] / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
        ).glob("*.json"))
    pointer_path = context["output"] / RTA4_PILOT_CHECKPOINT
    pointer_before = pointer_path.read_bytes()
    original = target.read_bytes()
    target.write_bytes(original + b" ")
    damaged_bytes = target.read_bytes()
    with pytest.raises(RTA4PilotExecutionError):
        runner._adopt_recovery_candidate(
            authorization, transaction_hook=None,
        )
    assert target.read_bytes() == damaged_bytes
    assert pointer_path.read_bytes() == pointer_before
    assert authorization.generation_path.exists()
    if authorization.event_path is not None:
        assert authorization.event_path.exists()
    target.write_bytes(original)
    fresh = runner._resume_preflight(allow_recovery_artifacts=True)
    assert fresh.recovery_candidate == authorization
    runner._adopt_recovery_candidate(
        authorization, transaction_hook=None,
    )
    assert authorization.generation_path.exists()
    if authorization.event_path is not None:
        assert authorization.event_path.exists()
    assert json.loads(pointer_path.read_text())[
        "checkpoint_generation"
    ] == authorization.generation_number


def _interrupted_raw_recovery_context(tmp_path, *, event_pair):
    context = _context(tmp_path)
    stage = (
        "after_executing_checkpoint_event"
        if event_pair else "after_executing_checkpoint_generation"
    )
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
    authorization = runner._resume_preflight(
        allow_recovery_artifacts=True,
    ).recovery_candidate
    assert isinstance(
        authorization, pilot_execution.AuthorizedRecoveryCandidate,
    )
    assert (authorization.event_path is not None) is event_pair
    return context, runner, authorization


def _interrupted_complete_recovery_context(tmp_path):
    context = _context(tmp_path)

    def hook(stage):
        if stage == "after_pilot_complete_checkpoint_generation":
            raise RTA4PilotExecutionInterrupted(stage)

    runner = PilotExecutionRunner(
        CONFIGS, context["manifest"], context["execution"],
    )
    with pytest.raises(RTA4PilotExecutionInterrupted):
        runner.run(
            certificate_provider=_synthetic_certificate,
            rta_callback=_synthetic_rta,
            simulation_callback=_synthetic_simulator(
                context["root"] / "complete-recovery-traces"
            ),
            use_processes=False, transaction_hook=hook,
        )
    authorization = runner._resume_preflight(
        allow_recovery_artifacts=True,
    ).recovery_candidate
    assert isinstance(
        authorization, pilot_execution.AuthorizedRecoveryCandidate,
    )
    assert authorization.event_path is None
    return context, runner, authorization


def test_generation_only_adoption_does_not_repeat_execution_or_overhead(
    tmp_path,
):
    context, runner, authorization = _interrupted_raw_recovery_context(
        tmp_path, event_pair=False,
    )
    rta_calls = []
    simulation_calls = []

    def rta(record, certificate):
        rta_calls.append(str(record.execution_id))
        return _synthetic_rta(record, certificate)

    simulator = _synthetic_simulator(
        context["root"] / "reconstructed-resume-traces"
    )

    def simulation(*args):
        simulation_calls.append(str(args[0].execution_id))
        return simulator(*args)

    resumed = runner.run(
        resume=True,
        certificate_provider=_synthetic_certificate,
        rta_callback=rta, simulation_callback=simulation,
        use_processes=False,
    )
    assert resumed.complete
    assert len(rta_calls) + len(simulation_calls) == len(runner.records) - 1
    event_path = (
        context["output"] / "rta4_pilot_checkpoint_events"
        / f"{authorization.generation_number:08d}.json"
    )
    event = json.loads(event_path.read_text())
    assert event["measurement_origin"] == "RECOVERY_RECONSTRUCTED"
    assert event["checkpoint_write_milliseconds"] == 0
    adopted_execution = json.loads(
        authorization.generation_path.read_text()
    )["completed_raw_execution_order"][-1]
    final = json.loads((
        context["output"] / RTA4_PILOT_FINAL_TERMINAL_DIRECTORY
        / f"{adopted_execution}.json"
    ).read_text())
    assert final["checkpoint_overhead_milliseconds"] == 0
    assert audit_pilot_namespace(
        context["output"], CONFIGS,
    )["checkpoint_state"] == "PILOT_COMPLETE"


@pytest.mark.parametrize(
    "crash_stage",
    [
        "before_recovery_event",
        "after_recovery_event",
        "after_recovery_pointer",
    ],
)
def test_recovery_adoption_crash_windows_resume_deterministically(
    tmp_path, crash_stage,
):
    context, runner, authorization = _interrupted_raw_recovery_context(
        tmp_path, event_pair=False,
    )
    pointer_path = context["output"] / RTA4_PILOT_CHECKPOINT
    previous_pointer = pointer_path.read_bytes()
    callback_calls = []

    def hook(stage):
        if stage == crash_stage:
            raise RTA4PilotExecutionInterrupted(stage)

    with pytest.raises(RTA4PilotExecutionInterrupted):
        runner.run(
            resume=True, max_records=0,
            certificate_provider=_synthetic_certificate,
            rta_callback=lambda *args: callback_calls.append(args),
            use_processes=False, transaction_hook=hook,
        )
    event_path = (
        context["output"] / "rta4_pilot_checkpoint_events"
        / f"{authorization.generation_number:08d}.json"
    )
    if crash_stage == "before_recovery_event":
        assert not event_path.exists()
        assert pointer_path.read_bytes() == previous_pointer
    else:
        assert event_path.is_file()
    if crash_stage != "after_recovery_pointer":
        assert pointer_path.read_bytes() == previous_pointer
    resumed = runner.run(
        resume=True, max_records=0,
        certificate_provider=_synthetic_certificate,
        rta_callback=lambda *args: callback_calls.append(args),
        use_processes=False,
    )
    assert resumed.processed_count == 0
    assert callback_calls == []
    assert authorization.generation_path.is_file()
    assert event_path.is_file()
    assert json.loads(pointer_path.read_text())[
        "checkpoint_generation"
    ] == authorization.generation_number


@pytest.mark.parametrize(
    "damage",
    [
        "store-manifest", "certificate", "raw",
        "worker-registry", "root-inventory",
    ],
)
def test_pair_adoption_rejects_namespace_changes_without_mutation(
    tmp_path, damage,
):
    context, runner, authorization = _interrupted_raw_recovery_context(
        tmp_path, event_pair=True,
    )
    pointer_path = context["output"] / RTA4_PILOT_CHECKPOINT
    if damage == "store-manifest":
        target = context["store"] / pilot_execution.RTA4_PILOT_STORE_MANIFEST
    elif damage == "certificate":
        target = next(
            path for path in context["store"].glob("*.json")
            if path.name != pilot_execution.RTA4_PILOT_STORE_MANIFEST
        )
    elif damage == "raw":
        target = next((
            context["output"] / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
        ).glob("*.json"))
    elif damage == "worker-registry":
        target = (
            context["output"]
            / pilot_execution.RTA4_PILOT_WORKER_TEMP_REGISTRY
        )
    else:
        target = context["output"] / "unexpected-recovery-evidence"
        target.write_text("foreign\n", encoding="utf-8")
    if damage != "root-inventory":
        target.write_bytes(target.read_bytes() + b" ")
    pointer_before = pointer_path.read_bytes()
    output_before = _file_bytes(context["output"])
    store_before = _file_bytes(context["store"])
    with pytest.raises(RTA4PilotExecutionError):
        runner._adopt_recovery_candidate(
            authorization, transaction_hook=None,
        )
    assert pointer_path.read_bytes() == pointer_before
    assert _file_bytes(context["output"]) == output_before
    assert _file_bytes(context["store"]) == store_before
    assert authorization.generation_path.is_file()
    assert authorization.event_path is not None
    assert authorization.event_path.is_file()


@pytest.mark.parametrize("damage", ["final", "trace", "audit", "seal"])
def test_complete_adoption_rejects_file_evidence_changes(
    tmp_path, damage,
):
    context, runner, authorization = (
        _interrupted_complete_recovery_context(tmp_path)
    )
    if damage == "final":
        target = next((
            context["output"] / RTA4_PILOT_FINAL_TERMINAL_DIRECTORY
        ).glob("*.json"))
    elif damage == "trace":
        target = next((
            context["output"] / pilot_execution.RTA4_PILOT_TRACE_DIRECTORY
        ).glob("*.json"))
    elif damage == "audit":
        target = context["output"] / RTA4_PILOT_AUDIT
    else:
        target = context["output"] / RTA4_PILOT_COMPLETION_SEAL
    target.write_bytes(target.read_bytes() + b" ")
    pointer_path = context["output"] / RTA4_PILOT_CHECKPOINT
    pointer_before = pointer_path.read_bytes()
    output_before = _file_bytes(context["output"])
    store_before = _file_bytes(context["store"])
    with pytest.raises(RTA4PilotExecutionError):
        runner._adopt_recovery_candidate(
            authorization, transaction_hook=None,
        )
    assert pointer_path.read_bytes() == pointer_before
    assert _file_bytes(context["output"]) == output_before
    assert _file_bytes(context["store"]) == store_before
    assert authorization.generation_path.is_file()
    event_path = (
        context["output"] / "rta4_pilot_checkpoint_events"
        / f"{authorization.generation_number:08d}.json"
    )
    assert not event_path.exists()


def test_final_prewrite_revalidation_rejects_injected_change(
    tmp_path,
):
    context, runner, authorization = _interrupted_raw_recovery_context(
        tmp_path, event_pair=True,
    )
    pointer_path = context["output"] / RTA4_PILOT_CHECKPOINT
    pointer_before = pointer_path.read_bytes()
    generation_before = authorization.generation_path.read_bytes()
    event_before = authorization.event_path.read_bytes()

    def mutate_before_pointer(stage):
        if stage == "before_recovery_pointer":
            authorization.generation_path.write_bytes(
                generation_before + b" "
            )

    with pytest.raises(RTA4PilotExecutionError):
        runner._adopt_recovery_candidate(
            authorization, transaction_hook=mutate_before_pointer,
        )
    assert pointer_path.read_bytes() == pointer_before
    assert authorization.generation_path.read_bytes() == (
        generation_before + b" "
    )
    assert authorization.event_path.read_bytes() == event_before


def test_worker_recovery_failure_does_not_adopt_candidate(
    tmp_path, monkeypatch,
):
    context, runner, authorization = _interrupted_raw_recovery_context(
        tmp_path, event_pair=True,
    )
    runner._register_worker_batch((runner.records[0],))
    pointer_path = context["output"] / RTA4_PILOT_CHECKPOINT
    pointer_before = pointer_path.read_bytes()
    generation_before = authorization.generation_path.read_bytes()
    event_before = authorization.event_path.read_bytes()
    monkeypatch.setattr(
        pilot_execution.shutil, "rmtree",
        lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
    )
    with pytest.raises(RTA4PilotExecutionError, match="could not clean"):
        runner.run(
            resume=True, max_records=0,
            certificate_provider=_synthetic_certificate,
            rta_callback=_synthetic_rta, use_processes=False,
        )
    assert pointer_path.read_bytes() == pointer_before
    assert authorization.generation_path.read_bytes() == generation_before
    assert authorization.event_path.read_bytes() == event_before


def test_candidate_change_after_worker_recovery_blocks_adoption(
    tmp_path, monkeypatch,
):
    context, runner, authorization = _interrupted_raw_recovery_context(
        tmp_path, event_pair=True,
    )
    runner._register_worker_batch((runner.records[0],))
    pointer_path = context["output"] / RTA4_PILOT_CHECKPOINT
    pointer_before = pointer_path.read_bytes()
    generation_before = authorization.generation_path.read_bytes()
    event_before = authorization.event_path.read_bytes()
    recover = runner._recover_worker_temporaries

    def recover_then_change():
        recover()
        authorization.generation_path.write_bytes(
            generation_before + b" "
        )

    monkeypatch.setattr(
        runner, "_recover_worker_temporaries", recover_then_change,
    )
    with pytest.raises(RTA4PilotExecutionError):
        runner.run(
            resume=True, max_records=0,
            certificate_provider=_synthetic_certificate,
            rta_callback=_synthetic_rta, use_processes=False,
        )
    assert pointer_path.read_bytes() == pointer_before
    assert authorization.generation_path.read_bytes() == (
        generation_before + b" "
    )
    assert authorization.event_path.read_bytes() == event_before
    registry = runner._load_worker_registry()
    assert not pilot_execution._worker_registry_active_entries(registry)
    assert list((
        context["output"] / pilot_execution.RTA4_PILOT_WORKER_TRACE_DIRECTORY
    ).iterdir()) == []


def test_error_timeout_trace_rules_use_file_backed_audit(tmp_path):
    error_context = _context(tmp_path / "error")
    error_runner = PilotExecutionRunner(
        CONFIGS, error_context["manifest"], error_context["execution"],
    )
    simulation_index = next(
        index for index, record in enumerate(error_runner.records, 1)
        if record.kind == "simulation"
    )
    simulator = _synthetic_simulator(
        error_context["root"] / "error-bound-traces"
    )

    def error_with_trace(*args):
        result = dict(simulator(*args))
        result["__pilot_metric_overrides__"] = {
            "engineering_error": True,
        }
        return result

    error_runner.run(
        max_records=simulation_index,
        certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta,
        simulation_callback=error_with_trace,
        use_processes=False,
    )
    error_raw = next(
        json.loads(path.read_text())
        for path in (
            error_context["output"] / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
        ).glob("*.json")
        if json.loads(path.read_text())["kind"] == "simulation"
    )
    assert error_raw["engineering_error"] is True
    assert error_raw["trace_filename"] is not None
    audit_pilot_namespace(
        error_context["output"], CONFIGS, require_complete=False,
    )
    trace_path = (
        error_context["output"] / pilot_execution.RTA4_PILOT_TRACE_DIRECTORY
        / error_raw["trace_filename"]
    )
    trace_path.write_bytes(trace_path.read_bytes() + b" ")
    with pytest.raises(RTA4PilotExecutionError, match="trace"):
        audit_pilot_namespace(
            error_context["output"], CONFIGS, require_complete=False,
        )

    timeout_context = _context(tmp_path / "timeout")
    timeout_runner = PilotExecutionRunner(
        CONFIGS, timeout_context["manifest"], timeout_context["execution"],
    )
    timeout_index = next(
        index for index, record in enumerate(timeout_runner.records, 1)
        if record.kind == "simulation"
    )

    def timeout_without_trace(*_args):
        return {
            "trace_path": None,
            "__pilot_metric_overrides__": {
                "engineering_error": False,
                "timed_out": True,
            },
        }

    timeout_runner.run(
        max_records=timeout_index,
        certificate_provider=_synthetic_certificate,
        rta_callback=_synthetic_rta,
        simulation_callback=timeout_without_trace,
        use_processes=False,
    )
    timeout_raw = next(
        json.loads(path.read_text())
        for path in (
            timeout_context["output"] / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
        ).glob("*.json")
        if json.loads(path.read_text())["kind"] == "simulation"
    )
    assert timeout_raw["engineering_error"] is False
    assert timeout_raw["timed_out"] is True
    assert timeout_raw["trace_filename"] is None
    audit_pilot_namespace(
        timeout_context["output"], CONFIGS, require_complete=False,
    )


def test_complete_orphan_requires_exact_audit_and_seal_binding(
    tmp_path,
):
    context = _context(tmp_path)

    def hook(stage):
        if stage == "after_pilot_complete_checkpoint_generation":
            raise RTA4PilotExecutionInterrupted(stage)

    with pytest.raises(RTA4PilotExecutionInterrupted):
        PilotExecutionRunner(
            CONFIGS, context["manifest"], context["execution"],
        ).run(
            certificate_provider=_synthetic_certificate,
            rta_callback=_synthetic_rta,
            simulation_callback=_synthetic_simulator(
                context["root"] / "complete-orphan-traces"
            ),
            use_processes=False, transaction_hook=hook,
        )
    pointer = json.loads(
        (context["output"] / RTA4_PILOT_CHECKPOINT).read_text()
    )
    generation = pointer["checkpoint_generation"] + 1
    path = (
        context["output"] / "rta4_pilot_checkpoints"
        / f"{generation:08d}.json"
    )
    original = path.read_bytes()

    def final_rollback(document):
        execution_id = document["final_terminal_execution_order"].pop()
        document["final_terminal_digests"].pop(execution_id)

    cases = (
        ("audit-id", lambda row: row.__setitem__("audit_id", "f" * 64)),
        (
            "audit-sha",
            lambda row: row.__setitem__(
                "audit_document_sha256", "e" * 64,
            ),
        ),
        (
            "seal-id",
            lambda row: row.__setitem__(
                "completion_seal_id", "d" * 64,
            ),
        ),
        (
            "seal-sha",
            lambda row: row.__setitem__(
                "completion_seal_sha256", "c" * 64,
            ),
        ),
        ("final-rollback", final_rollback),
    )
    for label, mutate in cases:
        document = json.loads(original)
        mutate(document)
        atomic_write_json(path, _rehash_checkpoint_document(document))
        before = path.read_bytes()
        with pytest.raises(RTA4PilotExecutionError):
            audit_pilot_namespace(
                context["output"], CONFIGS, require_complete=False,
                reconstruct_store=False, allow_recovery_artifacts=True,
            )
        assert path.read_bytes() == before, label
        path.write_bytes(original)


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
