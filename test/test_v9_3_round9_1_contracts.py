from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

import deployment.autodl.package_inventory as package_inventory
from experiments.v9_3.output_inventory import (
    ADMINISTRATIVE_EVIDENCE,
    PACKAGE_ARCHIVE_SCHEMA,
    PACKAGE_ARCHIVE_SCHEMA_VERSION,
    VERIFIED_PACKAGE_MANIFEST_SCHEMA,
    VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION,
)
from test_v9_3_round9_contracts import COMMIT_SHA, _real_package_case


def _entry(relative: str, content: bytes) -> dict[str, object]:
    return {
        "relative_path": relative,
        "file_type": "regular",
        "mode": 0o644,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "category": "authoritative_derived",
        "package": True,
        "inventory_schema": "ROUND9_1_TEST_INVENTORY_V1",
        "inventory_schema_version": 1,
        "authoritative_scientific_content": True,
    }


def _small_package_case(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    content = b"authoritative result\n"
    result = source / "core1" / "summary.json"
    result.parent.mkdir(parents=True)
    result.write_bytes(content)
    manifest = tmp_path / "package-manifest.json"
    entries = [_entry("core1/summary.json", content)]
    value = {
        "schema": VERIFIED_PACKAGE_MANIFEST_SCHEMA,
        "schema_version": VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION,
        "archive_schema": PACKAGE_ARCHIVE_SCHEMA,
        "archive_schema_version": PACKAGE_ARCHIVE_SCHEMA_VERSION,
        "output_root": str(source.resolve()),
        "profile": "formal",
        "experiments": ["core1"],
        "inventory_schemas": [["ROUND9_1_TEST_INVENTORY_V1", 1]],
        "entries": entries,
    }
    manifest.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(
        package_inventory,
        "_reconstruct_expected_entries",
        lambda loaded, _root: tuple(loaded["entries"]),
    )
    archive = tmp_path / "publication" / "results.tar.gz"
    return source, manifest, archive, Path(f"{archive}.sha256")


def _publication_names(archive: Path) -> tuple[str, str, str]:
    return archive.name, f"{archive.name}.manifest.json", f"{archive.name}.sha256"


@pytest.mark.parametrize(
    ("phase", "replacement"),
    (
        ("before_first", "symlink_to_displaced"),
        ("before_first", "symlink_to_external"),
        ("before_first", "directory"),
        ("after_first", "symlink_to_displaced"),
        ("after_second", "symlink_to_displaced"),
        ("after_verification", "symlink_to_displaced"),
        ("before_first", "missing"),
        ("before_first", "empty_directory"),
    ),
)
def test_round9_1_publication_parent_identity_change_fails_closed(
    tmp_path, monkeypatch, phase, replacement,
):
    source, manifest, archive, archive_sha = _small_package_case(
        tmp_path, monkeypatch,
    )
    publication = archive.parent
    displaced = tmp_path / "displaced-publication"
    external = tmp_path / "external-target"
    competitor = b"competitor object must survive byte-for-byte\n"
    swapped = False

    def swap_parent():
        nonlocal swapped
        if swapped:
            return
        publication.rename(displaced)
        (displaced / "competitor.bin").write_bytes(competitor)
        if replacement == "symlink_to_displaced":
            publication.symlink_to(displaced, target_is_directory=True)
        elif replacement == "symlink_to_external":
            external.mkdir()
            (external / "competitor.bin").write_bytes(competitor)
            publication.symlink_to(external, target_is_directory=True)
        elif replacement in {"directory", "empty_directory"}:
            publication.mkdir()
            if replacement == "directory":
                (publication / "competitor.bin").write_bytes(competitor)
        elif replacement != "missing":  # pragma: no cover - fixture guard
            raise AssertionError(replacement)
        swapped = True

    real_publish = package_inventory._publish_exclusive
    publish_calls = 0

    def publish_at_phase(*args, **kwargs):
        nonlocal publish_calls
        publish_calls += 1
        if phase == "before_first" and publish_calls == 1:
            swap_parent()
        result = real_publish(*args, **kwargs)
        if phase == "after_first" and publish_calls == 1:
            swap_parent()
        if phase == "after_second" and publish_calls == 2:
            swap_parent()
        return result

    monkeypatch.setattr(package_inventory, "_publish_exclusive", publish_at_phase)
    real_verify = package_inventory.verify_published_package

    def verify_then_swap(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        if phase == "after_verification":
            swap_parent()
        return result

    monkeypatch.setattr(
        package_inventory, "verify_published_package", verify_then_swap,
    )

    with pytest.raises(RuntimeError):
        package_inventory.package_verified_inventory(
            manifest, source, archive, archive_sha, COMMIT_SHA,
        )

    assert swapped
    assert (displaced / "competitor.bin").read_bytes() == competitor
    if external.exists():
        assert (external / "competitor.bin").read_bytes() == competitor
    if publication.is_dir() and not publication.is_symlink():
        marker = publication / "competitor.bin"
        if marker.exists():
            assert marker.read_bytes() == competitor
    for root in (displaced, external, publication):
        if root.is_dir():
            present = {path.name for path in root.iterdir()}
            assert not set(_publication_names(archive)).issubset(present)
    assert {path.name for path in displaced.iterdir()} == {"competitor.bin"}
    if external.exists():
        assert {path.name for path in external.iterdir()} == {"competitor.bin"}
    if publication.is_dir() and not publication.is_symlink():
        expected = {"competitor.bin"} if replacement == "directory" else set()
        assert {path.name for path in publication.iterdir()} == expected


def test_round9_1_publication_normal_path_still_succeeds(tmp_path, monkeypatch):
    source, manifest, archive, archive_sha = _small_package_case(
        tmp_path, monkeypatch,
    )
    result = package_inventory.package_verified_inventory(
        manifest, source, archive, archive_sha, COMMIT_SHA,
    )
    archive_manifest = Path(result["archive_manifest"])
    assert package_inventory.verify_published_package(
        archive, archive_manifest, archive_sha,
    )
    assert archive.is_file() and archive_manifest.is_file() and archive_sha.is_file()
    assert {path.name for path in archive.parent.iterdir()} == set(
        _publication_names(archive)
    )


def _read_tar(path: Path) -> list[tuple[tarfile.TarInfo, bytes]]:
    records = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            extracted = archive.extractfile(member) if member.isreg() else None
            records.append((copy.copy(member), extracted.read() if extracted else b""))
    return records


def _write_tar(path: Path, records: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member, content in records:
            archive.addfile(member, io.BytesIO(content) if member.isreg() else None)


def _record_index(records, name: str) -> int:
    return next(index for index, (member, _content) in enumerate(records) if member.name == name)


def _outer_record(outer, name: str) -> dict[str, object]:
    return next(record for record in outer["members"] if record["path"] == name)


def _replace_member_bytes(records, name: str, content: bytes) -> None:
    index = _record_index(records, name)
    member, _old = records[index]
    member.size = len(content)
    records[index] = (member, content)


def _sync_outer_member(outer, name: str, content: bytes, *, mode=None) -> None:
    record = _outer_record(outer, name)
    record["size"] = len(content)
    record["sha256"] = hashlib.sha256(content).hexdigest()
    if mode is not None:
        record["mode"] = mode


def _mutate_archive_contract(archive: Path, archive_manifest: Path, mutation: str):
    records = _read_tar(archive)
    outer = json.loads(archive_manifest.read_text(encoding="utf-8"))
    package_name = "package/package_manifest.json"
    commit_name = "package/commit_sha.txt"
    result_index = next(
        index for index, (member, _content) in enumerate(records)
        if member.name.startswith("package/results/")
    )
    result_name = records[result_index][0].name

    if mutation == "outer_base_commit":
        outer["base_commit"] = "b" * 40
    elif mutation == "outer_inventory_schemas":
        outer["output_inventory_schemas"] = [["LEGAL_BUT_WRONG_SCHEMA_V1", 1]]
    elif mutation == "result_category":
        _outer_record(outer, result_name)["category"] = ADMINISTRATIVE_EVIDENCE
    elif mutation == "result_authoritative":
        record = _outer_record(outer, result_name)
        record["authoritative_scientific_content"] = not record[
            "authoritative_scientific_content"
        ]
    elif mutation == "management_category":
        _outer_record(outer, package_name)["category"] = "authoritative_raw"
    elif mutation == "management_authoritative":
        _outer_record(outer, commit_name)["authoritative_scientific_content"] = True
    elif mutation == "delete_result":
        records.pop(result_index)
        outer["members"] = [item for item in outer["members"] if item["path"] != result_name]
    elif mutation == "add_result":
        content = b"not authorized by embedded package manifest\n"
        member = tarfile.TarInfo("package/results/unlisted.bin")
        member.type = tarfile.REGTYPE
        member.mode = 0o644
        member.size = len(content)
        records.append((member, content))
        outer["members"].append({
            "path": member.name,
            "file_type": "regular",
            "mode": member.mode,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "category": "authoritative_raw",
            "authoritative_scientific_content": True,
            "source": "RESULT_ENTRY",
        })
    elif mutation == "result_bytes":
        member, content = records[result_index]
        changed = content + b"synchronized outer digest\n"
        member.size = len(changed)
        records[result_index] = (member, changed)
        _sync_outer_member(outer, result_name, changed)
    elif mutation in {"package_entry", "package_and_result"}:
        package_index = _record_index(records, package_name)
        package_bytes = records[package_index][1]
        embedded = json.loads(package_bytes.decode("utf-8"))
        embedded_entry = next(
            item for item in embedded["entries"]
            if f"package/results/{item['relative_path']}" == result_name
        )
        if mutation == "package_entry":
            embedded_entry["category"] = ADMINISTRATIVE_EVIDENCE
        else:
            member, content = records[result_index]
            changed = content + b"self-consistent but not frozen\n"
            member.size = len(changed)
            records[result_index] = (member, changed)
            embedded_entry["size"] = len(changed)
            embedded_entry["sha256"] = hashlib.sha256(changed).hexdigest()
            _sync_outer_member(outer, result_name, changed)
        changed_manifest = (json.dumps(embedded, sort_keys=True) + "\n").encode()
        _replace_member_bytes(records, package_name, changed_manifest)
        _sync_outer_member(outer, package_name, changed_manifest)
    elif mutation == "commit_and_outer":
        content = ("c" * 40 + "\n").encode()
        _replace_member_bytes(records, commit_name, content)
        _sync_outer_member(outer, commit_name, content)
        outer["base_commit"] = "c" * 40
    elif mutation in {"delete_package", "delete_commit"}:
        name = package_name if mutation == "delete_package" else commit_name
        records.pop(_record_index(records, name))
        outer["members"] = [item for item in outer["members"] if item["path"] != name]
    elif mutation in {"duplicate_package", "duplicate_commit"}:
        name = package_name if mutation == "duplicate_package" else commit_name
        index = _record_index(records, name)
        records.append((copy.copy(records[index][0]), records[index][1]))
    elif mutation == "package_duplicate_key":
        package_bytes = records[_record_index(records, package_name)][1].rstrip()
        changed = package_bytes[:-1] + b',"schema":"DUPLICATE"}'
        _replace_member_bytes(records, package_name, changed)
        _sync_outer_member(outer, package_name, changed)
    elif mutation == "package_nonfinite_json":
        package_bytes = records[_record_index(records, package_name)][1]
        changed = package_bytes.replace(b'"schema_version": 2', b'"schema_version": NaN', 1)
        assert changed != package_bytes
        _replace_member_bytes(records, package_name, changed)
        _sync_outer_member(outer, package_name, changed)
    elif mutation == "package_mode":
        index = _record_index(records, package_name)
        records[index][0].mode = 0o600
        _outer_record(outer, package_name)["mode"] = 0o600
    elif mutation == "commit_extra_content":
        content = (COMMIT_SHA + "\nextra\n").encode()
        _replace_member_bytes(records, commit_name, content)
        _sync_outer_member(outer, commit_name, content)
    else:  # pragma: no cover - test helper guard
        raise AssertionError(mutation)

    _write_tar(archive, records)
    outer["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive_manifest.write_text(json.dumps(outer, sort_keys=True), encoding="utf-8")


@pytest.mark.parametrize(
    "mutation",
    (
        "outer_base_commit",
        "outer_inventory_schemas",
        "result_category",
        "result_authoritative",
        "management_category",
        "management_authoritative",
        "delete_result",
        "add_result",
        "result_bytes",
        "package_entry",
        "package_and_result",
        "commit_and_outer",
        "delete_package",
        "delete_commit",
        "duplicate_package",
        "duplicate_commit",
        "package_duplicate_key",
        "package_nonfinite_json",
        "package_mode",
        "commit_extra_content",
    ),
)
def test_round9_1_embedded_contract_synchronized_mutations_are_rejected(
    tmp_path, monkeypatch, mutation,
):
    source, package_manifest, archive, archive_sha = _real_package_case(
        tmp_path, monkeypatch,
    )
    result = package_inventory.package_verified_inventory(
        package_manifest, source, archive, archive_sha, COMMIT_SHA,
    )
    archive_manifest = Path(result["archive_manifest"])
    _mutate_archive_contract(archive, archive_manifest, mutation)
    archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive_sha.write_text(f"{archive_digest}  {archive.name}\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        package_inventory.verify_published_package(
            archive, archive_manifest, archive_sha,
        )


def test_round9_1_normal_archive_binds_embedded_inventory_exactly(
    tmp_path, monkeypatch,
):
    source, package_manifest, archive, archive_sha = _real_package_case(
        tmp_path, monkeypatch,
    )
    result = package_inventory.package_verified_inventory(
        package_manifest, source, archive, archive_sha, COMMIT_SHA,
    )
    archive_manifest = Path(result["archive_manifest"])
    outer = json.loads(archive_manifest.read_text(encoding="utf-8"))
    embedded = json.loads(package_manifest.read_text(encoding="utf-8"))
    with tarfile.open(archive, "r:gz") as handle:
        tar_names = [member.name for member in handle.getmembers()]
        embedded_bytes = handle.extractfile("package/package_manifest.json").read()
        commit_bytes = handle.extractfile("package/commit_sha.txt").read()
    result_names = {
        f"package/results/{entry['relative_path']}" for entry in embedded["entries"]
    }
    assert set(tar_names) == result_names | {
        "package/package_manifest.json", "package/commit_sha.txt",
    }
    assert len(tar_names) == len(embedded["entries"]) + 2
    assert len(outer["members"]) == len(embedded["entries"]) + 2
    assert embedded_bytes == package_manifest.read_bytes()
    assert commit_bytes == (COMMIT_SHA + "\n").encode()
    assert package_inventory.verify_published_package(
        archive, archive_manifest, archive_sha,
    ) == tuple(sorted(tar_names))
