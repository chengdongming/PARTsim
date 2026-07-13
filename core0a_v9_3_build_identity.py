#!/usr/bin/env python3
"""Create the non-circular implementation identity used by CORE-0A evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
CONTRACT_ZIP = ROOT / "docs/ASAP_BLOCK_v1_3_12_机器合同静态冻结候选包.zip"
FORMAL_TEMPLATE = ROOT / "docs/ASAP_BLOCK_v1_3_12_机器合同静态冻结候选包/ASAP_BLOCK_formal_contract_template_v1_3_12.yaml"
THEORY_SHA256 = "524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e"
CONTRACT_ZIP_SHA256 = "b67882290d4d4688a0e81fd98f95e9d998537facfb9f5945d1ec125143959895"
SOURCE_FILES = (
    "asap_block_rta_v9_3.py",
    "asap_block_rta_v9_3_taskset.py",
    "asap_block_v9_3_runner.py",
    "asap_block_v1_3_12_schema_binding.py",
    "asap_block_v9_3_v1_3_12_microcases.py",
    "core0a_v9_3_build_identity.py",
    "core0a_v9_3_evidence.py",
    "core0a_v9_3_evidence_schema.py",
    "core0a_v9_3_independent_aggregator.py",
    "core0a_v9_3_second_rebuild_verifier.py",
    "core0a_v9_3_package_validator.py",
    "core0a_v9_3_oracles.py",
    "core0a_v9_3_scheduler_model.py",
    "scripts/core0a_v9_3_mutation_harness.py",
    "scripts/core0a_v9_3_mutation_probe.py",
    "docs/audits/v9_3_core0a_finite_state_domain.json",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def create(implementation_commit: str, allow_dirty: bool):
    resolved = git("rev-parse", implementation_commit)
    if resolved != implementation_commit:
        raise ValueError("implementation commit must be a full SHA")
    if git("rev-parse", "HEAD") != implementation_commit:
        raise ValueError("implementation commit is not current HEAD")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    clean = status == ""
    if not clean and not allow_dirty:
        raise ValueError("implementation worktree is not clean")
    formal = yaml.safe_load(FORMAL_TEMPLATE.read_text(encoding="utf-8"))
    if formal["theory_contract"]["theory_document_sha256"] != THEORY_SHA256:
        raise ValueError("frozen formal template theory hash mismatch")
    if formal["theory_contract"]["fixed_carry_in_corollary_sha256"] != THEORY_SHA256:
        raise ValueError("frozen fixed-carry-in theory hash mismatch")
    if sha256(CONTRACT_ZIP) != CONTRACT_ZIP_SHA256:
        raise ValueError("contract ZIP hash mismatch")
    missing = [name for name in SOURCE_FILES if not (ROOT / name).is_file()]
    if missing:
        raise ValueError("missing relevant source files: {}".format(missing))
    identity = {
        "identity_version": "CORE0A-BUILD-1.0",
        "implementation_commit_sha": implementation_commit,
        "git_status_clean": clean,
        "source_files": {name: sha256(ROOT / name) for name in SOURCE_FILES},
        "python_environment": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "byteorder": sys.byteorder,
            "machine": platform.machine(),
            "system": platform.system(),
        },
        "theory_sha256": THEORY_SHA256,
        "contract_zip_sha256": CONTRACT_ZIP_SHA256,
    }
    identity["build_identity_hash"] = hashlib.sha256(
        b"CORE0A:BUILD_IDENTITY:v1\0" + canonical(identity)
    ).hexdigest()
    return identity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    try:
        value = create(args.implementation_commit, args.allow_dirty)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "status": "CREATED",
            "build_identity_hash": value["build_identity_hash"],
            "git_status_clean": value["git_status_clean"],
        }, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
