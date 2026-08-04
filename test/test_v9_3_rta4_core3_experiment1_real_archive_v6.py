from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile

import pytest
import yaml

from experiments.common.exact_service_curve import (
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
)
from experiments.v9_3 import rta4_core3_experiment1_audit_v6 as audit_module
from experiments.v9_3 import rta4_formal_execution
from experiments.v9_3.rta4_core3_experiment1_audit_v6 import (
    EXPERIMENT1_E0_V6,
    EXPERIMENT1_METHODS_V6,
    load_experiment1_rta_v6,
    _campaign_from_archive,
    RTA4Core3Experiment1AuditV6Error,
)
from experiments.v9_3.rta4_formal_config import domain_hash
from experiments.v9_3.rta4_formal_plan_v5 import (
    describe_formal_plan_v5,
    iter_formal_plan_v5,
)
from experiments.v9_3.rta4_local_execution_v5 import (
    LocalResultWriterV5,
    RTA4_LOCAL_RESULT_DOMAIN_V5,
    _terminal_row,
)
from experiments.v9_3.rta4_task_source_v4 import (
    EXPLICIT_MANIFEST_SCHEMA_V1,
    PRIORITY_POLICY_RM,
)


def _tasks() -> list[dict]:
    return [
        {
            "name": f"tau_{index + 1}", "C": 1,
            "D": 9 + 10 * index, "T": 10 + 10 * index,
            "power": "1/10",
        }
        for index in range(10)
    ]


def _physical_environment() -> dict:
    return {
        "execution_backend": "PHYSICAL_CORE_PROCESS_SLOTS",
        "physical_core_binding_required": True,
        "topology_fingerprint": "synthetic-e1-archive",
        "available_physical_core_count": 1,
        "allowed_logical_cpus": [0],
        "topology_selection_policy": "SYNTHETIC_ARCHIVE_TEST",
        "physical_execution_groups": [{
            "selected_physical_cores": [{
                "logical_cpu_id": 0, "physical_package_id": 0,
                "physical_core_id": 0,
            }],
        }],
    }


def _build_synthetic_archive(root: Path) -> Path:
    confirmation = root / "03_final_confirmation"
    configs = confirmation / "configs"
    manifests = confirmation / "manifests"
    configs.mkdir(parents=True)
    manifests.mkdir()
    names = {
        "batch_a": (
            "rta4_e1_wang_confirm_batch_a.yaml",
            "t10_wang_confirm_batch_a.json",
        ),
        "batch_b": (
            "rta4_e1_wang_confirm_batch_b.yaml",
            "t10_wang_confirm_batch_b.json",
        ),
    }
    for batch_index, (batch, (campaign_name, manifest_name)) in enumerate(
        names.items()
    ):
        taskset_id = f"synthetic-{batch}"
        manifest = {
            "schema": EXPLICIT_MANIFEST_SCHEMA_V1,
            "processors": 4,
            "priority_policy": PRIORITY_POLICY_RM,
            "task_count": 10,
            "taskset_count": 1,
            "task_order": [row["name"] for row in _tasks()],
            "tasksets": [{
                "taskset_id": taskset_id,
                "source_seed": None,
                "tasks": _tasks(),
            }],
        }
        manifest_path = manifests / manifest_name
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
        )
        campaign = {
            "campaign_id": f"synthetic-e1-{batch}",
            "core": "CORE-1",
            "processors": 4,
            "task_count": 10,
            "normalized_utilization": ["1/2"],
            "tasksets_per_utilization": 1,
            "e0": list(EXPERIMENT1_E0_V6),
            "methods": list(EXPERIMENT1_METHODS_V6),
            "task_source": {
                "mode": "EXPLICIT_TASKSET_MANIFEST",
                "manifest_path": f"/root/autodl-tmp/old/{manifest_name}",
            },
            "service_curve": {
                "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
                "rate": "11/2", "latency": "2/5", "time_unit": "tick",
            },
            "runtime": {},
        }
        campaign_path = configs / campaign_name
        campaign_path.write_text(
            yaml.safe_dump(campaign, sort_keys=False), encoding="utf-8",
        )
        loaded = _campaign_from_archive(campaign_path, manifest_path)
        plan = describe_formal_plan_v5(
            loaded.normalized_scientific_config,
            loaded.task_sources,
            loaded.service_curve,
        )
        records = tuple(iter_formal_plan_v5(
            loaded.normalized_scientific_config,
            loaded.task_sources,
            loaded.service_curve,
        ))
        writer = LocalResultWriterV5(
            confirmation / "outputs" / batch,
            campaign=loaded, plan=plan, records=records,
            execution_environment=_physical_environment(), resume=False,
        )
        task_order = list(loaded.task_sources[0].source.tasksets[0].task_order)
        proven = batch_index == 0
        for record in records:
            method = record.material["v3_grid_material"]["method"]
            nested = {
                "solver_status": "COMPLETED" if proven else "NO_CANDIDATE",
                "taskset_proven": proven,
                "taskset_certification_status": (
                    "CERTIFIED_TASKSET" if proven else "NOT_CERTIFIED"
                ),
                "task_energy_material_identity": hashlib.sha256(
                    taskset_id.encode("utf-8")
                ).hexdigest(),
                "service_material_identity": "a" * 64,
                "beta_material_identity": "b" * 64,
                "task_results": [{
                    "task_solver_status": (
                        "CANDIDATE_FOUND" if proven else "NO_CANDIDATE"
                    ),
                    "task_certification_status": (
                        "CERTIFIED" if proven else "NOT_CERTIFIED"
                    ),
                    "candidate_response_time": index + 1 if proven else "NA",
                } for index in range(10)],
                "mechanism_rows": [{
                    "priority_rank": index,
                    "task_id": task_id,
                    "method": method,
                } for index, task_id in enumerate(task_order)],
            }
            terminal = _terminal_row(
                writer, record, nested,
                worker_backend="PHYSICAL_CORE_PROCESS_SLOTS",
                physical_core_binding_required=True,
            )
            writer.write_result(terminal)
    return root


def test_synthetic_real_confirmation_layout_binds_plan_and_terminal(
    tmp_path, monkeypatch,
):
    archive_root = _build_synthetic_archive(tmp_path / "package")
    monkeypatch.setattr(audit_module, "EXPERIMENT1_BATCH_TASKSET_COUNT_V6", 1)
    monkeypatch.setattr(audit_module, "EXPERIMENT1_BATCH_RTA_RESULT_COUNT_V6", 28)
    monkeypatch.setattr(audit_module, "EXPERIMENT1_TASKSET_COUNT_V6", 2)
    monkeypatch.setattr(audit_module, "EXPERIMENT1_RTA_RESULT_COUNT_V6", 56)
    index = load_experiment1_rta_v6(archive_root)
    assert len(index) == 56
    assert {key[1] for key in index} == set(EXPERIMENT1_METHODS_V6)
    assert {key[2] for key in index} == set(EXPERIMENT1_E0_V6)
    assert {row["taskset_proven"] for row in index.values()} == {True, False}
    for row in index.values():
        assert [task["priority_rank"] for task in row["task_results"]] == list(range(10))
        assert [task["task_id"] for task in row["task_results"]] == [
            f"tau_{index}" for index in range(1, 11)
        ]


def _small_contract(monkeypatch) -> None:
    monkeypatch.setattr(audit_module, "EXPERIMENT1_BATCH_TASKSET_COUNT_V6", 1)
    monkeypatch.setattr(audit_module, "EXPERIMENT1_BATCH_RTA_RESULT_COUNT_V6", 28)
    monkeypatch.setattr(audit_module, "EXPERIMENT1_TASKSET_COUNT_V6", 2)
    monkeypatch.setattr(audit_module, "EXPERIMENT1_RTA_RESULT_COUNT_V6", 56)


def test_synthetic_layout_rejects_missing_terminal(tmp_path, monkeypatch):
    archive_root = _build_synthetic_archive(tmp_path / "package")
    _small_contract(monkeypatch)
    terminal = next(archive_root.rglob("local_terminal_results_v5/*.json"))
    terminal.unlink()
    with pytest.raises(RTA4Core3Experiment1AuditV6Error, match="terminal set"):
        load_experiment1_rta_v6(archive_root)


def test_synthetic_layout_rejects_noncontiguous_mechanism_rank(
    tmp_path, monkeypatch,
):
    archive_root = _build_synthetic_archive(tmp_path / "package")
    _small_contract(monkeypatch)
    terminal_path = next(archive_root.rglob("local_terminal_results_v5/*.json"))
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["result"]["mechanism_rows"][0]["priority_rank"] = 1
    unsigned = dict(terminal)
    unsigned.pop("result_identity")
    terminal["result_identity"] = domain_hash(
        RTA4_LOCAL_RESULT_DOMAIN_V5, unsigned,
    )
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    with pytest.raises(
        RTA4Core3Experiment1AuditV6Error, match="priority/task/method",
    ):
        load_experiment1_rta_v6(archive_root)


def _safe_extract_confirmation(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        selected = []
        for member in bundle.getmembers():
            parts = PurePosixPath(member.name).parts
            if "03_final_confirmation" not in parts:
                continue
            if (
                PurePosixPath(member.name).is_absolute()
                or ".." in parts
                or member.issym()
                or member.islnk()
            ):
                raise AssertionError(f"unsafe archive member: {member.name}")
            selected.append(member)
        if not selected:
            raise AssertionError("archive contains no 03_final_confirmation files")
        destination = destination.resolve()
        for member in selected:
            target = (destination / member.name).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise AssertionError(
                    f"unsafe archive target: {member.name}"
                ) from exc
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise AssertionError(f"unsupported archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = bundle.extractfile(member)
            if stream is None:
                raise AssertionError(f"unreadable archive member: {member.name}")
            target.write_bytes(stream.read())


def test_real_archive_extractor_rejects_path_traversal(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo(
            "wrapper/03_final_confirmation/../../escape.json"
        )
        payload = b"{}"
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    with pytest.raises(AssertionError, match="unsafe archive member"):
        _safe_extract_confirmation(archive, tmp_path / "extract")


def test_real_complete_archive(tmp_path, monkeypatch):
    value = os.environ.get("ASAP_BLOCK_E1_COMPLETE_ARCHIVE")
    if not value:
        pytest.skip("ASAP_BLOCK_E1_COMPLETE_ARCHIVE is not set")
    archive = Path(value).resolve(strict=True)
    extraction = tmp_path / "real-e1"
    extraction.mkdir()
    _safe_extract_confirmation(archive, extraction)
    monkeypatch.setattr(
        rta4_formal_execution, "dispatch_formal_rta",
        lambda *args, **kwargs: pytest.fail("the archive loader recomputed RTA"),
    )
    index = load_experiment1_rta_v6(extraction)
    assert len(index) == 22400
    assert len({key[0] for key in index}) == 800
    assert {key[1] for key in index} == set(EXPERIMENT1_METHODS_V6)
    assert {key[2] for key in index} == set(EXPERIMENT1_E0_V6)
    confirmation = next(extraction.rglob("03_final_confirmation"))
    assert len(list((confirmation / "outputs/batch_a/local_terminal_results_v5").glob("*.json"))) == 11200
    assert len(list((confirmation / "outputs/batch_b/local_terminal_results_v5").glob("*.json"))) == 11200
    samples = [
        next(row for row in index.values() if row["method"] == method)
        for method in EXPERIMENT1_METHODS_V6
    ]
    samples.extend([
        next(row for row in index.values() if row["taskset_proven"] is True),
        next(row for row in index.values() if row["taskset_proven"] is False),
    ])
    for row in samples:
        assert [task["priority_rank"] for task in row["task_results"]] == list(range(10))
        assert all("task_id" in task for task in row["task_results"])
