from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

import deployment.autodl.package_inventory as package_inventory
from experiments.v9_3.plot_cli import render_plot_data
from experiments.v9_3.plotting_data import (
    ANALYSIS_SOLVER_STATUSES, CORE_PLOT_TYPES, PLOT_ROW_SCHEMA, PLOT_SCHEMA,
    PLOT_SCHEMA_VERSION,
    PlotTableError,
    validate_canonical_plot_table,
)
from experiments.v9_3.output_inventory import (
    ARCHIVE_MANIFEST_SCHEMA, ARCHIVE_MANIFEST_SCHEMA_VERSION,
    PACKAGE_ARCHIVE_SCHEMA, PACKAGE_ARCHIVE_SCHEMA_VERSION,
    VERIFIED_PACKAGE_MANIFEST_SCHEMA, VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION,
    verified_package_entries,
)
from deployment.autodl.verify_outputs import verify_core12_output
from test_v9_3_deployment_verify_outputs import (
    TEST_COMMIT_SHA, _formal_bundle as _shared_formal_bundle, _physical_csv,
)


COMMIT_SHA = TEST_COMMIT_SHA


def _formal_bundle(tmp_path, monkeypatch):
    return _shared_formal_bundle(
        tmp_path, monkeypatch, commit_sha=TEST_COMMIT_SHA,
    )


def _write_physical(path: Path, physical) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(physical)


def _mutated_plot_case(tmp_path, monkeypatch, name, plot_type, **changes):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    source_root = bundle / name
    source = source_root / f"{name}_plot_data.csv"
    physical = _physical_csv(source)
    header = physical[0]
    row = next(item for item in physical[1:] if item[header.index("plot")] == plot_type)
    for field, value in changes.items():
        row[header.index(field)] = value
    case_root = tmp_path / "mutated-plot"
    target = case_root / source.name
    _write_physical(target, (header, row))
    return target, source_root / "run_config.yaml", case_root / "plots"


def _assert_plot_rejected_before_render(data, config, output):
    core = "CORE-1" if data.name.startswith("core1") else "CORE-2"
    with pytest.raises(PlotTableError, match="plot schema"):
        validate_canonical_plot_table(data, expected_core=core)
    with pytest.raises(PlotTableError, match="plot schema"):
        render_plot_data(data, config, output)
    assert not output.exists()
    assert not any(
        path.suffix in {".png", ".pdf", ".tmp"}
        for path in data.parent.rglob("*") if path.is_file()
    )


def test_round9_red_plot_artifact_physically_binds_v3_schema(tmp_path, monkeypatch):
    assert PLOT_SCHEMA == "ASAP_BLOCK_V9_3_CANONICAL_PLOT_ROWS_V3"
    assert PLOT_SCHEMA_VERSION == 3
    bundle = _formal_bundle(tmp_path, monkeypatch)
    for name in ("core1", "core2"):
        physical = _physical_csv(bundle / name / f"{name}_plot_data.csv")
        assert physical[0][:2] == ["plot_schema", "plot_schema_version"]
        assert {tuple(row[:2]) for row in physical[1:]} == {(PLOT_SCHEMA, "3")}


def test_round9_plot_matrix_is_the_complete_single_authority():
    assert len(PLOT_ROW_SCHEMA) == 14
    for plot_type, contract in PLOT_ROW_SCHEMA.items():
        assert contract.plot_type == plot_type
        assert contract.schema == PLOT_SCHEMA
        assert contract.schema_version == PLOT_SCHEMA_VERSION
        assert contract.exact_header[:2] == (
            "plot_schema", "plot_schema_version",
        )
        assert contract.primary_key == contract.sort_key
        assert plot_type in CORE_PLOT_TYPES[contract.core]


@pytest.mark.parametrize("mutation", ("unknown_version", "v2_header", "runtime_constant"))
def test_round9_unknown_or_v2_plot_schema_fails_before_render(
    tmp_path, monkeypatch, mutation,
):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    source = bundle / "core1" / "core1_plot_data.csv"
    physical = _physical_csv(source)
    if mutation == "unknown_version":
        physical[1][physical[0].index("plot_schema_version")] = "999"
    elif mutation == "v2_header":
        physical = [row[2:] for row in physical]
    else:
        monkeypatch.setattr(
            "experiments.v9_3.plotting_data.PLOT_SCHEMA_VERSION", 999,
        )
    target = tmp_path / "schema-case" / source.name
    _write_physical(target, physical)
    _assert_plot_rejected_before_render(
        target, bundle / "core1" / "run_config.yaml",
        target.parent / "plots",
    )


@pytest.mark.parametrize(
    ("status", "y", "accepted"),
    (
        ("COMPLETED", "", True), ("COMPLETED", "0", False),
        ("NO_CANDIDATE", "0", True), ("NO_CANDIDATE", "", False),
        ("TIMEOUT", "", True), ("TIMEOUT", "1", True),
        ("NUMERIC_ERROR", "2", True), ("NUMERIC_ERROR", "", False),
        ("NOT_APPLICABLE_DEPENDENCY", "", True),
        ("NOT_APPLICABLE_DEPENDENCY", "0", False),
        ("INTERNAL_CONFORMANCE_FAILURE", "", True),
        ("INTERNAL_CONFORMANCE_FAILURE", "0", False),
        ("UNSUPPORTED_EXPERIMENT_VARIANT", "", True),
        ("UNSUPPORTED_EXPERIMENT_VARIANT", "0", False),
    ),
)
def test_round9_first_failed_priority_complete_status_truth_table(
    tmp_path, monkeypatch, status, y, accepted,
):
    data, config, output = _mutated_plot_case(
        tmp_path, monkeypatch, "core2", "first_failed_priority",
        outcome=status, y=y,
    )
    if accepted:
        assert validate_canonical_plot_table(data, expected_core="CORE-2")
    else:
        _assert_plot_rejected_before_render(data, config, output)


@pytest.mark.parametrize(
    ("value", "accepted"),
    (
        ("0", True), ("1", True), ("0.5", True),
        ("0.014000000000000002", True), ("1e-09", True),
        ("1/2", False), ("True", False), ("False", False),
        ("NaN", False), ("Infinity", False), ("-Infinity", False),
        ("", False), (" 0.5", False), ("0.5 ", False),
        ("0x1.0p-1", False), ("-0", False),
    ),
)
def test_round9_runtime_plot_strict_decimal_grammar(
    tmp_path, monkeypatch, value, accepted,
):
    data, config, output = _mutated_plot_case(
        tmp_path, monkeypatch, "core1", "runtime", y=value,
    )
    if accepted:
        assert validate_canonical_plot_table(data, expected_core="CORE-1")
    else:
        _assert_plot_rejected_before_render(data, config, output)


def test_round9_plot_primary_key_and_sort_order_fail_closed(tmp_path, monkeypatch):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    source = bundle / "core2" / "core2_plot_data.csv"
    physical = _physical_csv(source)
    for label, rows in (
        ("duplicate", [physical[0], physical[1], copy.deepcopy(physical[1])]),
        ("order", [physical[0], physical[2], physical[1]]),
    ):
        target = tmp_path / label / source.name
        _write_physical(target, rows)
        _assert_plot_rejected_before_render(
            target, bundle / "core2" / "run_config.yaml",
            target.parent / "plots",
        )


@pytest.mark.parametrize(
    ("name", "plot_type"), (("core1", "runtime"), ("core2", "variant_runtime")),
)
def test_round9_red_runtime_plot_rejects_fraction_before_render(
    tmp_path, monkeypatch, name, plot_type,
):
    data, config, output = _mutated_plot_case(
        tmp_path, monkeypatch, name, plot_type, y="1/2",
    )
    _assert_plot_rejected_before_render(data, config, output)


def test_round9_red_first_failed_no_candidate_requires_priority_before_render(
    tmp_path, monkeypatch,
):
    data, config, output = _mutated_plot_case(
        tmp_path, monkeypatch, "core2", "first_failed_priority",
        outcome="NO_CANDIDATE", y="",
    )
    _assert_plot_rejected_before_render(data, config, output)


def _entry(relative: str, content: bytes):
    return {
        "relative_path": relative,
        "file_type": "regular",
        "mode": 0o644,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "category": "authoritative_derived",
        "package": True,
        "inventory_schema": "ASAP_BLOCK_V9_3_OUTPUT_INVENTORY_V1",
        "inventory_schema_version": 1,
        "authoritative_scientific_content": True,
    }


def _two_entry_package_case(tmp_path):
    root = tmp_path / "source"
    first = b"summary\n"
    second = b"raw state\n"
    (root / "core1").mkdir(parents=True)
    (root / "core1" / "summary.json").write_bytes(first)
    (root / "core1" / "checkpoint.json").write_bytes(second)
    entries = [
        _entry("core1/checkpoint.json", second),
        _entry("core1/summary.json", first),
    ]
    manifest = tmp_path / "package-manifest.json"
    manifest.write_text(json.dumps({
        "schema": "ASAP_BLOCK_V9_3_VERIFIED_PACKAGE_PATHS_V2",
        "schema_version": 2,
        "archive_schema": "ASAP_BLOCK_V9_3_VERIFIED_PACKAGE_ARCHIVE_V1",
        "archive_schema_version": 1,
        "output_root": str(root.resolve()),
        "profile": "formal",
        "experiments": ["core1"],
        "inventory_schemas": [["ASAP_BLOCK_V9_3_OUTPUT_INVENTORY_V1", 1]],
        "entries": entries,
    }, sort_keys=True), encoding="utf-8")
    archive = tmp_path / "published" / "results.tar.gz"
    return root, manifest, archive, Path(str(archive) + ".sha256")


def _real_package_case(tmp_path, monkeypatch, *, presentation=False):
    root = _formal_bundle(tmp_path, monkeypatch)
    core1 = root / "core1"
    if presentation:
        render_plot_data(
            core1 / "core1_plot_data.csv", core1 / "run_config.yaml",
            core1 / "plots",
        )
    inventory = verify_core12_output("core1", core1)
    entries = [{
        **entry, "relative_path": f"core1/{entry['relative_path']}",
    } for entry in verified_package_entries(core1, inventory)]
    manifest = tmp_path / "package-manifest.json"
    manifest.write_text(json.dumps({
        "schema": VERIFIED_PACKAGE_MANIFEST_SCHEMA,
        "schema_version": VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION,
        "archive_schema": PACKAGE_ARCHIVE_SCHEMA,
        "archive_schema_version": PACKAGE_ARCHIVE_SCHEMA_VERSION,
        "output_root": str(root.resolve()),
        "profile": "formal",
        "experiments": ["core1"],
        "inventory_schemas": [[inventory.schema, inventory.schema_version]],
        "entries": entries,
    }, sort_keys=True), encoding="utf-8")
    archive = tmp_path / "published" / "results.tar.gz"
    return root, manifest, archive, Path(str(archive) + ".sha256")


@pytest.mark.parametrize("mutation", ("delete", "category", "inventory_schema"))
def test_round9_red_package_reconstructs_exact_frozen_inventory(
    tmp_path, monkeypatch, mutation,
):
    source, manifest, archive, archive_sha = _real_package_case(
        tmp_path, monkeypatch,
    )
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if mutation == "delete":
        value["entries"].pop()
    elif mutation == "category":
        value["entries"][0]["category"] = "ARBITRARY_NOT_FROZEN"
    else:
        value["entries"][0]["inventory_schema"] = "ARBITRARY_SCHEMA_V999"
        value["inventory_schemas"] = [["ARBITRARY_SCHEMA_V999", 1], [
            "ASAP_BLOCK_V9_3_OUTPUT_INVENTORY_V1", 1,
        ]]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    with pytest.raises(RuntimeError, match="inventory|manifest|category"):
        package_inventory.package_verified_inventory(
            manifest, source, archive, archive_sha, COMMIT_SHA,
        )
    assert before == {
        p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    assert not archive.exists()
    assert not archive_sha.exists()
    assert not Path(str(archive) + ".manifest.json").exists()


@pytest.mark.parametrize(
    "mutation",
    (
        "delete_summary", "delete_raw_state", "delete_presentation",
        "extra_entry", "duplicate_entry", "category", "package_bool",
        "authoritative_bool", "inventory_schema", "inventory_version",
        "manifest_schema", "manifest_version", "unknown_top", "missing_top",
        "entry_metadata",
    ),
)
def test_round9_package_manifest_exact_inventory_negative_matrix(
    tmp_path, monkeypatch, mutation,
):
    source, manifest, archive, archive_sha = _real_package_case(
        tmp_path, monkeypatch, presentation=mutation == "delete_presentation",
    )
    before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }
    value = json.loads(manifest.read_text(encoding="utf-8"))
    entries = value["entries"]
    if mutation == "delete_summary":
        entries.remove(next(item for item in entries if item["relative_path"].endswith("/summary.json")))
    elif mutation == "delete_raw_state":
        entries.remove(next(item for item in entries if "/result_state/" in item["relative_path"]))
    elif mutation == "delete_presentation":
        entries.remove(next(item for item in entries if "/plots/" in item["relative_path"]))
    elif mutation == "extra_entry":
        extra = copy.deepcopy(entries[0])
        extra["relative_path"] = "core1/unknown.extra"
        entries.append(extra)
    elif mutation == "duplicate_entry":
        entries.append(copy.deepcopy(entries[0]))
    elif mutation == "category":
        entries[0]["category"] = "ARBITRARY_NOT_FROZEN"
    elif mutation == "package_bool":
        entries[0]["package"] = 1
    elif mutation == "authoritative_bool":
        entries[0]["authoritative_scientific_content"] = 1
    elif mutation == "inventory_schema":
        entries[0]["inventory_schema"] = "ARBITRARY_SCHEMA_V999"
        value["inventory_schemas"] = sorted({
            (item["inventory_schema"], item["inventory_schema_version"])
            for item in entries
        })
    elif mutation == "inventory_version":
        entries[0]["inventory_schema_version"] = 999
        value["inventory_schemas"] = sorted({
            (item["inventory_schema"], item["inventory_schema_version"])
            for item in entries
        })
    elif mutation == "manifest_schema":
        value["schema"] = "ARBITRARY_PACKAGE_MANIFEST_V999"
    elif mutation == "manifest_version":
        value["schema_version"] = 999
    elif mutation == "unknown_top":
        value["unknown"] = True
    elif mutation == "missing_top":
        del value["profile"]
    elif mutation == "entry_metadata":
        entries[0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RuntimeError):
        package_inventory.package_verified_inventory(
            manifest, source, archive, archive_sha, COMMIT_SHA,
        )
    assert before == {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }
    assert not archive.exists()
    assert not archive_sha.exists()
    assert not Path(str(archive) + ".manifest.json").exists()


def test_round9_red_destination_parent_swap_cannot_escape_root(tmp_path, monkeypatch):
    source, manifest, _archive, _sha = _real_package_case(tmp_path, monkeypatch)
    destination = tmp_path / "destination"
    external = tmp_path / "external"
    external.mkdir()
    observed_escape = False
    original_parent = package_inventory._open_parent_components
    swapped = False

    def swap_after_check(root, relative, created, **kwargs):
        nonlocal swapped
        checked = original_parent(root, relative, created, **kwargs)
        if not swapped and kwargs.get("create", True):
            parent = destination / "core1"
            parent.rename(tmp_path / "displaced-parent")
            parent.symlink_to(external, target_is_directory=True)
            swapped = True
        return checked

    monkeypatch.setattr(package_inventory, "_open_parent_components", swap_after_check)
    with pytest.raises(RuntimeError):
        package_inventory.copy_verified_inventory(manifest, source, destination)
    observed_escape = any(external.iterdir())
    assert not observed_escape


def test_round9_destination_parent_inode_replacement_fails_before_write(
    tmp_path, monkeypatch,
):
    source, manifest, _archive, _sha = _real_package_case(tmp_path, monkeypatch)
    destination = tmp_path / "destination"
    displaced = tmp_path / "displaced-parent"
    original = package_inventory._open_parent_components
    swapped = False

    def replace_parent(root, relative, created, **kwargs):
        nonlocal swapped
        result = original(root, relative, created, **kwargs)
        if not swapped and kwargs.get("create", True):
            parent = destination / "core1"
            parent.rename(displaced)
            parent.mkdir()
            swapped = True
        return result

    monkeypatch.setattr(package_inventory, "_open_parent_components", replace_parent)
    with pytest.raises(RuntimeError, match="destination parent"):
        package_inventory.copy_verified_inventory(manifest, source, destination)
    assert not any(displaced.iterdir())
    assert not any((destination / "core1").iterdir())


@pytest.mark.parametrize("kind", ("regular", "symlink", "directory"))
def test_round9_destination_publish_collision_is_exclusive_and_preserved(
    tmp_path, monkeypatch, kind,
):
    source, manifest, _archive, _sha = _real_package_case(tmp_path, monkeypatch)
    destination = tmp_path / "destination"
    original_link = package_inventory.os.link
    competitor = b"competitor destination\n"
    injected = False

    def collide(source_name, destination_name, **kwargs):
        nonlocal injected
        if not injected and kwargs.get("dst_dir_fd") is not None:
            injected = True
            parent = kwargs["dst_dir_fd"]
            if kind == "regular":
                descriptor = os.open(
                    destination_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644, dir_fd=parent,
                )
                try:
                    os.write(descriptor, competitor)
                finally:
                    os.close(descriptor)
            elif kind == "symlink":
                os.symlink("competitor-target", destination_name, dir_fd=parent)
            else:
                os.mkdir(destination_name, dir_fd=parent)
        return original_link(source_name, destination_name, **kwargs)

    monkeypatch.setattr(package_inventory.os, "link", collide)
    with pytest.raises(RuntimeError, match="destination already exists"):
        package_inventory.copy_verified_inventory(manifest, source, destination)
    competitor_path = destination / "core1" / "analysis_attempts.csv"
    if kind == "regular":
        assert competitor_path.read_bytes() == competitor
    elif kind == "symlink":
        assert competitor_path.is_symlink()
        assert os.readlink(competitor_path) == "competitor-target"
    else:
        assert competitor_path.is_dir()


def test_round9_destination_temporary_collision_is_exclusive_and_preserved(
    tmp_path, monkeypatch,
):
    source, manifest, _archive, _sha = _real_package_case(tmp_path, monkeypatch)
    destination = tmp_path / "destination"
    token = "fixedtoken"
    competitor = b"temporary competitor\n"
    original = package_inventory._open_parent_components
    injected = False

    def inject_temporary(root, relative, created, **kwargs):
        nonlocal injected
        result = original(root, relative, created, **kwargs)
        if not injected and kwargs.get("create", True):
            parent_descriptor, name, _identity = result
            temporary = f".{name}.{os.getpid()}.{token}.tmp"
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644,
                dir_fd=parent_descriptor,
            )
            try:
                os.write(descriptor, competitor)
            finally:
                os.close(descriptor)
            injected = True
        return result

    monkeypatch.setattr(package_inventory.secrets, "token_hex", lambda _n: token)
    monkeypatch.setattr(package_inventory, "_open_parent_components", inject_temporary)
    with pytest.raises(RuntimeError, match="copy failed"):
        package_inventory.copy_verified_inventory(manifest, source, destination)
    temporary = (
        destination / "core1"
        / f".analysis_attempts.csv.{os.getpid()}.{token}.tmp"
    )
    assert temporary.read_bytes() == competitor


@pytest.mark.parametrize("kind", ("regular", "symlink", "directory"))
def test_round9_preexisting_destination_object_is_never_modified(
    tmp_path, monkeypatch, kind,
):
    source, manifest, _archive, _sha = _real_package_case(tmp_path, monkeypatch)
    destination = tmp_path / "destination"
    target = destination / "history"
    destination.mkdir()
    if kind == "regular":
        target.write_bytes(b"history\n")
    elif kind == "symlink":
        target.symlink_to("history-target")
    else:
        target.mkdir()
    before = os.readlink(target) if target.is_symlink() else None
    with pytest.raises(RuntimeError, match="destination"):
        package_inventory.copy_verified_inventory(manifest, source, destination)
    if kind == "regular":
        assert target.read_bytes() == b"history\n"
    elif kind == "symlink":
        assert os.readlink(target) == before
    else:
        assert target.is_dir()


def test_round9_red_cleanup_preserves_competing_sidecar(tmp_path, monkeypatch):
    source, manifest, archive, archive_sha = _real_package_case(tmp_path, monkeypatch)
    competitor = b"competitor-owned-sidecar\n"
    original = package_inventory._publish_exclusive
    calls = 0

    def collide(root_descriptor, temporary, final):
        nonlocal calls
        calls += 1
        if calls == 3:
            descriptor = os.open(
                final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644,
                dir_fd=root_descriptor,
            )
            try:
                os.write(descriptor, competitor)
            finally:
                os.close(descriptor)
            raise RuntimeError("injected competing publication")
        return original(root_descriptor, temporary, final)

    monkeypatch.setattr(package_inventory, "_publish_exclusive", collide)
    with pytest.raises(RuntimeError):
        package_inventory.package_verified_inventory(
            manifest, source, archive, archive_sha, COMMIT_SHA,
        )
    assert archive_sha.read_bytes() == competitor


@pytest.mark.parametrize("artifact", ("archive", "manifest", "sha"))
def test_round9_preexisting_publication_artifact_is_preserved(
    tmp_path, monkeypatch, artifact,
):
    source, manifest, archive, archive_sha = _real_package_case(tmp_path, monkeypatch)
    archive_manifest = Path(str(archive) + ".manifest.json")
    paths = {"archive": archive, "manifest": archive_manifest, "sha": archive_sha}
    history = b"historical package artifact\n"
    paths[artifact].parent.mkdir(parents=True)
    paths[artifact].write_bytes(history)
    with pytest.raises(RuntimeError, match="overwrite"):
        package_inventory.package_verified_inventory(
            manifest, source, archive, archive_sha, COMMIT_SHA,
        )
    assert paths[artifact].read_bytes() == history


@pytest.mark.parametrize("collision_call", (2, 3))
def test_round9_publication_collision_rolls_back_only_owned_inodes(
    tmp_path, monkeypatch, collision_call,
):
    source, manifest, archive, archive_sha = _real_package_case(tmp_path, monkeypatch)
    archive_manifest = Path(str(archive) + ".manifest.json")
    competitor = f"competitor-{collision_call}\n".encode()
    original = package_inventory._publish_exclusive
    calls = 0
    collision_path = None

    def collide(root_descriptor, temporary, final):
        nonlocal calls, collision_path
        calls += 1
        if calls == collision_call:
            collision_path = archive.parent / final
            descriptor = os.open(
                final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644,
                dir_fd=root_descriptor,
            )
            try:
                os.write(descriptor, competitor)
            finally:
                os.close(descriptor)
            raise RuntimeError("injected exclusive publication collision")
        return original(root_descriptor, temporary, final)

    monkeypatch.setattr(package_inventory, "_publish_exclusive", collide)
    with pytest.raises(RuntimeError):
        package_inventory.package_verified_inventory(
            manifest, source, archive, archive_sha, COMMIT_SHA,
        )
    assert collision_path is not None
    assert collision_path.read_bytes() == competitor
    if collision_call == 2:
        assert not archive.exists()
        assert not archive_sha.exists()
    else:
        assert not archive.exists()
        assert not archive_manifest.exists()


def test_round9_cleanup_preserves_replacement_of_previously_owned_inode(
    tmp_path, monkeypatch,
):
    source, manifest, archive, archive_sha = _real_package_case(tmp_path, monkeypatch)
    competitor = b"replacement archive inode\n"
    original = package_inventory._publish_exclusive
    calls = 0

    def replace_then_fail(root_descriptor, temporary, final):
        nonlocal calls
        calls += 1
        if calls == 1:
            identity = original(root_descriptor, temporary, final)
            os.unlink(final, dir_fd=root_descriptor)
            descriptor = os.open(
                final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644,
                dir_fd=root_descriptor,
            )
            try:
                os.write(descriptor, competitor)
            finally:
                os.close(descriptor)
            return identity
        raise RuntimeError("injected later publication failure")

    monkeypatch.setattr(package_inventory, "_publish_exclusive", replace_then_fail)
    with pytest.raises(RuntimeError, match="ownership changed"):
        package_inventory.package_verified_inventory(
            manifest, source, archive, archive_sha, COMMIT_SHA,
        )
    assert archive.read_bytes() == competitor


def test_round9_failure_without_competitor_removes_all_owned_final_objects(
    tmp_path, monkeypatch,
):
    source, manifest, archive, archive_sha = _real_package_case(tmp_path, monkeypatch)
    archive_manifest = Path(str(archive) + ".manifest.json")
    original = package_inventory._publish_exclusive
    calls = 0

    def fail_sha(root_descriptor, temporary, final):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("injected SHA publication failure")
        return original(root_descriptor, temporary, final)

    monkeypatch.setattr(package_inventory, "_publish_exclusive", fail_sha)
    with pytest.raises(RuntimeError):
        package_inventory.package_verified_inventory(
            manifest, source, archive, archive_sha, COMMIT_SHA,
        )
    assert not archive.exists()
    assert not archive_manifest.exists()
    assert not archive_sha.exists()


def test_round9_red_success_publishes_complete_external_archive_manifest(
    tmp_path, monkeypatch,
):
    source, manifest, archive, archive_sha = _real_package_case(tmp_path, monkeypatch)
    result = package_inventory.package_verified_inventory(
        manifest, source, archive, archive_sha, COMMIT_SHA,
    )
    archive_manifest = Path(str(archive) + ".manifest.json")
    assert archive_manifest.is_file()
    value = json.loads(archive_manifest.read_text(encoding="utf-8"))
    assert value["schema"] == "ASAP_BLOCK_V9_3_ARCHIVE_MANIFEST_V1"
    assert value["schema_version"] == 1
    assert value["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert result["archive_manifest"] == str(archive_manifest)
    assert package_inventory.verify_package_archive(
        archive, archive_manifest,
    ) == tuple(sorted(member["path"] for member in value["members"]))


def _rewrite_archive_member(path: Path, mutation: str) -> None:
    with tarfile.open(path, "r:gz") as source:
        records = []
        for member in source.getmembers():
            extracted = source.extractfile(member) if member.isreg() else None
            records.append((copy.copy(member), extracted.read() if extracted else b""))
    package_index = next(i for i, (m, _c) in enumerate(records) if m.name == "package/package_manifest.json")
    commit_index = next(i for i, (m, _c) in enumerate(records) if m.name == "package/commit_sha.txt")
    result_index = next(i for i, (m, _c) in enumerate(records) if m.name.startswith("package/results/"))
    if mutation == "missing_package_manifest":
        records.pop(package_index)
    elif mutation == "missing_commit":
        records.pop(commit_index)
    elif mutation == "missing_result":
        records.pop(result_index)
    elif mutation == "extra":
        info = tarfile.TarInfo("package/extra")
        info.type = tarfile.REGTYPE
        info.mode = 0o644
        info.size = 1
        records.append((info, b"x"))
    elif mutation == "duplicate":
        member, content = records[result_index]
        records.append((copy.copy(member), content))
    else:
        member, content = records[result_index]
        if mutation == "type":
            member.type = tarfile.SYMTYPE
            member.linkname = "elsewhere"
            member.size = 0
            content = b""
        elif mutation == "mode":
            member.mode ^= 0o040
        elif mutation == "size":
            content += b"x"
            member.size = len(content)
        elif mutation == "sha":
            content = bytes([content[0] ^ 1]) + content[1:]
        records[result_index] = (member, content)
    with tarfile.open(path, "w:gz") as destination:
        for member, content in records:
            destination.addfile(
                member, io.BytesIO(content) if member.isreg() else None,
            )


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_package_manifest", "missing_commit", "missing_result",
        "extra", "duplicate", "type", "mode", "size", "sha",
    ),
)
def test_round9_tar_verifier_consumes_external_member_manifest(
    tmp_path, monkeypatch, mutation,
):
    source, manifest, archive, archive_sha = _real_package_case(tmp_path, monkeypatch)
    package_inventory.package_verified_inventory(
        manifest, source, archive, archive_sha, COMMIT_SHA,
    )
    archive_manifest = Path(str(archive) + ".manifest.json")
    _rewrite_archive_member(archive, mutation)
    value = json.loads(archive_manifest.read_text(encoding="utf-8"))
    value["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive_manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RuntimeError, match="archive|member"):
        package_inventory.verify_package_archive(archive, archive_manifest)


@pytest.mark.parametrize(
    "mutation", ("missing_member", "extra_member", "duplicate_member", "schema", "version", "archive_sha"),
)
def test_round9_external_archive_manifest_mutations_fail_closed(
    tmp_path, monkeypatch, mutation,
):
    source, manifest, archive, archive_sha = _real_package_case(tmp_path, monkeypatch)
    package_inventory.package_verified_inventory(
        manifest, source, archive, archive_sha, COMMIT_SHA,
    )
    archive_manifest = Path(str(archive) + ".manifest.json")
    value = json.loads(archive_manifest.read_text(encoding="utf-8"))
    if mutation == "missing_member":
        value["members"].pop()
    elif mutation == "extra_member":
        member = copy.deepcopy(value["members"][-1])
        member["path"] = "package/results/extra"
        value["members"].append(member)
    elif mutation == "duplicate_member":
        value["members"].append(copy.deepcopy(value["members"][-1]))
    elif mutation == "schema":
        value["schema"] = "ARBITRARY_ARCHIVE_MANIFEST_V999"
    elif mutation == "version":
        value["schema_version"] = 999
    else:
        value["archive_sha256"] = "0" * 64
    archive_manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RuntimeError, match="archive|member|manifest"):
        package_inventory.verify_package_archive(archive, archive_manifest)


def test_round9_sidecar_sha_is_part_of_verified_three_file_set(
    tmp_path, monkeypatch,
):
    source, manifest, archive, archive_sha = _real_package_case(tmp_path, monkeypatch)
    package_inventory.package_verified_inventory(
        manifest, source, archive, archive_sha, COMMIT_SHA,
    )
    archive_manifest = Path(str(archive) + ".manifest.json")
    archive_sha.write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="SHA sidecar"):
        package_inventory.verify_published_package(
            archive, archive_manifest, archive_sha,
        )


def test_round9_three_file_success_has_exact_member_bijection(
    tmp_path, monkeypatch,
):
    source, package_manifest, archive, archive_sha = _real_package_case(
        tmp_path, monkeypatch, presentation=True,
    )
    result = package_inventory.package_verified_inventory(
        package_manifest, source, archive, archive_sha, COMMIT_SHA,
    )
    archive_manifest = Path(result["archive_manifest"])
    value = json.loads(archive_manifest.read_text(encoding="utf-8"))
    assert value["schema"] == ARCHIVE_MANIFEST_SCHEMA
    assert value["schema_version"] == ARCHIVE_MANIFEST_SCHEMA_VERSION
    assert value["archive_filename"] == archive.name
    assert value["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest_paths = {item["path"] for item in value["members"]}
    with tarfile.open(archive, "r:gz") as handle:
        tar_paths = {member.name for member in handle.getmembers()}
    assert tar_paths == manifest_paths
    package_value = json.loads(package_manifest.read_text(encoding="utf-8"))
    result_paths = {
        f"package/results/{entry['relative_path']}"
        for entry in package_value["entries"]
    }
    assert result_paths < manifest_paths
    assert {
        "package/package_manifest.json", "package/commit_sha.txt",
    } < manifest_paths
    assert result["entry_count"] == len(result_paths)
    assert result["tar_member_count"] == len(manifest_paths)
    assert package_inventory.verify_published_package(
        archive, archive_manifest, archive_sha,
    ) == tuple(sorted(manifest_paths))
