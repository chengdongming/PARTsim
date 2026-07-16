from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import tarfile

import pytest

from experiments.v9_3.aggregation import (
    aggregate_core1, aggregate_core2, validate_run_closure_read_only,
)
from experiments.v9_3.derived_outputs import (
    DerivedOutputError, build_core1_derived_outputs, build_core2_derived_outputs,
)
from experiments.v9_3.output_inventory import (
    ADMINISTRATIVE_EVIDENCE, AUTHORITATIVE_DERIVED, AUTHORITATIVE_RAW,
    OPTIONAL_PRESENTATION, inventory_for_closure,
)
from experiments.v9_3.plot_cli import render_plot_data
from experiments.v9_3.result_writer import write_file_hashes
from test_v9_3_deployment_verify_outputs import (
    _canonical_bundle_bytes, _formal_bundle, _physical_csv, _restore, _snapshot,
    _write_physical_csv,
)
from deployment.autodl.verify_outputs import verify_core12_output


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_RESULTS = PROJECT_ROOT / "deployment" / "autodl" / "package_results.sh"


def _permuted_closures(closure):
    row_fields = (
        "cells", "requests", "attempts", "tasksets", "tasks",
        "dependencies", "dominance", "failures",
    )
    yield "deep_copy", copy.deepcopy(closure)
    for field in row_fields:
        yield f"reverse_{field}", replace(
            copy.deepcopy(closure),
            **{field: tuple(reversed(getattr(closure, field)))},
        )
    yield "reverse_variants", replace(
        copy.deepcopy(closure),
        requests=tuple(sorted(
            closure.requests,
            key=lambda row: (row["variant"], row["analysis_id"]),
            reverse=True,
        )),
        tasksets=tuple(sorted(
            closure.tasksets,
            key=lambda row: (row["analysis_variant"], row["analysis_id"]),
            reverse=True,
        )),
        tasks=tuple(sorted(
            closure.tasks,
            key=lambda row: (
                row["analysis_variant"], row["analysis_id"], row["task_id"],
            ),
            reverse=True,
        )),
    )
    for seed in range(20):
        rng = random.Random(seed)
        changes = {}
        for field in row_fields:
            values = list(copy.deepcopy(getattr(closure, field)))
            rng.shuffle(values)
            changes[field] = tuple(values)
        yield f"seed_{seed}", replace(copy.deepcopy(closure), **changes)


@pytest.mark.parametrize(
    ("name", "core", "builder"),
    (
        ("core1", "CORE-1", build_core1_derived_outputs),
        ("core2", "CORE-2", build_core2_derived_outputs),
    ),
)
def test_round7_real_closure_all_row_orders_produce_identical_full_bundle(
    tmp_path, monkeypatch, name, core, builder
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    closure = validate_run_closure_read_only(formal / name, core)
    expected = builder(closure)
    expected_bytes = _canonical_bundle_bytes(expected)
    assert len(expected.tables) == 7
    for label, candidate in _permuted_closures(closure):
        actual = builder(candidate)
        assert actual.summary == expected.summary, label
        assert [
            (table.filename, table.columns, table.rows) for table in actual.tables
        ] == [
            (table.filename, table.columns, table.rows) for table in expected.tables
        ], label
        assert actual.table(f"{name}_plot_data.csv").rows == expected.table(
            f"{name}_plot_data.csv"
        ).rows, label
        assert _canonical_bundle_bytes(actual) == expected_bytes, label


@pytest.mark.parametrize(
    ("name", "core", "builder"),
    (
        ("core1", "CORE-1", build_core1_derived_outputs),
        ("core2", "CORE-2", build_core2_derived_outputs),
    ),
)
def test_round7_persisted_raw_csv_row_order_is_a_legal_closure_order(
    tmp_path, monkeypatch, name, core, builder
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    root = formal / name
    baseline = _snapshot(root)
    expected = _canonical_bundle_bytes(
        builder(validate_run_closure_read_only(root, core))
    )
    for filename in (
        "cells.csv", "analysis_requests.csv", "analysis_attempts.csv",
        "per_taskset_results.csv", "per_task_results.csv",
        "dependency_records.csv", "dominance_checks.csv",
    ):
        _restore(root, baseline)
        physical = _physical_csv(root / filename)
        physical[1:] = reversed(physical[1:])
        _write_physical_csv(root / filename, physical)
        closure = validate_run_closure_read_only(root, core)
        assert _canonical_bundle_bytes(builder(closure)) == expected, filename


@pytest.mark.parametrize("invalid", (float("nan"), float("inf"), float("-inf"), -1.0, -0.0, True))
def test_round7_builder_rejects_invalid_runtime_at_boundary(
    tmp_path, monkeypatch, invalid
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    closure = validate_run_closure_read_only(formal / "core2", "CORE-2")
    rows = [dict(row) for row in closure.tasksets]
    rows[0]["runtime_wall_seconds"] = invalid
    with pytest.raises(DerivedOutputError, match="runtime|finite|bool"):
        build_core2_derived_outputs(replace(closure, tasksets=tuple(rows)))


@pytest.mark.parametrize(
    ("name", "core", "builder"),
    (
        ("core1", "CORE-1", build_core1_derived_outputs),
        ("core2", "CORE-2", build_core2_derived_outputs),
    ),
)
def test_round7_inventory_has_all_four_categories_and_exact_dynamic_ids(
    tmp_path, monkeypatch, name, core, builder
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    closure = validate_run_closure_read_only(formal / name, core)
    derived = builder(closure)
    inventory = inventory_for_closure(core, closure, derived.required_paths)
    categories = {entry.category for entry in inventory.entries}
    assert categories == {
        AUTHORITATIVE_RAW, AUTHORITATIVE_DERIVED, OPTIONAL_PRESENTATION,
        ADMINISTRATIVE_EVIDENCE,
    }
    assert inventory.derived_paths == frozenset(derived.required_paths)
    assert len(inventory.optional_presentation_paths) == 14
    for request in closure.requests:
        assert f"terminal_results/{request['analysis_id']}.json" in inventory.required_paths
    for result in closure.tasksets:
        expected = f"result_state/{result['analysis_id']}.pickle"
        assert (expected in inventory.required_paths) == (
            result["terminal_origin"] == "PRODUCTION_ANALYZER"
        )


LEGACY_CASES = (
    "summary_v0", "summary_old", "core1_plot_v0", "core2_plot_v0",
    "unknown_comparison", "unknown_ablation", "unknown_plot_png",
    "unknown_plot_pdf", "temporary", "hidden", "old_header",
    "wrong_extension", "unknown_hashed", "unknown_unhashed",
)


def _add_legacy_artifact(root: Path, case: str) -> None:
    names = {
        "summary_v0": "summary_v0.json",
        "summary_old": "summary.old.json",
        "core1_plot_v0": "core1_plot_data_v0.csv",
        "core2_plot_v0": "core2_plot_data_v0.csv",
        "unknown_comparison": "unknown_comparison.csv",
        "unknown_ablation": "unknown_ablation.csv",
        "temporary": ".summary.json.123.tmp",
        "hidden": ".unknown_derived",
        "wrong_extension": "summary.txt",
        "unknown_hashed": "unknown_extra.json",
        "unknown_unhashed": "unknown_extra.json",
    }
    if case == "unknown_plot_png" or case == "unknown_plot_pdf":
        extension = "png" if case.endswith("png") else "pdf"
        path = root / "plots" / f"unknown_plot.{extension}"
    elif case == "old_header":
        physical = _physical_csv(root / "summary.csv")
        physical[0][0] = "legacy_cell"
        _write_physical_csv(root / "summary.csv", physical)
        return
    else:
        path = root / names[case]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"legacy\n")
    if case != "unknown_unhashed":
        write_file_hashes(root)


def _package(formal: Path, package_dir: Path):
    environment = os.environ.copy()
    environment.update({
        "PARTSIM_ROOT": str(PROJECT_ROOT),
        "PARTSIM_OUTPUT_ROOT": str(formal),
        "PARTSIM_PACKAGE_DIR": str(package_dir),
        "PARTSIM_RUN_MODE": "formal",
    })
    return subprocess.run(
        ["bash", str(PACKAGE_RESULTS)], cwd=PROJECT_ROOT, env=environment,
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("case", LEGACY_CASES)
def test_round7_legacy_or_unknown_artifacts_fail_closed_everywhere(
    tmp_path, monkeypatch, case
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    root = formal / "core2"
    _add_legacy_artifact(root, case)
    before_root = _snapshot(root)
    with pytest.raises(RuntimeError):
        aggregate_core2(root)
    assert _snapshot(root) == before_root
    with pytest.raises(RuntimeError):
        verify_core12_output("core2", root)
    assert _snapshot(root) == before_root

    before_bundle = _snapshot(formal)
    package_dir = tmp_path / "packages"
    result = _package(formal, package_dir)
    assert result.returncode != 0, case
    assert _snapshot(formal) == before_bundle
    assert not list(package_dir.glob("*.tar.gz")) if package_dir.exists() else True


def test_round7_reaggregation_and_package_are_exact_inventory_only(
    tmp_path, monkeypatch
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    before = _snapshot(formal)
    aggregate_core1(formal / "core1")
    aggregate_core2(formal / "core2")
    assert _snapshot(formal) == before

    for name in ("core1", "core2"):
        render_plot_data(
            formal / name / f"{name}_plot_data.csv",
            formal / name / "run_config.yaml",
            formal / name / "plots",
        )
        verify_core12_output(name, formal / name)

    package_dir = tmp_path / "packages"
    result = _package(formal, package_dir)
    assert result.returncode == 0, result.stderr
    archives = list(package_dir.glob("*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0], "r:gz") as archive:
        manifest = json.load(archive.extractfile("package/package_manifest.json"))
        expected = {
            entry["relative_path"]: entry for entry in manifest["entries"]
        }
        actual = {
            member.name[len("package/results/"):]: member
            for member in archive.getmembers()
            if member.isfile() and member.name.startswith("package/results/")
        }
        assert set(actual) == set(expected)
        assert not any("unknown" in path or "_v0" in path for path in actual)
        for relative, entry in expected.items():
            member = actual[relative]
            content = archive.extractfile(member).read()
            assert hashlib.sha256(content).hexdigest() == entry["sha256"]
            assert member.mode == entry["mode"]


PLOT_MUTATIONS = (
    "unknown_plot", "old_header", "missing_task_id", "extra_column",
    "swapped_columns", "duplicate_primary_key", "illegal_taskset_task_id",
    "missing_task_level_task_id", "whitespace", "row_order",
    "filename_mismatch", "core_mismatch",
)


def _mutate_plot_table(physical, core: str, case: str):
    changed = copy.deepcopy(physical)
    header = changed[0]
    if case == "unknown_plot":
        changed[1][header.index("plot")] = "unknown_plot"
    elif case == "old_header":
        changed[0][header.index("task_id")] = "legacy_task"
    elif case == "missing_task_id":
        index = header.index("task_id")
        for row in changed:
            row.pop(index)
    elif case == "extra_column":
        changed[0].append("unknown")
        for row in changed[1:]:
            row.append("")
    elif case == "swapped_columns":
        for row in changed:
            row[0], row[1] = row[1], row[0]
    elif case == "duplicate_primary_key":
        changed.append(copy.deepcopy(changed[1]))
    elif case == "illegal_taskset_task_id":
        task_plots = (
            {"loc_vs_cw_scatter", "response_reduction_distribution"}
            if core == "CORE-1" else {"ablation"}
        )
        row = next(row for row in changed[1:] if row[header.index("plot")] not in task_plots)
        row[header.index("task_id")] = "1"
    elif case == "missing_task_level_task_id":
        task_plots = (
            {"loc_vs_cw_scatter", "response_reduction_distribution"}
            if core == "CORE-1" else {"ablation"}
        )
        row = next(row for row in changed[1:] if row[header.index("plot")] in task_plots)
        row[header.index("task_id")] = ""
    elif case == "whitespace":
        changed[1][header.index("cell_id")] += " "
    elif case == "row_order":
        changed[1], changed[2] = changed[2], changed[1]
    return changed


@pytest.mark.parametrize("name,core", (("core1", "CORE-1"), ("core2", "CORE-2")))
@pytest.mark.parametrize("case", PLOT_MUTATIONS)
def test_round7_plot_consumer_executes_shared_frozen_contract_before_writes(
    tmp_path, monkeypatch, name, core, case
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    root = formal / name
    source = _physical_csv(root / f"{name}_plot_data.csv")
    data_name = f"{name}_plot_data.csv"
    config = root / "run_config.yaml"
    if case == "filename_mismatch":
        data_name = "wrong_plot_data.csv"
    elif case == "core_mismatch":
        config = formal / ("core2" if name == "core1" else "core1") / "run_config.yaml"
    data_path = tmp_path / data_name
    _write_physical_csv(data_path, _mutate_plot_table(source, core, case))
    output_dir = tmp_path / f"plots-{name}-{case}"
    with pytest.raises(RuntimeError) as captured:
        render_plot_data(data_path, config, output_dir)
    assert "plot schema" in str(captured.value) or "plot type" in str(captured.value)
    assert not output_dir.exists()
