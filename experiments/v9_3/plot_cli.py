"""Plot already-exported long-form CSV without recomputing statistics."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping

import yaml


def render_plot_data(
    data_path: Path, config_path: Path, output_dir: Path
) -> list[Path]:
    # Import lazily so running/analyzing experiments does not require matplotlib.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with data_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    plot_config = config.get("plots", {})
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for plot_name in sorted({row["plot"] for row in rows}):
        members = [row for row in rows if row["plot"] == plot_name]
        settings = plot_config.get(plot_name, {})
        figure, axis = plt.subplots()
        groups = sorted({row.get("variant", "") for row in members})
        for group in groups:
            selected = [row for row in members if row.get("variant", "") == group]
            points = []
            for row in selected:
                try:
                    points.append((float(row["x"]), float(row["y"])))
                except (TypeError, ValueError):
                    continue
            if points:
                axis.scatter(
                    [point[0] for point in points], [point[1] for point in points],
                    label=group or settings.get("default_legend", "data"), alpha=.75,
                )
        axis.set_title(settings.get("title", plot_name.replace("_", " ").title()))
        axis.set_xlabel(settings.get("x_label", "x"))
        axis.set_ylabel(settings.get("y_label", "y"))
        handles, labels = axis.get_legend_handles_labels()
        if settings.get("legend", True) and handles:
            axis.legend()
        figure.tight_layout()
        for extension in ("png", "pdf"):
            path = output_dir / f"{plot_name}.{extension}"
            figure.savefig(path)
            outputs.append(path)
        plt.close(figure)
    return outputs
