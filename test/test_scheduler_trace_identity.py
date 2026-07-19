import json
import os
import signal
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
import acceptance_ratio_test as acceptance
from experiments.v9_3.simulation_engine import run_paired_simulation
from experiments.v9_3.simulation_result import SimulationStatus
from scripts.build_identity import validate_build_identity


RTSIM_ENV = "PARTSIM_RTSIM_BIN"


def resolve_rtsim_binary(environment=None):
    """Resolve only an explicitly selected, non-workspace integration build."""
    environment = os.environ if environment is None else environment
    raw = str(environment.get(RTSIM_ENV, "")).strip()
    if not raw:
        return None
    binary = Path(raw).expanduser().resolve(strict=False)
    if not binary.is_file():
        raise ValueError("{} does not name a file: {}".format(
            RTSIM_ENV, binary
        ))
    if not os.access(binary, os.X_OK):
        raise ValueError("{} is not executable: {}".format(
            RTSIM_ENV, binary
        ))
    build_root = binary.parent.parent
    workspace_build = (PROJECT_ROOT / "build").resolve()
    if build_root == workspace_build:
        raise ValueError(
            "workspace build is not accepted as a fresh integration binary"
        )
    if not (build_root / "CMakeCache.txt").is_file():
        raise ValueError("binary is not from a configured CMake build")
    if not (build_root / "librtsim" / "librtsim.so").is_file():
        raise ValueError("binary build has no matching librtsim.so")
    validate_build_identity(binary, PROJECT_ROOT, required_build_type="Release")
    return binary


def require_rtsim_binary():
    try:
        binary = resolve_rtsim_binary()
    except ValueError as error:
        pytest.fail(str(error))
    if binary is None:
        pytest.skip(
            "set PARTSIM_RTSIM_BIN to the fresh CMake build rtsim binary"
        )
    return binary, binary.parent.parent


def test_binary_resolver_requires_explicit_environment():
    assert resolve_rtsim_binary({}) is None


def test_binary_resolver_rejects_missing_binary(tmp_path):
    with pytest.raises(ValueError, match="does not name a file"):
        resolve_rtsim_binary({RTSIM_ENV: str(tmp_path / "missing")})


def test_binary_resolver_rejects_wrong_build_layout(tmp_path):
    binary = tmp_path / "wrong" / "rtsim" / "rtsim"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    with pytest.raises(ValueError, match="configured CMake build"):
        resolve_rtsim_binary({RTSIM_ENV: str(binary)})


def test_binary_resolver_rejects_workspace_build_when_present():
    binary = PROJECT_ROOT / "build" / "rtsim" / "rtsim"
    if not binary.is_file():
        pytest.skip("workspace binary is absent")
    with pytest.raises(ValueError, match="workspace build"):
        resolve_rtsim_binary({RTSIM_ENV: str(binary)})


def test_binary_resolver_rejects_layout_without_build_identity(tmp_path):
    build_root = tmp_path / "layout-only"
    binary = build_root / "rtsim" / "rtsim"
    library = build_root / "librtsim" / "librtsim.so"
    binary.parent.mkdir(parents=True)
    library.parent.mkdir(parents=True)
    (build_root / "CMakeCache.txt").write_text(
        "CMAKE_BUILD_TYPE:STRING=Release\n", encoding="utf-8"
    )
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    library.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="identity is missing"):
        resolve_rtsim_binary({RTSIM_ENV: str(binary)})


def system_yaml(scheduler, initial=1.0, maximum=1.0, harvest=0.0):
    return f"""cpu_islands:
  - name: test_cpus
    numcpus: 1
    kernel:
      scheduler: {scheduler}
      task_placement: global
    volts: [1.00]
    freqs: [8100]
    base_freq: 8100
    power_model: test_model
    speed_model: test_model
energy_management:
  initial_energy: {initial}
  max_energy: {maximum}
  time_of_day_ms: 43200000
  base_harvesting_rate: {harvest}
  use_real_solar_data: false
  solar_data_file: ""
  pv_efficiency: 0.18
  pv_area_m2: 1.0
  periodic_collection_interval_ms: 1
  enable_energy_recovery: false
  scheduler_energy_model:
    base_power: 0.5
    workload_coefficients:
      bzip2: 1.2
      idle: 0.1
    frequency_power_ratios:
      8100: 0.93
power_models:
  - name: test_model
    type: balsini_pannocchi
    params:
      - workload: bzip2
        power_params: [0.001, 1.0, 1.0, 1.0e-10]
        speed_params: [1, 0, 0, 0]
      - workload: idle
        power_params: [0.001, 1.0, 1.0, 1.0e-10]
        speed_params: [1, 0, 0, 0]
"""


def task_yaml(wcet=1, deadline=20, iat=50, phase=0):
    return f"""taskset:
  - name: H
    iat: {iat}
    deadline: {deadline}
    startcpu: 0
    ph: {phase}
    qs: 100
    params: "period=50,wcet={wcet},arrival_offset=0,workload=bzip2"
    code:
      - fixed({wcet}, bzip2)
"""


def run_scheduler(tmp_path, scheduler, *, initial=1.0, maximum=1.0,
                  harvest=0.0, duration=4, wcet=1, deadline=20,
                  iat=50, phase=0, task_text=None):
    rtsim, build_root = require_rtsim_binary()
    system = tmp_path / f"{scheduler}_system.yml"
    tasks = tmp_path / f"{scheduler}_tasks.yml"
    trace = tmp_path / f"{scheduler}.json"
    system.write_text(
        system_yaml(scheduler, initial, maximum, harvest), encoding="utf-8"
    )
    tasks.write_text(
        task_text if task_text is not None else task_yaml(
            wcet, deadline, iat=iat, phase=phase
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(build_root / "librtsim")
    try:
        semantic_hash = acceptance.taskset_semantic_hash(tasks)
    except ValueError:
        # Invalid-model fixtures must reach the C++ loader to verify its
        # fail-fast diagnostics; no trace can be published for these cases.
        semantic_hash = "0" * 64
    completed = subprocess.run(
        [str(rtsim), str(system), str(tasks), str(duration),
         "--trace", str(trace), "--semantic-traces",
         "--run-id", "test-run-{}".format(scheduler),
         "--taskset-semantic-hash", semantic_hash],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    trace_data = None
    if trace.exists():
        try:
            trace_data = json.loads(trace.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            trace_data = None
    return completed, trace_data


def direct_trace_command(tmp_path, trace, run_id, duration=50):
    rtsim, build_root = require_rtsim_binary()
    system = tmp_path / "round6_lock_system.yml"
    tasks = tmp_path / "round6_lock_tasks.yml"
    system.write_text(system_yaml("gpfp_asap_block"), encoding="utf-8")
    tasks.write_text(task_yaml(), encoding="utf-8")
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(build_root / "librtsim")
    command = [
        str(rtsim), str(system), str(tasks), str(duration),
        "--trace", str(trace), "--semantic-traces", "--run-id", run_id,
        "--taskset-semantic-hash", acceptance.taskset_semantic_hash(tasks),
    ]
    return command, env


@pytest.mark.parametrize("scheduler", acceptance.ALGORITHMS)
def test_nine_schedulers_emit_canonical_run_identity(tmp_path, scheduler):
    completed, trace = run_scheduler(tmp_path, scheduler)
    assert completed.returncode == 0, completed.stderr
    assert trace["configured_scheduler"] == scheduler
    assert trace["scheduler_display_name"] == (
        acceptance.ALGO_DISPLAY_NAMES[scheduler]
    )
    assert trace["scheduler_implementation"] == (
        acceptance.SCHEDULER_IMPLEMENTATIONS[scheduler]
    )
    assert trace["scheduler_rtti_name"]
    assert trace["run_id"] == "test-run-{}".format(scheduler)
    assert trace["taskset_semantic_hash"] == acceptance.taskset_semantic_hash(
        tmp_path / f"{scheduler}_tasks.yml"
    )
    assert trace["trace_schema_version"] == acceptance.TRACE_SCHEMA_VERSION
    assert trace["simulation_completed"] is True
    assert trace["simulation_completion_reason"] == "reached_horizon"


@pytest.mark.parametrize("scheduler", acceptance.ALGORITHMS)
def test_nine_schedulers_are_trace_deterministic_across_three_runs(
        tmp_path, scheduler):
    traces = []
    for _ in range(3):
        completed, trace = run_scheduler(tmp_path, scheduler)
        assert completed.returncode == 0, completed.stderr
        traces.append(json.dumps(trace, sort_keys=True, separators=(",", ":")))
    assert len(set(traces)) == 1


def test_unknown_scheduler_fails_without_trace_identity_fallback(tmp_path):
    completed, trace = run_scheduler(tmp_path, "gpfp_unknown_scheduler")
    assert completed.returncode != 0
    assert trace is None


def test_unknown_scheduler_does_not_replace_existing_trace(tmp_path):
    old_trace = tmp_path / "gpfp_unknown_scheduler.json"
    old_bytes = b'{"old_run_id":"preserved"}\n'
    old_trace.write_bytes(old_bytes)
    completed, _trace = run_scheduler(tmp_path, "gpfp_unknown_scheduler")
    assert completed.returncode != 0
    assert old_trace.read_bytes() == old_bytes
    assert not list(tmp_path.glob("*.partial.*"))


def test_formal_json_trace_requires_taskset_semantic_hash(tmp_path):
    trace = tmp_path / "missing-taskset-hash.json"
    command, env = direct_trace_command(
        tmp_path, trace, "missing-taskset-hash"
    )
    hash_index = command.index("--taskset-semantic-hash")
    del command[hash_index:hash_index + 2]
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, env=env, capture_output=True,
        text=True, timeout=30,
    )
    assert completed.returncode != 0
    assert "formal JSON trace requires" in completed.stderr
    assert not trace.exists()


def test_concurrent_different_runs_cannot_publish_same_trace(tmp_path):
    trace = tmp_path / "contended.json"
    command_a, env = direct_trace_command(tmp_path, trace, "round6-a", 500)
    command_b, _ = direct_trace_command(tmp_path, trace, "round6-b", 500)
    first = subprocess.Popen(
        command_a, cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    second = subprocess.Popen(
        command_b, cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    first_output = first.communicate(timeout=30)
    second_output = second.communicate(timeout=30)

    assert sorted([first.returncode, second.returncode]) == [0, 1], (
        first_output, second_output
    )
    published = json.loads(trace.read_text(encoding="utf-8"))
    assert published["run_id"] in {"round6-a", "round6-b"}
    assert not (tmp_path / "contended.json.lock").exists()
    assert not list(tmp_path.glob("contended.json.partial.*"))


def test_existing_trace_is_only_idempotent_for_same_run_and_bytes(tmp_path):
    trace = tmp_path / "existing.json"
    command, env = direct_trace_command(tmp_path, trace, "round6-original")
    first = subprocess.run(
        command, cwd=PROJECT_ROOT, env=env, capture_output=True,
        text=True, timeout=30,
    )
    assert first.returncode == 0, first.stderr
    original = trace.read_bytes()

    same = subprocess.run(
        command, cwd=PROJECT_ROOT, env=env, capture_output=True,
        text=True, timeout=30,
    )
    assert same.returncode == 0, same.stderr
    assert trace.read_bytes() == original

    different, _ = direct_trace_command(tmp_path, trace, "round6-other")
    refused = subprocess.run(
        different, cwd=PROJECT_ROOT, env=env, capture_output=True,
        text=True, timeout=30,
    )
    assert refused.returncode != 0
    assert "trace_target_exists_for_different_run" in refused.stderr
    assert trace.read_bytes() == original


def test_stale_trace_lock_is_not_silently_stolen(tmp_path):
    trace = tmp_path / "stale-lock.json"
    lock = tmp_path / "stale-lock.json.lock"
    lock.write_text("pid=999999\nrun_id=crashed-run\n", encoding="utf-8")
    command, env = direct_trace_command(tmp_path, trace, "new-run")
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, env=env, capture_output=True,
        text=True, timeout=30,
    )
    assert completed.returncode != 0
    assert "trace_target_locked" in completed.stderr
    assert lock.is_file()
    assert not trace.exists()
    assert not list(tmp_path.glob("stale-lock.json.partial.*"))


@pytest.mark.parametrize('replacement', [
    'metadata', 'nonce', 'run_id', 'inode', 'delete_recreate',
])
def test_original_owner_never_removes_replaced_trace_lock(
        tmp_path, replacement):
    trace = tmp_path / ('owner-{}.json'.format(replacement))
    command, env = direct_trace_command(
        tmp_path, trace, 'round7-original', 50000
    )
    process = subprocess.Popen(
        command, cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    lock = Path(str(trace) + '.lock')
    deadline = time.monotonic() + 10
    while not lock.is_file() and process.poll() is None \
            and time.monotonic() < deadline:
        time.sleep(0.002)
    assert lock.is_file(), process.communicate(timeout=5)[1]
    process.send_signal(signal.SIGSTOP)
    original = lock.read_text(encoding='utf-8')
    if replacement == 'metadata':
        lock.write_text(original + 'replacement=1\n', encoding='utf-8')
    elif replacement == 'nonce':
        lock.write_text(
            original.replace('nonce=', 'nonce=replacement-', 1),
            encoding='utf-8',
        )
    elif replacement == 'run_id':
        lock.write_text(
            original.replace('run_id=round7-original',
                             'run_id=round7-replacement'),
            encoding='utf-8',
        )
    else:
        replacement_path = lock.with_suffix('.replacement')
        replacement_path.write_text(
            'pid=999999\nrun_id=round7-replacement\nnonce=new-owner\n'
            'target={}\n'.format(trace),
            encoding='utf-8',
        )
        if replacement == 'delete_recreate':
            lock.unlink()
        os.replace(replacement_path, lock)
    process.send_signal(signal.SIGCONT)
    stdout, stderr = process.communicate(timeout=60)
    assert process.returncode == 0, stdout + stderr
    assert lock.is_file()
    assert 'round7-replacement' in lock.read_text(encoding='utf-8') or (
        replacement in {'metadata', 'nonce'}
    )

def test_strict_json_validator_rejects_keyword_substring_fixture(tmp_path):
    trace = tmp_path / "malformed.json"
    malformed = json.dumps({
        "fake": (
            '\"trace_schema_version\": 2, \"run_count\": 1, '
            '\"simulation_completed\": true, '
            '\"run_id\": \"round6-malformed\"'
        )
    }).encode("utf-8")
    trace.write_bytes(malformed)
    command, env = direct_trace_command(
        tmp_path, trace, "round6-malformed"
    )
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, env=env, capture_output=True,
        text=True, timeout=30,
    )
    assert completed.returncode != 0
    assert "invalid trace_schema_version" in completed.stderr
    assert trace.read_bytes() == malformed
    assert not (tmp_path / "malformed.json.lock").exists()
    assert not list(tmp_path.glob("malformed.json.partial.*"))


def test_strict_json_validator_rejects_duplicate_top_level_keys(tmp_path):
    trace = tmp_path / "duplicate.json"
    malformed = (
        '{"trace_schema_version":2,"trace_schema_version":2,'
        '"run_id":"round6-duplicate"}'
    ).encode("utf-8")
    trace.write_bytes(malformed)
    command, env = direct_trace_command(
        tmp_path, trace, "round6-duplicate"
    )
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, env=env, capture_output=True,
        text=True, timeout=30,
    )
    assert completed.returncode != 0
    assert "duplicate JSON key" in completed.stderr
    assert trace.read_bytes() == malformed


def test_external_basestat_without_transaction_hooks_is_abstract(tmp_path):
    source = tmp_path / "incomplete_stat.cpp"
    source.write_text(
        '#include <metasim/basestat.hpp>\n'
        'class IncompleteStat : public MetaSim::BaseStat {\n'
        ' public:\n'
        '  void record(double) override {}\n'
        '  void initValue() override {}\n'
        '};\n'
        'int main() { IncompleteStat value; return 0; }\n',
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["c++", "-std=c++17", "-I", str(PROJECT_ROOT / "libmetasim/include"),
         "-c", str(source), "-o", str(tmp_path / "incomplete_stat.o")],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode != 0
    assert "abstract" in completed.stderr
    assert "captureRunState" in completed.stderr
    assert "rollbackRun" in completed.stderr


def test_equal_priority_load_order_changes_hash_when_dispatch_changes(tmp_path):
    def ordered_tasks(names):
        entries = []
        for name in names:
            entries.append(
                "  - name: {}\n    iat: 20\n    deadline: 20\n"
                "    startcpu: 0\n    ph: 0\n    qs: 100\n"
                "    params: workload=bzip2\n"
                "    code:\n      - fixed(2, bzip2)\n".format(name)
            )
        return "resources: []\ntaskset:\n" + "".join(entries)

    traces = []
    hashes = []
    for label, order in (("ab", ("A", "B")), ("ba", ("B", "A"))):
        case = tmp_path / label
        case.mkdir()
        text = ordered_tasks(order)
        task_file = case / "semantic.yml"
        task_file.write_text(text, encoding="utf-8")
        hashes.append(acceptance.taskset_semantic_hash(task_file))
        completed, trace = run_scheduler(
            case, "gpfp_asap_block", duration=5, task_text=text
        )
        assert completed.returncode == 0, completed.stderr
        traces.append(tuple(
            event["task_name"] for event in trace["events"]
            if event.get("event_type") == "scheduled"
        ))

    assert traces[0] != traces[1]
    assert traces[0][:2] == ("A", "B")
    assert traces[1][:2] == ("B", "A")
    assert hashes[0] != hashes[1]


@pytest.mark.parametrize(
    "wcet,deadline,iat,phase",
    [(2, 5, 10, 0), (2, 10, 10, 0), (2, 5, 10, 3)],
)
def test_direct_yaml_accepts_constrained_deadline_contract(
        tmp_path, wcet, deadline, iat, phase):
    completed, _ = run_scheduler(
        tmp_path, "gpfp_asap_block", duration=1, wcet=wcet,
        deadline=deadline, iat=iat, phase=phase,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "wcet,deadline,iat",
    [(2, 11, 10), (6, 5, 10), (-1, 5, 10), (0, 5, 10),
     (2, 0, 10), (2, 5, 0)],
)
def test_direct_yaml_rejects_invalid_constrained_deadline_contract(
        tmp_path, wcet, deadline, iat):
    completed, _ = run_scheduler(
        tmp_path, "gpfp_asap_block", duration=1, wcet=wcet,
        deadline=deadline, iat=iat,
    )
    assert completed.returncode != 0
    assert "invalid_task_model" in completed.stderr
    assert "task=H" in completed.stderr
    assert "C=" in completed.stderr
    assert "D=" in completed.stderr
    assert "T=" in completed.stderr


@pytest.mark.parametrize(
    "field,raw",
    [
        ("deadline", "-1"), ("deadline", "abc"),
        ("deadline", "10abc"), ("deadline", "null"),
        ("deadline", "true"), ("deadline", "NaN"),
        ("deadline", "Inf"), ("iat", "-1"),
        ("iat", "abc"), ("ph", "-1"), ("ph", "10abc"),
        ("runtime", "-1"), ("runtime", "abc"),
    ],
)
def test_present_invalid_numeric_field_never_uses_default(
        tmp_path, field, raw):
    text = task_yaml(2, 5, iat=10, phase=0)
    if field == "runtime":
        text = text.replace("    deadline: 5\n", (
            "    runtime: {}\n    deadline: 5\n".format(raw)
        ))
    else:
        original = {
            "deadline": "    deadline: 5",
            "iat": "    iat: 10",
            "ph": "    ph: 0",
        }[field]
        text = text.replace(original, "    {}: {}".format(field, raw))

    completed, trace = run_scheduler(
        tmp_path, "gpfp_asap_block", duration=1, task_text=text
    )
    assert completed.returncode != 0
    assert "invalid_task_model" in completed.stderr
    assert trace is None
    assert not (tmp_path / "gpfp_asap_block.json").exists()


def test_missing_deadline_uses_implicit_deadline_only_when_absent(tmp_path):
    text = task_yaml(2, 5, iat=10, phase=0).replace(
        "    deadline: 5\n", ""
    )
    completed, trace = run_scheduler(
        tmp_path, "gpfp_asap_block", duration=1, task_text=text
    )
    assert completed.returncode == 0, completed.stderr
    assert trace["simulation_completed"] is True


def test_one_invalid_task_rejects_entire_taskset_before_trace(tmp_path):
    valid = task_yaml(2, 5, iat=10, phase=0)
    invalid = task_yaml(2, -1, iat=10, phase=0).replace(
        "taskset:\n", "", 1
    ).replace("name: H", "name: invalid", 1)
    completed, trace = run_scheduler(
        tmp_path, "gpfp_asap_block", duration=1,
        task_text=valid + invalid,
    )
    assert completed.returncode != 0
    assert "invalid_task_model" in completed.stderr
    assert "task=invalid" in completed.stderr
    assert trace is None
    assert not (tmp_path / "gpfp_asap_block.json").exists()


def test_worker_classifies_negative_deadline_as_error_without_trace(tmp_path):
    rtsim, _build_root = require_rtsim_binary()
    system = tmp_path / "worker_system.yml"
    tasks = tmp_path / "worker_tasks.yml"
    trace_dir = tmp_path / "worker_traces"
    trace_dir.mkdir()
    system.write_text(system_yaml("gpfp_asap_block"), encoding="utf-8")
    tasks.write_text(task_yaml(2, -1, iat=10), encoding="utf-8")
    result = acceptance.run_single_simulation_worker((
        "gpfp_asap_block", str(system), str(tasks), 0, 0.5, 1,
        str(trace_dir), {
            "keep_traces": True,
            "simulator_bin": str(rtsim),
            "taskset_semantic_hash": acceptance.taskset_semantic_hash(tasks),
        },
    ))
    assert result["simulation_status"] == "error"
    assert result["reason"] == "invalid_task_model"
    assert result["accepted"] is False
    assert result["rejected"] is False
    assert result["trace_path"] == ""


def test_worker_failure_never_references_or_replaces_old_trace(tmp_path):
    rtsim, _build_root = require_rtsim_binary()
    system = tmp_path / "worker_old_system.yml"
    tasks = tmp_path / "worker_old_tasks.yml"
    trace_dir = tmp_path / "worker_old_traces"
    trace_dir.mkdir()
    system.write_text(system_yaml("gpfp_asap_block"), encoding="utf-8")
    tasks.write_text(task_yaml(2, -1, iat=10), encoding="utf-8")
    old_trace = trace_dir / "trace_gpfp_asap_block_u0.50_000.json"
    old_bytes = b'{"old_run_id":"do-not-reuse"}\n'
    old_trace.write_bytes(old_bytes)
    result = acceptance.run_single_simulation_worker((
        "gpfp_asap_block", str(system), str(tasks), 0, 0.5, 1,
        str(trace_dir), {
            "keep_traces": True,
            "run_id": "new-run",
            "simulator_bin": str(rtsim),
            "taskset_semantic_hash": acceptance.taskset_semantic_hash(tasks),
        },
    ))
    assert result["simulation_status"] == "error"
    assert result["trace_path"] == ""
    assert old_trace.read_bytes() == old_bytes


def test_st_simultaneous_boundary_has_one_canonical_reason(tmp_path):
    observations = {}
    for scheduler in (
        "gpfp_st_block", "gpfp_st_nonblock", "gpfp_st_sync"
    ):
        completed, trace = run_scheduler(
            tmp_path,
            scheduler,
            initial=0.0005,
            maximum=0.0006,
            harvest=0.1,
            duration=4,
            wcet=2,
            deadline=3,
        )
        assert completed.returncode == 0, completed.stderr
        releases = [
            event for event in trace["events"]
            if event.get("event_type") == "st_charge_release"
        ]
        assert releases
        scheduled = [
            float(event["time"]) for event in trace["events"]
            if event.get("event_type") == "scheduled"
            and event.get("task_name") == "H"
        ]
        assert not any(
            event.get("release_reason") == "energy_sufficient"
            for event in trace["events"]
        )
        observations[scheduler] = (
            float(releases[0]["time"]),
            releases[0]["release_reason"],
            tuple(scheduled),
        )

    assert set(observations.values()) == {
        (1.0, "battery_full_and_slack_exhausted", (1.0,))
    }


def test_real_500ms_accepted_trace_reaches_horizon(tmp_path):
    completed, trace = run_scheduler(
        tmp_path, "gpfp_asap_block", duration=500, wcet=1, deadline=20
    )
    assert completed.returncode == 0, completed.stderr
    evaluation = acceptance.TraceParser(
        str(tmp_path / "gpfp_asap_block.json")
    ).evaluate(500)
    assert evaluation.status == "accepted"
    assert evaluation.observed_horizon_ms == 500
    assert trace["simulation_completed"] is True
    assert trace["simulation_completion_reason"] == "reached_horizon"
    assert not any(
        event.get("event_type") == "dline_miss"
        for event in trace["events"]
    )


def test_real_500ms_deadline_miss_is_rejected_once_per_job(tmp_path):
    completed, trace = run_scheduler(
        tmp_path, "gpfp_asap_block", duration=500, wcet=2, deadline=2,
        initial=0.0, maximum=1.0, harvest=0.0,
    )
    assert completed.returncode == 0, completed.stderr
    misses = [
        event for event in trace["events"]
        if event.get("event_type") == "dline_miss"
    ]
    assert len(misses) == 10
    assert len({event["job_id"] for event in misses}) == len(misses)
    assert all(float(event["remaining_execution_ms"]) > 0 for event in misses)
    assert all(float(event["time"]) == float(event["deadline"])
               for event in misses)

    evaluation = acceptance.TraceParser(
        str(tmp_path / "gpfp_asap_block.json")
    ).evaluate(500)
    assert evaluation.status == "rejected"
    assert evaluation.reason == "deadline_miss"


def test_deadline_miss_fail_fast_true_and_false_both_continue_to_horizon(
    tmp_path,
):
    """EXT-1B's legacy-named flag never truncates a native request."""

    rtsim, _build_root = require_rtsim_binary()
    base_system = tmp_path / "base_system.yml"
    base_system.write_text(
        system_yaml(
            "gpfp_asap_nonblock", initial=0.0, maximum=1.0, harvest=0.0,
        ),
        encoding="utf-8",
    )
    task_payload = [{
        "task_id": "0", "priority_rank": 0, "C": 2, "D": 2, "T": 10,
        "D_over_T": "1/5", "workload": "bzip2", "P": "1",
        "arrival_offset": 0,
    }]
    traces = {}
    for fail_fast in (True, False):
        run_root = tmp_path / str(fail_fast).lower()
        execution = run_paired_simulation(
            simulation_id_value="deadline-miss-continuation",
            base_system_path=base_system,
            run_root=run_root,
            task_payload=task_payload,
            taskset_hash="a" * 64,
            processors=1,
            exact_e0=Fraction(0),
            energy_config={
                "simulation_initial_battery": "0",
                "battery_capacity": "1",
            },
            simulation_config={
                "horizon": 25,
                "warmup": 0,
                "minimum_jobs_per_task": 2,
                "maximum_horizon": 25,
                "horizon_extension_policy": "none",
                "deadline_miss_fail_fast": fail_fast,
                "timeout_seconds": 10,
                "trace_mode": "semantic",
                "trace_on_failure": True,
                "retain_trace": True,
                "simulator_bin": str(rtsim),
            },
            scheduler_id="gpfp_asap_nonblock",
        )
        result = execution.result
        assert result.status is SimulationStatus.DEADLINE_MISS
        assert result.simulation_completed is True
        assert result.completion_reason == "reached_horizon"
        assert result.horizon == 25
        assert result.metrics["missed_jobs"] == 3
        assert execution.retained_trace_path is not None
        trace = json.loads(execution.retained_trace_path.read_text(encoding="utf-8"))
        misses = [
            event for event in trace["events"]
            if event.get("event_type") == "dline_miss"
        ]
        first_miss = min(float(event["time"]) for event in misses)
        assert [float(event["time"]) for event in misses] == [2.0, 12.0, 22.0]
        assert any(
            event.get("event_type") == "arrival"
            and float(event["time"]) > first_miss
            for event in trace["events"]
        )
        assert trace["observed_simulation_end_ms"] == 25
        assert trace["simulation_completed"] is True
        assert trace["simulation_completion_reason"] == "reached_horizon"
        traces[fail_fast] = trace["events"]

    assert traces[True] == traces[False]


def test_completion_exactly_at_deadline_has_no_false_miss(tmp_path):
    completed, trace = run_scheduler(
        tmp_path, "gpfp_asap_block", duration=500, wcet=2, deadline=2
    )
    assert completed.returncode == 0, completed.stderr
    assert not any(
        event.get("event_type") == "dline_miss"
        for event in trace["events"]
    )
    evaluation = acceptance.TraceParser(
        str(tmp_path / "gpfp_asap_block.json")
    ).evaluate(500)
    assert evaluation.status == "accepted"
