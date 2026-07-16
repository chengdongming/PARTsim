from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile

import pytest

import deployment.autodl.package_inventory as package_inventory
from experiments.v9_3.output_inventory import (
    PACKAGE_ARCHIVE_SCHEMA,
    PACKAGE_ARCHIVE_SCHEMA_VERSION,
    VERIFIED_PACKAGE_MANIFEST_SCHEMA,
    VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION,
)
from experiments.v9_3.plot_cli import render_plot_data
from experiments.v9_3.plotting_data import (
    PLOT_ROW_SCHEMA, PLOT_SCHEMA, PLOT_SCHEMA_VERSION, PlotTableError,
    validate_canonical_plot_table,
)
from test_v9_3_deployment_verify_outputs import _formal_bundle, _physical_csv


def _write_physical(path: Path, physical) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(physical)


def test_round8_plot_schema_is_one_machine_consumable_14_type_matrix():
    assert PLOT_SCHEMA == "ASAP_BLOCK_V9_3_CANONICAL_PLOT_ROWS_V3"
    assert PLOT_SCHEMA_VERSION == 3
    assert len(PLOT_ROW_SCHEMA) == 14
    assert {contract.core for contract in PLOT_ROW_SCHEMA.values()} == {
        "CORE-1", "CORE-2",
    }
    for contract in PLOT_ROW_SCHEMA.values():
        assert contract.level in {"task", "taskset"}
        assert contract.task_id_rule in {"required", "empty"}
        assert contract.relation_rule in {"absent", "empty", "fixed", "mapped"}
        assert contract.primary_key[0:3] == (
            "plot", "cell_id", "taskset_id",
        )
        assert contract.x_domain and contract.y_domain and contract.outcomes


@pytest.fixture(autouse=True)
def _retain_round8_internal_manifest_fixtures(monkeypatch):
    """Round 8 copy/race fixtures predate Round 9 closure reconstruction.

    Round 9 has separate real-closure tests for the new exact-inventory gate;
    these older tests continue isolating the lower copy/archive boundaries.
    """

    monkeypatch.setattr(
        package_inventory,
        "_reconstruct_expected_entries",
        lambda manifest, _root: tuple(manifest["entries"]),
    )


def _set(header, row, **changes):
    changed = copy.deepcopy(row)
    for field, value in changes.items():
        changed[header.index(field)] = value
    return changed


def _semantic_mutations(core: str, plot_type: str, header, source):
    task_level = plot_type in {
        "loc_vs_cw_scatter", "response_reduction_distribution", "ablation",
    }
    mutations = [
        ("plot_schema", _set(header, source, plot_schema="UNKNOWN_PLOT_SCHEMA")),
        ("plot_schema_version", _set(header, source, plot_schema_version="999")),
        ("task_id", _set(
            header, source, task_id="" if task_level else "0"
        )),
        ("variant", _set(header, source, variant="WRONG_VARIANT")),
        ("x_empty", _set(header, source, x="")),
        ("y_invalid_outcome", _set(
            header, source, outcome="WRONG_OUTCOME"
        )),
    ]
    if core == "CORE-2":
        relation_plots = {
            "ablation", "ablation_gain_loss", "dependency_applicability",
        }
        mutations.append((
            "relation",
            _set(
                header, source,
                relation=(
                    "WRONG_RELATION" if plot_type in relation_plots
                    else "UNEXPECTED_RELATION"
                ),
            ),
        ))

    x_invalid = {
        "certification_outcome_matrix": "2",
        "loc_vs_cw_scatter": "-1",
        "response_reduction_distribution": "1/2",
        "ablation": "-1",
        "ablation_gain_loss": "-1",
        "dependency_applicability": "-1",
    }.get(plot_type, "-1")
    y_invalid = {
        "runtime": "-1",
        "variant_runtime": "-1",
        "loc_vs_cw_scatter": "-1",
        "response_reduction_distribution": "2",
        "first_failed_priority": "-1",
        "envelope_search_cost": "1/2",
        "ablation": "1/2",
        "ablation_gain_loss": "2",
    }.get(plot_type, "2")
    mutations.extend((
        ("x_domain", _set(header, source, x=x_invalid)),
        ("y_domain", _set(header, source, y=y_invalid)),
    ))
    if plot_type != "first_failed_priority":
        mutations.append(("y_empty", _set(header, source, y="")))

    association = {
        "certification_ratio": {"outcome": "TIMEOUT", "y": "1"},
        "certification_ratio_e0": {"outcome": "TIMEOUT", "y": "1"},
        "variant_certification": {"outcome": "TIMEOUT", "y": "1"},
        "timeout_rate": {"outcome": "TIMEOUT", "y": "0"},
        "certification_outcome_matrix": {"x": "0", "y": "1", "outcome": "00"},
        "loc_vs_cw_scatter": {"x": "10", "y": "5", "outcome": "VIOLATION"},
        "response_reduction_distribution": {"x": "1", "outcome": "EQUAL"},
        "first_failed_priority": {"outcome": "COMPLETED", "y": "0"},
        "ablation": {"y": "1", "outcome": "EQUAL"},
        "ablation_gain_loss": {"y": "1", "outcome": "LOSS"},
        "dependency_applicability": {"y": "1", "outcome": "INVALID"},
    }.get(plot_type)
    if association is not None:
        mutations.append(("association", _set(header, source, **association)))
    return mutations


@pytest.mark.parametrize(
    ("name", "core"), (("core1", "CORE-1"), ("core2", "CORE-2")),
)
def test_round8_all_plot_types_reject_impossible_row_semantics_before_writes(
    tmp_path, monkeypatch, name, core,
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    root = formal / name
    physical = _physical_csv(root / f"{name}_plot_data.csv")
    header = physical[0]
    by_type = {}
    for row in physical[1:]:
        by_type.setdefault(row[header.index("plot")], row)
    assert len(by_type) == 7

    for plot_type, source in sorted(by_type.items()):
        for case, changed in _semantic_mutations(core, plot_type, header, source):
            case_root = tmp_path / "plot-mutations" / name / plot_type / case
            data_path = case_root / f"{name}_plot_data.csv"
            _write_physical(data_path, (header, changed))
            output_dir = case_root / "plots"
            with pytest.raises(PlotTableError, match="plot schema"):
                validate_canonical_plot_table(data_path, expected_core=core)
            with pytest.raises(PlotTableError, match="plot schema"):
                render_plot_data(data_path, root / "run_config.yaml", output_dir)
            assert not output_dir.exists(), f"{name}:{plot_type}:{case}"
            assert not any(
                path.suffix in {".png", ".pdf", ".tmp"}
                for path in case_root.rglob("*") if path.is_file()
            )


def _verified_manifest(root: Path, source: Path, *, mode: int = 0o644) -> Path:
    content = source.read_bytes()
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": VERIFIED_PACKAGE_MANIFEST_SCHEMA,
        "schema_version": VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION,
        "archive_schema": PACKAGE_ARCHIVE_SCHEMA,
        "archive_schema_version": PACKAGE_ARCHIVE_SCHEMA_VERSION,
        "output_root": str((root / "source").resolve()),
        "profile": "formal",
        "experiments": ["core1"],
        "inventory_schemas": [["TEST_INVENTORY_V1", 1]],
        "entries": [{
            "relative_path": "core1/summary.json",
            "file_type": "regular",
            "sha256": hashlib.sha256(content).hexdigest(),
            "mode": mode,
            "size": len(content),
            "category": "authoritative_derived",
            "package": True,
            "inventory_schema": "TEST_INVENTORY_V1",
            "inventory_schema_version": 1,
            "authoritative_scientific_content": True,
        }],
    }, sort_keys=True), encoding="utf-8")
    return manifest


def test_round8_package_rejects_source_mode_change(tmp_path):
    source_root = tmp_path / "source"
    source = source_root / "core1" / "summary.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"authoritative\n")
    source.chmod(0o644)
    manifest = _verified_manifest(tmp_path, source)
    source.chmod(0o600)
    with pytest.raises(RuntimeError, match="mode"):
        package_inventory.copy_verified_inventory(
            manifest, source_root, tmp_path / "destination"
        )


def test_round8_package_rejects_type_swap_at_copy_boundary(
    tmp_path, monkeypatch,
):
    source_root = tmp_path / "source"
    source = source_root / "core1" / "summary.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"authoritative\n")
    source.chmod(0o644)
    alternate = tmp_path / "same-content"
    alternate.write_bytes(source.read_bytes())
    manifest = _verified_manifest(tmp_path, source)

    future_open = getattr(package_inventory, "_open_source_fd", None)
    if future_open is not None:
        def swap_then_open(root, relative):
            source.unlink()
            source.symlink_to(alternate)
            return future_open(root, relative)
        monkeypatch.setattr(package_inventory, "_open_source_fd", swap_then_open)
    else:
        real_copy = package_inventory.shutil.copy2
        def swap_then_copy(src, destination, *, follow_symlinks=True):
            Path(src).unlink()
            Path(src).symlink_to(alternate)
            return real_copy(src, destination, follow_symlinks=follow_symlinks)
        monkeypatch.setattr(package_inventory.shutil, "copy2", swap_then_copy)

    with pytest.raises(RuntimeError, match="regular|symlink|type"):
        package_inventory.copy_verified_inventory(
            manifest, source_root, tmp_path / "destination"
        )
    assert not (tmp_path / "final.tar.gz").exists()
    assert not (tmp_path / "final.tar.gz.sha256").exists()


def test_round8_authoritative_archive_api_exists():
    assert hasattr(package_inventory, "package_verified_inventory")


COMMIT_SHA = "6aa9d7196bcecf2896f6436bda8f32e8405a1521"


def _package_case(tmp_path):
    source_root = tmp_path / "source"
    source = source_root / "core1" / "summary.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"authoritative\n")
    source.chmod(0o640)
    manifest = _verified_manifest(tmp_path, source, mode=0o640)
    archive = tmp_path / "published" / "results.tar.gz"
    archive_sha = archive.with_suffix(archive.suffix + ".sha256")
    return source_root, source, manifest, archive, archive_sha


def _tree_snapshot(root: Path):
    output = []
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted((*directories, *files)):
            path = current_path / name
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(metadata.st_mode):
                detail = ("symlink", os.readlink(path))
            elif stat.S_ISREG(metadata.st_mode):
                detail = ("regular", hashlib.sha256(path.read_bytes()).hexdigest())
            elif stat.S_ISDIR(metadata.st_mode):
                detail = ("directory", None)
            else:
                detail = ("other", None)
            output.append((relative, stat.S_IMODE(metadata.st_mode), detail))
    return tuple(output)


def _package(source_root, manifest, archive, archive_sha):
    return package_inventory.package_verified_inventory(
        manifest, source_root, archive, archive_sha, COMMIT_SHA,
    )


def test_round8_package_normal_regular_file_manifest_and_tar_are_exact(tmp_path):
    source_root, source, manifest, archive, archive_sha = _package_case(tmp_path)
    before = _tree_snapshot(source_root)
    result = _package(source_root, manifest, archive, archive_sha)
    assert _tree_snapshot(source_root) == before
    assert result["entry_count"] == 1
    assert archive.is_file() and archive_sha.is_file()
    expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert archive_sha.read_text(encoding="utf-8") == (
        f"{expected_digest}  {archive.name}\n"
    )
    archive_manifest = Path(str(archive) + ".manifest.json")
    assert package_inventory.verify_package_archive(
        archive, archive_manifest,
    ) == (
        "package/commit_sha.txt",
        "package/package_manifest.json",
        "package/results/core1/summary.json",
    )
    value = json.loads(manifest.read_text(encoding="utf-8"))
    entry = value["entries"][0]
    assert entry == {
        "relative_path": "core1/summary.json",
        "file_type": "regular",
        "mode": 0o640,
        "size": len(b"authoritative\n"),
        "sha256": hashlib.sha256(b"authoritative\n").hexdigest(),
        "category": "authoritative_derived",
        "package": True,
        "inventory_schema": "TEST_INVENTORY_V1",
        "inventory_schema_version": 1,
        "authoritative_scientific_content": True,
    }
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        assert [member.name for member in members] == [
            "package/package_manifest.json",
            "package/commit_sha.txt",
            "package/results/core1/summary.json",
        ]
        assert all(member.isreg() for member in members)
        result_member = members[-1]
        assert result_member.mode == entry["mode"]
        assert result_member.size == entry["size"]
        assert hashlib.sha256(
            handle.extractfile(result_member).read()
        ).hexdigest() == entry["sha256"]


@pytest.mark.parametrize(
    "mutation",
    ("symlink_before", "content", "size", "type", "intermediate_symlink"),
)
def test_round8_package_rejects_changed_source_and_preserves_output_root(
    tmp_path, mutation,
):
    source_root, source, manifest, archive, archive_sha = _package_case(tmp_path)
    if mutation == "symlink_before":
        alternate = tmp_path / "alternate"
        alternate.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(alternate)
    elif mutation == "content":
        source.write_bytes(b"Authoritative\n")  # same size, different digest
        source.chmod(0o640)
    elif mutation == "size":
        source.write_bytes(b"authoritative changed size\n")
        source.chmod(0o640)
    elif mutation == "type":
        source.unlink()
        source.mkdir()
    elif mutation == "intermediate_symlink":
        original_parent = source.parent
        moved_parent = tmp_path / "real-core1"
        original_parent.rename(moved_parent)
        original_parent.symlink_to(moved_parent, target_is_directory=True)
    before = _tree_snapshot(source_root)
    with pytest.raises(RuntimeError):
        _package(source_root, manifest, archive, archive_sha)
    assert _tree_snapshot(source_root) == before
    assert not archive.exists()
    assert not archive_sha.exists()


@pytest.mark.parametrize("kind", ("symlink", "non_regular"))
def test_round8_package_rejects_preexisting_destination_objects(
    tmp_path, kind,
):
    source_root, _source, manifest, archive, archive_sha = _package_case(tmp_path)
    destination = tmp_path / "destination"
    target = destination / "core1" / "summary.json"
    target.parent.mkdir(parents=True)
    if kind == "symlink":
        target.symlink_to(tmp_path / "nowhere")
    else:
        target.mkdir()
    before = _tree_snapshot(source_root)
    with pytest.raises(RuntimeError):
        package_inventory.copy_verified_inventory(
            manifest, source_root, destination,
        )
    assert _tree_snapshot(source_root) == before
    assert not archive.exists()
    assert not archive_sha.exists()


@pytest.mark.parametrize("mutation", ("mode", "sha"))
def test_round8_package_rejects_post_copy_destination_mismatch(
    tmp_path, monkeypatch, mutation,
):
    source_root, _source, manifest, archive, archive_sha = _package_case(tmp_path)
    destination = tmp_path / "destination"
    real_verify = package_inventory._verify_destination

    def mutate_then_verify(parent_descriptor, name, entry):
        if mutation == "mode":
            os.chmod(name, 0o600, dir_fd=parent_descriptor)
        else:
            descriptor = os.open(name, os.O_WRONLY, dir_fd=parent_descriptor)
            try:
                os.write(descriptor, b"Authoritative\n")
            finally:
                os.close(descriptor)
            os.chmod(name, 0o640, dir_fd=parent_descriptor)
        return real_verify(parent_descriptor, name, entry)

    monkeypatch.setattr(package_inventory, "_verify_destination", mutate_then_verify)
    before = _tree_snapshot(source_root)
    with pytest.raises(RuntimeError, match="destination"):
        package_inventory.copy_verified_inventory(
            manifest, source_root, destination,
        )
    assert _tree_snapshot(source_root) == before
    assert not destination.exists()
    assert not archive.exists()
    assert not archive_sha.exists()


def _rewrite_tar(path: Path, mutation: str) -> None:
    with tarfile.open(path, "r:gz") as source:
        records = []
        for member in source.getmembers():
            extracted = source.extractfile(member) if member.isreg() else None
            records.append((copy.copy(member), extracted.read() if extracted else b""))
    result_index = next(
        index for index, (member, _content) in enumerate(records)
        if member.name.startswith("package/results/")
    )
    member, content = records[result_index]
    if mutation == "symlink":
        member.type = tarfile.SYMTYPE
        member.linkname = "elsewhere"
        member.size = 0
        records[result_index] = (member, b"")
    elif mutation == "mode":
        member.mode ^= 0o040
    elif mutation == "content":
        records[result_index] = (member, b"Authoritative\n")
    elif mutation == "extra":
        extra = tarfile.TarInfo("package/results/extra")
        extra.type = tarfile.REGTYPE
        extra.mode = 0o644
        extra.size = 1
        records.append((extra, b"x"))
    elif mutation == "missing":
        records.pop(result_index)
    elif mutation == "duplicate":
        records.append((copy.copy(member), content))
    else:  # pragma: no cover - test helper guard
        raise AssertionError(mutation)
    with tarfile.open(path, "w:gz") as destination:
        for changed, data in records:
            destination.addfile(
                changed, io.BytesIO(data) if changed.isreg() else None,
            )


@pytest.mark.parametrize(
    "mutation", ("symlink", "mode", "content", "extra", "missing", "duplicate"),
)
def test_round8_archive_verifier_rejects_member_divergence(tmp_path, mutation):
    source_root, _source, manifest, archive, archive_sha = _package_case(tmp_path)
    _package(source_root, manifest, archive, archive_sha)
    _rewrite_tar(archive, mutation)
    with pytest.raises(RuntimeError, match="archive|member"):
        package_inventory.verify_package_archive(
            archive, Path(str(archive) + ".manifest.json"),
        )


@pytest.mark.parametrize("failure", ("archive_verify", "sha_publish"))
def test_round8_package_failure_leaves_no_final_tar_or_sha(
    tmp_path, monkeypatch, failure,
):
    source_root, _source, manifest, archive, archive_sha = _package_case(tmp_path)
    before = _tree_snapshot(source_root)
    if failure == "archive_verify":
        def reject_archive(*_args, **_kwargs):
            raise RuntimeError("injected archive verification failure")
        monkeypatch.setattr(
            package_inventory, "verify_package_archive", reject_archive,
        )
    else:
        real_publish = package_inventory._publish_exclusive
        calls = 0

        def reject_sha_publish(root_descriptor, temporary, final):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected SHA publication failure")
            return real_publish(root_descriptor, temporary, final)

        monkeypatch.setattr(
            package_inventory, "_publish_exclusive", reject_sha_publish,
        )
    with pytest.raises(RuntimeError):
        _package(source_root, manifest, archive, archive_sha)
    assert _tree_snapshot(source_root) == before
    assert not archive.exists()
    assert not archive_sha.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("file_type", "symlink"),
        ("relative_path", "../escape"),
        ("relative_path", "/absolute"),
    ),
)
def test_round8_package_manifest_rejects_unauthorized_type_or_path(
    tmp_path, field, value,
):
    source_root, _source, manifest, archive, archive_sha = _package_case(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["entries"][0][field] = value
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError):
        _package(source_root, manifest, archive, archive_sha)
    assert not archive.exists()
    assert not archive_sha.exists()


def test_round8_package_manifest_rejects_duplicate_relative_path(tmp_path):
    source_root, _source, manifest, archive, archive_sha = _package_case(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["entries"].append(copy.deepcopy(data["entries"][0]))
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicate"):
        _package(source_root, manifest, archive, archive_sha)
    assert not archive.exists()
    assert not archive_sha.exists()
