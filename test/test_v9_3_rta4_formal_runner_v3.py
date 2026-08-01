from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest
import yaml

from experiments.v9_3.rta4_formal_config import domain_hash
from experiments.v9_3.rta4_formal_config_v3 import (
    RTA4_FORMAL_PROFILE_V3, load_rta4_campaign_v3,
)
from experiments.v9_3.rta4_formal_lifecycle_v3 import (
    build_authorization_v3, build_prepared_config_v3,
)
from experiments.v9_3.rta4_formal_plan_v3 import iter_formal_plan_v3
from experiments.v9_3.rta4_formal_runner_v3 import (
    AuthorizedRTA4RunnerV3, RTA4_CHECKPOINT_V3,
    RTA4_TASKSET_STORE_MANIFEST_V3, RTA4FormalRunnerV3Error,
)
from experiments.v9_3.rta4_process_isolation_v3 import (
    ISOLATED_INTERNAL_ERROR, ISOLATED_RESULT, ISOLATED_SERIALIZATION_ERROR,
    ISOLATED_TIMEOUT, execute_isolated_call_v3,
)
from experiments.v9_3.rta4_production_build_manifest_v3 import (
    PRODUCTION_BUILD_MANIFEST_DOMAIN_V3,
    PRODUCTION_BUILD_MANIFEST_SCHEMA_V3,
    PRODUCTION_BUILD_PROFILE_V3,
)
from experiments.v9_3.rta4_shared_energy import (
    FrozenMapping, ServiceHorizonContract, SharedEnergyRunContext,
    VerifiedSolarServiceMaterialV2, construct_task_energy_material,
)
from experiments.v9_3.simulation_engine import SharedSolarInput
from scripts.create_v9_3_rta4_campaign import campaign_template


ROOT = Path(__file__).resolve().parents[1]


def _campaign(tmp_path: Path, *, skeletons: int = 2) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = campaign_template("CORE-1")
    raw.update({
        "campaign_id": "bounded-core1-v3",
        "task_count": 3,
        "normalized_utilization": ["1/2"],
        "tasksets_per_utilization": skeletons,
        "e0": ["0", "1/2"],
        "methods": ["CW_THETA_CW", "SEQ_THETA_SEQ"],
    })
    path = tmp_path / "campaign.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _manifest(tmp_path: Path) -> tuple[Path, dict]:
    material = {
        "manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA_V3,
        "formal_profile": PRODUCTION_BUILD_PROFILE_V3,
        "repository": {
            "source_root": str(ROOT),
            "git_commit": "c" * 40,
            "git_tree": "d" * 40,
        },
    }
    document = {
        **material,
        "manifest_id": domain_hash(PRODUCTION_BUILD_MANIFEST_DOMAIN_V3, material),
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


def _artifacts(
    tmp_path: Path, *, output: str = "results", store: str = "store",
    workers: int = 2, timeout: int = 3, skeletons: int = 2,
):
    campaign = load_rta4_campaign_v3(_campaign(tmp_path, skeletons=skeletons))
    manifest_path, manifest = _manifest(tmp_path)
    prepared = build_prepared_config_v3(
        campaign, production_manifest_path=manifest_path,
        output_root=tmp_path / output, taskset_store=tmp_path / store,
        worker_count=workers, max_in_flight=workers, timeout_seconds=timeout,
    )
    return campaign, manifest, prepared, build_authorization_v3(prepared)


class _FakeContextFactory:
    def __call__(self, records, *, taskset_provider, production_build_manifest_path,
                 system_config_path, energy_support_path, source_root,
                 taskset_store_identity):
        del energy_support_path, source_root
        build_id = json.loads(
            Path(production_build_manifest_path).read_text()
        )["manifest_id"]
        task_materials = {}
        services = {}
        bindings = {}
        for record in records:
            certificate = taskset_provider(record)
            task = construct_task_energy_material(
                certificate,
                taskset_provider.workloads_for(record, certificate),
                system_config_path=system_config_path,
                taskset_store_identity=taskset_store_identity,
                production_build_manifest_identity=build_id,
                profile_id=RTA4_FORMAL_PROFILE_V3,
            )
            service_id = domain_hash(
                "ASAP_BLOCK:TEST:V3:SERVICE", {
                    "service_scale": record.material.get("service_scale", "1"),
                },
            )
            beta_id = domain_hash("ASAP_BLOCK:TEST:V3:BETA", service_id)
            service = SimpleNamespace(
                service_material_identity=service_id,
                beta_material_identity=beta_id,
                production_build_manifest_identity=build_id,
            )
            task_materials[task.task_energy_material_identity] = task
            services[service_id] = service
            bindings[record.record_id] = FrozenMapping({
                "task_energy_material_identity": task.task_energy_material_identity,
                "service_material_identity": service_id,
            })
        return SharedEnergyRunContext(
            build_id, FrozenMapping(task_materials), FrozenMapping(services),
            FrozenMapping(bindings), FrozenMapping({}), True,
        )


class _ExactContextFactory:
    def __call__(self, records, *, taskset_provider,
                 production_build_manifest_path, system_config_path,
                 energy_support_path, source_root, taskset_store_identity):
        del energy_support_path, source_root
        build_id = json.loads(
            Path(production_build_manifest_path).read_text()
        )["manifest_id"]
        task_materials = {}
        services = {}
        bindings = {}
        for record in records:
            certificate = taskset_provider(record)
            task = construct_task_energy_material(
                certificate,
                taskset_provider.workloads_for(record, certificate),
                system_config_path=system_config_path,
                taskset_store_identity=taskset_store_identity,
                production_build_manifest_identity=build_id,
                profile_id=RTA4_FORMAL_PROFILE_V3,
            )
            analysis_horizon = max(
                item.relative_deadline for item in certificate.tasks
            ) - 1
            horizon = ServiceHorizonContract(
                analysis_horizon, 0, analysis_horizon,
            )
            trace = tuple(Fraction(1) for _ in range(analysis_horizon))
            beta = SharedSolarInput(trace, {}).beta(analysis_horizon)
            service_id = domain_hash(
                "ASAP_BLOCK:TEST:V3:EXACT_SERVICE",
                {"scale": record.material.get("service_scale", "1")},
            )
            service = VerifiedSolarServiceMaterialV2(
                "a" * 64, "b" * 64, "c" * 64, "d" * 64, build_id,
                "1" * 64, "2" * 64, "3" * 64, 1, 0, Fraction(1),
                horizon, trace, beta, "4" * 64, "5" * 64,
                service_id, "{}",
            )
            task_materials[task.task_energy_material_identity] = task
            services[service.service_material_identity] = service
            bindings[record.record_id] = FrozenMapping({
                "task_energy_material_identity": task.task_energy_material_identity,
                "service_material_identity": service.service_material_identity,
            })
        return SharedEnergyRunContext(
            build_id, FrozenMapping(task_materials), FrozenMapping(services),
            FrozenMapping(bindings), FrozenMapping({}), True,
        )


class _FakeRTA:
    calls: list[str] = []

    def __init__(self, config, *, run_context, timeout_contract,
                 identity_contract=None, **_kwargs):
        del config, identity_contract
        self.context = run_context
        self.timeouts = timeout_contract

    def __call__(self, record, certificate):
        type(self).calls.append(record.execution_id)
        binding = self.context.binding_for(record.record_id)
        service = self.context.service_materials[
            binding["service_material_identity"]
        ]
        budget = self.timeouts[record.material["method"]][
            "initial_timeout_seconds"
        ]
        analysis = domain_hash("ASAP_BLOCK:TEST:V3:ANALYSIS", {
            "mathematical_request_identity": record.mathematical_request_id,
            "taskset_identity": certificate.taskset_id,
        })
        attempt = {
            "attempt_index": 0, "timeout_seconds": budget,
            "status": "PROVEN_SCHEDULABLE", "runtime_wall_seconds": "0",
            "runtime_cpu_seconds": "0", "peak_rss_bytes": 0,
            "error_classification": "NONE", "analysis_identity": analysis,
            "taskset_identity": certificate.taskset_id,
            "task_energy_material_identity": binding[
                "task_energy_material_identity"
            ],
            "service_material_identity": binding["service_material_identity"],
            "beta_material_identity": service.beta_material_identity,
            "production_build_manifest_identity": self.context.production_build_manifest_identity,
        }
        return FrozenMapping({
            "solver_status": "PROVEN_SCHEDULABLE",
            "taskset_proven": True,
            "analysis_id": analysis,
            "attempts": (FrozenMapping(attempt),),
            "timeout_seconds": budget,
            "runtime_wall_seconds": "0",
            "runtime_cpu_seconds": "0",
            "peak_rss_bytes": 0,
        })


class _SlowFakeRTA(_FakeRTA):
    def __call__(self, record, certificate):
        time.sleep(0.1)
        return super().__call__(record, certificate)


class _CrashingFakeRTA(_FakeRTA):
    def __call__(self, record, certificate):
        del record, certificate
        os._exit(23)


class _AttemptFakeRTA(_FakeRTA):
    def __call__(self, record, certificate):
        successful = dict(super().__call__(record, certificate))
        first = dict(successful["attempts"][0])
        if record.ordinal == 0:
            return FrozenMapping(successful)
        timed = {
            **first,
            "status": "TIMEOUT",
            "error_classification": "UNIFIED_RTA_ADAPTER_TIMEOUT",
        }
        second = {
            **first,
            "attempt_index": 1,
            "timeout_seconds": first["timeout_seconds"] * 2,
        }
        if record.ordinal == 1:
            successful["attempts"] = (
                FrozenMapping(timed), FrozenMapping(second),
            )
            return FrozenMapping(successful)
        second.update({
            "status": "TIMEOUT",
            "error_classification": "UNIFIED_RTA_ADAPTER_TIMEOUT",
        })
        successful.update({
            "solver_status": "TIMEOUT",
            "taskset_proven": False,
            "attempts": (FrozenMapping(timed), FrozenMapping(second)),
            "timeout_seconds": second["timeout_seconds"],
        })
        return FrozenMapping(successful)


def _isolation_identity(value):
    return value


def _isolation_sleep(seconds):
    time.sleep(seconds)
    return "late"


def _isolation_crash():
    os._exit(29)


def _runner(tmp_path: Path, **artifact_kwargs):
    campaign, manifest, prepared, authorization = _artifacts(
        tmp_path, **artifact_kwargs,
    )
    runner = AuthorizedRTA4RunnerV3(
        prepared, authorization,
        _manifest_loader=lambda _path, live: manifest,
        _context_factory=_FakeContextFactory(),
        _rta_executor_factory=_FakeRTA,
        _test_worker_backend="thread",
    )
    return campaign, prepared, authorization, runner


def _process_runner(tmp_path: Path, *, executor=_SlowFakeRTA, workers=4,
                    skeletons=2):
    campaign, manifest, prepared, authorization = _artifacts(
        tmp_path, workers=workers, skeletons=skeletons,
    )
    runner = AuthorizedRTA4RunnerV3(
        prepared, authorization,
        _manifest_loader=lambda _path, live: manifest,
        _context_factory=_FakeContextFactory(),
        _rta_executor_factory=executor,
    )
    return campaign, prepared, authorization, runner


def test_bounded_one_record_execute_writes_atomic_terminal_and_checkpoint(tmp_path):
    _FakeRTA.calls.clear()
    _, prepared, _, runner = _runner(tmp_path)
    summary = runner.run(max_records=1)
    assert summary.processed_records == 1
    assert summary.pending_records == 7
    assert summary.complete is False
    assert summary.checkpoint_path.is_file()
    checkpoint = json.loads(summary.checkpoint_path.read_text())
    assert len(checkpoint["completed_execution_ids"]) == 1
    assert len(_FakeRTA.calls) == 1
    assert len(list((Path(prepared["operational"]["output_root"])
                     / "formal_terminal_results_v3").glob("*.json"))) == 1


def test_bounded_multi_record_then_resume_finishes_without_duplicates(tmp_path):
    _FakeRTA.calls.clear()
    _, prepared, _, runner = _runner(tmp_path)
    first = runner.run(max_records=3)
    assert first.processed_records == 3
    before = tuple(_FakeRTA.calls)
    resumed = runner.run(resume=True)
    assert resumed.processed_records == 5
    assert resumed.pending_records == 0 and resumed.complete
    assert set(before).isdisjoint(_FakeRTA.calls[3:])
    terminals = list((Path(prepared["operational"]["output_root"])
                      / "formal_terminal_results_v3").glob("*.json"))
    assert len(terminals) == 8


def test_validate_only_never_constructs_context_or_solver(tmp_path):
    _, manifest, prepared, authorization = _artifacts(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("solver/material path executed")

    summary = AuthorizedRTA4RunnerV3(
        prepared, authorization,
        _manifest_loader=lambda _path, live: manifest,
        _context_factory=forbidden, _rta_executor_factory=forbidden,
    ).run(validate_only=True)
    assert summary.processed_records == 0 and summary.pending_records == 8
    assert not Path(prepared["operational"]["output_root"]).exists()


def test_pairing_reuses_one_generated_taskset_across_e0_and_methods(tmp_path):
    _, prepared, _, runner = _runner(tmp_path, skeletons=1)
    runner.run()
    bindings = list((Path(prepared["operational"]["taskset_store"])
                     / "slot_bindings_v3").glob("*.json"))
    assert len(bindings) == 1
    terminals = [json.loads(path.read_text()) for path in
                 (Path(prepared["operational"]["output_root"])
                  / "formal_terminal_results_v3").glob("*.json")]
    assert len(terminals) == 4
    assert len({row["taskset_identity"] for row in terminals}) == 1
    assert len({row["generation_request_identity"] for row in terminals}) == 1


def test_resume_rejects_v2_or_malformed_checkpoint(tmp_path):
    _, prepared, _, runner = _runner(tmp_path)
    runner.run(max_records=1)
    checkpoint = Path(prepared["operational"]["output_root"]) / RTA4_CHECKPOINT_V3
    checkpoint.write_text(json.dumps({
        "checkpoint_schema": "ASAP_BLOCK_V9_3_RTA4_CHECKPOINT_V2",
    }), encoding="utf-8")
    with pytest.raises(Exception, match="legacy or malformed"):
        runner.run(resume=True)


def test_output_and_store_campaign_conflicts_are_rejected(tmp_path):
    _, prepared, _, runner = _runner(tmp_path)
    runner.run(max_records=1)
    campaign_path = _campaign(tmp_path, skeletons=1)
    raw = yaml.safe_load(campaign_path.read_text())
    raw["campaign_id"] = "other-bounded-core1-v3"
    campaign_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    campaign = load_rta4_campaign_v3(campaign_path)
    manifest_path, _ = _manifest(tmp_path)
    other = build_prepared_config_v3(
        campaign, production_manifest_path=manifest_path,
        output_root=prepared["operational"]["output_root"],
        taskset_store=prepared["operational"]["taskset_store"],
    )
    with pytest.raises(Exception):
        AuthorizedRTA4RunnerV3(
            other, build_authorization_v3(other),
        ).run(validate_only=True)


def test_store_marker_conflict_is_fail_closed(tmp_path):
    _, prepared, _, runner = _runner(tmp_path)
    runner.run(max_records=1)
    marker = (Path(prepared["operational"]["taskset_store"])
              / RTA4_TASKSET_STORE_MANIFEST_V3)
    changed = json.loads(marker.read_text())
    changed["plan_sha256"] = "0" * 64
    marker.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(RTA4FormalRunnerV3Error, match="another campaign"):
        runner.run(resume=True)


def test_worker_and_timeout_change_only_prepared_authorization_identity(tmp_path):
    campaign, _, first, first_auth = _artifacts(tmp_path, workers=1, timeout=3)
    manifest_path, _ = _manifest(tmp_path)
    second = build_prepared_config_v3(
        campaign, production_manifest_path=manifest_path,
        output_root=tmp_path / "other-results", taskset_store=tmp_path / "other-store",
        worker_count=2, max_in_flight=2, timeout_seconds=9,
    )
    second_auth = build_authorization_v3(second)
    assert first["normalized_scientific_config_sha256"] == second[
        "normalized_scientific_config_sha256"
    ]
    assert first["plan_sha256"] == second["plan_sha256"]
    assert first["prepared_config_id"] != second["prepared_config_id"]
    assert first_auth["authorization_id"] != second_auth["authorization_id"]


def test_authorization_binds_formal_scope_store_core_and_full_range(tmp_path):
    _, prepared, authorization, _ = _runner(tmp_path)
    assert authorization["execution_class"] == "FORMAL_AUTHORIZED"
    assert authorization["core"] == "CORE-1"
    assert authorization["taskset_store"] == prepared["operational"]["taskset_store"]
    assert authorization["allowed_record_range"] == {"start": 0, "stop": 8}
    changed = deepcopy(authorization)
    changed["allowed_record_range"]["stop"] = 7
    with pytest.raises(Exception, match="binding mismatch"):
        AuthorizedRTA4RunnerV3(prepared, changed)


def test_identical_bounded_runs_have_same_mathematical_identities(tmp_path):
    _, first_prepared, _, first_runner = _runner(tmp_path / "one")
    first_runner.run(max_records=2)
    _, second_prepared, _, second_runner = _runner(tmp_path / "two")
    second_runner.run(max_records=2)

    def math_ids(prepared):
        directory = (Path(prepared["operational"]["output_root"])
                     / "formal_terminal_results_v3")
        return sorted(json.loads(path.read_text())["mathematical_result_identity"]
                      for path in directory.glob("*.json"))

    assert math_ids(first_prepared) == math_ids(second_prepared)


def test_plan_records_expose_generation_material_and_stable_pairing(tmp_path):
    campaign = load_rta4_campaign_v3(_campaign(tmp_path, skeletons=1))
    records = tuple(iter_formal_plan_v3(campaign.normalized_scientific_config))
    assert len({record.taskset_skeleton_slot_id for record in records}) == 1
    assert len({record.taskset_slot_id for record in records}) == 1
    assert all(record.material["processor_count"] == 4 for record in records)
    assert all(record.material["task_count"] == 3 for record in records)


def test_v3_cli_execute_dispatches_to_authorized_runner(
    tmp_path, monkeypatch, capsys,
):
    campaign, _, prepared, authorization = _artifacts(tmp_path)
    prepared_path = tmp_path / "prepared.json"
    authorization_path = tmp_path / "authorization.json"
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")
    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
    observed = []

    class FakeRunner:
        def __init__(self, observed_prepared, observed_authorization):
            assert observed_prepared == prepared
            assert observed_authorization == authorization

        def run(self, *, resume, validate_only, max_records):
            observed.append((resume, validate_only, max_records))
            return SimpleNamespace(
                core="CORE-1", execution_class="FORMAL_AUTHORIZED",
                production_build_manifest_identity=prepared[
                    "production_manifest"
                ]["production_build_manifest_identity"],
                processed_records=1, pending_records=7, complete=False,
                checkpoint_path=Path(prepared["operational"]["output_root"])
                / RTA4_CHECKPOINT_V3,
                execution_backend="PROCESS_POOL_SPAWN",
                worker_process_ids=(101, 102),
                worker_intervals_ns=((101, 1, 2), (102, 1, 2)),
            )

    import experiments.v9_3.rta4_formal_runner_v3 as runner_module
    import scripts.run_v9_3_rta4_formal as cli

    monkeypatch.setattr(runner_module, "AuthorizedRTA4RunnerV3", FakeRunner)
    monkeypatch.setattr(sys, "argv", [
        "run_v9_3_rta4_formal.py", "--campaign-config",
        str(campaign.campaign_path), "--prepared-config", str(prepared_path),
        "--authorization", str(authorization_path), "--execute",
        "--max-records", "1",
    ])
    assert cli.main() == 0
    assert observed == [(False, False, 1)]
    assert json.loads(capsys.readouterr().out)["processed_records"] == 1


def test_production_process_pool_canary_observes_multiple_worker_pids(tmp_path):
    _, prepared, _, runner = _process_runner(tmp_path, workers=4, skeletons=2)
    summary = runner.run()
    assert summary.execution_backend == "PROCESS_POOL_SPAWN"
    assert len(summary.worker_process_ids) >= 2
    assert os.getpid() not in summary.worker_process_ids
    assert any(
        first_pid != second_pid
        and max(first_start, second_start) < min(first_end, second_end)
        for first_pid, first_start, first_end in summary.worker_intervals_ns
        for second_pid, second_start, second_end in summary.worker_intervals_ns
    )
    assert summary.complete and summary.processed_records == 8
    terminals = Path(prepared["operational"]["output_root"]) / (
        "formal_terminal_results_v3"
    )
    assert len(list(terminals.glob("*.json"))) == 8


def test_formal_runner_rejects_thread_backend_without_test_doubles(tmp_path):
    _, _, prepared, authorization = _artifacts(tmp_path)
    with pytest.raises(RTA4FormalRunnerV3Error, match="forbidden in production"):
        AuthorizedRTA4RunnerV3(
            prepared, authorization, _test_worker_backend="thread",
        )


def test_thread_era_manifest_invalidates_prepared_and_authorization_chain(tmp_path):
    _, _, prepared, authorization = _artifacts(tmp_path)
    old_material = {
        "manifest_schema": (
            "ASAP_BLOCK_V9_3_RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST_"
            "V3_PARAMETERIZED"
        ),
        "formal_profile": (
            "ASAP_BLOCK_V9_3_RTA4_FORMAL_V3_PARAMETERIZED_SHARED_ENERGY"
        ),
    }
    old_domain = "ASAP_BLOCK:V9.3:RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST:v3"
    Path(prepared["production_manifest"]["absolute_path"]).write_text(
        json.dumps({
            **old_material,
            "manifest_id": domain_hash(old_domain, old_material),
        }),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="cannot bind production manifest"):
        AuthorizedRTA4RunnerV3(prepared, authorization)


def test_single_and_multi_process_results_are_mathematically_identical(tmp_path):
    _, one_prepared, _, one = _process_runner(
        tmp_path / "one", workers=1, skeletons=1,
    )
    _, many_prepared, _, many = _process_runner(
        tmp_path / "many", workers=4, skeletons=1,
    )
    one_summary = one.run()
    many_summary = many.run()
    assert len(one_summary.worker_process_ids) == 1
    assert len(many_summary.worker_process_ids) >= 2

    def mathematical_rows(prepared):
        terminal_root = Path(prepared["operational"]["output_root"]) / (
            "formal_terminal_results_v3"
        )
        rows = [json.loads(path.read_text()) for path in terminal_root.glob("*.json")]
        return sorted((
            row["mathematical_request_identity"],
            row["taskset_identity"],
            row["generation_request_identity"],
            row["status"],
            row["response_result"],
            row["mathematical_result_identity"],
        ) for row in rows)

    assert mathematical_rows(one_prepared) == mathematical_rows(many_prepared)


def test_resume_reconciles_terminal_written_before_checkpoint(tmp_path):
    _FakeRTA.calls.clear()
    _, prepared, _, runner = _runner(tmp_path)
    runner.run(max_records=1)
    checkpoint = Path(prepared["operational"]["output_root"]) / RTA4_CHECKPOINT_V3
    stale_checkpoint = checkpoint.read_bytes()
    runner.run(resume=True, max_records=1)
    terminal_root = Path(prepared["operational"]["output_root"]) / (
        "formal_terminal_results_v3"
    )
    terminal_bytes = {
        path.name: path.read_bytes() for path in terminal_root.glob("*.json")
    }
    checkpoint.write_bytes(stale_checkpoint)
    calls_before = tuple(_FakeRTA.calls)
    summary = runner.run(resume=True, max_records=0)
    assert summary.processed_records == 0
    assert tuple(_FakeRTA.calls) == calls_before
    assert terminal_bytes == {
        path.name: path.read_bytes() for path in terminal_root.glob("*.json")
    }
    repaired = json.loads(checkpoint.read_text())
    assert len(repaired["completed_execution_ids"]) == 2
    assert len(set(repaired["completed_execution_ids"])) == 2


def test_retry_attempt_history_preserves_success_and_two_timeout_contract(tmp_path):
    campaign, manifest, prepared, authorization = _artifacts(
        tmp_path, workers=1, skeletons=1,
    )
    runner = AuthorizedRTA4RunnerV3(
        prepared, authorization,
        _manifest_loader=lambda _path, live: manifest,
        _context_factory=_FakeContextFactory(),
        _rta_executor_factory=_AttemptFakeRTA,
        _test_worker_backend="thread",
    )
    runner.run(max_records=3)
    rows = sorted(
        (json.loads(path.read_text()) for path in
         (Path(prepared["operational"]["output_root"])
          / "formal_terminal_results_v3").glob("*.json")),
        key=lambda row: row["plan_record_identity"],
    )
    attempts = sorted((len(row["attempts"]), row["status"]) for row in rows)
    assert attempts == [
        (1, "PROVEN_SCHEDULABLE"),
        (2, "PROVEN_SCHEDULABLE"),
        (2, "TIMEOUT"),
    ]
    for row in rows:
        if len(row["attempts"]) == 2:
            assert [attempt["timeout_seconds"] for attempt in row["attempts"]] == [3, 6]


def test_e1_timeout_contract_remains_120_240_with_two_attempts(tmp_path):
    _, manifest, prepared, authorization = _artifacts(
        tmp_path, workers=1, timeout=120, skeletons=1,
    )
    runner = AuthorizedRTA4RunnerV3(
        prepared, authorization,
        _manifest_loader=lambda _path, live: manifest,
        _context_factory=_FakeContextFactory(),
        _rta_executor_factory=_AttemptFakeRTA,
        _test_worker_backend="thread",
    )
    runner.run(max_records=2)
    rows = [json.loads(path.read_text()) for path in
            (Path(prepared["operational"]["output_root"])
             / "formal_terminal_results_v3").glob("*.json")]
    assert sorted(
        [attempt["timeout_seconds"] for attempt in row["attempts"]]
        for row in rows
    ) == [[120], [120, 240]]


def test_broken_process_pool_is_internal_error_not_rta_timeout(tmp_path):
    _, prepared, _, runner = _process_runner(
        tmp_path, executor=_CrashingFakeRTA, workers=2, skeletons=1,
    )
    summary = runner.run(max_records=2)
    assert summary.processed_records == 2
    rows = [json.loads(path.read_text()) for path in
            (Path(prepared["operational"]["output_root"])
             / "formal_terminal_results_v3").glob("*.json")]
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"INTERNAL_ERROR"}
    assert all(
        "PROCESS_POOL_BROKEN" in row["attempts"][0]["error_classification"]
        for row in rows
    )


def test_process_request_serialization_failure_is_parent_persisted(tmp_path):
    _, manifest, prepared, authorization = _artifacts(
        tmp_path, workers=1, skeletons=1,
    )
    unpickleable_factory = lambda *_args, **_kwargs: None
    runner = AuthorizedRTA4RunnerV3(
        prepared, authorization,
        _manifest_loader=lambda _path, live: manifest,
        _context_factory=_FakeContextFactory(),
        _rta_executor_factory=unpickleable_factory,
    )
    summary = runner.run(max_records=1)
    assert summary.processed_records == 1
    row_path = next((Path(prepared["operational"]["output_root"])
                     / "formal_terminal_results_v3").glob("*.json"))
    row = json.loads(row_path.read_text())
    assert row["status"] == "INTERNAL_ERROR"
    assert "PROCESS_SERIALIZATION_FAILURE" in row["attempts"][0][
        "error_classification"
    ]


def test_spawn_isolation_success_timeout_crash_and_serialization():
    successful = execute_isolated_call_v3(
        _isolation_identity, ("ok",), 2,
    )
    assert successful.status == ISOLATED_RESULT
    assert successful.value == "ok"
    assert successful.worker_pid not in {None, os.getpid()}

    timed = execute_isolated_call_v3(
        _isolation_sleep, (2.0,), 0.05,
    )
    assert timed.status == ISOLATED_TIMEOUT
    assert timed.cleanup_status in {
        "REAPED_AFTER_TERMINATE", "REAPED_AFTER_KILL",
    }

    crashed = execute_isolated_call_v3(_isolation_crash, (), 2)
    assert crashed.status == ISOLATED_INTERNAL_ERROR
    assert crashed.error_classification != "UNIFIED_RTA_ADAPTER_TIMEOUT"

    serialization = execute_isolated_call_v3(
        _isolation_identity, (lambda: None,), 2,
    )
    assert serialization.status == ISOLATED_SERIALIZATION_ERROR
    assert "SERIALIZATION_FAILURE" in serialization.error_classification


def test_real_v2_adapter_runs_inside_process_pool_and_hard_attempt_boundary(tmp_path):
    _, manifest, prepared, authorization = _artifacts(
        tmp_path, workers=2, timeout=3, skeletons=1,
    )
    runner = AuthorizedRTA4RunnerV3(
        prepared, authorization,
        _manifest_loader=lambda _path, live: manifest,
        _context_factory=_ExactContextFactory(),
    )
    summary = runner.run(max_records=2)
    assert summary.execution_backend == "PROCESS_POOL_SPAWN"
    assert len(summary.worker_process_ids) == 2
    rows = [json.loads(path.read_text()) for path in
            (Path(prepared["operational"]["output_root"])
             / "formal_terminal_results_v3").glob("*.json")]
    assert len(rows) == 2
    assert all(row["status"] != "INTERNAL_ERROR" for row in rows)
    assert all(1 <= len(row["attempts"]) <= 2 for row in rows)
    assert all(
        [attempt["timeout_seconds"] for attempt in row["attempts"]]
        in ([3], [3, 6])
        for row in rows
    )


def test_worker_protocol_has_no_parent_persistence_capability():
    from dataclasses import fields
    from experiments.v9_3.rta4_formal_workers_v3 import V3WorkerRequest

    names = {field.name for field in fields(V3WorkerRequest)}
    assert not names.intersection({
        "writer", "checkpoint", "taskset_store", "terminal_root",
    })
