#!/usr/bin/env python3
"""Copy and archive exactly the regular-file objects authorized by verifier."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import sys
import tarfile
import tempfile
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.output_inventory import (  # noqa: E402
    ADMINISTRATIVE_EVIDENCE, ARCHIVE_MANIFEST_SCHEMA,
    ARCHIVE_MANIFEST_SCHEMA_VERSION, PACKAGE_ARCHIVE_SCHEMA,
    PACKAGE_ARCHIVE_SCHEMA_VERSION, PACKAGE_CATEGORIES, PACKAGE_FILE_TYPE,
    VERIFIED_PACKAGE_MANIFEST_SCHEMA, VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION,
    verified_package_entries,
)


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
_ENTRY_FIELDS = frozenset({
    "relative_path", "file_type", "mode", "size", "sha256", "category",
    "package", "inventory_schema", "inventory_schema_version",
    "authoritative_scientific_content",
})
_MANIFEST_FIELDS = frozenset({
    "schema", "schema_version", "archive_schema", "archive_schema_version",
    "output_root", "profile", "experiments", "inventory_schemas", "entries",
})
_ARCHIVE_MANIFEST_FIELDS = frozenset({
    "schema", "schema_version", "archive_filename", "archive_sha256",
    "base_commit", "package_manifest_schema", "package_manifest_schema_version",
    "output_inventory_schemas", "members",
})
_ARCHIVE_MEMBER_FIELDS = frozenset({
    "path", "file_type", "mode", "size", "sha256", "category",
    "authoritative_scientific_content", "source",
})
_ARCHIVE_MEMBER_SOURCES = frozenset({
    "RESULT_ENTRY", "PACKAGE_MANIFEST", "COMMIT_IDENTITY",
})


@dataclass(frozen=True)
class VerifiedPackageEntry:
    relative_path: str
    file_type: str
    mode: int
    size: int
    sha256: str
    category: str
    package: bool
    inventory_schema: str
    inventory_schema_version: int
    authoritative_scientific_content: bool


@dataclass(frozen=True)
class ObjectIdentity:
    device: int
    inode: int
    file_type: int


@dataclass(frozen=True)
class PublishedOwnership:
    identity: ObjectIdentity
    ctime_ns: int
    size: int
    sha256: str


def _object_identity(metadata: os.stat_result) -> ObjectIdentity:
    return ObjectIdentity(
        metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode),
    )


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError("package path must be non-empty text")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
        or "\\" in value
    ):
        raise RuntimeError(f"unsafe package path: {value!r}")
    return value


def _reject_duplicate_json_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeError(f"duplicate JSON key in package metadata: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json_constant(value: str):
    raise RuntimeError(f"non-finite JSON constant in package metadata: {value}")


def _load_strict_json_bytes(content: bytes, description: str) -> Mapping[str, object]:
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"{description} is unreadable") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{description} is not an object")
    return value


def _require_fd_platform() -> None:
    required = ("O_NOFOLLOW", "O_DIRECTORY")
    missing = [name for name in required if not hasattr(os, name)]
    if missing or os.open not in getattr(os, "supports_dir_fd", set()):
        raise RuntimeError(
            f"platform lacks fail-closed package fd support: {missing}"
        )


def _sha256_fd(descriptor: int) -> tuple[str, int]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    size = 0
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            break
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def _frozen_stat(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _open_source_fd(source_root: Path, relative: str) -> int:
    """Open one source through no-follow directory FDs anchored at root."""

    _require_fd_platform()
    parts = PurePosixPath(relative).parts
    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptors: list[int] = []
    try:
        current = os.open(source_root, directory_flags)
        descriptors.append(current)
        for component in parts[:-1]:
            current = os.open(
                component, directory_flags, dir_fd=current,
            )
            descriptors.append(current)
        result = os.open(parts[-1], file_flags, dir_fd=current)
    except OSError as exc:
        raise RuntimeError(
            f"package source is not a stable regular path: {relative}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return result


def _load_verified_manifest(
    manifest_path: Path, source_root: Path,
) -> tuple[Mapping[str, object], tuple[VerifiedPackageEntry, ...]]:
    manifest_path = Path(manifest_path)
    try:
        content = manifest_path.read_bytes()
    except OSError as exc:
        raise RuntimeError("package manifest is unreadable") from exc
    value = _load_strict_json_bytes(content, "package manifest")
    return _validate_verified_manifest(value, source_root)


def _validate_verified_manifest(
    value: Mapping[str, object], source_root: Path,
) -> tuple[Mapping[str, object], tuple[VerifiedPackageEntry, ...]]:
    """Apply the one authoritative package-manifest contract to a JSON value."""

    source_root = Path(source_root).resolve(strict=True)
    if set(value) != _MANIFEST_FIELDS:
        raise RuntimeError("package manifest top-level schema mismatch")
    if (
        value.get("schema") != VERIFIED_PACKAGE_MANIFEST_SCHEMA
        or value.get("schema_version") != VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION
        or value.get("archive_schema") != PACKAGE_ARCHIVE_SCHEMA
        or value.get("archive_schema_version") != PACKAGE_ARCHIVE_SCHEMA_VERSION
    ):
        raise RuntimeError("package manifest schema mismatch")
    raw_output_root = value.get("output_root")
    if not isinstance(raw_output_root, str) or not raw_output_root:
        raise RuntimeError("package manifest source root is invalid")
    try:
        manifest_root = Path(raw_output_root).resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("package manifest source root is invalid") from exc
    if manifest_root != source_root:
        raise RuntimeError("package manifest source root mismatch")
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise RuntimeError("package manifest has no verified entries")

    entries = []
    seen = set()
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != _ENTRY_FIELDS:
            raise RuntimeError("package manifest entry schema mismatch")
        relative = _safe_relative(raw.get("relative_path"))
        if relative in seen:
            raise RuntimeError(f"duplicate package path: {relative}")
        seen.add(relative)
        file_type = raw.get("file_type")
        digest = raw.get("sha256")
        mode = raw.get("mode")
        size = raw.get("size")
        category = raw.get("category")
        inventory_schema = raw.get("inventory_schema")
        inventory_version = raw.get("inventory_schema_version")
        if file_type != PACKAGE_FILE_TYPE:
            raise RuntimeError(f"unsupported package file type: {relative}")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise RuntimeError(f"invalid package SHA-256: {relative}")
        if type(mode) is not int or not 0 <= mode <= 0o7777:
            raise RuntimeError(f"invalid package mode: {relative}")
        if type(size) is not int or size < 0:
            raise RuntimeError(f"invalid package size: {relative}")
        if category not in PACKAGE_CATEGORIES:
            raise RuntimeError(f"invalid package category: {relative}")
        if type(raw.get("package")) is not bool or raw.get("package") is not True:
            raise RuntimeError(f"package entry is not authorized: {relative}")
        if not isinstance(inventory_schema, str) or not inventory_schema:
            raise RuntimeError(f"invalid inventory schema: {relative}")
        if type(inventory_version) is not int or inventory_version < 1:
            raise RuntimeError(f"invalid inventory schema version: {relative}")
        if type(raw.get("authoritative_scientific_content")) is not bool:
            raise RuntimeError(f"invalid authoritative flag: {relative}")
        entries.append(VerifiedPackageEntry(
            relative, file_type, mode, size, digest, category, True,
            inventory_schema, inventory_version,
            raw["authoritative_scientific_content"],
        ))
    recorded_schemas = value.get("inventory_schemas")
    expected_schemas = sorted({
        (entry.inventory_schema, entry.inventory_schema_version)
        for entry in entries
    })
    if recorded_schemas != [list(item) for item in expected_schemas]:
        raise RuntimeError("package manifest inventory schema set mismatch")
    expected_entries = _reconstruct_expected_entries(value, source_root)
    if raw_entries != list(expected_entries):
        raise RuntimeError(
            "package manifest entries do not exactly match frozen output inventory"
        )
    return value, tuple(entries)


def _load_embedded_verified_manifest(
    content: bytes,
) -> tuple[Mapping[str, object], tuple[VerifiedPackageEntry, ...]]:
    """Parse embedded bytes through the same authoritative loader as the source."""

    value = _load_strict_json_bytes(content, "embedded package manifest")
    raw_output_root = value.get("output_root")
    if not isinstance(raw_output_root, str) or not raw_output_root:
        raise RuntimeError("embedded package manifest source root is invalid")
    return _validate_verified_manifest(value, Path(raw_output_root))


def _reconstruct_expected_entries(
    manifest: Mapping[str, object], source_root: Path,
) -> tuple[Mapping[str, object], ...]:
    """Independently rebuild the unique package set from verified closures."""

    from deployment.autodl.verify_outputs import (  # local: avoid CLI cycle
        EXPERIMENTS, FORMAL_EXPERIMENTS, _legacy_verified_package_entries,
        verify_core12_output,
    )

    profile = manifest.get("profile")
    experiments = manifest.get("experiments")
    if profile not in {"smoke", "formal"}:
        raise RuntimeError("package manifest profile is invalid")
    if (
        not isinstance(experiments, list)
        or not experiments
        or any(not isinstance(name, str) for name in experiments)
        or len(experiments) != len(set(experiments))
    ):
        raise RuntimeError("package manifest experiments are invalid")
    allowed = set(FORMAL_EXPERIMENTS if profile == "formal" else EXPERIMENTS)
    if not set(experiments).issubset(allowed):
        raise RuntimeError("package manifest experiment/profile mismatch")
    rebuilt = []
    for name in experiments:
        root = source_root / name
        if name in FORMAL_EXPERIMENTS:
            inventory = verify_core12_output(name, root)
            local_entries = verified_package_entries(root, inventory)
        else:
            local_entries = _legacy_verified_package_entries(root)
        rebuilt.extend({
            **entry,
            "relative_path": f"{name}/{entry['relative_path']}",
        } for entry in local_entries)
    return tuple(rebuilt)


@dataclass
class DestinationRoot:
    descriptor: int
    parent_descriptor: int
    name: str
    created: bool
    identity: ObjectIdentity


@dataclass(frozen=True)
class PublicationRoot:
    descriptor: int
    pathname: Path
    identity: ObjectIdentity
    initial_ctime_ns: int
    initial_mode: int


def _open_destination_root(destination_root: Path) -> DestinationRoot:
    """Create/open the destination leaf and pin it beneath its parent FD."""

    _require_fd_platform()
    destination_root = Path(destination_root).absolute()
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    parent_descriptor = _open_or_create_directory_path(
        destination_root.parent, flags,
    )
    created = False
    descriptor = -1
    try:
        try:
            os.mkdir(destination_root.name, 0o755, dir_fd=parent_descriptor)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(
            destination_root.name, flags, dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("package destination root is not a real directory")
        if os.listdir(descriptor):
            raise RuntimeError("package destination is not empty")
        return DestinationRoot(
            descriptor, parent_descriptor, destination_root.name, created,
            _object_identity(metadata),
        )
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        if created:
            try:
                os.rmdir(destination_root.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        os.close(parent_descriptor)
        raise


def _open_or_create_directory_path(path: Path, flags: int | None = None) -> int:
    """Walk an absolute directory path from `/` using only anchored dir_fds."""

    path = Path(path).absolute()
    flags = flags or (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    current = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            try:
                next_descriptor = os.open(
                    component, flags, dir_fd=current,
                )
            except FileNotFoundError:
                os.mkdir(component, 0o755, dir_fd=current)
                next_descriptor = os.open(
                    component, flags, dir_fd=current,
                )
            os.close(current)
            current = next_descriptor
        return current
    except Exception:
        os.close(current)
        raise


def _open_existing_directory_path(path: Path) -> int:
    """Open every component without following symlinks or creating objects."""

    _require_fd_platform()
    path = Path(path).absolute()
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    current = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = next_descriptor
        return current
    except Exception:
        os.close(current)
        raise


def _assert_publication_path_identity(root: PublicationRoot) -> None:
    """Require the caller's path to still name the pinned directory inode."""

    descriptor = -1
    try:
        descriptor = _open_existing_directory_path(root.pathname)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or _object_identity(metadata) != root.identity
        ):
            raise RuntimeError("package publication directory identity changed")
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError("package publication directory identity changed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _open_publication_root(path: Path) -> PublicationRoot:
    """Create, pin, and record the common publication directory."""

    pathname = Path(path).absolute()
    descriptor = _open_or_create_directory_path(pathname)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("package publication root is not a directory")
        root = PublicationRoot(
            descriptor=descriptor,
            pathname=pathname,
            identity=_object_identity(metadata),
            initial_ctime_ns=metadata.st_ctime_ns,
            initial_mode=stat.S_IMODE(metadata.st_mode),
        )
        _assert_publication_path_identity(root)
        return root
    except Exception:
        os.close(descriptor)
        raise


def _open_parent_components(
    root_descriptor: int,
    relative: str,
    created_directories: list[tuple[str, ObjectIdentity]],
    *,
    create: bool = True,
) -> tuple[int, str, ObjectIdentity]:
    """Return an anchored parent FD, creating every component via dir_fd."""

    parts = PurePosixPath(_safe_relative(relative)).parts
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    current = os.dup(root_descriptor)
    prefix: list[str] = []
    try:
        for component in parts[:-1]:
            prefix.append(component)
            made = False
            if create:
                try:
                    os.mkdir(component, 0o755, dir_fd=current)
                    made = True
                except FileExistsError:
                    pass
            next_descriptor = os.open(component, flags, dir_fd=current)
            metadata = os.fstat(next_descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_descriptor)
                raise RuntimeError(
                    f"package destination parent is not a directory: {relative}"
                )
            if made:
                created_directories.append((
                    "/".join(prefix), _object_identity(metadata),
                ))
            os.close(current)
            current = next_descriptor
        metadata = os.fstat(current)
        return current, parts[-1], _object_identity(metadata)
    except Exception as exc:
        os.close(current)
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(
            f"package destination parent is not stable: {relative}"
        ) from exc


def _open_existing_parent(root_descriptor: int, relative: str) -> tuple[int, str]:
    return _open_parent_components(
        root_descriptor, relative, [], create=False,
    )[0:2]


def _parent_identity_from_root(root_descriptor: int, relative: str) -> ObjectIdentity:
    descriptor, _name = _open_existing_parent(root_descriptor, relative)
    try:
        return _object_identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _unlink_if_owned(
    parent_descriptor: int, name: str, identity: ObjectIdentity,
) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if _object_identity(current) != identity:
        return False
    os.unlink(name, dir_fd=parent_descriptor)
    return True


def _write_all(descriptor: int, block: bytes) -> None:
    view = memoryview(block)
    while view:
        count = os.write(descriptor, view)
        if count <= 0:
            raise RuntimeError("short write while copying package source")
        view = view[count:]


def _read_fd_bytes(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    blocks = []
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            return b"".join(blocks)
        blocks.append(block)


def _open_regular_at(root_descriptor: int, name: str) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_descriptor,
        )
    except OSError as exc:
        raise RuntimeError(f"package publication artifact is unreadable: {name}") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise RuntimeError(f"package publication artifact is not regular: {name}")
    return descriptor


def _verify_regular_fd(
    descriptor: int,
    *,
    expected_mode: int,
    expected_content: bytes | None = None,
    expected_sha256: str | None = None,
) -> tuple[ObjectIdentity, int, str]:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != expected_mode
    ):
        raise RuntimeError("package publication staging metadata mismatch")
    digest, size = _sha256_fd(descriptor)
    if metadata.st_size != size:
        raise RuntimeError("package publication staging size changed")
    if expected_content is not None:
        expected_sha256 = hashlib.sha256(expected_content).hexdigest()
        if size != len(expected_content):
            raise RuntimeError("package publication staging size mismatch")
    if expected_sha256 is not None and digest != expected_sha256:
        raise RuntimeError("package publication staging digest mismatch")
    return _object_identity(metadata), size, digest


def _create_publication_temporary(
    root_descriptor: int, *, mode: int = 0o644,
) -> tuple[str, int, ObjectIdentity]:
    """Create one random O_EXCL regular file beneath the pinned root FD."""

    for _attempt in range(128):
        name = f".partsim-package-{os.getpid()}-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                mode,
                dir_fd=root_descriptor,
            )
        except FileExistsError:
            continue
        os.fchmod(descriptor, mode)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise RuntimeError("package publication temporary is not regular")
        return name, descriptor, _object_identity(metadata)
    raise RuntimeError("cannot allocate unique package publication temporary")


def _name_exists_at(root_descriptor: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _verify_destination(
    parent_descriptor: int, name: str, entry: VerifiedPackageEntry,
) -> None:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent_descriptor,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(
                f"packaged destination is not regular: {entry.relative_path}"
            )
        digest, size = _sha256_fd(descriptor)
        if (
            stat.S_IMODE(metadata.st_mode) != entry.mode
            or size != entry.size
            or metadata.st_size != entry.size
            or digest != entry.sha256
        ):
            raise RuntimeError(
                f"packaged destination metadata mismatch: {entry.relative_path}"
            )
    finally:
        os.close(descriptor)


def _copy_verified_entry(
    source_root: Path,
    destination_root_descriptor: int,
    entry: VerifiedPackageEntry,
    created_directories: list[tuple[str, ObjectIdentity]],
) -> tuple[str, ObjectIdentity]:
    parent_descriptor, destination_name, parent_identity = _open_parent_components(
        destination_root_descriptor, entry.relative_path, created_directories,
    )
    temporary_name = (
        f".{destination_name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    source_descriptor = -1
    destination_descriptor = -1
    temporary_identity: ObjectIdentity | None = None
    published_identity: ObjectIdentity | None = None
    destination_verified = False
    try:
        if _parent_identity_from_root(
            destination_root_descriptor, entry.relative_path,
        ) != parent_identity:
            raise RuntimeError(
                f"package destination parent changed: {entry.relative_path}"
            )
        source_descriptor = _open_source_fd(source_root, entry.relative_path)
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(
                f"package source is not a regular file: {entry.relative_path}"
            )
        if stat.S_IMODE(before.st_mode) != entry.mode:
            raise RuntimeError(f"package source mode changed: {entry.relative_path}")
        if before.st_size != entry.size:
            raise RuntimeError(f"package source size changed: {entry.relative_path}")
        destination_descriptor = os.open(
            temporary_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            entry.mode,
            dir_fd=parent_descriptor,
        )
        temporary_identity = _object_identity(os.fstat(destination_descriptor))
        os.lseek(source_descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        copied_size = 0
        while True:
            block = os.read(source_descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            copied_size += len(block)
            _write_all(destination_descriptor, block)
        after = os.fstat(source_descriptor)
        if _frozen_stat(before) != _frozen_stat(after):
            raise RuntimeError(
                f"package source changed during copy: {entry.relative_path}"
            )
        if copied_size != entry.size or digest.hexdigest() != entry.sha256:
            raise RuntimeError(
                f"package source content changed: {entry.relative_path}"
            )
        os.fchmod(destination_descriptor, entry.mode)
        os.fsync(destination_descriptor)
        destination_metadata = os.fstat(destination_descriptor)
        os.lseek(destination_descriptor, 0, os.SEEK_SET)
        destination_digest, destination_size = _sha256_fd(destination_descriptor)
        if (
            not stat.S_ISREG(destination_metadata.st_mode)
            or stat.S_IMODE(destination_metadata.st_mode) != entry.mode
            or destination_metadata.st_size != entry.size
            or destination_size != entry.size
            or destination_digest != entry.sha256
        ):
            raise RuntimeError(
                f"package temporary destination mismatch: {entry.relative_path}"
            )
        os.close(destination_descriptor)
        destination_descriptor = -1
        if _parent_identity_from_root(
            destination_root_descriptor, entry.relative_path,
        ) != parent_identity:
            raise RuntimeError(
                f"package destination parent changed: {entry.relative_path}"
            )
        try:
            os.link(
                temporary_name, destination_name,
                src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise RuntimeError(
                f"package destination already exists: {entry.relative_path}"
            ) from exc
        published_metadata = os.stat(
            destination_name, dir_fd=parent_descriptor, follow_symlinks=False,
        )
        published_identity = _object_identity(published_metadata)
        if published_identity != temporary_identity:
            raise RuntimeError(
                f"package destination publication identity mismatch: {entry.relative_path}"
            )
        _unlink_if_owned(parent_descriptor, temporary_name, temporary_identity)
        temporary_identity = None
        _verify_destination(parent_descriptor, destination_name, entry)
        destination_verified = True
        return entry.relative_path, published_identity
    except OSError as exc:
        raise RuntimeError(
            f"package copy failed for regular file: {entry.relative_path}"
        ) from exc
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if temporary_identity is not None:
            _unlink_if_owned(parent_descriptor, temporary_name, temporary_identity)
        if published_identity is not None and not destination_verified:
            _unlink_if_owned(
                parent_descriptor, destination_name, published_identity,
            )
        os.close(parent_descriptor)


def copy_verified_inventory(
    manifest_path: Path, source_root: Path, destination_root: Path,
) -> tuple[str, ...]:
    """Copy every entry from the same no-follow fd that was revalidated."""

    source_root = Path(source_root).resolve(strict=True)
    destination_root = Path(destination_root)
    _value, entries = _load_verified_manifest(manifest_path, source_root)
    destination = _open_destination_root(destination_root)
    copied = []
    owned_files: list[tuple[str, ObjectIdentity]] = []
    created_directories: list[tuple[str, ObjectIdentity]] = []
    completed = False
    try:
        for entry in entries:
            owned_files.append(_copy_verified_entry(
                source_root, destination.descriptor, entry, created_directories,
            ))
            copied.append(entry.relative_path)
        completed = True
        return tuple(copied)
    except Exception:
        for relative, identity in reversed(owned_files):
            try:
                parent, name = _open_existing_parent(
                    destination.descriptor, relative,
                )
            except Exception:
                continue
            try:
                _unlink_if_owned(parent, name, identity)
            finally:
                os.close(parent)
        for relative, identity in reversed(created_directories):
            try:
                components = PurePosixPath(relative).parts
                parent_relative = "/".join((*components[:-1], "placeholder"))
                parent, _placeholder = _open_existing_parent(
                    destination.descriptor, parent_relative,
                )
                current = os.stat(
                    components[-1], dir_fd=parent, follow_symlinks=False,
                )
                if _object_identity(current) == identity:
                    os.rmdir(components[-1], dir_fd=parent)
                os.close(parent)
            except (OSError, RuntimeError):
                pass
        raise
    finally:
        root_empty = not os.listdir(destination.descriptor)
        os.close(destination.descriptor)
        if destination.created and not completed and root_empty:
            try:
                current = os.stat(
                    destination.name, dir_fd=destination.parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    _object_identity(current) == destination.identity
                ):
                    os.rmdir(
                        destination.name, dir_fd=destination.parent_descriptor,
                    )
            except OSError:
                pass
        os.close(destination.parent_descriptor)


def _tar_add_bytes(
    archive: tarfile.TarFile, name: str, content: bytes, mode: int,
) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = mode
    info.size = len(content)
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, io.BytesIO(content))


def _tar_add_entry(
    archive: tarfile.TarFile,
    destination_root: Path,
    entry: VerifiedPackageEntry,
) -> None:
    descriptor = _open_source_fd(destination_root, entry.relative_path)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != entry.mode
            or metadata.st_size != entry.size
        ):
            raise RuntimeError(
                f"archive source metadata mismatch: {entry.relative_path}"
            )
        info = tarfile.TarInfo(f"package/results/{entry.relative_path}")
        info.type = tarfile.REGTYPE
        info.mode = entry.mode
        info.size = entry.size
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            archive.addfile(info, handle)
    finally:
        os.close(descriptor)


def _archive_member_record(
    path: str,
    content_sha256: str,
    size: int,
    mode: int,
    category: str,
    authoritative: bool,
    source: str,
) -> Mapping[str, object]:
    return {
        "path": path,
        "file_type": PACKAGE_FILE_TYPE,
        "mode": mode,
        "size": size,
        "sha256": content_sha256,
        "category": category,
        "authoritative_scientific_content": authoritative,
        "source": source,
    }


def _build_archive_manifest(
    *,
    archive_filename: str,
    archive_sha256: str,
    commit_sha: str,
    package_manifest: Mapping[str, object],
    package_manifest_bytes: bytes,
    entries: tuple[VerifiedPackageEntry, ...],
) -> Mapping[str, object]:
    commit_bytes = (commit_sha + "\n").encode("ascii")
    members = [
        _archive_member_record(
            "package/package_manifest.json",
            hashlib.sha256(package_manifest_bytes).hexdigest(),
            len(package_manifest_bytes), 0o644, ADMINISTRATIVE_EVIDENCE,
            False, "PACKAGE_MANIFEST",
        ),
        _archive_member_record(
            "package/commit_sha.txt", hashlib.sha256(commit_bytes).hexdigest(),
            len(commit_bytes), 0o644, ADMINISTRATIVE_EVIDENCE,
            False, "COMMIT_IDENTITY",
        ),
    ]
    members.extend(_archive_member_record(
        f"package/results/{entry.relative_path}", entry.sha256, entry.size,
        entry.mode, entry.category, entry.authoritative_scientific_content,
        "RESULT_ENTRY",
    ) for entry in entries)
    return {
        "schema": ARCHIVE_MANIFEST_SCHEMA,
        "schema_version": ARCHIVE_MANIFEST_SCHEMA_VERSION,
        "archive_filename": archive_filename,
        "archive_sha256": archive_sha256,
        "base_commit": commit_sha,
        "package_manifest_schema": package_manifest["schema"],
        "package_manifest_schema_version": package_manifest["schema_version"],
        "output_inventory_schemas": package_manifest["inventory_schemas"],
        "members": members,
    }


def _load_archive_manifest_bytes(content: bytes) -> Mapping[str, object]:
    value = _load_strict_json_bytes(content, "archive manifest")
    if set(value) != _ARCHIVE_MANIFEST_FIELDS:
        raise RuntimeError("archive manifest top-level schema mismatch")
    if (
        value.get("schema") != ARCHIVE_MANIFEST_SCHEMA
        or value.get("schema_version") != ARCHIVE_MANIFEST_SCHEMA_VERSION
        or value.get("package_manifest_schema") != VERIFIED_PACKAGE_MANIFEST_SCHEMA
        or value.get("package_manifest_schema_version")
        != VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION
        or not isinstance(value.get("archive_filename"), str)
        or not _SHA256_RE.fullmatch(str(value.get("archive_sha256", "")))
        or not _COMMIT_RE.fullmatch(str(value.get("base_commit", "")))
    ):
        raise RuntimeError("archive manifest identity/schema mismatch")
    schemas = value.get("output_inventory_schemas")
    if (
        not isinstance(schemas, list) or not schemas
        or any(
            not isinstance(item, list) or len(item) != 2
            or not isinstance(item[0], str) or not item[0]
            or type(item[1]) is not int or item[1] < 1
            for item in schemas
        )
    ):
        raise RuntimeError("archive manifest output inventory schemas are invalid")
    members = value.get("members")
    if not isinstance(members, list) or not members:
        raise RuntimeError("archive manifest members are invalid")
    names = []
    source_counts = {item: 0 for item in _ARCHIVE_MEMBER_SOURCES}
    for member in members:
        if not isinstance(member, dict) or set(member) != _ARCHIVE_MEMBER_FIELDS:
            raise RuntimeError("archive manifest member schema mismatch")
        name = _safe_relative(member.get("path"))
        names.append(name)
        if member.get("file_type") != PACKAGE_FILE_TYPE:
            raise RuntimeError(f"archive manifest member type mismatch: {name}")
        if type(member.get("mode")) is not int or not 0 <= member["mode"] <= 0o7777:
            raise RuntimeError(f"archive manifest member mode is invalid: {name}")
        if type(member.get("size")) is not int or member["size"] < 0:
            raise RuntimeError(f"archive manifest member size is invalid: {name}")
        if not _SHA256_RE.fullmatch(str(member.get("sha256", ""))):
            raise RuntimeError(f"archive manifest member digest is invalid: {name}")
        if member.get("category") not in PACKAGE_CATEGORIES:
            raise RuntimeError(f"archive manifest member category is invalid: {name}")
        if type(member.get("authoritative_scientific_content")) is not bool:
            raise RuntimeError(f"archive manifest authoritative flag is invalid: {name}")
        source = member.get("source")
        if source not in _ARCHIVE_MEMBER_SOURCES:
            raise RuntimeError(f"archive manifest member source is invalid: {name}")
        source_counts[source] += 1
        if source == "PACKAGE_MANIFEST" and name != "package/package_manifest.json":
            raise RuntimeError("archive manifest package-manifest member path mismatch")
        if source == "COMMIT_IDENTITY" and name != "package/commit_sha.txt":
            raise RuntimeError("archive manifest commit member path mismatch")
        if source == "RESULT_ENTRY" and not name.startswith("package/results/"):
            raise RuntimeError("archive manifest result member path mismatch")
    if len(names) != len(set(names)):
        raise RuntimeError("archive manifest contains duplicate members")
    if source_counts["PACKAGE_MANIFEST"] != 1 or source_counts["COMMIT_IDENTITY"] != 1:
        raise RuntimeError("archive manifest management member set mismatch")
    return value


def _load_archive_manifest(path: Path) -> Mapping[str, object]:
    try:
        content = Path(path).read_bytes()
    except OSError as exc:
        raise RuntimeError("archive manifest is unreadable") from exc
    return _load_archive_manifest_bytes(content)


def _expected_embedded_member_records(
    package_manifest: Mapping[str, object],
    entries: tuple[VerifiedPackageEntry, ...],
    package_manifest_bytes: bytes,
    commit_bytes: bytes,
) -> Mapping[str, Mapping[str, object]]:
    expected = {
        "package/package_manifest.json": _archive_member_record(
            "package/package_manifest.json",
            hashlib.sha256(package_manifest_bytes).hexdigest(),
            len(package_manifest_bytes), 0o644, ADMINISTRATIVE_EVIDENCE,
            False, "PACKAGE_MANIFEST",
        ),
        "package/commit_sha.txt": _archive_member_record(
            "package/commit_sha.txt", hashlib.sha256(commit_bytes).hexdigest(),
            len(commit_bytes), 0o644, ADMINISTRATIVE_EVIDENCE,
            False, "COMMIT_IDENTITY",
        ),
    }
    for entry in entries:
        name = f"package/results/{entry.relative_path}"
        if name in expected:
            raise RuntimeError(f"duplicate embedded package result path: {name}")
        expected[name] = _archive_member_record(
            name, entry.sha256, entry.size, entry.mode, entry.category,
            entry.authoritative_scientific_content, "RESULT_ENTRY",
        )
    return expected


def _verify_package_archive_bytes(
    archive_bytes: bytes,
    archive_manifest_bytes: bytes,
    *,
    expected_archive_filename: str,
) -> tuple[str, ...]:
    """Bind outer records to embedded manifest, commit, and exact tar bytes."""

    manifest = _load_archive_manifest_bytes(archive_manifest_bytes)
    if manifest["archive_filename"] != expected_archive_filename:
        raise RuntimeError("archive manifest filename mismatch")
    archive_digest = hashlib.sha256(archive_bytes).hexdigest()
    if archive_digest != manifest["archive_sha256"]:
        raise RuntimeError("archive SHA-256 does not match archive manifest")

    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise RuntimeError("package archive contains duplicate members")
            actual = {}
            for member in members:
                if not member.isreg():
                    raise RuntimeError(
                        f"package archive member is not regular: {member.name}"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise RuntimeError(
                        f"cannot read package archive member: {member.name}"
                    )
                actual[member.name] = (member, extracted.read())
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise RuntimeError("package archive is unreadable") from exc

    package_name = "package/package_manifest.json"
    commit_name = "package/commit_sha.txt"
    if package_name not in actual:
        raise RuntimeError("package archive lacks embedded package manifest")
    if commit_name not in actual:
        raise RuntimeError("package archive lacks embedded commit identity")
    package_member, package_bytes = actual[package_name]
    commit_member, commit_bytes = actual[commit_name]
    if package_member.mode != 0o644:
        raise RuntimeError("embedded package manifest mode mismatch")
    if commit_member.mode != 0o644:
        raise RuntimeError("embedded commit identity mode mismatch")

    package_manifest, entries = _load_embedded_verified_manifest(package_bytes)
    if re.fullmatch(rb"[0-9a-f]{40,64}\n", commit_bytes) is None:
        raise RuntimeError("embedded commit identity has invalid frozen format")
    commit_sha = commit_bytes[:-1].decode("ascii")
    expected = _expected_embedded_member_records(
        package_manifest, entries, package_bytes, commit_bytes,
    )
    outer = {member["path"]: member for member in manifest["members"]}

    if manifest["base_commit"] != commit_sha:
        raise RuntimeError("archive/embedded commit identity mismatch")
    if manifest["package_manifest_schema"] != package_manifest["schema"]:
        raise RuntimeError("archive/embedded package schema mismatch")
    if (
        manifest["package_manifest_schema_version"]
        != package_manifest["schema_version"]
    ):
        raise RuntimeError("archive/embedded package schema version mismatch")
    if manifest["output_inventory_schemas"] != package_manifest["inventory_schemas"]:
        raise RuntimeError("archive/embedded output inventory schemas mismatch")
    if set(actual) != set(expected):
        raise RuntimeError("package archive/embedded manifest member set mismatch")
    if set(outer) != set(expected):
        raise RuntimeError("archive manifest/embedded manifest member set mismatch")

    run_metadata_commits = set()
    for entry in entries:
        if not entry.relative_path.endswith("/run_metadata.json"):
            continue
        name = f"package/results/{entry.relative_path}"
        metadata_value = _load_strict_json_bytes(
            actual[name][1], f"embedded run metadata {entry.relative_path}",
        )
        git_head = metadata_value.get("git_head")
        if git_head is None:
            continue
        if not isinstance(git_head, str) or _COMMIT_RE.fullmatch(git_head) is None:
            raise RuntimeError(
                f"embedded run metadata commit is invalid: {entry.relative_path}"
            )
        run_metadata_commits.add(git_head)
    if run_metadata_commits and run_metadata_commits != {commit_sha}:
        raise RuntimeError("archive commit disagrees with embedded run metadata")

    for name, expected_record in expected.items():
        if outer[name] != expected_record:
            raise RuntimeError(f"archive member record is not derived truth: {name}")
        member, content = actual[name]
        if (
            member.mode != expected_record["mode"]
            or member.size != expected_record["size"]
            or len(content) != expected_record["size"]
            or hashlib.sha256(content).hexdigest() != expected_record["sha256"]
        ):
            raise RuntimeError(f"package archive member metadata mismatch: {name}")
    return tuple(sorted(expected))


def verify_package_archive(
    archive_path: Path,
    archive_manifest_path: Path,
    *,
    expected_archive_filename: str | None = None,
    _publication_root_fd: int | None = None,
) -> tuple[str, ...]:
    """Verify the complete external/embedded/tar contract."""

    archive_path = Path(archive_path)
    archive_manifest_path = Path(archive_manifest_path)
    expected_name = expected_archive_filename or archive_path.name
    descriptors = []
    try:
        if _publication_root_fd is None:
            archive_bytes = archive_path.read_bytes()
            archive_manifest_bytes = archive_manifest_path.read_bytes()
        else:
            archive_descriptor = _open_regular_at(
                _publication_root_fd, archive_path.name,
            )
            descriptors.append(archive_descriptor)
            manifest_descriptor = _open_regular_at(
                _publication_root_fd, archive_manifest_path.name,
            )
            descriptors.append(manifest_descriptor)
            _verify_regular_fd(archive_descriptor, expected_mode=0o644)
            _verify_regular_fd(manifest_descriptor, expected_mode=0o644)
            archive_bytes = _read_fd_bytes(archive_descriptor)
            archive_manifest_bytes = _read_fd_bytes(manifest_descriptor)
    except OSError as exc:
        raise RuntimeError("package archive publication is unreadable") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return _verify_package_archive_bytes(
        archive_bytes, archive_manifest_bytes,
        expected_archive_filename=expected_name,
    )


def verify_published_package(
    archive_path: Path,
    archive_manifest_path: Path,
    archive_sha_path: Path,
    *,
    expected_archive_filename: str | None = None,
    _publication_root_fd: int | None = None,
) -> tuple[str, ...]:
    """Verify the frozen tar/manifest/SHA publication as one three-file set."""

    archive_path = Path(archive_path)
    archive_manifest_path = Path(archive_manifest_path)
    archive_sha_path = Path(archive_sha_path)
    expected_name = expected_archive_filename or archive_path.name
    members = verify_package_archive(
        archive_path, archive_manifest_path,
        expected_archive_filename=expected_name,
        _publication_root_fd=_publication_root_fd,
    )
    descriptors = []
    try:
        if _publication_root_fd is None:
            archive_bytes = archive_path.read_bytes()
            sidecar_bytes = archive_sha_path.read_bytes()
        else:
            archive_descriptor = _open_regular_at(
                _publication_root_fd, archive_path.name,
            )
            descriptors.append(archive_descriptor)
            sha_descriptor = _open_regular_at(
                _publication_root_fd, archive_sha_path.name,
            )
            descriptors.append(sha_descriptor)
            _verify_regular_fd(archive_descriptor, expected_mode=0o644)
            _verify_regular_fd(sha_descriptor, expected_mode=0o644)
            archive_bytes = _read_fd_bytes(archive_descriptor)
            sidecar_bytes = _read_fd_bytes(sha_descriptor)
    except OSError as exc:
        raise RuntimeError("archive SHA sidecar is unreadable") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    expected_digest = hashlib.sha256(archive_bytes).hexdigest()
    expected_sidecar = f"{expected_digest}  {expected_name}\n".encode("utf-8")
    if sidecar_bytes != expected_sidecar:
        raise RuntimeError("archive SHA sidecar mismatch")
    return members


def _publish_exclusive(
    publication_root_fd: int,
    temporary_name: str,
    final_name: str,
) -> PublishedOwnership:
    """No-overwrite publish using only basenames beneath one pinned root FD."""

    if (
        PurePosixPath(temporary_name).name != temporary_name
        or PurePosixPath(final_name).name != final_name
    ):
        raise RuntimeError("package publication requires plain basenames")
    if _name_exists_at(publication_root_fd, final_name):
        raise RuntimeError(
            f"refusing to overwrite existing package artifact: {final_name}"
        )
    temporary_descriptor = _open_regular_at(
        publication_root_fd, temporary_name,
    )
    published_identity = None
    try:
        temporary_metadata = os.fstat(temporary_descriptor)
        temporary_identity = _object_identity(temporary_metadata)
        temporary_digest, temporary_size = _sha256_fd(temporary_descriptor)
        temporary_mode = stat.S_IMODE(temporary_metadata.st_mode)
        try:
            os.link(
                temporary_name, final_name,
                src_dir_fd=publication_root_fd,
                dst_dir_fd=publication_root_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise RuntimeError(
                f"cannot atomically publish package artifact: {final_name}"
            ) from exc
        final_metadata = os.stat(
            final_name, dir_fd=publication_root_fd, follow_symlinks=False,
        )
        published_identity = _object_identity(final_metadata)
        if published_identity != temporary_identity:
            raise RuntimeError(
                f"published package artifact identity mismatch: {final_name}"
            )
        if not _unlink_if_owned(
            publication_root_fd, temporary_name, temporary_identity,
        ):
            raise RuntimeError(
                f"published package temporary ownership changed: {temporary_name}"
            )
        final_descriptor = _open_regular_at(publication_root_fd, final_name)
        try:
            final_metadata = os.fstat(final_descriptor)
            final_digest, final_size = _sha256_fd(final_descriptor)
            if (
                _object_identity(final_metadata) != published_identity
                or stat.S_IMODE(final_metadata.st_mode) != temporary_mode
                or final_metadata.st_size != temporary_size
                or final_size != temporary_size
                or final_digest != temporary_digest
            ):
                raise RuntimeError(
                    f"published package artifact content changed: {final_name}"
                )
            return PublishedOwnership(
                published_identity, final_metadata.st_ctime_ns, final_size,
                final_digest,
            )
        finally:
            os.close(final_descriptor)
    except Exception:
        if published_identity is not None:
            try:
                _unlink_if_owned(
                    publication_root_fd, final_name, published_identity,
                )
            except OSError:
                pass
        raise
    finally:
        os.close(temporary_descriptor)


def _publication_matches_owned(
    publication_root_fd: int,
    name: str,
    identity: ObjectIdentity | PublishedOwnership,
) -> bool:
    try:
        metadata = os.stat(
            name, dir_fd=publication_root_fd, follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    expected_identity = (
        identity.identity if isinstance(identity, PublishedOwnership) else identity
    )
    if _object_identity(metadata) != expected_identity:
        return False
    if isinstance(identity, PublishedOwnership):
        if metadata.st_ctime_ns != identity.ctime_ns or metadata.st_size != identity.size:
            return False
        descriptor = _open_regular_at(publication_root_fd, name)
        try:
            digest, size = _sha256_fd(descriptor)
        finally:
            os.close(descriptor)
        if size != identity.size or digest != identity.sha256:
            return False
    return True


def _unlink_publication_if_owned(
    publication_root_fd: int,
    name: str,
    identity: ObjectIdentity | PublishedOwnership,
) -> bool:
    if not _publication_matches_owned(publication_root_fd, name, identity):
        return False
    os.unlink(name, dir_fd=publication_root_fd)
    return True


def package_verified_inventory(
    manifest_path: Path,
    source_root: Path,
    archive_path: Path,
    archive_sha_path: Path,
    commit_sha: str,
    archive_manifest_path: Path | None = None,
) -> Mapping[str, object]:
    """Copy, archive, re-open, and atomically publish one verified allowlist."""

    if not isinstance(commit_sha, str) or not _COMMIT_RE.fullmatch(commit_sha):
        raise RuntimeError("package commit SHA is invalid")
    source_root = Path(source_root).resolve(strict=True)
    manifest_path = Path(manifest_path)
    archive_path = Path(archive_path)
    archive_sha_path = Path(archive_sha_path)
    archive_manifest_path = (
        Path(archive_manifest_path)
        if archive_manifest_path is not None
        else Path(str(archive_path) + ".manifest.json")
    )
    publication_paths = (
        archive_path, archive_manifest_path, archive_sha_path,
    )
    if len({path.name for path in publication_paths}) != 3:
        raise RuntimeError("package publication artifact names must be distinct")
    publication_parents = {path.parent.absolute() for path in publication_paths}
    if len(publication_parents) != 1:
        raise RuntimeError(
            "archive, archive manifest, and SHA must share one publication directory"
        )
    manifest, entries = _load_verified_manifest(manifest_path, source_root)
    manifest_bytes = manifest_path.read_bytes()
    commit_bytes = (commit_sha + "\n").encode("ascii")
    publication_root = _open_publication_root(next(iter(publication_parents)))
    root_descriptor = publication_root.descriptor
    archive_name = archive_path.name
    archive_manifest_name = archive_manifest_path.name
    archive_sha_name = archive_sha_path.name
    temporary_archive: str | None = None
    temporary_archive_identity: ObjectIdentity | None = None
    temporary_archive_descriptor = -1
    temporary_manifest: str | None = None
    temporary_manifest_identity: ObjectIdentity | None = None
    temporary_manifest_descriptor = -1
    temporary_sha: str | None = None
    temporary_sha_identity: ObjectIdentity | None = None
    temporary_sha_descriptor = -1
    published: list[tuple[str, PublishedOwnership]] = []
    try:
        _assert_publication_path_identity(publication_root)
        if any(
            _name_exists_at(root_descriptor, name)
            for name in (archive_name, archive_manifest_name, archive_sha_name)
        ):
            raise RuntimeError("refusing to overwrite an existing package")
        with tempfile.TemporaryDirectory(
            prefix="partsim-v9-3-package-results-",
        ) as temporary:
            destination_root = Path(temporary) / "results"
            copied = copy_verified_inventory(
                manifest_path, source_root, destination_root,
            )
            (
                temporary_archive,
                temporary_archive_descriptor,
                temporary_archive_identity,
            ) = _create_publication_temporary(
                root_descriptor,
            )
            with os.fdopen(
                os.dup(temporary_archive_descriptor), "wb",
            ) as archive_handle:
                with tarfile.open(fileobj=archive_handle, mode="w:gz") as archive:
                    _tar_add_bytes(
                        archive, "package/package_manifest.json",
                        manifest_bytes, 0o644,
                    )
                    _tar_add_bytes(
                        archive, "package/commit_sha.txt", commit_bytes, 0o644,
                    )
                    for entry in entries:
                        _tar_add_entry(archive, destination_root, entry)
            os.fsync(temporary_archive_descriptor)
            archive_identity, _archive_size, archive_digest = _verify_regular_fd(
                temporary_archive_descriptor, expected_mode=0o644,
            )
            if archive_identity != temporary_archive_identity:
                raise RuntimeError("package archive staging identity changed")
            archive_manifest = _build_archive_manifest(
                archive_filename=archive_name,
                archive_sha256=archive_digest,
                commit_sha=commit_sha,
                package_manifest=manifest,
                package_manifest_bytes=manifest_bytes,
                entries=entries,
            )
            archive_manifest_bytes = (
                json.dumps(
                    archive_manifest, ensure_ascii=False, sort_keys=True,
                    indent=2, allow_nan=False,
                ) + "\n"
            ).encode("utf-8")
            (
                temporary_manifest,
                temporary_manifest_descriptor,
                temporary_manifest_identity,
            ) = _create_publication_temporary(
                root_descriptor,
            )
            _write_all(temporary_manifest_descriptor, archive_manifest_bytes)
            os.fsync(temporary_manifest_descriptor)
            manifest_identity, _manifest_size, _manifest_digest = _verify_regular_fd(
                temporary_manifest_descriptor,
                expected_mode=0o644,
                expected_content=archive_manifest_bytes,
            )
            if manifest_identity != temporary_manifest_identity:
                raise RuntimeError("archive manifest staging identity changed")
            sidecar_bytes = f"{archive_digest}  {archive_name}\n".encode("utf-8")
            (
                temporary_sha,
                temporary_sha_descriptor,
                temporary_sha_identity,
            ) = _create_publication_temporary(
                root_descriptor,
            )
            _write_all(temporary_sha_descriptor, sidecar_bytes)
            os.fsync(temporary_sha_descriptor)
            sha_identity, _sha_size, _sha_digest = _verify_regular_fd(
                temporary_sha_descriptor,
                expected_mode=0o644,
                expected_content=sidecar_bytes,
            )
            if sha_identity != temporary_sha_identity:
                raise RuntimeError("archive SHA staging identity changed")

            os.close(temporary_archive_descriptor)
            temporary_archive_descriptor = -1
            os.close(temporary_manifest_descriptor)
            temporary_manifest_descriptor = -1
            os.close(temporary_sha_descriptor)
            temporary_sha_descriptor = -1
            verified_members = verify_package_archive(
                Path(temporary_archive), Path(temporary_manifest),
                expected_archive_filename=archive_name,
                _publication_root_fd=root_descriptor,
            )
            expected_results = tuple(sorted(
                f"package/results/{relative}" for relative in copied
            ))
            actual_results = tuple(
                name for name in verified_members
                if name.startswith("package/results/")
            )
            if expected_results != actual_results:
                raise RuntimeError("copied paths/archive manifest paths mismatch")
            _assert_publication_path_identity(publication_root)
            published.append((
                archive_name,
                _publish_exclusive(
                    root_descriptor, temporary_archive, archive_name,
                ),
            ))
            temporary_archive = None
            temporary_archive_identity = None
            published.append((
                archive_manifest_name,
                _publish_exclusive(
                    root_descriptor, temporary_manifest, archive_manifest_name,
                ),
            ))
            temporary_manifest = None
            temporary_manifest_identity = None
            published.append((
                archive_sha_name,
                _publish_exclusive(
                    root_descriptor, temporary_sha, archive_sha_name,
                ),
            ))
            temporary_sha = None
            temporary_sha_identity = None
            if verify_published_package(
                Path(archive_name), Path(archive_manifest_name),
                Path(archive_sha_name),
                expected_archive_filename=archive_name,
                _publication_root_fd=root_descriptor,
            ) != verified_members:
                raise RuntimeError("published archive verification changed")
            if not all(
                _publication_matches_owned(root_descriptor, name, ownership)
                for name, ownership in published
            ):
                raise RuntimeError("published package ownership changed")
            _assert_publication_path_identity(publication_root)
            return {
                "archive": str(archive_path),
                "archive_manifest": str(archive_manifest_path),
                "archive_sha256": archive_digest,
                "entry_count": len(copied),
                "tar_member_count": len(verified_members),
                "schema": PACKAGE_ARCHIVE_SCHEMA,
                "schema_version": PACKAGE_ARCHIVE_SCHEMA_VERSION,
            }
    except Exception as exc:
        conflicts = []
        for name, identity in reversed(published):
            try:
                removed = _unlink_publication_if_owned(
                    root_descriptor, name, identity,
                )
            except OSError:
                removed = False
            if not removed and _name_exists_at(root_descriptor, name):
                conflicts.append(name)
        if conflicts:
            raise RuntimeError(
                "package publication failed and ownership changed for: "
                + ", ".join(conflicts)
            ) from exc
        raise
    finally:
        for descriptor in (
            temporary_archive_descriptor,
            temporary_manifest_descriptor,
            temporary_sha_descriptor,
        ):
            if descriptor >= 0:
                os.close(descriptor)
        for temporary_name, identity in (
            (temporary_archive, temporary_archive_identity),
            (temporary_manifest, temporary_manifest_identity),
            (temporary_sha, temporary_sha_identity),
        ):
            if temporary_name is not None and identity is not None:
                try:
                    _unlink_publication_if_owned(
                        root_descriptor, temporary_name, identity,
                    )
                except OSError:
                    pass
        os.close(root_descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination-root", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--archive-manifest", type=Path)
    parser.add_argument("--archive-sha", type=Path)
    parser.add_argument("--commit-sha")
    args = parser.parse_args()
    if args.destination_root is not None:
        if any(value is not None for value in (
            args.archive, args.archive_manifest, args.archive_sha,
            args.commit_sha,
        )):
            parser.error("--destination-root cannot be combined with archive options")
        copied = copy_verified_inventory(
            args.manifest, args.source_root, args.destination_root,
        )
        print(f"copied {len(copied)} verified regular files")
        return 0
    if args.archive is None or args.archive_sha is None or args.commit_sha is None:
        parser.error("archive mode requires --archive, --archive-sha, and --commit-sha")
    result = package_verified_inventory(
        args.manifest, args.source_root, args.archive, args.archive_sha,
        args.commit_sha, args.archive_manifest,
    )
    print(
        f"packaged {result['entry_count']} verified regular files: "
        f"{result['archive']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
