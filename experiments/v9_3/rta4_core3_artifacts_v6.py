"""Deterministic, fail-closed storage for large CORE-3 JSON artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, BinaryIO, Mapping, Sequence
import zlib

from .rta4_core3_contracts_v6 import (
    CORE3_ARTIFACT_STORAGE_CODEC_V1,
    require_normalized_core3_artifact_storage_v1,
)


CORE3_ARTIFACT_CHUNK_BYTES_V1 = 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RTA4Core3ArtifactV6Error(ValueError):
    """Raised when a stored CORE-3 artifact cannot be trusted."""


def _strict_json_handle(handle: Any, label: str) -> Any:
    def no_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise RTA4Core3ArtifactV6Error(
                    f"duplicate JSON key in {label}: {key}"
                )
            value[key] = item
        return value

    try:
        return json.load(handle, object_pairs_hook=no_duplicates)
    except RTA4Core3ArtifactV6Error:
        raise
    except Exception as exc:
        raise RTA4Core3ArtifactV6Error(
            f"CORE-3 JSON artifact is unreadable: {label}"
        ) from exc


def strict_json_file_v6(path: Path | str) -> Any:
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as handle:
            return _strict_json_handle(handle, str(source))
    except RTA4Core3ArtifactV6Error:
        raise
    except OSError as exc:
        raise RTA4Core3ArtifactV6Error(
            f"CORE-3 JSON artifact is unreadable: {source}"
        ) from exc


def artifact_sha256_size_v1(path: Path | str) -> tuple[str, int]:
    path = Path(path)
    digest = hashlib.sha256()
    count = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(CORE3_ARTIFACT_CHUNK_BYTES_V1)
                if not chunk:
                    break
                digest.update(chunk)
                count += len(chunk)
    except OSError as exc:
        raise RTA4Core3ArtifactV6Error(
            f"cannot read CORE-3 artifact bytes: {path}"
        ) from exc
    return digest.hexdigest(), count


def _verify_single_gzip_stream(
    path: Path,
    *,
    expected_storage_sha256: str,
    expected_storage_bytes: int,
    expected_uncompressed_sha256: str,
    expected_uncompressed_bytes: int,
) -> None:
    storage_digest = hashlib.sha256()
    uncompressed_digest = hashlib.sha256()
    storage_count = 0
    uncompressed_count = 0
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(CORE3_ARTIFACT_CHUNK_BYTES_V1)
                if not chunk:
                    break
                storage_digest.update(chunk)
                storage_count += len(chunk)
                if decoder.eof:
                    raise RTA4Core3ArtifactV6Error(
                        "gzip artifact contains trailing or concatenated data"
                    )
                decoded = decoder.decompress(chunk)
                if decoder.unused_data:
                    raise RTA4Core3ArtifactV6Error(
                        "gzip artifact contains trailing or concatenated data"
                    )
                uncompressed_digest.update(decoded)
                uncompressed_count += len(decoded)
                if uncompressed_count > expected_uncompressed_bytes:
                    raise RTA4Core3ArtifactV6Error(
                        "gzip artifact exceeds its bound uncompressed size"
                    )
            decoded = decoder.flush()
    except RTA4Core3ArtifactV6Error:
        raise
    except (OSError, zlib.error) as exc:
        raise RTA4Core3ArtifactV6Error(
            f"invalid gzip artifact: {path}"
        ) from exc
    uncompressed_digest.update(decoded)
    uncompressed_count += len(decoded)
    if not decoder.eof:
        raise RTA4Core3ArtifactV6Error("gzip artifact is truncated")
    if (
        storage_count != expected_storage_bytes
        or storage_digest.hexdigest() != expected_storage_sha256
    ):
        raise RTA4Core3ArtifactV6Error(
            "gzip artifact storage hash/size mismatch"
        )
    if (
        uncompressed_count != expected_uncompressed_bytes
        or uncompressed_digest.hexdigest() != expected_uncompressed_sha256
    ):
        raise RTA4Core3ArtifactV6Error(
            "gzip artifact uncompressed hash/size mismatch"
        )


def _strict_gzip_json(path: Path) -> Any:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return _strict_json_handle(handle, str(path))
    except RTA4Core3ArtifactV6Error:
        raise
    except (OSError, EOFError) as exc:
        raise RTA4Core3ArtifactV6Error(
            f"invalid gzip JSON artifact: {path}"
        ) from exc


def fsync_directory_v1(path: Path | str) -> None:
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_to_deterministic_gzip(
    source: Path, destination: BinaryIO, *, compresslevel: int, mtime: int,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    with source.open("rb") as input_handle:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=compresslevel,
            fileobj=destination,
            mtime=mtime,
        ) as gzip_handle:
            while True:
                chunk = input_handle.read(CORE3_ARTIFACT_CHUNK_BYTES_V1)
                if not chunk:
                    break
                digest.update(chunk)
                count += len(chunk)
                gzip_handle.write(chunk)
    return digest.hexdigest(), count


def publish_deterministic_gzip_json_v1(
    source_path: Path | str,
    destination_path: Path | str,
    storage_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Stream, verify, and atomically publish one deterministic gzip JSON."""

    contract = require_normalized_core3_artifact_storage_v1(storage_contract)
    source = Path(source_path)
    destination = Path(destination_path)
    if not source.is_file() or source.resolve() == destination.resolve():
        raise RTA4Core3ArtifactV6Error(
            "CORE-3 compression source is missing or aliases its destination"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    published_new = False
    try:
        with os.fdopen(descriptor, "wb") as output_handle:
            uncompressed_sha256, uncompressed_bytes = (
                _copy_to_deterministic_gzip(
                    source,
                    output_handle,
                    compresslevel=contract["compresslevel"],
                    mtime=contract["mtime"],
                )
            )
            output_handle.flush()
            os.fsync(output_handle.fileno())
        storage_sha256, storage_bytes = artifact_sha256_size_v1(temporary)
        _verify_single_gzip_stream(
            temporary,
            expected_storage_sha256=storage_sha256,
            expected_storage_bytes=storage_bytes,
            expected_uncompressed_sha256=uncompressed_sha256,
            expected_uncompressed_bytes=uncompressed_bytes,
        )
        _strict_gzip_json(temporary)
        if destination.exists():
            existing_sha256, existing_bytes = artifact_sha256_size_v1(
                destination
            )
            if (
                existing_sha256 != storage_sha256
                or existing_bytes != storage_bytes
            ):
                raise RTA4Core3ArtifactV6Error(
                    "refusing to replace a different CORE-3 artifact"
                )
            temporary.unlink()
        else:
            os.replace(temporary, destination)
            published_new = True
            fsync_directory_v1(destination.parent)
        _verify_single_gzip_stream(
            destination,
            expected_storage_sha256=storage_sha256,
            expected_storage_bytes=storage_bytes,
            expected_uncompressed_sha256=uncompressed_sha256,
            expected_uncompressed_bytes=uncompressed_bytes,
        )
        _strict_gzip_json(destination)
        return {
            "storage_codec": CORE3_ARTIFACT_STORAGE_CODEC_V1,
            "storage_compresslevel": contract["compresslevel"],
            "storage_mtime": contract["mtime"],
            "uncompressed_sha256": uncompressed_sha256,
            "storage_sha256": storage_sha256,
            "uncompressed_bytes": uncompressed_bytes,
            "storage_bytes": storage_bytes,
            "artifact_storage_contract_identity": contract[
                "storage_contract_identity"
            ],
        }
    except Exception:
        if temporary.exists():
            temporary.unlink()
        if published_new and destination.exists():
            destination.unlink()
        raise


def prefixed_artifact_binding_v1(
    prefix: str, relative_path: str, binding: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "storage_codec", "storage_compresslevel", "storage_mtime",
        "uncompressed_sha256", "storage_sha256", "uncompressed_bytes",
        "storage_bytes", "artifact_storage_contract_identity",
    }
    if set(binding) != required:
        raise RTA4Core3ArtifactV6Error("artifact binding field set mismatch")
    return {
        f"{prefix}_relative_path": relative_path,
        **{f"{prefix}_{key}": value for key, value in binding.items()},
    }


def artifact_binding_from_row_v1(
    row: Mapping[str, Any], prefix: str,
) -> dict[str, Any] | None:
    suffixes = (
        "relative_path", "storage_codec", "storage_compresslevel",
        "storage_mtime", "uncompressed_sha256", "storage_sha256",
        "uncompressed_bytes", "storage_bytes",
        "artifact_storage_contract_identity",
    )
    fields = {f"{prefix}_{suffix}" for suffix in suffixes}
    relative_field = f"{prefix}_relative_path"
    storage_fields = fields - {relative_field}
    present_storage = storage_fields.intersection(row)
    if not present_storage:
        return None
    present = fields.intersection(row)
    if present != fields:
        raise RTA4Core3ArtifactV6Error(
            f"partial {prefix} artifact binding"
        )
    return {suffix: row[f"{prefix}_{suffix}"] for suffix in suffixes}


def load_bound_gzip_json_v1(
    run_root: Path | str,
    binding: Mapping[str, Any],
    *,
    reject_unbound_raw: bool = True,
) -> Any:
    root = Path(run_root).resolve(strict=True)
    relative = binding.get("relative_path")
    if type(relative) is not str or Path(relative).is_absolute():
        raise RTA4Core3ArtifactV6Error("invalid artifact relative path")
    try:
        path = (root / relative).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RTA4Core3ArtifactV6Error(
            "artifact path escapes its run root or is missing"
        ) from exc
    if (
        binding.get("storage_codec") != CORE3_ARTIFACT_STORAGE_CODEC_V1
        or type(binding.get("storage_compresslevel")) is not int
        or not 1 <= binding["storage_compresslevel"] <= 9
        or binding.get("storage_mtime") != 0
        or not _SHA256.fullmatch(str(binding.get("uncompressed_sha256")))
        or not _SHA256.fullmatch(str(binding.get("storage_sha256")))
        or type(binding.get("uncompressed_bytes")) is not int
        or binding["uncompressed_bytes"] < 0
        or type(binding.get("storage_bytes")) is not int
        or binding["storage_bytes"] < 1
        or not _SHA256.fullmatch(str(
            binding.get("artifact_storage_contract_identity")
        ))
    ):
        raise RTA4Core3ArtifactV6Error(
            "invalid or unknown CORE-3 artifact binding"
        )
    if reject_unbound_raw and path.name.endswith(".json.gz"):
        raw = path.with_suffix("")
        if raw.exists():
            raise RTA4Core3ArtifactV6Error(
                "bound gzip conflicts with an unbound raw artifact"
            )
    _verify_single_gzip_stream(
        path,
        expected_storage_sha256=str(binding["storage_sha256"]),
        expected_storage_bytes=binding["storage_bytes"],
        expected_uncompressed_sha256=str(binding["uncompressed_sha256"]),
        expected_uncompressed_bytes=binding["uncompressed_bytes"],
    )
    return _strict_gzip_json(path)


def load_legacy_bound_json_v1(
    run_root: Path | str,
    relative_path: str,
    expected_uncompressed_sha256: str,
) -> Any:
    """Read an explicitly bound legacy raw JSON artifact fail closed."""

    root = Path(run_root).resolve(strict=True)
    if type(relative_path) is not str or Path(relative_path).is_absolute():
        raise RTA4Core3ArtifactV6Error("invalid legacy artifact relative path")
    if (
        type(expected_uncompressed_sha256) is not str
        or not _SHA256.fullmatch(expected_uncompressed_sha256)
    ):
        raise RTA4Core3ArtifactV6Error("invalid legacy artifact SHA-256")
    try:
        path = (root / relative_path).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RTA4Core3ArtifactV6Error(
            "legacy artifact path escapes its run root or is missing"
        ) from exc
    if path.with_name(f"{path.name}.gz").exists():
        raise RTA4Core3ArtifactV6Error(
            "bound legacy raw artifact conflicts with an unbound gzip"
        )
    observed_sha256, _observed_bytes = artifact_sha256_size_v1(path)
    if observed_sha256 != expected_uncompressed_sha256:
        raise RTA4Core3ArtifactV6Error("legacy artifact SHA-256 mismatch")
    return strict_json_file_v6(path)


__all__ = [
    "CORE3_ARTIFACT_CHUNK_BYTES_V1", "RTA4Core3ArtifactV6Error",
    "artifact_binding_from_row_v1", "artifact_sha256_size_v1",
    "fsync_directory_v1", "load_bound_gzip_json_v1",
    "load_legacy_bound_json_v1",
    "prefixed_artifact_binding_v1", "publish_deterministic_gzip_json_v1",
    "strict_json_file_v6",
]
