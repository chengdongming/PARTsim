#!/usr/bin/env python3
"""Validate a complete independently replayable CORE-0A result artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path


VERSION = "CORE0A-PACKAGE-REBUILD2-2.0"
PLACEHOLDERS = ("CONTAINING_COMMIT_B", "CURRENT_COMMIT", "TODO", "PLACEHOLDER", "UNKNOWN")
DETERMINISM_FIELDS = (
    "N_environments", "N_repetitions", "N_files_compared",
    "N_raw_files_compared", "N_raw_differences", "N_gate_differences",
    "N_manifest_differences", "N_zip_differences",
    "excluded_execution_only_fields",
)


def validate_rebuild2_metadata(root: Path, authoritative_pointer: Path | None):
    errors = []
    try:
        raw = json.loads((root / "raw_evidence_manifest.json").read_text(encoding="utf-8"))
        superseded = {(x["commit"], x["zip_sha256"], x["status"]) for x in raw["superseded_core0a_evidence"]}
        required = {
            ("dcb55f6a22f4d772a74f94ac7799b79cf5da8541", "d56c2f671b8ea201e6e53a4199cba333f3dcc6eb1e09ff06a1bfa8b76db8dd50", "INVALIDATED"),
            ("01f582b094f376a8e00640e22d0d2f25506d0e35", "a51ceee47c9f0e32a80a23f4c419af1271d35b29522d91b6630812bb362a2995", "INVALIDATED"),
        }
        if superseded != required or raw.get("pilot_authorized") is not False:
            errors.append("invalidated evidence list/pilot authorization mismatch")
    except Exception as exc:
        errors.append("raw invalidation metadata invalid: {}".format(exc))
    try:
        report = json.loads((root / "determinism_report.json").read_text(encoding="utf-8"))
        missing = [field for field in DETERMINISM_FIELDS if field not in report]
        if missing:
            errors.append("determinism N_* fields missing: {}".format(missing))
        for field in DETERMINISM_FIELDS[:-1]:
            if isinstance(report.get(field), bool) or not isinstance(report.get(field), int) or report[field] < 0:
                errors.append("determinism field is not a nonnegative measured count: {}".format(field))
        if not isinstance(report.get("excluded_execution_only_fields"), list):
            errors.append("excluded execution-only fields must be a list")
    except Exception as exc:
        errors.append("determinism report invalid: {}".format(exc))
    if authoritative_pointer is not None:
        try:
            pointer = json.loads(authoritative_pointer.read_text(encoding="utf-8"))
            required_fields = {"implementation_commit", "evidence_commit", "zip_filename", "zip_sha256", "runtime_manifest_sha256", "raw_manifest_sha256", "status", "generated_from_identity"}
            if not required_fields <= set(pointer):
                errors.append("authoritative pointer fields missing")
            serialized = json.dumps(pointer, sort_keys=True)
            if any(token in serialized for token in PLACEHOLDERS):
                errors.append("authoritative pointer contains placeholder")
            for field in ("implementation_commit", "evidence_commit"):
                value = pointer.get(field, "")
                if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
                    errors.append("authoritative pointer has invalid {}".format(field))
        except Exception as exc:
            errors.append("authoritative pointer invalid: {}".format(exc))
    return errors


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, cwd: Path):
    process = subprocess.run(
        [str(value) for value in command],
        cwd=cwd,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return {
        "command": [str(value) for value in command],
        "exit_code": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
    }


def validate(root: Path, zip_path: Path | None, authoritative_pointer: Path | None = None):
    root = root.resolve()
    errors = []
    commands = {}
    if not root.is_dir():
        return {"status": "FAILED", "validator_version": VERSION, "errors": ["root is not a directory"]}
    nonregular = sorted(
        path.name for path in root.iterdir() if path.is_symlink() or not path.is_file()
    )
    if nonregular:
        errors.append("non-regular package entries: {}".format(nonregular))
    errors.extend(validate_rebuild2_metadata(root, authoritative_pointer))
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        declared = set(manifest["files"]) | {"manifest.json", "sha256sum.txt"}
        actual = {path.name for path in root.iterdir() if path.is_file() and not path.is_symlink()}
        if declared != actual:
            errors.append("manifest/actual file set mismatch")
        for name, digest in manifest["files"].items():
            if sha256(root / name) != digest:
                errors.append("manifest member hash mismatch: {}".format(name))
    except Exception as exc:
        errors.append("manifest validation failed: {}".format(exc))

    result_validator = (root / "ASAP_BLOCK_result_validator_v1_3_12.py").resolve()
    acceptance_validator = (
        root / "ASAP_BLOCK_acceptance_report_validator_v1_3_12.py"
    ).resolve()
    aggregator = (root / "core0a_v9_3_second_rebuild_verifier.py").resolve()
    commands["full_result_validator"] = run(
        [sys.executable, "-B", result_validator, root, "--profile", "CORE0A"], root
    )
    commands["acceptance_validator"] = run(
        [
            sys.executable,
            "-B",
            acceptance_validator,
            (root / "acceptance_report.yaml").resolve(),
            "--formal-contract",
            (root / "formal_contract.yaml").resolve(),
        ],
        root,
    )
    commands["independent_gate_replay"] = run(
        [
            sys.executable,
            "-B",
            aggregator,
            "--evidence-root",
            root,
            "--template",
            (root / "ASAP_BLOCK_acceptance_report_template_v1_3_12.yaml").resolve(),
            "--compare-report",
            (root / "acceptance_report.yaml").resolve(),
        ],
        root,
    )
    for label, result in commands.items():
        if result["exit_code"]:
            errors.append(
                "{} failed: exit={} stdout={} stderr={}".format(
                    label, result["exit_code"], result["stdout"][-500:], result["stderr"][-500:]
                )
            )

    zip_info = None
    if zip_path is not None:
        zip_path = zip_path.resolve()
        try:
            with zipfile.ZipFile(zip_path) as archive:
                infos = archive.infolist()
                names = [item.filename for item in infos]
                if names != sorted(names) or len(names) != len(set(names)):
                    errors.append("ZIP members are not unique canonical order")
                if any(Path(name).name != name for name in names):
                    errors.append("ZIP contains non-basename member")
                actual_names = sorted(
                    path.name for path in root.iterdir() if path.is_file() and not path.is_symlink()
                )
                if names != actual_names:
                    errors.append("ZIP/directory member set mismatch")
                for info in infos:
                    if info.date_time != (2026, 7, 13, 0, 0, 0):
                        errors.append("ZIP member timestamp mismatch: {}".format(info.filename))
                    if archive.read(info) != (root / info.filename).read_bytes():
                        errors.append("ZIP member bytes mismatch: {}".format(info.filename))
            zip_info = {"file": zip_path.name, "sha256": sha256(zip_path), "members": len(names)}
        except Exception as exc:
            errors.append("ZIP validation failed: {}".format(exc))
    return {
        "status": "PASSED" if not errors else "FAILED",
        "validator_version": VERSION,
        "profile": "CORE0A",
        "errors": errors,
        "commands": {
            key: {
                "command": value["command"],
                "exit_code": value["exit_code"],
            }
            for key, value in commands.items()
        },
        "zip": zip_info,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--zip", dest="zip_path", type=Path)
    parser.add_argument("--authoritative-pointer", type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.root, args.zip_path, args.authoritative_pointer)
    except Exception as exc:
        result = {"status": "FAILED", "validator_version": VERSION, "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
