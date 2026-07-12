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


VERSION = "CORE0A-PACKAGE-1.0"


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


def validate(root: Path, zip_path: Path | None):
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
    aggregator = (root / "core0a_v9_3_independent_aggregator.py").resolve()
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
    args = parser.parse_args()
    try:
        result = validate(args.root, args.zip_path)
    except Exception as exc:
        result = {"status": "FAILED", "validator_version": VERSION, "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
