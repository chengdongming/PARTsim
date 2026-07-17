import copy
import csv
from dataclasses import replace
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.aggregation import aggregate_core1, aggregate_core2
from experiments.v9_3.aggregation import validate_run_closure_read_only
from experiments.v9_3.derived_outputs import (
    DerivedOutputBundle, build_core1_derived_outputs, build_core2_derived_outputs,
)
from experiments.v9_3.execution_engine import ExecutionEngine
from experiments.v9_3.config import canonical_json
from experiments.v9_3.plot_cli import render_plot_data
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS, DEPENDENCY_COLUMNS, DOMINANCE_COLUMNS,
    TASKSET_RESULT_COLUMNS, atomic_write_json, read_csv, write_csv,
    write_file_hashes,
)
from deployment.autodl.verify_outputs import verify_core12_output
from v9_3_experiment_helpers import (
    install_fake_materialization, make_config, successful_execution,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERIFY_OUTPUTS = PROJECT_ROOT / "deployment" / "autodl" / "verify_outputs.py"
TEST_COMMIT_SHA = "6aa9d7196bcecf2896f6436bda8f32e8405a1521"


def _formal_bundle(tmp_path, monkeypatch, *, commit_sha=None):
    if commit_sha is not None:
        monkeypatch.setattr(
            engine_module.ExecutionEngine,
            "_git_head",
            staticmethod(lambda: commit_sha),
        )
    bundle = tmp_path / "formal-bundle"
    for name, core, aggregate in (
        ("core1", "CORE-1", aggregate_core1),
        ("core2", "CORE-2", aggregate_core2),
    ):
        material = tmp_path / f"material-{name}"
        install_fake_materialization(monkeypatch, material)
        monkeypatch.setattr(
            engine_module,
            "execute_isolated",
            lambda request, timeout: successful_execution(request),
        )
        config = make_config(material, core)
        config["execution"]["output_root"] = str(bundle / name)
        outcome = ExecutionEngine(config).run()
        aggregate(outcome.output_root)
    return bundle


def _verify(bundle, experiment=None):
    selection = ["--experiment", experiment] if experiment else []
    return subprocess.run(
        [
            sys.executable,
            str(VERIFY_OUTPUTS),
            "--output-root",
            str(bundle),
            "--profile",
            "formal",
            *selection,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _snapshot(root):
    return {
        path.relative_to(root).as_posix(): (path.stat().st_mode & 0o777, path.read_bytes())
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _assert_direct_verifier_rejects(bundle, name, label):
    root = bundle / name
    before = _snapshot(root)
    with pytest.raises(RuntimeError):
        verify_core12_output(name, root)
    after = _snapshot(root)
    assert after == before, f"verify_outputs wrote files for {label}"


def _restore(root, snapshot):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and path.relative_to(root).as_posix() not in snapshot:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for relative, (mode, content) in snapshot.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)


def _taskset_columns():
    return (
        TASKSET_RESULT_COLUMNS
        if "failure_origin" in TASKSET_RESULT_COLUMNS
        else (*TASKSET_RESULT_COLUMNS, "failure_origin")
    )


def _sync_terminal_csv(root, changes, csv_changes=None):
    terminal_path = next((root / "terminal_results").glob("*.json"))
    payload = json.loads(terminal_path.read_text(encoding="utf-8"))
    analysis_id = payload["taskset_row"]["analysis_id"]
    payload["taskset_row"].update(changes)
    terminal_path.write_text(json.dumps(payload), encoding="utf-8")
    result_path = root / "per_taskset_results.csv"
    rows = read_csv(result_path)
    target = next(row for row in rows if row["analysis_id"] == analysis_id)
    target.update(csv_changes if csv_changes is not None else changes)
    write_csv(result_path, _taskset_columns(), rows)
    return analysis_id


def _mutate_case(bundle, case):
    core1 = bundle / "core1"
    core2 = bundle / "core2"
    if case == "certification":
        _sync_terminal_csv(
            core1,
            {"certification_status": "NOT_CERTIFIED"},
        )
        changed = core1
    elif case == "taskset_proven":
        _sync_terminal_csv(
            core1, {"taskset_proven": False}, {"taskset_proven": "False"}
        )
        changed = core1
    elif case == "counters":
        _sync_terminal_csv(
            core1,
            {
                "n_tasks_evaluated": 1,
                "n_tasks_candidate_found": 1,
                "n_tasks_certified": 1,
            },
            {
                "n_tasks_evaluated": "1",
                "n_tasks_candidate_found": "1",
                "n_tasks_certified": "1",
            },
        )
        changed = core1
    elif case == "json_bool":
        _sync_terminal_csv(
            core1, {"diagnostic_mode": True}, {"diagnostic_mode": "True"}
        )
        changed = core1
    elif case == "failure_origin":
        _sync_terminal_csv(
            core1, {"failure_origin": "WORKER_ERROR_PAYLOAD"}
        )
        changed = core1
    elif case == "final_attempt_origin":
        analysis_id = _sync_terminal_csv(
            core1, {"failure_origin": "ANALYZER_RESULT"}
        )
        path = core1 / "analysis_attempts.csv"
        rows = read_csv(path)
        target = [row for row in rows if row["analysis_id"] == analysis_id][-1]
        target["failure_origin"] = "WORKER_ERROR_PAYLOAD"
        write_csv(path, ATTEMPT_COLUMNS, rows)
        changed = core1
    elif case == "dependency":
        path = core2 / "dependency_records.csv"
        rows = read_csv(path)
        rows[0]["fallback_used"] = "True"
        write_csv(path, DEPENDENCY_COLUMNS, rows)
        changed = core2
    elif case == "dominance":
        path = core2 / "dominance_checks.csv"
        rows = read_csv(path)
        rows[0]["status"] = "VIOLATION"
        write_csv(path, DOMINANCE_COLUMNS, rows)
        changed = core2
    elif case == "state_science":
        _sync_terminal_csv(
            core1,
            {"fixed_carry_in_interface_status": "ACTIVE"},
        )
        changed = core1
    else:
        raise AssertionError(case)
    write_file_hashes(changed)


def _mutate_round6_derived_case(bundle, case):
    root = bundle / "core2"
    if case == "summary_enriched":
        path = root / "summary.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["variants"][0]["mean_candidate_task_count"] = 999
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    elif case == "dependency_summary":
        path = root / "core2_dependency_summary.csv"
        rows = read_csv(path)
        rows[0]["count"] = "999"
        write_csv(path, tuple(rows[0]), rows)
    elif case == "derived_plot":
        path = root / "core2_plot_data.csv"
        rows = read_csv(path)
        target = next(row for row in rows if row["plot"] == "ablation")
        target["y"] = "999"
        write_csv(path, tuple(rows[0]), rows)
    else:
        raise AssertionError(case)
    write_file_hashes(root)


def test_verify_outputs_accepts_complete_contract_valid_outputs(
    tmp_path, monkeypatch
):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    result = _verify(bundle)
    assert result.returncode == 0, result.stderr
    core2_only = _verify(bundle, "core2")
    assert core2_only.returncode == 0, core2_only.stderr


def test_verify_outputs_rejects_synchronized_mutations_after_rehash_without_writes(
    tmp_path, monkeypatch
):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    baseline = _snapshot(bundle)
    cases = (
        "certification",
        "taskset_proven",
        "counters",
        "json_bool",
        "failure_origin",
        "final_attempt_origin",
        "dependency",
        "dominance",
        "state_science",
    )
    for case in cases:
        _restore(bundle, baseline)
        _mutate_case(bundle, case)
        before = _snapshot(bundle)
        result = _verify(bundle)
        after = _snapshot(bundle)
        assert after == before, f"verify_outputs wrote files for {case}"
        assert result.returncode != 0, f"mutation passed verification: {case}"
        assert result.stderr or result.stdout


@pytest.mark.parametrize(
    "case", ("summary_enriched", "dependency_summary", "derived_plot")
)
def test_round6_verifier_rejects_derived_output_mutation_after_rehash(
    tmp_path, monkeypatch, case
):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    _mutate_round6_derived_case(bundle, case)
    before = _snapshot(bundle)
    result = _verify(bundle, "core2")
    after = _snapshot(bundle)
    assert after == before, f"verify_outputs wrote files for {case}"
    assert result.returncode != 0, f"derived mutation passed verification: {case}"


def _json_leaf_paths(value, path=()):
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _json_leaf_paths(value[key], path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _json_leaf_paths(item, path + (index,))
    else:
        yield path


def _json_parent(value, path):
    current = value
    for component in path[:-1]:
        current = current[component]
    return current, path[-1]


def _changed_json_scalar(value):
    if value is None:
        return "unexpected-non-null"
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    if type(value) is float:
        return value + 0.125
    if type(value) is str:
        return value + "-tampered"
    raise AssertionError(type(value))


@pytest.mark.parametrize(
    ("name", "expected_core", "builder"),
    (
        ("core1", "CORE-1", build_core1_derived_outputs),
        ("core2", "CORE-2", build_core2_derived_outputs),
    ),
)
def test_round6_verifier_rejects_every_formal_summary_leaf_and_structure(
    tmp_path, monkeypatch, name, expected_core, builder
):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    root = bundle / name
    baseline = _snapshot(bundle)
    closure = validate_run_closure_read_only(root, expected_core)
    canonical = builder(closure).summary
    persisted = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    assert persisted == canonical

    for path in _json_leaf_paths(canonical):
        _restore(bundle, baseline)
        value = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        parent, key = _json_parent(value, path)
        parent[key] = _changed_json_scalar(parent[key])
        atomic_write_json(root / "summary.json", value)
        write_file_hashes(root)
        _assert_direct_verifier_rejects(bundle, name, f"{name}:summary:{path}")

    structural = {}
    value = copy.deepcopy(canonical)
    value.pop("schema")
    structural["missing_field"] = value
    value = copy.deepcopy(canonical)
    value["unknown_extra_field"] = 1
    structural["extra_field"] = value
    value = copy.deepcopy(canonical)
    value["variants"].pop()
    structural["missing_variant"] = value
    value = copy.deepcopy(canonical)
    value["variants"].append(copy.deepcopy(value["variants"][0]))
    structural["duplicate_variant"] = value
    value = copy.deepcopy(canonical)
    value["variants"][0]["variant"] = "UNKNOWN_VARIANT"
    structural["wrong_variant"] = value
    value = copy.deepcopy(canonical)
    value["requested_count"] = str(value["requested_count"])
    structural["wrong_type"] = value
    value = copy.deepcopy(canonical)
    value["requested_count"] = True
    structural["bool_as_integer"] = value
    value = copy.deepcopy(canonical)
    value["certification_ratio_unconditional"] = 0.123456789
    structural["ratio_count_mismatch"] = value
    value = copy.deepcopy(canonical)
    if name == "core1":
        value["dependency_summary"] = []
    else:
        value["comparison_by_cell"] = []
    structural["cross_core_field"] = value
    value = copy.deepcopy(canonical)
    value["variants"][0].pop("runtime_mean")
    structural["missing_variant_field"] = value
    value = copy.deepcopy(canonical)
    value["variants"][0]["unknown_variant_field"] = 1
    structural["extra_variant_field"] = value

    for case, changed in structural.items():
        _restore(bundle, baseline)
        atomic_write_json(root / "summary.json", changed)
        write_file_hashes(root)
        _assert_direct_verifier_rejects(bundle, name, f"{name}:summary:{case}")


def _wrong_json_type(value):
    if value is None:
        return 0
    if type(value) is bool:
        return 0
    if type(value) is int:
        return 0.5
    if type(value) is float:
        return 0
    if type(value) is str:
        return []
    raise AssertionError(type(value))


@pytest.mark.parametrize(
    ("name", "expected_core", "builder"),
    (
        ("core1", "CORE-1", build_core1_derived_outputs),
        ("core2", "CORE-2", build_core2_derived_outputs),
    ),
)
def test_round7_verifier_rejects_wrong_type_for_every_real_summary_leaf_and_json_edges(
    tmp_path, monkeypatch, name, expected_core, builder
):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    root = bundle / name
    baseline = _snapshot(bundle)
    canonical = builder(
        validate_run_closure_read_only(root, expected_core)
    ).summary

    for path in _json_leaf_paths(canonical):
        _restore(bundle, baseline)
        changed = copy.deepcopy(canonical)
        parent, key = _json_parent(changed, path)
        parent[key] = _wrong_json_type(parent[key])
        atomic_write_json(root / "summary.json", changed)
        write_file_hashes(root)
        _assert_direct_verifier_rejects(
            bundle, name, f"{name}:summary:wrong_type:{path}"
        )

    structural = []
    changed = copy.deepcopy(canonical)
    changed["requested_count"] = True
    structural.append(("int_to_bool", changed, None))
    changed = copy.deepcopy(canonical)
    bool_path = next(
        path for path in _json_leaf_paths(canonical)
        if type(_json_parent(canonical, path)[0][_json_parent(canonical, path)[1]]) is bool
    )
    parent, key = _json_parent(changed, bool_path)
    parent[key] = 1
    structural.append(("bool_to_int", changed, None))
    changed = copy.deepcopy(canonical)
    changed["requested_count"] = None
    structural.append(("nonnull_to_null", changed, None))
    changed = copy.deepcopy(canonical)
    changed["variants"] = list(reversed(changed["variants"]))
    structural.append(("list_order", changed, None))
    changed = copy.deepcopy(canonical)
    changed["runtime_summary"] = []
    structural.append(("dict_structure", changed, None))
    for label, constant in (
        ("NaN", float("nan")),
        ("Infinity", float("inf")),
        ("minus_Infinity", float("-inf")),
        ("negative_zero", -0.0),
    ):
        changed = copy.deepcopy(canonical)
        changed["certification_ratio_unconditional"] = constant
        structural.append((label, changed, "noncanonical"))

    for label, changed, raw_mode in structural:
        _restore(bundle, baseline)
        if raw_mode:
            (root / "summary.json").write_text(
                json.dumps(changed, allow_nan=True, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            atomic_write_json(root / "summary.json", changed)
        write_file_hashes(root)
        _assert_direct_verifier_rejects(
            bundle, name, f"{name}:summary:{label}"
        )

    _restore(bundle, baseline)
    valid_text = json.dumps(canonical, sort_keys=True)
    duplicate = '{"schema":"duplicate",' + valid_text.lstrip()[1:]
    (root / "summary.json").write_text(duplicate + "\n", encoding="utf-8")
    write_file_hashes(root)
    _assert_direct_verifier_rejects(
        bundle, name, f"{name}:summary:duplicate_key"
    )


def _physical_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def _write_physical_csv(path, physical):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(physical)


def _select_column(header, candidates, excluded=()):
    for candidate in candidates:
        for index, column in enumerate(header):
            if candidate in column and column not in excluded:
                return index
    for index, column in enumerate(header):
        if column not in excluded:
            return index
    raise AssertionError("table has no selectable column")


def _changed_csv_scalar(value):
    if value == "":
        return "unexpected"
    if value == "True":
        return "False"
    if value == "False":
        return "True"
    try:
        return str(float(value) + 0.25)
    except ValueError:
        return value + "-tampered"


def _csv_mutations(physical, table):
    header = physical[0]
    key_columns = set(table.primary_key)

    def field_case(label, candidates, excluded=key_columns, replacement=None):
        changed = copy.deepcopy(physical)
        index = _select_column(header, candidates, excluded)
        changed[1][index] = (
            replacement if replacement is not None
            else _changed_csv_scalar(changed[1][index])
        )
        return label, changed

    cases = [
        field_case("count", ("count", "denominator", "certified")),
        field_case("ratio", ("ratio", "normalized", "mean")),
        field_case("candidate", ("candidate", "reduction")),
        field_case("runtime", ("runtime", "seconds", "status")),
        field_case("search_cost", ("checked_", "envelope", "priority")),
        field_case("cell_id", ("cell_id",), excluded=()),
        field_case("taskset_id", ("taskset_id",), excluded=()),
        field_case("e0", ("exact_e0", "source_e0", "target_e0", "x"), excluded=()),
        field_case("variant", ("variant", "left_variant", "right_variant"), excluded=()),
    ]
    changed = copy.deepcopy(physical)
    changed.pop(1)
    cases.append(("missing_row", changed))
    changed = copy.deepcopy(physical)
    changed.append(copy.deepcopy(changed[1]))
    cases.append(("duplicate_row", changed))
    changed = copy.deepcopy(physical)
    extra = copy.deepcopy(changed[1])
    key_index = header.index(table.primary_key[0])
    extra[key_index] = "EXTRA_PRIMARY_KEY"
    changed.append(extra)
    cases.append(("extra_row", changed))
    changed = copy.deepcopy(physical)
    changed[0][0] = "unknown_header"
    cases.append(("header", changed))
    cases.append(field_case("bool_as_integer", ("count", "denominator", "candidate", "reduction"), replacement="True"))
    changed = copy.deepcopy(physical)
    changed[1] = changed[1][:-1]
    cases.append(("row_width", changed))
    if table.row_type_column is not None:
        changed = copy.deepcopy(physical)
        changed[1][header.index(table.row_type_column)] = "UNKNOWN_ROW_TYPE"
        cases.append(("unknown_row_type", changed))
    else:
        changed = copy.deepcopy(physical)
        if len(changed) > 2:
            for column in table.primary_key:
                index = header.index(column)
                changed[2][index] = changed[1][index]
        else:
            changed.append(copy.deepcopy(changed[1]))
        cases.append(("primary_key_conflict", changed))

    changed = copy.deepcopy(physical)
    if len(changed) == 2:
        extra = copy.deepcopy(changed[1])
        extra[header.index(table.primary_key[0])] = "ZZZ_EXTRA_KEY"
        changed.append(extra)
    changed[1], changed[2] = changed[2], changed[1]
    cases.append(("swapped_rows", changed))

    changed = copy.deepcopy(physical)
    changed[0].append("unknown_extra_column")
    for row in changed[1:]:
        row.append("")
    cases.append(("unknown_column", changed))

    changed = copy.deepcopy(physical)
    delete_index = len(changed[0]) - 1
    for row in changed:
        row.pop(delete_index)
    cases.append(("deleted_column", changed))

    changed = copy.deepcopy(physical)
    blank = next(
        (
            (row_index, column_index)
            for row_index, row in enumerate(changed[1:], start=1)
            for column_index, value in enumerate(row)
            if value == ""
        ),
        (1, _select_column(header, ("runtime", "mean", "count"))),
    )
    changed[blank[0]][blank[1]] = "None"
    cases.append(("empty_to_None", changed))

    changed = copy.deepcopy(physical)
    bool_cell = next(
        (
            (row_index, column_index)
            for row_index, row in enumerate(changed[1:], start=1)
            for column_index, value in enumerate(row)
            if value == "True"
        ),
        None,
    )
    if bool_cell is None:
        bool_index = _select_column(
            header, ("certified", "dominance_expected", "gain", "loss"),
            excluded=key_columns,
        )
        bool_cell = (1, bool_index)
    original = changed[bool_cell[0]][bool_cell[1]]
    changed[bool_cell[0]][bool_cell[1]] = "1" if original != "1" else "0"
    cases.append(("True_to_1", changed))

    changed = copy.deepcopy(physical)
    numeric_index = _select_column(
        header, ("count", "priority", "runtime", "x", "y"),
        excluded=key_columns,
    )
    changed[1][numeric_index] = "0" + (changed[1][numeric_index] or "0")
    cases.append(("leading_zero", changed))

    changed = copy.deepcopy(physical)
    space_index = _select_column(header, ("cell_id", "variant"), excluded=())
    changed[1][space_index] += " "
    cases.append(("surrounding_space", changed))

    changed = copy.deepcopy(physical)
    for row in changed:
        row[0], row[1] = row[1], row[0]
    cases.append(("column_order", changed))

    assert len(cases) == 24
    return cases


def test_round6_verifier_rejects_full_formal_csv_mutation_matrix(
    tmp_path, monkeypatch
):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    baseline = _snapshot(bundle)
    specifications = (
        (
            "core1", "CORE-1",
            build_core1_derived_outputs(
                validate_run_closure_read_only(bundle / "core1", "CORE-1")
            ),
        ),
        (
            "core2", "CORE-2",
            build_core2_derived_outputs(
                validate_run_closure_read_only(bundle / "core2", "CORE-2")
            ),
        ),
    )
    for name, _expected_core, derived in specifications:
        for table in derived.tables:
            for case, changed in _csv_mutations(
                _physical_csv(bundle / name / table.filename), table
            ):
                _restore(bundle, baseline)
                root = bundle / name
                _write_physical_csv(root / table.filename, changed)
                write_file_hashes(root)
                _assert_direct_verifier_rejects(
                    bundle, name, f"{name}:{table.filename}:{case}"
                )


def test_round6_builders_are_pure_canonical_and_all_outputs_are_required(
    tmp_path, monkeypatch
):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    specifications = (
        ("core1", "CORE-1", build_core1_derived_outputs),
        ("core2", "CORE-2", build_core2_derived_outputs),
    )
    for name, expected_core, builder in specifications:
        root = bundle / name
        before_files = _snapshot(root)
        closure = validate_run_closure_read_only(root, expected_core)
        before_closure = copy.deepcopy(closure)
        first = builder(closure)
        second = builder(closure)
        assert first == second
        assert closure == before_closure
        assert _snapshot(root) == before_files
        assert first.required_paths == (
            "summary.json", *(table.filename for table in first.tables)
        )
        assert len(first.required_paths) == len(set(first.required_paths))
        assert first.summary["variants"] == list(first.table("summary.csv").rows)
        for table in first.tables:
            assert table.columns
            assert table.primary_key
            assert tuple(column for column, _types in table.field_types) == table.columns
            keys = [
                tuple(row[column] for column in table.primary_key)
                for row in table.rows
            ]
            assert len(keys) == len(set(keys))


def test_round6_verifier_requires_every_canonical_derived_output(
    tmp_path, monkeypatch
):
    bundle = _formal_bundle(tmp_path, monkeypatch)
    baseline = _snapshot(bundle)
    specifications = (
        (
            "core1",
            build_core1_derived_outputs(
                validate_run_closure_read_only(bundle / "core1", "CORE-1")
            ),
        ),
        (
            "core2",
            build_core2_derived_outputs(
                validate_run_closure_read_only(bundle / "core2", "CORE-2")
            ),
        ),
    )
    for name, derived in specifications:
        for relative in derived.required_paths:
            _restore(bundle, baseline)
            root = bundle / name
            (root / relative).unlink()
            write_file_hashes(root)
            _assert_direct_verifier_rejects(
                bundle, name, f"{name}:missing:{relative}"
            )


def _canonical_bundle_bytes(bundle: DerivedOutputBundle) -> bytes:
    tables = []
    for table in bundle.tables:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(
            stream, fieldnames=table.columns, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(table.rows)
        tables.append({
            "filename": table.filename,
            "header": list(table.columns),
            "rows": list(table.rows),
            "bytes": stream.getvalue(),
        })
    return canonical_json({
        "schema": bundle.schema,
        "summary": bundle.summary,
        "tables": tables,
    }).encode("utf-8")


def test_round8_runtime_binary64_contract_is_stable_across_closure_row_order(
    tmp_path, monkeypatch
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    closure = validate_run_closure_read_only(formal / "core2", "CORE-2")
    runtime_values = ("0.012", "0.013", "0.014", "0.015", "0.016")
    tasksets = []
    for row, runtime in zip(closure.tasksets, runtime_values):
        changed = dict(row)
        changed["runtime_wall_seconds"] = runtime
        tasksets.append(changed)
    canonical = replace(closure, tasksets=tuple(tasksets))
    reordered = replace(
        copy.deepcopy(canonical), tasksets=tuple(reversed(tasksets))
    )

    first = build_core2_derived_outputs(canonical)
    second = build_core2_derived_outputs(reordered)
    assert first.summary == second.summary
    assert [
        (table.filename, table.columns, table.rows) for table in first.tables
    ] == [
        (table.filename, table.columns, table.rows) for table in second.tables
    ]
    assert _canonical_bundle_bytes(first) == _canonical_bundle_bytes(second)
    mean = first.summary["runtime_summary"]["mean"]
    assert mean.hex() == "0x1.cac083126e97ap-7"
    assert canonical_json({"mean": mean}) == (
        '{"mean":0.014000000000000002}'
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=("mean",), lineterminator="\n"
    )
    writer.writeheader()
    writer.writerow({"mean": mean})
    assert stream.getvalue() == "mean\n0.014000000000000002\n"


def test_round7_red_legacy_derived_namespace_is_fail_closed(
    tmp_path, monkeypatch
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    root = formal / "core2"
    (root / "summary_v0.json").write_text("{}\n", encoding="utf-8")
    write_file_hashes(root)
    before = _snapshot(root)

    with pytest.raises(RuntimeError):
        aggregate_core2(root)
    assert _snapshot(root) == before
    with pytest.raises(RuntimeError):
        verify_core12_output("core2", root)
    assert _snapshot(root) == before


def test_round7_red_plot_consumer_rejects_unknown_type_without_writes(
    tmp_path, monkeypatch
):
    formal = _formal_bundle(tmp_path, monkeypatch)
    root = formal / "core1"
    physical = _physical_csv(root / "core1_plot_data.csv")
    physical[1][physical[0].index("plot")] = "unknown_plot"
    data_path = tmp_path / "core1_plot_data.csv"
    _write_physical_csv(data_path, physical)
    output_dir = tmp_path / "plots"

    with pytest.raises(RuntimeError):
        render_plot_data(data_path, root / "run_config.yaml", output_dir)
    assert not output_dir.exists()
