import json

import pytest

import experiments.v9_3.execution_engine as engine_module
import experiments.v9_3.formal_authorization as authorization
from experiments.v9_3.aggregation import aggregate_core1
from experiments.v9_3.config import dump_config
from experiments.v9_3.execution_engine import ExecutionEngine
from v9_3_experiment_helpers import (
    install_fake_materialization, make_config, successful_execution,
)


REPOSITORY_IDENTITY = {
    "git_commit": "6aa9d7196bcecf2896f6436bda8f32e8405a1521",
    "git_tree": "1" * 40,
    "repository_clean": True,
}


def _authorization_fixture(tmp_path, monkeypatch, core_name="CORE-1"):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    monkeypatch.setattr(
        authorization,
        "_repository_identity",
        lambda project_root: dict(REPOSITORY_IDENTITY),
    )
    config = make_config(tmp_path, core_name)
    store = tmp_path / "store"
    store.mkdir(parents=True, exist_ok=True)
    (store / "pairing_manifest.json").write_text(
        json.dumps({
            "schema": authorization.FORMAL_TASKSET_STORE_SCHEMA,
            "contract": {
                "task_workload_contract": {
                    "version": authorization.TASK_WORKLOAD_CONTRACT_VERSION,
                },
            },
        }),
        encoding="utf-8",
    )
    source = tmp_path / "source-freeze.yaml"
    prepared = tmp_path / "prepared.yaml"
    dump_config(config, source)
    dump_config(config, prepared)
    binding = authorization.expected_binding(
        config,
        project_root=tmp_path,
        source_freeze_config=source,
        prepared_config=prepared,
    )
    document = authorization.make_authorization_document(binding)
    path = tmp_path / "formal-authorization.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return config, source, prepared, path, document


def test_nonformal_direct_python_entry_is_explicitly_false(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    outcome = ExecutionEngine(make_config(tmp_path)).run()
    metadata = json.loads(
        (outcome.output_root / "run_metadata.json").read_text(encoding="utf-8")
    )
    seal = json.loads(
        (outcome.output_root / "formal_authorization_seal.json").read_text(
            encoding="utf-8"
        )
    )
    summary = aggregate_core1(outcome.output_root)
    assert metadata["formal_large_scale_run"] is False
    assert seal["formal_large_scale_run"] is False
    assert summary["formal_large_scale_run"] is False
    assert summary["formal_authorization_id"] is None


def test_valid_file_authorization_is_shared_by_metadata_summary_and_seal(
    tmp_path, monkeypatch
):
    config, source, prepared, path, document = _authorization_fixture(
        tmp_path, monkeypatch
    )
    outcome = ExecutionEngine(
        config,
        authorization_path=path,
        source_config_path=source,
        prepared_config_path=prepared,
    ).run()
    metadata = json.loads(
        (outcome.output_root / "run_metadata.json").read_text(encoding="utf-8")
    )
    seal = json.loads(
        (outcome.output_root / "formal_authorization_seal.json").read_text(
            encoding="utf-8"
        )
    )
    summary = aggregate_core1(outcome.output_root)
    assert metadata["formal_large_scale_run"] is True
    assert seal["formal_large_scale_run"] is True
    assert summary["formal_large_scale_run"] is True
    assert (
        metadata["formal_authorization_id"]
        == seal["authorization_id"]
        == summary["formal_authorization_id"]
        == document["authorization_id"]
    )


def test_aggregation_rechecks_authorization_bound_files(tmp_path, monkeypatch):
    config, source, prepared, path, _document = _authorization_fixture(
        tmp_path, monkeypatch
    )
    outcome = ExecutionEngine(
        config,
        authorization_path=path,
        source_config_path=source,
        prepared_config_path=prepared,
    ).run()
    prepared.write_text(
        prepared.read_text(encoding="utf-8") + "\n# tampered after execution\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="no longer valid"):
        aggregate_core1(outcome.output_root)


@pytest.mark.parametrize(
    "field",
    (
        "git_commit", "git_tree", "repository_clean",
        "source_freeze_config_sha256", "prepared_config_sha256",
        "config_semantic_hash", "worker_count", "core", "output_root",
        "taskset_store_identity", "token",
    ),
)
def test_formal_authorization_rejects_every_tampered_binding(
    tmp_path, monkeypatch, field
):
    config, source, prepared, path, document = _authorization_fixture(
        tmp_path, monkeypatch
    )
    if field == "token":
        document["formal_confirmation_token"] = "wrong-token"
    else:
        binding = dict(document["binding"])
        if field == "repository_clean":
            binding[field] = False
        elif field == "worker_count":
            binding[field] = int(binding[field]) + 1
        else:
            binding[field] = "tampered"
        document = authorization.make_authorization_document(binding)
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(
        authorization.FormalAuthorizationError,
        match="authorization binding mismatch",
    ):
        ExecutionEngine(
            config,
            authorization_path=path,
            source_config_path=source,
            prepared_config_path=prepared,
        ).run()


def test_formal_authorization_requires_both_config_files(tmp_path, monkeypatch):
    config, source, prepared, path, _document = _authorization_fixture(
        tmp_path, monkeypatch
    )
    with pytest.raises(
        authorization.FormalAuthorizationError,
        match="--source-freeze-config",
    ):
        ExecutionEngine(config, authorization_path=path).run()


@pytest.mark.parametrize(
    "authorized_core,runtime_core",
    (("CORE-1", "CORE-2"), ("CORE-2", "CORE-1")),
)
def test_formal_authorization_rejects_cross_core_reuse(
    tmp_path, monkeypatch, authorized_core, runtime_core,
):
    config, source, prepared, path, _document = _authorization_fixture(
        tmp_path, monkeypatch, authorized_core,
    )
    runtime = make_config(tmp_path / "runtime", runtime_core)
    runtime["execution"]["taskset_store"] = config["execution"]["taskset_store"]
    with pytest.raises(
        authorization.FormalAuthorizationError,
        match="authorization binding mismatch",
    ):
        authorization.verify_authorization(
            runtime,
            authorization_path=path,
            source_freeze_config=source,
            prepared_config=prepared,
            project_root=tmp_path,
        )


@pytest.mark.parametrize("bound_file", ("source", "prepared"))
def test_formal_authorization_rejects_changed_bound_config_file(
    tmp_path, monkeypatch, bound_file,
):
    config, source, prepared, path, _document = _authorization_fixture(
        tmp_path, monkeypatch,
    )
    target = source if bound_file == "source" else prepared
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# changed after authorization\n",
        encoding="utf-8",
    )
    with pytest.raises(
        authorization.FormalAuthorizationError,
        match="authorization binding mismatch",
    ):
        authorization.verify_authorization(
            config,
            authorization_path=path,
            source_freeze_config=source,
            prepared_config=prepared,
            project_root=tmp_path,
        )
