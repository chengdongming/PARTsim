"""Version-bound C++ ``std::stod`` proofs for RTA4 solar CSV inputs."""

from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile
from types import MappingProxyType
from typing import Any, Dict, Mapping, Sequence

import yaml

import asap_block_rta as legacy_rta

from .config import canonical_json, domain_hash, fraction_text
from .rta4_formal_environment import load_strict_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_SOURCE_RELATIVE_PATH = "tools/rta4_solar_stod_verifier.cpp"
PROOF_BUILDER_RELATIVE_PATH = "scripts/build_v9_3_rta4_solar_parse_proof.py"
PROOF_MODULE_RELATIVE_PATH = "experiments/v9_3/solar_parse_proof.py"

SOLAR_STOD_PARSER_CONTRACT = "ASAP_BLOCK_V9_3_RTA4_SOLAR_STOD_PARSER_V1"
SOLAR_STOD_PARSE_PROOF_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_SOLAR_STOD_PARSE_PROOF_V2"
)
SOLAR_STOD_PARSE_PROOF_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_SOLAR_STOD_PARSE_PROOF:v2"
)
SOLAR_STOD_PROOF_BUILDER_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_SOLAR_STOD_PROOF_BUILDER_V2"
)
SOLAR_STOD_DIAGNOSTIC_CLASSIFICATION = "UNVERIFIED_DIAGNOSTIC_REPLAY"
SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF_V1"
)
SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF:v1"
)
SOLAR_SEMANTIC_SERVICE_SOURCE_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_SOLAR_SEMANTIC_SERVICE_SOURCE_V1"
)
SOLAR_SEMANTIC_SERVICE_SOURCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_SOLAR_SEMANTIC_SERVICE_SOURCE:v1"
)
SOLAR_PARSER_ENVIRONMENT_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_SOLAR_PARSER_ENVIRONMENT_V1"
)
SOLAR_PARSER_ENVIRONMENT_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_SOLAR_PARSER_ENVIRONMENT:v1"
)
SOLAR_STOD_BUILD_STANDARD = "c++17"
SOLAR_STOD_NUMERIC_LOCALE = "LC_NUMERIC=C"
SOLAR_STOD_NORMALIZED_COMPILE_ARGUMENTS = (
    f"-std={SOLAR_STOD_BUILD_STANDARD}",
    "-O2",
    "-Wall",
    "-Wextra",
    "-pedantic",
    "<CHECKED_IN_VERIFIER_SOURCE>",
    "-o",
    "<PRIVATE_VERIFIER_BINARY>",
)
SOLAR_STOD_STRICT_DECIMAL = re.compile(
    r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))"
    r"(?:[eE][+-]?[0-9]+)?"
)


class SolarParseProofError(ValueError):
    """Raised when a C++ solar parse proof cannot be built or validated."""


def freeze_material(value: Any) -> Any:
    """Return a recursively immutable copy suitable for cached evidence."""

    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): freeze_material(item)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(freeze_material(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_material(item) for item in value)
    return value


def thaw_material(value: Any) -> Any:
    """Return a canonical-JSON-compatible mutable copy of frozen evidence."""

    if isinstance(value, Mapping):
        return {
            str(key): thaw_material(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [thaw_material(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (thaw_material(item) for item in value),
            key=lambda item: canonical_json(item),
        )
    return value


def _read_file_once(path: Path, *, label: str) -> bytes:
    """Read one resolved file through one descriptor and reject in-place drift."""

    resolved = path.resolve(strict=True)
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(resolved, flags)
    try:
        before = os.fstat(descriptor)
        chunks = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev, before.st_ino, before.st_size,
        before.st_mtime_ns, before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev, after.st_ino, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns,
    )
    payload = b"".join(chunks)
    if identity_before != identity_after or len(payload) != before.st_size:
        raise SolarParseProofError(
            f"{label} changed while its immutable snapshot was read"
        )
    return payload


def _display_resolved_path(path: Path, source_root: Path) -> str:
    try:
        return path.relative_to(source_root).as_posix()
    except ValueError:
        return path.as_posix()


def _source_material_from_bytes(
    path: Path,
    source_root: Path,
    payload: bytes,
) -> Dict[str, Any]:
    if not path.is_absolute() or not source_root.is_absolute():
        raise SolarParseProofError(
            "source material paths must be resolved before snapshotting"
        )
    return {
        "source_relative_path": _display_resolved_path(path, source_root),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _semantic_bytes_material(material: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "sha256": material["sha256"],
        "size": material["size"],
    }


def _secure_snapshot_write(
    workspace: Path,
    name: str,
    payload: bytes,
    *,
    executable: bool = False,
) -> Path:
    if Path(name).name != name or not name:
        raise SolarParseProofError("snapshot filename must be a private basename")
    path = workspace / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o555 if executable else 0o444)
    return path


def _checked_in_verifier_source_bytes(source_path: Path) -> bytes:
    resolved = source_path.resolve(strict=True)
    root = PROJECT_ROOT.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise SolarParseProofError(
            "formal-safe verifier source is outside the repository root"
        ) from exc
    if relative.as_posix() != VERIFIER_SOURCE_RELATIVE_PATH:
        raise SolarParseProofError(
            "formal-safe verifier source path is not the fixed checked-in source"
        )
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if head.returncode or not head.stdout.strip():
        raise SolarParseProofError("cannot bind verifier source to repository HEAD")
    committed = subprocess.run(
        [
            "git", "-C", str(root), "show",
            f"{head.stdout.strip()}:{VERIFIER_SOURCE_RELATIVE_PATH}",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if committed.returncode:
        raise SolarParseProofError("verifier source is not tracked at repository HEAD")
    current = _read_file_once(resolved, label="checked-in verifier source")
    if current != committed.stdout:
        raise SolarParseProofError(
            "formal-safe verifier source differs from the checked-in HEAD blob"
        )
    return current


def _system_document_from_bytes(payload: bytes) -> Mapping[str, Any]:
    try:
        document = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise SolarParseProofError(
            f"cannot parse snapshotted system template: {exc}"
        ) from exc
    if not isinstance(document, Mapping):
        raise SolarParseProofError("snapshotted system template must be a mapping")
    return document


def _support_document_from_bytes(payload: bytes) -> Mapping[str, Any]:
    try:
        document = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise SolarParseProofError(
            f"cannot parse snapshotted energy support: {exc}"
        ) from exc
    if not isinstance(document, Mapping):
        raise SolarParseProofError("snapshotted energy support must be a mapping")
    return document


def _resolve_solar_path_from_system_bytes(
    system_path: Path,
    payload: bytes,
) -> Path:
    document = _system_document_from_bytes(payload)
    energy = document.get("energy_management")
    if not isinstance(energy, Mapping):
        raise SolarParseProofError(
            "snapshotted system has no energy_management mapping"
        )
    if energy.get("use_real_solar_data") is not True:
        raise SolarParseProofError("parse proof requires real solar data")
    raw_path = energy.get("solar_data_file")
    if not isinstance(raw_path, str) or not raw_path:
        raise SolarParseProofError("snapshotted system has no solar_data_file")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = system_path.parent / candidate
    try:
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise SolarParseProofError(
            f"solar data file not found: {candidate}"
        ) from exc


def _rewrite_snapshot_solar_path(
    system_bytes: bytes,
    runtime_solar_path: str,
) -> bytes:
    try:
        text = system_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SolarParseProofError(
            "system template is not UTF-8 text"
        ) from exc
    rendered = []
    replacements = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        stripped = content.strip()
        if not stripped.startswith("solar_data_file:"):
            rendered.append(line)
            continue
        indent = content[:len(content) - len(content.lstrip())]
        comment = ""
        if "#" in content:
            comment = "  #" + content.split("#", 1)[1]
        rendered.append(
            f"{indent}solar_data_file: "
            f"{json.dumps(runtime_solar_path)}{comment}{ending}"
        )
        replacements += 1
    if replacements != 1:
        raise SolarParseProofError(
            "system template must contain exactly one solar_data_file"
        )
    derived = "".join(rendered).encode("utf-8")
    original_document = deepcopy(dict(_system_document_from_bytes(system_bytes)))
    derived_document = _system_document_from_bytes(derived)
    original_energy = original_document.get("energy_management")
    if not isinstance(original_energy, dict):
        raise SolarParseProofError(
            "system energy_management cannot be normalized"
        )
    original_energy["solar_data_file"] = runtime_solar_path
    if original_document != derived_document:
        raise SolarParseProofError(
            "snapshot system rewrite changed fields other than solar_data_file"
        )
    return derived


@dataclass(frozen=True)
class SnapshotSource:
    original_path: Path
    payload: bytes
    material: Mapping[str, Any]


class ImmutableSolarReplaySnapshot:
    """Private, one-read source snapshot shared by proof and replay."""

    def __init__(
        self,
        *,
        source_root: Path | str,
        base_system_path: Path | str,
        energy_support_path: Path | str,
        declared_solar_csv_path: Path | str | None = None,
        expected_proof_path: Path | str | None = None,
    ) -> None:
        root = Path(source_root).resolve(strict=True)
        system_path = Path(base_system_path)
        if not system_path.is_absolute():
            system_path = root / system_path
        system_path = system_path.resolve(strict=True)
        support_path = Path(energy_support_path)
        if not support_path.is_absolute():
            support_path = root / support_path
        support_path = support_path.resolve(strict=True)

        system_bytes = _read_file_once(
            system_path, label="system template",
        )
        support_bytes = _read_file_once(
            support_path, label="energy support",
        )
        solar_path = _resolve_solar_path_from_system_bytes(
            system_path, system_bytes,
        )
        if declared_solar_csv_path is not None:
            declared = Path(declared_solar_csv_path)
            if not declared.is_absolute():
                declared = root / declared
            if declared.resolve(strict=True) != solar_path:
                raise SolarParseProofError(
                    "declared solar CSV does not match snapshotted system bytes"
                )
        solar_bytes = _read_file_once(solar_path, label="solar CSV")

        verifier_source_path = (
            PROJECT_ROOT / VERIFIER_SOURCE_RELATIVE_PATH
        ).resolve(strict=True)
        verifier_source_bytes = _checked_in_verifier_source_bytes(
            verifier_source_path
        )
        expected_path: Path | None = None
        expected_bytes: bytes | None = None
        if expected_proof_path is not None:
            expected_path = Path(expected_proof_path).resolve(strict=True)
            expected_bytes = _read_file_once(
                expected_path, label="expected solar parse proof",
            )

        workspace = Path(tempfile.mkdtemp(prefix="v9_3_verified_solar_"))
        os.chmod(workspace, 0o700)
        self._workspace = workspace
        self._csv_descriptor: int | None = None
        try:
            self.system = SnapshotSource(
                system_path,
                system_bytes,
                freeze_material(_source_material_from_bytes(
                    system_path, root, system_bytes,
                )),
            )
            self.support = SnapshotSource(
                support_path,
                support_bytes,
                freeze_material(_source_material_from_bytes(
                    support_path, root, support_bytes,
                )),
            )
            self.solar_csv = SnapshotSource(
                solar_path,
                solar_bytes,
                freeze_material(_source_material_from_bytes(
                    solar_path, root, solar_bytes,
                )),
            )
            self.verifier_source = SnapshotSource(
                verifier_source_path,
                verifier_source_bytes,
                freeze_material(_source_material_from_bytes(
                    verifier_source_path, PROJECT_ROOT, verifier_source_bytes,
                )),
            )
            self.expected_proof = (
                None
                if expected_path is None or expected_bytes is None
                else SnapshotSource(
                    expected_path,
                    expected_bytes,
                    freeze_material(_source_material_from_bytes(
                        expected_path, root, expected_bytes,
                    )),
                )
            )

            self.system_source_path = _secure_snapshot_write(
                workspace, "system-source.yml", system_bytes,
            )
            self.support_path = _secure_snapshot_write(
                workspace, "energy-support.yml", support_bytes,
            )
            self.solar_csv_path = _secure_snapshot_write(
                workspace, "solar.csv", solar_bytes,
            )
            self.verifier_source_path = _secure_snapshot_write(
                workspace,
                "rta4_solar_stod_verifier.cpp",
                verifier_source_bytes,
            )
            self.expected_proof_path = (
                None
                if expected_bytes is None
                else _secure_snapshot_write(
                    workspace, "expected-proof.json", expected_bytes,
                )
            )
            flags = os.O_RDONLY
            self._csv_descriptor = os.open(self.solar_csv_path, flags)
            self.solar_runtime_path = (
                f"/proc/self/fd/{self._csv_descriptor}"
            )
            if not Path(self.solar_runtime_path).is_file():
                raise SolarParseProofError(
                    "platform cannot expose the immutable solar snapshot descriptor"
                )
            replay_system_bytes = _rewrite_snapshot_solar_path(
                system_bytes, self.solar_runtime_path,
            )
            self.system_path = _secure_snapshot_write(
                workspace, "replay-system-template.yml", replay_system_bytes,
            )
            self.source_root = root
        except Exception:
            self.close()
            raise

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def csv_descriptor(self) -> int:
        if self._csv_descriptor is None:
            raise SolarParseProofError("solar snapshot descriptor is closed")
        return self._csv_descriptor

    def write_private_file(
        self,
        name: str,
        payload: bytes,
        *,
        executable: bool = False,
    ) -> Path:
        return _secure_snapshot_write(
            self._workspace, name, payload, executable=executable,
        )

    def close(self) -> None:
        descriptor = getattr(self, "_csv_descriptor", None)
        if descriptor is not None:
            os.close(descriptor)
            self._csv_descriptor = None
        workspace = getattr(self, "_workspace", None)
        if workspace is not None and workspace.exists():
            shutil.rmtree(workspace)

    def __enter__(self) -> "ImmutableSolarReplaySnapshot":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_display_path(path: Path, source_root: Path) -> str:
    resolved = path.resolve(strict=True)
    root = source_root.resolve(strict=True)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def source_file_material(path: Path, source_root: Path) -> Dict[str, Any]:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise SolarParseProofError(f"parse-proof source is not a file: {resolved}")
    return {
        "source_relative_path": source_display_path(resolved, source_root),
        "sha256": file_sha256(resolved),
        "size": resolved.stat().st_size,
    }


def _absolute_file_material(path: Path) -> Dict[str, Any]:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise SolarParseProofError(f"parse-proof binary is not a file: {resolved}")
    return {
        "absolute_path": str(resolved),
        "sha256": file_sha256(resolved),
        "size": resolved.stat().st_size,
    }


def _direct_support_identity_material(
    document: Mapping[str, Any],
    *,
    source_root: Path,
) -> Dict[str, Any]:
    material = deepcopy(dict(document))
    energy = material.get("energy", material)
    if not isinstance(energy, dict):
        return material
    service = energy.get("service_curve")
    if not isinstance(service, dict):
        return material
    template_value = service.get("system_template")
    if not isinstance(template_value, str) or not template_value:
        return material
    declared = Path(template_value)
    if declared.is_absolute():
        resolved = declared.resolve()
        try:
            service["system_template"] = resolved.relative_to(
                source_root.resolve(strict=True)
            ).as_posix()
        except ValueError:
            service["system_template"] = resolved.as_posix()
    else:
        service["system_template"] = declared.as_posix()
    return material


def load_energy_support(
    value: Mapping[str, Any] | Path | str,
    *,
    source_root: Path,
) -> tuple[Dict[str, Any], Dict[str, Any], Path | None]:
    support_path: Path | None = None
    if isinstance(value, Mapping):
        document = dict(value)
        identity_document = _direct_support_identity_material(
            document, source_root=source_root,
        )
        payload = canonical_json(identity_document).encode("utf-8")
        source = {
            "source_relative_path": "<direct-mapping>",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    else:
        support_path = Path(value)
        if not support_path.is_absolute():
            support_path = source_root / support_path
        try:
            support_path = support_path.resolve(strict=True)
            payload = support_path.read_bytes()
            document = yaml.safe_load(payload)
        except (OSError, yaml.YAMLError) as exc:
            raise SolarParseProofError(
                f"cannot load parse-proof energy support: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise SolarParseProofError(
                "parse-proof energy support must contain a mapping"
            )
        source = source_file_material(support_path, source_root)
    energy = document.get("energy", document)
    if not isinstance(energy, dict):
        raise SolarParseProofError(
            "parse-proof energy support has no energy mapping"
        )
    return dict(energy), source, support_path


def _csv_error(
    *,
    source_path: str,
    physical_data_row_index: int,
    calendar_minute_index: int,
    category: str,
) -> SolarParseProofError:
    return SolarParseProofError(
        "shared solar CSV validation failed: "
        f"source={source_path} "
        f"physical_data_row_index={physical_data_row_index} "
        f"calendar_minute_index={calendar_minute_index} "
        f"category={category}"
    )


def validate_solar_csv_domain(
    solar_path: Path,
    *,
    source_root: Path,
    day_of_year: int,
    time_of_day_ms: int,
    horizon: int,
) -> Dict[str, Any]:
    """Preserve physical rows before asking C++ to prove accessed values."""

    source_path = source_display_path(solar_path, source_root)
    start_offset_ms = legacy_rta.materialize_runtime_start_offset_ms(
        day_of_year, time_of_day_ms,
    )
    first_minute = (start_offset_ms + 1) // 60000
    last_minute = (start_offset_ms + horizon) // 60000

    try:
        handle = solar_path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise SolarParseProofError(
            f"cannot read shared solar CSV {source_path}: {exc}"
        ) from exc
    with handle:
        header = handle.readline()
        if not header:
            raise _csv_error(
                source_path=source_path,
                physical_data_row_index=-1,
                calendar_minute_index=first_minute,
                category="MISSING_HEADER",
            )
        header_text = header.rstrip("\r\n")
        if not header_text.strip():
            raise _csv_error(
                source_path=source_path,
                physical_data_row_index=-1,
                calendar_minute_index=first_minute,
                category="EMPTY_HEADER",
            )
        try:
            header_row = next(csv.reader([header_text]))
            float(header_row[0])
        except (IndexError, ValueError):
            pass
        else:
            raise _csv_error(
                source_path=source_path,
                physical_data_row_index=-1,
                calendar_minute_index=first_minute,
                category="NUMERIC_HEADER_SHIFTS_PYTHON_INDEX",
            )

        physical_data_row_count = 0
        for raw_line in handle:
            data_index = physical_data_row_count
            physical_data_row_count += 1
            if data_index > last_minute:
                continue
            text = raw_line.rstrip("\r\n")
            if not text.strip():
                raise _csv_error(
                    source_path=source_path,
                    physical_data_row_index=data_index,
                    calendar_minute_index=data_index,
                    category="EMPTY_DATA_ROW",
                )
            stripped = text.strip()
            try:
                irradiance = float(stripped)
            except ValueError as exc:
                raise _csv_error(
                    source_path=source_path,
                    physical_data_row_index=data_index,
                    calendar_minute_index=data_index,
                    category="INVALID_NUMERIC_ROW",
                ) from exc
            if not math.isfinite(irradiance):
                raise _csv_error(
                    source_path=source_path,
                    physical_data_row_index=data_index,
                    calendar_minute_index=data_index,
                    category="NONFINITE_IRRADIANCE",
                )
            if SOLAR_STOD_STRICT_DECIMAL.fullmatch(stripped) is None:
                raise _csv_error(
                    source_path=source_path,
                    physical_data_row_index=data_index,
                    calendar_minute_index=data_index,
                    category="INVALID_NUMERIC_ROW",
                )
            if (
                first_minute <= data_index <= last_minute
                and irradiance < 0
            ):
                raise _csv_error(
                    source_path=source_path,
                    physical_data_row_index=data_index,
                    calendar_minute_index=data_index,
                    category="NEGATIVE_ACCESSED_IRRADIANCE",
                )

    if physical_data_row_count <= last_minute:
        raise _csv_error(
            source_path=source_path,
            physical_data_row_index=last_minute,
            calendar_minute_index=last_minute,
            category="INSUFFICIENT_PHYSICAL_DATA_ROWS",
        )
    return {
        "physical_data_row_count": physical_data_row_count,
        "first_accessed_data_row": first_minute,
        "last_accessed_data_row": last_minute,
        "first_calendar_minute_index": first_minute,
        "last_calendar_minute_index": last_minute,
        "accessed_sample_count": last_minute - first_minute + 1,
    }


def _resolve_compiler(compiler: Path | str) -> Path:
    candidate = str(compiler)
    located = shutil.which(candidate)
    path = Path(located if located is not None else candidate)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SolarParseProofError(f"C++ compiler not found: {compiler}") from exc
    if not resolved.is_file():
        raise SolarParseProofError(f"C++ compiler is not a file: {resolved}")
    return resolved


def _verifier_compile_arguments(source: Path, binary: Path) -> list[str]:
    return [
        f"-std={SOLAR_STOD_BUILD_STANDARD}",
        "-O2",
        "-Wall",
        "-Wextra",
        "-pedantic",
        str(source),
        "-o",
        str(binary),
    ]


def _describe_diagnostic_untrusted_verifier_build(
    *,
    compiler: Path | str,
    source: Path,
    binary: Path,
) -> Dict[str, Any]:
    compiler_path = _resolve_compiler(compiler)
    source_path = source.resolve(strict=True)
    binary_path = binary.resolve(strict=True)
    completed = subprocess.run(
        [str(compiler_path), "--version"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    if completed.returncode:
        raise SolarParseProofError("cannot record C++ compiler version")
    version = (completed.stdout + completed.stderr).strip()
    if not version:
        raise SolarParseProofError("C++ compiler version output is empty")
    return {
        "compiler": _absolute_file_material(compiler_path),
        "compiler_version": version,
        "cpp_standard": SOLAR_STOD_BUILD_STANDARD,
        "compile_arguments": _verifier_compile_arguments(
            source_path, binary_path,
        ),
    }


def build_diagnostic_untrusted_verifier_binary(
    binary: Path | str,
    *,
    compiler: Path | str = "c++",
    source: Path | str | None = None,
) -> Dict[str, Any]:
    source_path = Path(
        source or PROJECT_ROOT / VERIFIER_SOURCE_RELATIVE_PATH
    ).resolve(strict=True)
    binary_path = Path(binary).resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        binary_path.relative_to(temporary_root)
    except ValueError as exc:
        raise SolarParseProofError(
            "verifier binary output must be inside the local temporary root"
        ) from exc
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    compiler_path = _resolve_compiler(compiler)
    arguments = _verifier_compile_arguments(source_path, binary_path)
    completed = subprocess.run(
        [str(compiler_path), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    if completed.returncode:
        raise SolarParseProofError(
            "cannot build checked-in solar std::stod verifier: "
            + completed.stderr.strip()
        )
    if not binary_path.is_file() or not os.access(binary_path, os.X_OK):
        raise SolarParseProofError("verifier compiler produced no executable")
    return _describe_diagnostic_untrusted_verifier_build(
        compiler=compiler_path, source=source_path, binary=binary_path,
    )


def _strict_json_line(line: str) -> Mapping[str, Any]:
    def pairs(items: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SolarParseProofError(
                    f"duplicate verifier JSON key: {key}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            line,
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                SolarParseProofError(
                    f"non-finite verifier JSON constant: {token}"
                )
            ),
        )
    except SolarParseProofError:
        raise
    except Exception as exc:
        raise SolarParseProofError("invalid verifier JSON output") from exc
    if not isinstance(value, Mapping):
        raise SolarParseProofError("verifier output record must be a mapping")
    return value


def inspect_solar_rows_diagnostic_untrusted(
    verifier_binary: Path | str,
    solar_csv: Path | str,
    *,
    first_row: int,
    last_row: int,
) -> Dict[str, Any]:
    binary = Path(verifier_binary).resolve(strict=True)
    csv_path = Path(solar_csv).resolve(strict=True)
    completed = subprocess.run(
        [str(binary), str(csv_path), str(first_row), str(last_row)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    records = [
        _strict_json_line(line)
        for line in completed.stdout.splitlines()
        if line
    ]
    errors = [
        _strict_json_line(line)
        for line in completed.stderr.splitlines()
        if line
    ]
    identities = [
        record for record in records if record.get("record_type") == "identity"
    ]
    rows = [
        record for record in records if record.get("record_type") == "row"
    ]
    if len(identities) != 1:
        raise SolarParseProofError("verifier emitted no unique identity record")
    if any(record.get("record_type") not in {"identity", "row"} for record in records):
        raise SolarParseProofError("verifier emitted an unknown record type")
    return {
        "returncode": completed.returncode,
        "identity": dict(identities[0]),
        "rows": [dict(row) for row in rows],
        "errors": [dict(error) for error in errors],
    }


def _raw_csv_lines(path: Path) -> list[bytes]:
    payload = path.read_bytes()
    lines = payload.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def _python_binary64_bits(raw_line: bytes) -> str:
    try:
        text = raw_line.decode("utf-8").rstrip("\r").strip()
        value = float(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise SolarParseProofError(
            "accessed solar row is not Python numeric UTF-8 text"
        ) from exc
    if (
        SOLAR_STOD_STRICT_DECIMAL.fullmatch(text) is None
        or not math.isfinite(value)
        or value < 0
    ):
        raise SolarParseProofError(
            "accessed solar row violates the strict shared decimal policy"
        )
    return struct.pack(">d", value).hex()


def _resolve_inputs(
    *,
    source_root: Path | str,
    base_system_path: Path | str,
    energy_support: Mapping[str, Any] | Path | str,
    solar_csv_path: Path | str,
    day_of_year: int,
    time_of_day_ms: int,
    horizon: int,
) -> Dict[str, Any]:
    root = Path(source_root).resolve(strict=True)
    base = Path(base_system_path)
    if not base.is_absolute():
        base = root / base
    base = base.resolve(strict=True)
    solar = Path(solar_csv_path)
    if not solar.is_absolute():
        solar = root / solar
    solar = solar.resolve(strict=True)
    if (
        isinstance(day_of_year, bool)
        or not isinstance(day_of_year, int)
        or day_of_year <= 0
        or isinstance(time_of_day_ms, bool)
        or not isinstance(time_of_day_ms, int)
        or time_of_day_ms < 0
        or isinstance(horizon, bool)
        or not isinstance(horizon, int)
        or horizon <= 0
    ):
        raise SolarParseProofError("invalid parse-proof phase or horizon")

    try:
        system = legacy_rta.load_system_config(str(base))
    except Exception as exc:
        raise SolarParseProofError(
            f"cannot load parse-proof base system: {exc}"
        ) from exc
    if (
        system.day_of_year != day_of_year
        or system.time_of_day_ms != time_of_day_ms
    ):
        raise SolarParseProofError("parse-proof phase does not match base system")
    if not system.use_real_solar_data:
        raise SolarParseProofError("parse proof requires real solar data")
    try:
        configured_solar = Path(
            legacy_rta._resolve_solar_path(system)
        ).resolve(strict=True)
    except OSError as exc:
        raise SolarParseProofError("configured solar CSV is missing") from exc
    if configured_solar != solar:
        raise SolarParseProofError(
            "parse-proof CSV does not match the base system"
        )

    energy, support_source, support_path = load_energy_support(
        energy_support, source_root=root,
    )
    service = energy.get("service_curve")
    if not isinstance(service, Mapping):
        raise SolarParseProofError(
            "parse-proof energy support has no service_curve"
        )
    template_value = service.get("system_template")
    if not isinstance(template_value, str) or not template_value:
        raise SolarParseProofError(
            "parse-proof energy support has no system template"
        )
    declared = Path(template_value)
    candidates = (
        {declared.resolve()}
        if declared.is_absolute()
        else {
            (root / declared).resolve(),
            *(
                {(support_path.parent / declared).resolve()}
                if support_path is not None
                else set()
            ),
        }
    )
    if base not in candidates:
        raise SolarParseProofError(
            "parse-proof energy support system template mismatch"
        )
    declared_horizon = service.get("horizon")
    if declared_horizon is not None and (
        isinstance(declared_horizon, bool)
        or not isinstance(declared_horizon, int)
        or horizon > declared_horizon
    ):
        raise SolarParseProofError(
            "parse-proof horizon exceeds direct energy support"
        )
    if service.get("require_real_solar_data") is not True:
        raise SolarParseProofError(
            "parse-proof energy support must require real solar data"
        )

    csv_validation = validate_solar_csv_domain(
        solar,
        source_root=root,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        horizon=horizon,
    )
    return {
        "root": root,
        "base": base,
        "solar": solar,
        "support_source": support_source,
        "inputs": {
            "system_template": source_file_material(base, root),
            "energy_support": support_source,
            "solar_csv": source_file_material(solar, root),
            "day_of_year": day_of_year,
            "time_of_day_ms": time_of_day_ms,
            "materialized_start_offset_ms": (
                legacy_rta.materialize_runtime_start_offset_ms(
                    day_of_year, time_of_day_ms,
                )
            ),
            "horizon": horizon,
            "first_accessed_data_row": (
                csv_validation["first_accessed_data_row"]
            ),
            "last_accessed_data_row": (
                csv_validation["last_accessed_data_row"]
            ),
            "accessed_sample_count": csv_validation["accessed_sample_count"],
        },
        "csv_validation": csv_validation,
    }


def _proof_builder_sources(source_root: Path) -> list[Dict[str, Any]]:
    return [
        source_file_material(PROJECT_ROOT / relative, source_root)
        for relative in (
            PROOF_MODULE_RELATIVE_PATH,
            PROOF_BUILDER_RELATIVE_PATH,
        )
    ]


def build_diagnostic_untrusted_solar_stod_parse_proof(
    *,
    source_root: Path | str,
    base_system_path: Path | str,
    energy_support: Mapping[str, Any] | Path | str,
    solar_csv_path: Path | str,
    day_of_year: int,
    time_of_day_ms: int,
    horizon: int,
    verifier_binary: Path | str,
    compiler: Path | str = "c++",
    build_verifier: bool = True,
) -> Dict[str, Any]:
    """Diagnostic only: caller-selected binaries are not formal-safe."""

    resolved = _resolve_inputs(
        source_root=source_root,
        base_system_path=base_system_path,
        energy_support=energy_support,
        solar_csv_path=solar_csv_path,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        horizon=horizon,
    )
    root = resolved["root"]
    solar = resolved["solar"]
    verifier_source = (
        PROJECT_ROOT / VERIFIER_SOURCE_RELATIVE_PATH
    ).resolve(strict=True)
    verifier = Path(verifier_binary).resolve()
    if build_verifier:
        toolchain = build_diagnostic_untrusted_verifier_binary(
            verifier, compiler=compiler, source=verifier_source,
        )
    else:
        toolchain = _describe_diagnostic_untrusted_verifier_build(
            compiler=compiler,
            source=verifier_source,
            binary=verifier,
        )

    first = resolved["inputs"]["first_accessed_data_row"]
    last = resolved["inputs"]["last_accessed_data_row"]
    inspection = inspect_solar_rows_diagnostic_untrusted(
        verifier, solar, first_row=first, last_row=last,
    )
    if inspection["returncode"] != 0:
        categories = sorted({
            str(row.get("exception_category"))
            for row in inspection["rows"]
            if row.get("parse_status") != "success"
        } | {
            str(error.get("category")) for error in inspection["errors"]
        })
        raise SolarParseProofError(
            "C++ std::stod verifier rejected accessed solar rows: "
            + ",".join(categories)
        )

    identity = inspection["identity"]
    expected_identity_keys = {
        "record_type", "parser_contract_version", "numeric_locale",
        "cpp_standard", "standard_library", "libc",
        "double_is_iec559", "double_size_bytes",
    }
    if (
        set(identity) != expected_identity_keys
        or identity.get("record_type") != "identity"
        or identity.get("parser_contract_version")
        != SOLAR_STOD_PARSER_CONTRACT
        or identity.get("numeric_locale") != SOLAR_STOD_NUMERIC_LOCALE
        or identity.get("cpp_standard") != 201703
        or identity.get("double_is_iec559") is not True
        or identity.get("double_size_bytes") != 8
        or not isinstance(identity.get("standard_library"), str)
        or not identity["standard_library"]
        or not isinstance(identity.get("libc"), str)
        or not identity["libc"]
    ):
        raise SolarParseProofError("verifier runtime identity is unsupported")

    raw_lines = _raw_csv_lines(solar)
    if not raw_lines:
        raise SolarParseProofError("solar CSV has no header")
    verifier_rows = inspection["rows"]
    if len(verifier_rows) != last - first + 1:
        raise SolarParseProofError("verifier row coverage is incomplete")
    rows = []
    for expected_index, observed in zip(
        range(first, last + 1), verifier_rows,
    ):
        if expected_index + 1 >= len(raw_lines):
            raise SolarParseProofError("solar CSV row is missing")
        raw_line = raw_lines[expected_index + 1]
        python_bits = _python_binary64_bits(raw_line)
        if (
            observed.get("record_type") != "row"
            or observed.get("physical_data_row_index") != expected_index
            or observed.get("file_physical_line_number")
            != expected_index + 2
            or observed.get("parse_status") != "success"
            or observed.get("exception_category") != "none"
            or observed.get("finite") is not True
            or observed.get("negative") is not False
            or observed.get("binary64_bits") != python_bits
            or observed.get("raw_text_length") != len(raw_line)
            or isinstance(observed.get("consumed_characters"), bool)
            or not isinstance(observed.get("consumed_characters"), int)
            or observed["consumed_characters"] < 1
            or observed["consumed_characters"] > len(raw_line)
        ):
            raise SolarParseProofError(
                "Python/C++ accessed solar row semantics mismatch"
            )
        rows.append({
            **dict(observed),
            "calendar_minute_index": expected_index,
            "raw_text_sha256": hashlib.sha256(raw_line).hexdigest(),
            "python_binary64_bits": python_bits,
        })

    material = {
        "schema": SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF_SCHEMA,
        "classification": SOLAR_STOD_DIAGNOSTIC_CLASSIFICATION,
        "builder_version": SOLAR_STOD_PROOF_BUILDER_VERSION,
        "builder_sources": _proof_builder_sources(root),
        "parser_contract_version": SOLAR_STOD_PARSER_CONTRACT,
        "parser": {
            "verifier_source": source_file_material(verifier_source, root),
            "verifier_binary": _absolute_file_material(verifier),
            "toolchain": toolchain,
            "runtime_identity": identity,
        },
        "inputs": resolved["inputs"],
        "header": {
            "file_physical_line_number": 1,
            "raw_text_sha256": hashlib.sha256(raw_lines[0]).hexdigest(),
            "raw_text_length": len(raw_lines[0]),
        },
        "rows": rows,
    }
    return {
        **material,
        "proof_id": domain_hash(
            SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF_DOMAIN, material,
        ),
    }


def write_solar_stod_parse_proof(
    path: Path | str,
    proof: Mapping[str, Any],
) -> None:
    output = Path(path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        canonical_json(thaw_material(proof)) + "\n"
    ).encode("utf-8")
    temporary = output.with_name(output.name + f".partial.{os.getpid()}")
    temporary.write_bytes(payload)
    os.replace(temporary, output)


def _require_exact_mapping(
    value: Any,
    keys: set[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise SolarParseProofError(f"{label} structure mismatch")
    return value


def validate_diagnostic_untrusted_solar_stod_parse_proof(
    proof_path: Path | str,
    *,
    verifier_binary: Path | str,
    source_root: Path | str,
    base_system_path: Path | str,
    energy_support: Mapping[str, Any] | Path | str,
    solar_csv_path: Path | str,
    day_of_year: int,
    time_of_day_ms: int,
    horizon: int,
) -> Dict[str, Any]:
    """Diagnostic only: this does not establish a formal-safe trusted root."""

    path = Path(proof_path).resolve(strict=True)
    try:
        proof = load_strict_json(path)
    except Exception as exc:
        raise SolarParseProofError(f"cannot load strict parse proof: {path}") from exc
    top_keys = {
        "schema", "classification", "builder_version", "builder_sources",
        "parser_contract_version", "parser", "inputs", "header", "rows",
        "proof_id",
    }
    _require_exact_mapping(proof, top_keys, "parse proof")
    canonical_bytes = (canonical_json(proof) + "\n").encode("utf-8")
    if path.read_bytes() != canonical_bytes:
        raise SolarParseProofError("parse proof is not canonical JSON")
    if (
        proof.get("schema") != SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF_SCHEMA
        or proof.get("classification")
        != SOLAR_STOD_DIAGNOSTIC_CLASSIFICATION
        or proof.get("builder_version") != SOLAR_STOD_PROOF_BUILDER_VERSION
        or proof.get("parser_contract_version")
        != SOLAR_STOD_PARSER_CONTRACT
    ):
        raise SolarParseProofError("parse proof version mismatch")
    material = {key: proof[key] for key in top_keys if key != "proof_id"}
    if (
        domain_hash(SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF_DOMAIN, material)
        != proof.get("proof_id")
    ):
        raise SolarParseProofError("parse proof identity mismatch")

    resolved = _resolve_inputs(
        source_root=source_root,
        base_system_path=base_system_path,
        energy_support=energy_support,
        solar_csv_path=solar_csv_path,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        horizon=horizon,
    )
    root = resolved["root"]
    solar = resolved["solar"]
    if proof.get("inputs") != resolved["inputs"]:
        raise SolarParseProofError("parse proof input/phase/horizon drift")
    if proof.get("builder_sources") != _proof_builder_sources(root):
        raise SolarParseProofError("parse proof builder source drift")

    parser = _require_exact_mapping(
        proof.get("parser"),
        {
            "verifier_source", "verifier_binary", "toolchain",
            "runtime_identity",
        },
        "parse proof parser",
    )
    verifier_source = (
        PROJECT_ROOT / VERIFIER_SOURCE_RELATIVE_PATH
    ).resolve(strict=True)
    verifier = Path(verifier_binary).resolve(strict=True)
    if parser.get("verifier_source") != source_file_material(
        verifier_source, root,
    ):
        raise SolarParseProofError("parse proof verifier source drift")
    if parser.get("verifier_binary") != _absolute_file_material(verifier):
        raise SolarParseProofError("parse proof verifier binary drift")

    runtime_identity = _require_exact_mapping(
        parser.get("runtime_identity"),
        {
            "record_type", "parser_contract_version", "numeric_locale",
            "cpp_standard", "standard_library", "libc",
            "double_is_iec559", "double_size_bytes",
        },
        "parse proof runtime identity",
    )
    if (
        runtime_identity.get("record_type") != "identity"
        or runtime_identity.get("parser_contract_version")
        != SOLAR_STOD_PARSER_CONTRACT
        or runtime_identity.get("numeric_locale")
        != SOLAR_STOD_NUMERIC_LOCALE
        or runtime_identity.get("cpp_standard") != 201703
        or runtime_identity.get("double_is_iec559") is not True
        or runtime_identity.get("double_size_bytes") != 8
    ):
        raise SolarParseProofError("parse proof runtime contract mismatch")

    toolchain = _require_exact_mapping(
        parser.get("toolchain"),
        {
            "compiler", "compiler_version", "cpp_standard",
            "compile_arguments",
        },
        "parse proof toolchain",
    )
    compiler = _require_exact_mapping(
        toolchain.get("compiler"),
        {"absolute_path", "sha256", "size"},
        "parse proof compiler",
    )
    try:
        current_compiler = _absolute_file_material(
            Path(str(compiler["absolute_path"]))
        )
    except (OSError, KeyError, SolarParseProofError) as exc:
        raise SolarParseProofError("parse proof compiler is unavailable") from exc
    if current_compiler != compiler:
        raise SolarParseProofError("parse proof compiler binary drift")
    if (
        not isinstance(toolchain.get("compiler_version"), str)
        or not toolchain["compiler_version"]
        or toolchain.get("cpp_standard") != SOLAR_STOD_BUILD_STANDARD
        or toolchain.get("compile_arguments")
        != _verifier_compile_arguments(verifier_source, verifier)
    ):
        raise SolarParseProofError("parse proof toolchain contract mismatch")

    raw_lines = _raw_csv_lines(solar)
    header = _require_exact_mapping(
        proof.get("header"),
        {"file_physical_line_number", "raw_text_sha256", "raw_text_length"},
        "parse proof header",
    )
    if (
        not raw_lines
        or header.get("file_physical_line_number") != 1
        or header.get("raw_text_sha256")
        != hashlib.sha256(raw_lines[0]).hexdigest()
        or header.get("raw_text_length") != len(raw_lines[0])
    ):
        raise SolarParseProofError("parse proof header drift")

    first = resolved["inputs"]["first_accessed_data_row"]
    last = resolved["inputs"]["last_accessed_data_row"]
    rows = proof.get("rows")
    if not isinstance(rows, list) or len(rows) != last - first + 1:
        raise SolarParseProofError("parse proof row coverage mismatch")
    row_keys = {
        "record_type", "physical_data_row_index",
        "file_physical_line_number", "parse_status", "exception_category",
        "finite", "negative", "binary64_bits", "consumed_characters",
        "raw_text_length", "calendar_minute_index", "raw_text_sha256",
        "python_binary64_bits",
    }
    for expected_index, row in zip(range(first, last + 1), rows):
        _require_exact_mapping(row, row_keys, "parse proof row")
        if expected_index + 1 >= len(raw_lines):
            raise SolarParseProofError("parse proof source row is missing")
        raw_line = raw_lines[expected_index + 1]
        python_bits = _python_binary64_bits(raw_line)
        if (
            row.get("record_type") != "row"
            or row.get("physical_data_row_index") != expected_index
            or row.get("calendar_minute_index") != expected_index
            or row.get("file_physical_line_number") != expected_index + 2
            or row.get("raw_text_sha256")
            != hashlib.sha256(raw_line).hexdigest()
            or row.get("raw_text_length") != len(raw_line)
            or row.get("parse_status") != "success"
            or row.get("exception_category") != "none"
            or row.get("finite") is not True
            or row.get("negative") is not False
            or row.get("binary64_bits") != python_bits
            or row.get("python_binary64_bits") != python_bits
            or isinstance(row.get("consumed_characters"), bool)
            or not isinstance(row.get("consumed_characters"), int)
            or row["consumed_characters"] < 1
            or row["consumed_characters"] > len(raw_line)
        ):
            raise SolarParseProofError(
                "parse proof row/hash/Python-C++ bits mismatch"
            )
    reconstructed = build_diagnostic_untrusted_solar_stod_parse_proof(
        source_root=source_root,
        base_system_path=base_system_path,
        energy_support=energy_support,
        solar_csv_path=solar_csv_path,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        horizon=horizon,
        verifier_binary=verifier,
        compiler=Path(str(compiler["absolute_path"])),
        build_verifier=False,
    )
    if dict(proof) != reconstructed:
        raise SolarParseProofError(
            "parse proof does not match reconstructed C++ evidence"
        )
    return dict(proof)


def _formal_builder_sources() -> list[Dict[str, Any]]:
    result = []
    for relative in (
        PROOF_MODULE_RELATIVE_PATH,
        PROOF_BUILDER_RELATIVE_PATH,
    ):
        path = (PROJECT_ROOT / relative).resolve(strict=True)
        payload = _read_file_once(path, label=f"proof builder source {relative}")
        result.append(
            _source_material_from_bytes(path, PROJECT_ROOT, payload)
        )
    return result


def _compiler_version(compiler_path: Path) -> str:
    completed = subprocess.run(
        [str(compiler_path), "--version"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    if completed.returncode:
        raise SolarParseProofError("cannot record C++ compiler version")
    version = (completed.stdout + completed.stderr).strip()
    if not version:
        raise SolarParseProofError("C++ compiler version output is empty")
    return version


def _build_internal_verified_verifier(
    snapshot: ImmutableSolarReplaySnapshot,
    *,
    compiler: Path | str,
) -> tuple[Path, Dict[str, Any]]:
    """Compile the snapshotted checked-in source into the private workspace."""

    compiler_path = _resolve_compiler(compiler)
    compiler_bytes = _read_file_once(
        compiler_path, label="selected C++ compiler",
    )
    compiler_material = {
        "absolute_path": str(compiler_path),
        "sha256": hashlib.sha256(compiler_bytes).hexdigest(),
        "size": len(compiler_bytes),
    }
    version = _compiler_version(compiler_path)
    binary_path = snapshot.workspace / "internal-solar-stod-verifier"
    if binary_path.exists():
        raise SolarParseProofError(
            "private verifier binary path unexpectedly exists"
        )
    arguments = _verifier_compile_arguments(
        snapshot.verifier_source_path, binary_path,
    )
    completed = subprocess.run(
        [str(compiler_path), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    if completed.returncode:
        raise SolarParseProofError(
            "cannot build private checked-in solar std::stod verifier: "
            + completed.stderr.strip()
        )
    try:
        binary_stat = os.lstat(binary_path)
    except OSError as exc:
        raise SolarParseProofError(
            "compiler produced no private verifier binary"
        ) from exc
    if not stat.S_ISREG(binary_stat.st_mode) or stat.S_ISLNK(binary_stat.st_mode):
        raise SolarParseProofError(
            "compiler output is not a private regular verifier binary"
        )
    binary_bytes = _read_file_once(
        binary_path, label="private verifier binary",
    )
    if not binary_bytes:
        raise SolarParseProofError("private verifier binary is empty")
    os.chmod(binary_path, 0o555)
    compiler_after = _read_file_once(
        compiler_path, label="selected C++ compiler after build",
    )
    if compiler_after != compiler_bytes:
        raise SolarParseProofError(
            "selected C++ compiler changed during private verifier build"
        )
    return binary_path, {
        "compiler": compiler_material,
        "compiler_version": version,
        "cpp_standard": SOLAR_STOD_BUILD_STANDARD,
        "compile_arguments": list(
            SOLAR_STOD_NORMALIZED_COMPILE_ARGUMENTS
        ),
        "verifier_binary": {
            "sha256": hashlib.sha256(binary_bytes).hexdigest(),
            "size": len(binary_bytes),
        },
    }


def _inspection_from_completed(
    completed: subprocess.CompletedProcess[str],
) -> Dict[str, Any]:
    records = [
        _strict_json_line(line)
        for line in completed.stdout.splitlines()
        if line
    ]
    errors = [
        _strict_json_line(line)
        for line in completed.stderr.splitlines()
        if line
    ]
    identities = [
        record
        for record in records
        if record.get("record_type") == "identity"
    ]
    rows = [
        record
        for record in records
        if record.get("record_type") == "row"
    ]
    if len(identities) != 1:
        raise SolarParseProofError(
            "private verifier emitted no unique identity record"
        )
    if any(
        record.get("record_type") not in {"identity", "row"}
        for record in records
    ):
        raise SolarParseProofError(
            "private verifier emitted an unknown record type"
        )
    return {
        "returncode": completed.returncode,
        "identity": dict(identities[0]),
        "rows": [dict(row) for row in rows],
        "errors": [dict(error) for error in errors],
    }


def _inspect_internal_verified_rows(
    snapshot: ImmutableSolarReplaySnapshot,
    verifier_binary: Path,
    *,
    first_row: int,
    last_row: int,
) -> Dict[str, Any]:
    completed = subprocess.run(
        [
            str(verifier_binary),
            snapshot.solar_runtime_path,
            str(first_row),
            str(last_row),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=(snapshot.csv_descriptor,),
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    return _inspection_from_completed(completed)


def _validate_runtime_identity(identity: Mapping[str, Any]) -> None:
    expected_keys = {
        "record_type", "parser_contract_version", "numeric_locale",
        "cpp_standard", "standard_library", "libc",
        "double_is_iec559", "double_size_bytes",
    }
    if (
        set(identity) != expected_keys
        or identity.get("record_type") != "identity"
        or identity.get("parser_contract_version")
        != SOLAR_STOD_PARSER_CONTRACT
        or identity.get("numeric_locale") != SOLAR_STOD_NUMERIC_LOCALE
        or identity.get("cpp_standard") != 201703
        or identity.get("double_is_iec559") is not True
        or identity.get("double_size_bytes") != 8
        or not isinstance(identity.get("standard_library"), str)
        or not identity["standard_library"]
        or not isinstance(identity.get("libc"), str)
        or not identity["libc"]
    ):
        raise SolarParseProofError(
            "private verifier runtime identity is unsupported"
        )


def _raw_csv_lines_from_bytes(payload: bytes) -> list[bytes]:
    lines = payload.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def _snapshot_support_energy(
    snapshot: ImmutableSolarReplaySnapshot,
) -> tuple[Dict[str, Any], Mapping[str, Any]]:
    document = _support_document_from_bytes(snapshot.support.payload)
    energy = document.get("energy", document)
    if not isinstance(energy, Mapping):
        raise SolarParseProofError(
            "snapshotted energy support has no energy mapping"
        )
    service = energy.get("service_curve")
    if not isinstance(service, Mapping):
        raise SolarParseProofError(
            "snapshotted energy support has no service_curve"
        )
    template_value = service.get("system_template")
    if not isinstance(template_value, str) or not template_value:
        raise SolarParseProofError(
            "snapshotted energy support has no system template"
        )
    declared = Path(template_value)
    candidates = (
        {declared.resolve()}
        if declared.is_absolute()
        else {
            (snapshot.source_root / declared).resolve(),
            (snapshot.support.original_path.parent / declared).resolve(),
        }
    )
    if snapshot.system.original_path not in candidates:
        raise SolarParseProofError(
            "snapshotted energy support system template does not match"
        )
    if service.get("require_real_solar_data") is not True:
        raise SolarParseProofError(
            "snapshotted energy support must require real solar data"
        )
    return dict(energy), service


def build_live_solar_stod_parse_proof_from_snapshot(
    snapshot: ImmutableSolarReplaySnapshot,
    *,
    day_of_year: int,
    time_of_day_ms: int,
    horizon: int,
    compiler: Path | str = "c++",
) -> Mapping[str, Any]:
    """Build immutable live evidence from private source and CSV snapshots."""

    if (
        isinstance(day_of_year, bool)
        or not isinstance(day_of_year, int)
        or day_of_year <= 0
        or isinstance(time_of_day_ms, bool)
        or not isinstance(time_of_day_ms, int)
        or time_of_day_ms < 0
        or isinstance(horizon, bool)
        or not isinstance(horizon, int)
        or horizon <= 0
    ):
        raise SolarParseProofError("invalid parse-proof phase or horizon")
    try:
        system = legacy_rta.load_system_config(str(snapshot.system_path))
    except Exception as exc:
        raise SolarParseProofError(
            f"cannot load snapshotted base system: {exc}"
        ) from exc
    if (
        system.day_of_year != day_of_year
        or system.time_of_day_ms != time_of_day_ms
    ):
        raise SolarParseProofError(
            "parse-proof phase does not match snapshotted system"
        )
    if not system.use_real_solar_data:
        raise SolarParseProofError("parse proof requires real solar data")
    energy, service = _snapshot_support_energy(snapshot)
    declared_horizon = service.get("horizon")
    if declared_horizon is not None and (
        isinstance(declared_horizon, bool)
        or not isinstance(declared_horizon, int)
        or horizon > declared_horizon
    ):
        raise SolarParseProofError(
            "parse-proof horizon exceeds snapshotted energy support"
        )
    try:
        scale = Fraction(str(service.get("solar_scale", "1")))
    except (ValueError, ZeroDivisionError) as exc:
        raise SolarParseProofError(
            "snapshotted solar scale is invalid"
        ) from exc
    if scale <= 0:
        raise SolarParseProofError(
            "snapshotted solar scale must be positive"
        )

    csv_validation = validate_solar_csv_domain(
        Path(snapshot.solar_runtime_path),
        source_root=snapshot.workspace,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        horizon=horizon,
    )
    first = csv_validation["first_accessed_data_row"]
    last = csv_validation["last_accessed_data_row"]
    verifier, build = _build_internal_verified_verifier(
        snapshot, compiler=compiler,
    )
    inspection = _inspect_internal_verified_rows(
        snapshot, verifier, first_row=first, last_row=last,
    )
    if inspection["returncode"] != 0:
        categories = sorted({
            str(row.get("exception_category"))
            for row in inspection["rows"]
            if row.get("parse_status") != "success"
        } | {
            str(error.get("category"))
            for error in inspection["errors"]
        })
        raise SolarParseProofError(
            "private C++ std::stod verifier rejected accessed solar rows: "
            + ",".join(categories)
        )
    identity = inspection["identity"]
    _validate_runtime_identity(identity)

    raw_lines = _raw_csv_lines_from_bytes(snapshot.solar_csv.payload)
    if not raw_lines:
        raise SolarParseProofError("snapshotted solar CSV has no header")
    verifier_rows = inspection["rows"]
    if len(verifier_rows) != last - first + 1:
        raise SolarParseProofError(
            "private verifier row coverage is incomplete"
        )
    rows = []
    for expected_index, observed in zip(
        range(first, last + 1), verifier_rows,
    ):
        if expected_index + 1 >= len(raw_lines):
            raise SolarParseProofError(
                "snapshotted solar CSV row is missing"
            )
        raw_line = raw_lines[expected_index + 1]
        python_bits = _python_binary64_bits(raw_line)
        if (
            observed.get("record_type") != "row"
            or observed.get("physical_data_row_index") != expected_index
            or observed.get("file_physical_line_number")
            != expected_index + 2
            or observed.get("parse_status") != "success"
            or observed.get("exception_category") != "none"
            or observed.get("finite") is not True
            or observed.get("negative") is not False
            or observed.get("binary64_bits") != python_bits
            or observed.get("raw_text_length") != len(raw_line)
            or isinstance(observed.get("consumed_characters"), bool)
            or not isinstance(observed.get("consumed_characters"), int)
            or observed["consumed_characters"] < 1
            or observed["consumed_characters"] > len(raw_line)
        ):
            raise SolarParseProofError(
                "Python/private-C++ accessed solar row semantics mismatch"
            )
        rows.append({
            **dict(observed),
            "calendar_minute_index": expected_index,
            "raw_text_sha256": hashlib.sha256(raw_line).hexdigest(),
            "python_binary64_bits": python_bits,
        })

    source_descriptors = {
        "system_template": thaw_material(snapshot.system.material),
        "energy_support": thaw_material(snapshot.support.material),
        "solar_csv": thaw_material(snapshot.solar_csv.material),
    }
    header = {
        "file_physical_line_number": 1,
        "raw_text_sha256": hashlib.sha256(raw_lines[0]).hexdigest(),
        "raw_text_length": len(raw_lines[0]),
    }
    semantic = {
        "schema": SOLAR_SEMANTIC_SERVICE_SOURCE_SCHEMA,
        "source_bytes": {
            name: _semantic_bytes_material(material)
            for name, material in source_descriptors.items()
        },
        "day_of_year": day_of_year,
        "time_of_day_ms": time_of_day_ms,
        "materialized_start_offset_ms": (
            legacy_rta.materialize_runtime_start_offset_ms(
                day_of_year, time_of_day_ms,
            )
        ),
        "horizon": horizon,
        "solar_scale": fraction_text(scale),
        "physical_data_row_count": (
            csv_validation["physical_data_row_count"]
        ),
        "first_accessed_data_row": first,
        "last_accessed_data_row": last,
        "accessed_sample_count": (
            csv_validation["accessed_sample_count"]
        ),
        "parser_contract_version": SOLAR_STOD_PARSER_CONTRACT,
        "header": header,
        "rows": rows,
    }
    semantic_identity = domain_hash(
        SOLAR_SEMANTIC_SERVICE_SOURCE_DOMAIN, semantic,
    )
    environment = {
        "schema": SOLAR_PARSER_ENVIRONMENT_SCHEMA,
        "verifier_source": thaw_material(
            snapshot.verifier_source.material
        ),
        "compiler": build["compiler"],
        "compiler_version": build["compiler_version"],
        "cpp_standard": build["cpp_standard"],
        "compile_arguments": build["compile_arguments"],
        "verifier_binary": build["verifier_binary"],
        "runtime_identity": identity,
    }
    environment_identity = domain_hash(
        SOLAR_PARSER_ENVIRONMENT_DOMAIN, environment,
    )
    material = {
        "schema": SOLAR_STOD_PARSE_PROOF_SCHEMA,
        "builder_version": SOLAR_STOD_PROOF_BUILDER_VERSION,
        "builder_sources": _formal_builder_sources(),
        "parser_contract_version": SOLAR_STOD_PARSER_CONTRACT,
        "source_descriptors": source_descriptors,
        "semantic_service_source": semantic,
        "semantic_service_source_identity": semantic_identity,
        "parser_environment": environment,
        "parser_environment_identity": environment_identity,
    }
    live_identity = domain_hash(
        SOLAR_STOD_PARSE_PROOF_DOMAIN, material,
    )
    return freeze_material({
        **material,
        "live_proof_identity": live_identity,
        "proof_id": live_identity,
    })


def validate_expected_solar_stod_parse_proof(
    snapshot: ImmutableSolarReplaySnapshot,
    live_proof: Mapping[str, Any],
) -> None:
    """Compare an optional snapshotted expectation with fresh live evidence."""

    if snapshot.expected_proof_path is None:
        return
    try:
        expected = load_strict_json(snapshot.expected_proof_path)
    except Exception as exc:
        raise SolarParseProofError(
            "cannot load strict snapshotted expected parse proof"
        ) from exc
    expected_bytes = (
        canonical_json(expected) + "\n"
    ).encode("utf-8")
    assert snapshot.expected_proof is not None
    if expected_bytes != snapshot.expected_proof.payload:
        raise SolarParseProofError(
            "expected parse proof is not canonical JSON"
        )
    if expected != thaw_material(live_proof):
        raise SolarParseProofError(
            "expected parse proof does not match fresh internal live evidence"
        )


def build_solar_stod_parse_proof(
    *,
    source_root: Path | str,
    base_system_path: Path | str,
    energy_support: Path | str,
    solar_csv_path: Path | str,
    day_of_year: int,
    time_of_day_ms: int,
    horizon: int,
    compiler: Path | str = "c++",
    expected_proof_path: Path | str | None = None,
) -> Mapping[str, Any]:
    """Build formal-safe proof using only an internally compiled verifier."""

    if isinstance(energy_support, Mapping):
        raise SolarParseProofError(
            "formal-safe parse proof requires an energy-support file path"
        )
    with ImmutableSolarReplaySnapshot(
        source_root=source_root,
        base_system_path=base_system_path,
        energy_support_path=energy_support,
        declared_solar_csv_path=solar_csv_path,
        expected_proof_path=expected_proof_path,
    ) as snapshot:
        proof = build_live_solar_stod_parse_proof_from_snapshot(
            snapshot,
            day_of_year=day_of_year,
            time_of_day_ms=time_of_day_ms,
            horizon=horizon,
            compiler=compiler,
        )
        validate_expected_solar_stod_parse_proof(snapshot, proof)
        return proof


def validate_solar_stod_parse_proof(
    proof_path: Path | str,
    *,
    source_root: Path | str,
    base_system_path: Path | str,
    energy_support: Path | str,
    solar_csv_path: Path | str,
    day_of_year: int,
    time_of_day_ms: int,
    horizon: int,
    compiler: Path | str = "c++",
) -> Mapping[str, Any]:
    """Rebuild the trusted binary and compare expected proof with live evidence."""

    return build_solar_stod_parse_proof(
        source_root=source_root,
        base_system_path=base_system_path,
        energy_support=energy_support,
        solar_csv_path=solar_csv_path,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        horizon=horizon,
        compiler=compiler,
        expected_proof_path=proof_path,
    )


def solar_parser_build_binding(proof: Mapping[str, Any]) -> Dict[str, Any]:
    """Expose fields a future simulator build manifest must bind."""

    environment = proof["parser_environment"]
    runtime = environment["runtime_identity"]
    return {
        "proof_id": proof["proof_id"],
        "live_proof_identity": proof["live_proof_identity"],
        "semantic_service_source_identity": (
            proof["semantic_service_source_identity"]
        ),
        "parser_environment_identity": (
            proof["parser_environment_identity"]
        ),
        "parser_contract_version": proof["parser_contract_version"],
        "verifier_source_sha256": (
            environment["verifier_source"]["sha256"]
        ),
        "verifier_binary_sha256": (
            environment["verifier_binary"]["sha256"]
        ),
        "compiler_sha256": environment["compiler"]["sha256"],
        "compiler_version": environment["compiler_version"],
        "cpp_standard": environment["cpp_standard"],
        "standard_library": runtime["standard_library"],
        "libc": runtime["libc"],
        "numeric_locale": runtime["numeric_locale"],
    }


__all__ = [
    "ImmutableSolarReplaySnapshot",
    "SOLAR_PARSER_ENVIRONMENT_DOMAIN",
    "SOLAR_PARSER_ENVIRONMENT_SCHEMA",
    "SOLAR_SEMANTIC_SERVICE_SOURCE_DOMAIN",
    "SOLAR_SEMANTIC_SERVICE_SOURCE_SCHEMA",
    "SOLAR_STOD_BUILD_STANDARD", "SOLAR_STOD_NUMERIC_LOCALE",
    "SOLAR_STOD_DIAGNOSTIC_CLASSIFICATION",
    "SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF_DOMAIN",
    "SOLAR_STOD_DIAGNOSTIC_PARSE_PROOF_SCHEMA",
    "SOLAR_STOD_PARSE_PROOF_DOMAIN", "SOLAR_STOD_PARSE_PROOF_SCHEMA",
    "SOLAR_STOD_PARSER_CONTRACT", "SOLAR_STOD_PROOF_BUILDER_VERSION",
    "SOLAR_STOD_STRICT_DECIMAL", "SolarParseProofError",
    "build_diagnostic_untrusted_solar_stod_parse_proof",
    "build_diagnostic_untrusted_verifier_binary",
    "build_live_solar_stod_parse_proof_from_snapshot",
    "build_solar_stod_parse_proof",
    "file_sha256", "freeze_material",
    "inspect_solar_rows_diagnostic_untrusted", "load_energy_support",
    "solar_parser_build_binding", "source_display_path",
    "source_file_material", "validate_solar_csv_domain",
    "thaw_material", "validate_expected_solar_stod_parse_proof",
    "validate_solar_stod_parse_proof",
    "write_solar_stod_parse_proof",
]
