from __future__ import annotations

import csv
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

import experiments.v9_3.plot_cli as plot_cli
from experiments.v9_3.plotting_data import PlotTableError


EXPECTED_OUTPUT_NAMES = {
    f"{plot_name}.{extension}"
    for plot_name in (
        "certification_outcome_matrix",
        "certification_ratio",
        "certification_ratio_e0",
        "loc_vs_cw_scatter",
        "response_reduction_distribution",
        "runtime",
        "timeout_rate",
    )
    for extension in ("png", "pdf")
}


@pytest.fixture
def plot_inputs(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    data_path = root / "core1_plot_data.csv"
    columns = (
        "plot_schema", "plot_schema_version", "plot", "cell_id",
        "taskset_id", "utilization", "exact_e0", "variant", "task_id",
        "x", "y", "outcome",
    )
    common = {
        "plot_schema": "ASAP_BLOCK_V9_3_CANONICAL_PLOT_ROWS_V3",
        "plot_schema_version": "3",
        "cell_id": "cell-1",
        "taskset_id": "0",
        "exact_e0": "1",
    }
    rows = [
        {**common, "plot": "certification_outcome_matrix", "utilization": "",
         "variant": "LOC_THETA_LOC", "task_id": "", "x": "1", "y": "1",
         "outcome": "11"},
        {**common, "plot": "certification_ratio", "utilization": "1/2",
         "variant": "CW_THETA_CW", "task_id": "", "x": "1/2", "y": "1",
         "outcome": "COMPLETED"},
        {**common, "plot": "certification_ratio_e0", "utilization": "1/2",
         "variant": "CW_THETA_CW", "task_id": "", "x": "1", "y": "1",
         "outcome": "COMPLETED"},
        {**common, "plot": "loc_vs_cw_scatter", "utilization": "",
         "variant": "LOC_THETA_LOC", "task_id": "0", "x": "2", "y": "1",
         "outcome": "TIGHTER"},
        {**common, "plot": "response_reduction_distribution",
         "utilization": "", "variant": "LOC_THETA_LOC", "task_id": "0",
         "x": "1", "y": "1", "outcome": "TIGHTER"},
        {**common, "plot": "runtime", "utilization": "1/2",
         "variant": "CW_THETA_CW", "task_id": "", "x": "1/2", "y": "0.1",
         "outcome": "COMPLETED"},
        {**common, "plot": "timeout_rate", "utilization": "1/2",
         "variant": "CW_THETA_CW", "task_id": "", "x": "1/2", "y": "0",
         "outcome": "COMPLETED"},
    ]
    with data_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    config_path = root / "run_config.yaml"
    config_path.write_text("core: CORE-1\nplots: {}\n", encoding="utf-8")
    return data_path, config_path


def test_plot_publication_stages_beside_output(
    tmp_path, monkeypatch, plot_inputs,
):
    system_temporary = tmp_path / "system-temporary"
    system_temporary.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temporary))

    output_dir = tmp_path / "publication" / "plots"
    original_temporary_directory = tempfile.TemporaryDirectory
    stage_paths = []
    stage_devices = []

    def tracked_temporary_directory(*args, **kwargs):
        assert Path(kwargs["dir"]) == output_dir.parent
        assert output_dir.parent.is_dir()
        temporary = original_temporary_directory(*args, **kwargs)
        stage_paths.append(Path(temporary.name))
        stage_devices.append(Path(temporary.name).stat().st_dev)
        return temporary

    monkeypatch.setattr(
        plot_cli, "tempfile",
        SimpleNamespace(TemporaryDirectory=tracked_temporary_directory),
    )

    outputs = plot_cli.render_plot_data(*plot_inputs, output_dir)

    assert {path.name for path in outputs} == EXPECTED_OUTPUT_NAMES
    assert {path.name for path in output_dir.iterdir()} == EXPECTED_OUTPUT_NAMES
    assert all(path.is_file() and path.stat().st_size > 0 for path in outputs)
    assert len(stage_paths) == 1
    assert stage_paths[0].parent == output_dir.parent
    assert stage_devices == [output_dir.parent.stat().st_dev]
    assert system_temporary not in stage_paths[0].parents
    assert not stage_paths[0].exists()
    assert not list(output_dir.parent.glob("partsim-v9-3-plots-*"))


def test_plot_publication_still_rejects_unknown_output(tmp_path, plot_inputs):
    output_dir = tmp_path / "publication" / "plots"
    output_dir.mkdir(parents=True)
    unknown = output_dir / "unknown.txt"
    unknown.write_text("preserve me", encoding="utf-8")

    with pytest.raises(PlotTableError, match="unknown artifact"):
        plot_cli.render_plot_data(*plot_inputs, output_dir)

    assert unknown.read_text(encoding="utf-8") == "preserve me"
    assert set(output_dir.iterdir()) == {unknown}
    assert not list(output_dir.parent.glob("partsim-v9-3-plots-*"))
