from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from experiments.v9_3.result_writer import atomic_write_json
from experiments.v9_3.rta4_formal_authorization import (
    RTA4AuthorizationError, authorize_candidate,
    build_authorization_candidate, validate_authorization_document,
    verify_live_authorization,
)
from experiments.v9_3.rta4_formal_config import (
    RTA4_CORES, load_rta4_formal_config,
)
from experiments.v9_3.rta4_formal_environment import (
    RTA4EnvironmentError, build_command_manifest,
    build_dependency_manifest, build_environment_manifest,
    build_hardware_manifest, build_simulator_manifest,
    build_source_manifest, validate_source_manifest,
)
from experiments.v9_3.rta4_formal_freeze import (
    RTA4_FROZEN_ALL_PLAN_DIGEST, RTA4_TIMEOUT_METHODS,
    RTA4FreezeError, build_freeze_manifest, prepare_formal_configs,
    validate_prepared_config,
)
from experiments.v9_3.rta4_formal_pilot import (
    RTA4PilotError, build_pilot_manifest, build_pilot_report,
    validate_pilot_report,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def frozen_contract(tmp_path_factory):
    root = tmp_path_factory.mktemp("rta4-auth-contract")
    paths = {
        core: (
            ROOT / "configs"
            / f"v9_3_rta4_{core.lower().replace('-', '')}_unauthorized_pre_pilot_v1.yaml"
        ).resolve()
        for core in RTA4_CORES
    }
    configs = {
        core: load_rta4_formal_config(path, expected_core=core)
        for core, path in paths.items()
    }
    pilot = build_pilot_manifest(
        configs, core_record_counts={core: 1 for core in RTA4_CORES},
        selection_seed="RTA4-TEST-PILOT-V1", output_root=root / "pilot",
        config_paths=paths,
    )
    observations = [
        {
            "plan_record_id": row["plan_record_id"],
            "runtime_wall_milliseconds": index + 1,
            "peak_rss_bytes": 1024 + index,
            "timed_out": False,
            "attempt_count": 1,
        }
        for index, core in enumerate(RTA4_CORES)
        for row in pilot["selected_records"][core]
    ]
    report = build_pilot_report(pilot, observations)
    timeout = {
        "contract_version": "ASAP_BLOCK_V9_3_RTA4_TIMEOUT_V1",
        "pilot_report_id": report["pilot_report_id"],
        "methods": {
            method: {
                "initial_timeout_seconds": 10,
                "retry_timeout_seconds": 20,
                "maximum_attempts": 2,
                "failure_origin": "UNIFIED_RTA_ADAPTER",
                "pilot_evidence": report["pilot_report_id"],
            }
            for method in RTA4_TIMEOUT_METHODS
        },
    }
    operational = {}
    for core in RTA4_CORES:
        sources = (
            {"CORE-1": str(root / "core1")}
            if core in {"CORE-2", "CORE-3"}
            else {"CORE-4": str(root / "core4")}
            if core == "CORE-5B" else {}
        )
        operational[core] = {
            "worker_count": 2,
            "max_in_flight": 4,
            "memory_limit_bytes": 1024 * 1024,
            "checkpoint_interval_records": 2,
            "simulation_timeout_seconds": 30,
            "output_root": str(root / f"output-{core}"),
            "taskset_store": str(root / "taskset-store"),
            "source_closures": sources,
            "simulator_binary": "/bin/true" if core == "CORE-3" else None,
            "execution_order": (
                2 if core in {"CORE-2", "CORE-3", "CORE-5B"} else 1
            ),
            "resume_policy": "REVALIDATE_ALL_BINDINGS_SKIP_TERMINALS_V1",
        }
    prepared = prepare_formal_configs(
        configs, pilot_manifest=pilot, pilot_report=report,
        timeout_contract=timeout, operational=operational,
        config_paths=paths,
    )
    freeze = build_freeze_manifest(prepared)
    documents = {
        "pilot": root / "pilot.json",
        "report": root / "report.json",
        "freeze": root / "freeze.json",
    }
    atomic_write_json(documents["pilot"], pilot)
    atomic_write_json(documents["report"], report)
    atomic_write_json(documents["freeze"], freeze)
    for core in RTA4_CORES:
        path = root / f"prepared-{core}.json"
        atomic_write_json(path, prepared[core])
        documents[f"prepared-{core}"] = path
    return {
        "root": root, "configs": configs, "pilot": pilot, "report": report,
        "timeout": timeout, "prepared": prepared, "freeze": freeze,
        "documents": documents,
    }


def _source_repo(root: Path) -> tuple[Path, dict]:
    repo = root / "source-repo"
    repo.mkdir()
    source = repo / "entry.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(("git", "add", "entry.py"), cwd=repo, check=True)
    subprocess.run(
        (
            "git", "-c", "user.name=RTA4 Test",
            "-c", "user.email=rta4@example.invalid",
            "commit", "-qm", "source",
        ),
        cwd=repo, check=True,
    )
    return repo, build_source_manifest(repo, ("entry.py",))


def _candidate(frozen_contract, source, *, test_mode=False):
    prepared = frozen_contract["prepared"]["CORE-1"]
    dependencies = build_dependency_manifest()
    environment = build_environment_manifest(dependencies)
    command = build_command_manifest(
        ("python3", "scripts/run_v9_3_rta4_formal.py", "--execute"),
        cwd=ROOT, operation="execute", core="CORE-1",
    )
    return build_authorization_candidate(
        prepared_config=prepared,
        freeze_manifest=frozen_contract["freeze"],
        all_prepared_configs=frozen_contract["prepared"],
        pilot_manifest=frozen_contract["pilot"],
        pilot_report=frozen_contract["report"],
        source_manifest=source,
        dependency_manifest=dependencies,
        environment_manifest=environment,
        hardware_manifest=build_hardware_manifest(),
        command_manifest=command,
        simulator_manifest=build_simulator_manifest(None),
        prepared_config_path=frozen_contract["documents"]["prepared-CORE-1"],
        freeze_manifest_path=frozen_contract["documents"]["freeze"],
        pilot_manifest_path=frozen_contract["documents"]["pilot"],
        pilot_report_path=frozen_contract["documents"]["report"],
        authorization_path=frozen_contract["root"] / (
            "test-auth.json" if test_mode else "production-auth.json"
        ),
        test_mode=test_mode,
    )


def test_pilot_is_result_independent_and_report_is_engineering_only(
    frozen_contract,
):
    pilot = frozen_contract["pilot"]
    assert pilot["scientific_interpretation"].startswith("FORBIDDEN")
    assert all(
        len(pilot["selected_records"][core]) == 1 for core in RTA4_CORES
    )
    validate_pilot_report(frozen_contract["report"], pilot)
    contaminated = deepcopy(frozen_contract["report"])
    contaminated["scientific_results_included"] = True
    with pytest.raises(RTA4PilotError):
        validate_pilot_report(contaminated, pilot)


def test_freeze_preserves_scientific_identity_and_rejects_timeout_drift(
    frozen_contract,
):
    prepared = frozen_contract["prepared"]["CORE-1"]
    assert prepared["scientific_assertions"]["all_plan_digest"] == (
        RTA4_FROZEN_ALL_PLAN_DIGEST
    )
    validate_prepared_config(prepared)
    drift = deepcopy(prepared)
    drift["timeout_contract"]["methods"]["CW_D"][
        "pilot_evidence"
    ] = "0" * 64
    with pytest.raises(RTA4FreezeError):
        validate_prepared_config(drift)


def test_two_step_authorization_and_test_domain_are_disjoint(frozen_contract):
    _, source = _source_repo(frozen_contract["root"])
    candidate = _candidate(frozen_contract, source)
    with pytest.raises(RTA4AuthorizationError, match="exact"):
        authorize_candidate(candidate, confirm_authorization_id="approve")
    authorized = authorize_candidate(
        candidate, confirm_authorization_id=candidate["authorization_id"],
    )
    assert verify_live_authorization(authorized) == authorized
    test_candidate = _candidate(frozen_contract, source, test_mode=True)
    test_authorized = authorize_candidate(
        test_candidate,
        confirm_authorization_id=test_candidate["authorization_id"],
        test_mode=True,
    )
    with pytest.raises(RTA4AuthorizationError, match="TEST"):
        validate_authorization_document(test_authorized)
    validate_authorization_document(test_authorized, allow_test=True)


def test_source_manifest_rejects_byte_and_dirty_state_drift(
    frozen_contract,
):
    repo = frozen_contract["root"] / "drift-repo"
    repo.mkdir()
    path = repo / "bound.py"
    path.write_text("A = 1\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(("git", "add", "bound.py"), cwd=repo, check=True)
    subprocess.run(
        (
            "git", "-c", "user.name=RTA4 Test",
            "-c", "user.email=rta4@example.invalid",
            "commit", "-qm", "source",
        ),
        cwd=repo, check=True,
    )
    manifest = build_source_manifest(repo, ("bound.py",))
    path.write_text("A = 2\n", encoding="utf-8")
    with pytest.raises(RTA4EnvironmentError):
        validate_source_manifest(manifest)


def test_freeze_requires_complete_pilot(frozen_contract):
    stale = deepcopy(frozen_contract["report"])
    stale["pilot_status"] = "PILOT_PARTIAL"
    with pytest.raises(RTA4PilotError):
        validate_pilot_report(stale, frozen_contract["pilot"])
