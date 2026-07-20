from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from experiments.v9_3.config import dump_config, load_config
from experiments.v9_3.core4_sensitivity import Core4SensitivityRunner
import experiments.v9_3.formal_authorization as authorization


ROOT = Path(__file__).resolve().parents[1]
FORMAL_RUNS = (
    ("CORE-1", "scripts/run_v9_3_core1.py", "configs/v9_3_core1_formal.yaml"),
    ("CORE-2", "scripts/run_v9_3_core2.py", "configs/v9_3_core2_formal.yaml"),
    (
        "CORE-3 B20 r2", "scripts/run_v9_3_core3.py",
        "configs/v9_3_core3_formal_b20_r2.yaml",
    ),
    (
        "CORE-3 B100 r2", "scripts/run_v9_3_core3.py",
        "configs/v9_3_core3_formal_b100_r2.yaml",
    ),
    ("CORE-4", "scripts/run_v9_3_core4.py", "configs/v9_3_core4_formal.yaml"),
    (
        "CORE-5A", "scripts/run_v9_3_core5.py",
        "configs/v9_3_core5a_formal_algorithmic.yaml",
    ),
    (
        "CORE-5B", "scripts/run_v9_3_core5.py",
        "configs/v9_3_core5b_formal_workers.yaml",
    ),
)


def _prepared_formal_config(source: str, tmp_path: Path) -> tuple[Path, Path]:
    document = yaml.safe_load((ROOT / source).read_text(encoding="utf-8"))
    output_root = tmp_path / "output"
    document["execution"]["output_root"] = str(output_root)
    document["execution"]["taskset_store"] = str(tmp_path / "store")
    prepared = tmp_path / "prepared.yaml"
    prepared.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return prepared, output_root


@pytest.mark.parametrize("name,script,source", FORMAL_RUNS)
def test_all_seven_formal_units_fail_closed_without_authorization(
    tmp_path, name, script, source,
):
    prepared, output_root = _prepared_formal_config(source, tmp_path)
    completed = subprocess.run(
        [sys.executable, str(ROOT / script), "--config", str(prepared)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0, name
    assert "--formal-authorization" in completed.stderr, name
    assert not output_root.exists(), name


def test_all_seven_formal_configs_have_unique_ids_roots_and_frozen_status():
    configs = [
        load_config(ROOT / source)
        for _name, _script, source in FORMAL_RUNS
    ]
    assert all(
        config["parameter_status"] == authorization.FORMAL_PARAMETER_STATUS
        for config in configs
    )
    assert len({config["experiment_id"] for config in configs}) == 7
    assert len({config["execution"]["output_root"] for config in configs}) == 7


def _authorization_document(config, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        authorization,
        "_repository_identity",
        lambda _root: {
            "git_commit": "6aa9d7196bcecf2896f6436bda8f32e8405a1521",
            "git_tree": "1" * 40,
            "repository_clean": True,
        },
    )
    source = tmp_path / "source-freeze.yaml"
    prepared = tmp_path / "prepared.yaml"
    dump_config(config, source)
    dump_config(config, prepared)
    binding = authorization.expected_binding(
        config,
        project_root=ROOT,
        source_freeze_config=source,
        prepared_config=prepared,
    )
    document = authorization.make_authorization_document(binding)
    path = tmp_path / "formal-authorization.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return source, prepared, path, binding


def test_core3_authorization_binds_executable_binary_identity(
    tmp_path, monkeypatch,
):
    config = deepcopy(load_config(
        ROOT / "configs/v9_3_core3_formal_b20_r2.yaml",
        expected_core="CORE-3",
    ))
    config["execution"]["output_root"] = str(tmp_path / "output")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    simulator = tmp_path / "rtsim"
    simulator.write_bytes(b"formal simulator identity\n")
    simulator.chmod(0o755)
    config["simulation"]["simulator_bin"] = str(simulator)
    source, prepared, path, binding = _authorization_document(
        config, tmp_path, monkeypatch,
    )
    assert binding["simulator_binary"] == str(simulator)
    assert binding["simulator_binary_sha256"] == hashlib.sha256(
        simulator.read_bytes()
    ).hexdigest()
    authorization.verify_authorization(
        config,
        authorization_path=path,
        source_freeze_config=source,
        prepared_config=prepared,
        project_root=ROOT,
    )

    simulator.write_bytes(b"different simulator identity\n")
    simulator.chmod(0o755)
    with pytest.raises(
        authorization.FormalAuthorizationError,
        match="authorization binding mismatch",
    ):
        authorization.verify_authorization(
            config,
            authorization_path=path,
            source_freeze_config=source,
            prepared_config=prepared,
            project_root=ROOT,
        )


def test_core4_parent_persists_and_rechecks_external_authorization(
    tmp_path, monkeypatch,
):
    config = deepcopy(load_config(
        ROOT / "configs/v9_3_core4_formal.yaml", expected_core="CORE-4"
    ))
    config["execution"]["output_root"] = str(tmp_path / "output")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    source, prepared, path, _binding = _authorization_document(
        config, tmp_path, monkeypatch,
    )
    runner = Core4SensitivityRunner(
        config,
        authorization_path=path,
        source_config_path=source,
        prepared_config_path=prepared,
    )
    runner._initialize(resume=False)
    metadata = json.loads(
        (runner.root / "run_metadata.json").read_text(encoding="utf-8")
    )
    seal = json.loads(
        (runner.root / "formal_authorization_seal.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["formal_large_scale_run"] is True
    assert metadata["formal_authorization_id"] == seal["authorization_id"]
    runner._initialize(resume=True)


@pytest.mark.parametrize(
    "script,source",
    (
        ("scripts/run_v9_3_core1.py", "configs/v9_3_core1_formal.yaml"),
        ("scripts/run_v9_3_core2.py", "configs/v9_3_core2_formal.yaml"),
        ("scripts/run_v9_3_core4.py", "configs/v9_3_core4_formal.yaml"),
        (
            "scripts/run_v9_3_core5.py",
            "configs/v9_3_core5a_formal_algorithmic.yaml",
        ),
        (
            "scripts/run_v9_3_core5.py",
            "configs/v9_3_core5b_formal_workers.yaml",
        ),
    ),
)
def test_formal_plan_cannot_be_truncated(tmp_path, script, source):
    prepared, output_root = _prepared_formal_config(source, tmp_path)
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / script), "--config", str(prepared),
            "--max-cells", "1", "--max-tasksets", "1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "cannot be truncated" in completed.stderr
    assert not output_root.exists()
