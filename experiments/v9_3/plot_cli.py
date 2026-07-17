"""Plot already-exported long-form CSV without recomputing statistics."""

from __future__ import annotations

from fractions import Fraction
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

import yaml

from .plotting_data import (
    CORE_PLOT_TYPES, PlotTableError, validate_canonical_plot_table,
)


def render_plot_data(
    data_path: Path, config_path: Path, output_dir: Path
) -> list[Path]:
    data_path = Path(data_path)
    config_path = Path(config_path)
    output_dir = Path(output_dir)
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except Exception as exc:
        raise PlotTableError(f"plot schema config is unreadable: {config_path}") from exc
    if not isinstance(config, Mapping) or config.get("core") not in {"CORE-1", "CORE-2"}:
        raise PlotTableError(f"plot schema config has no frozen core: {config_path}")
    expected_core = str(config["core"])
    rows = validate_canonical_plot_table(data_path, expected_core=expected_core)
    plot_config = config.get("plots", {})
    if not isinstance(plot_config, Mapping):
        raise PlotTableError(f"plot schema config plots section is invalid: {config_path}")

    allowed_output_names = {
        f"{plot_type}.{extension}"
        for plot_type in CORE_PLOT_TYPES[expected_core]
        for extension in ("png", "pdf")
    }
    if output_dir.exists():
        for path in output_dir.rglob("*"):
            if path.is_symlink() or path.is_dir() or path.name not in allowed_output_names:
                raise PlotTableError(
                    f"plot schema output directory contains unknown artifact: {path}"
                )

    # Import lazily so running/analyzing experiments does not require matplotlib.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def number(value: str) -> float | None:
        if value == "":
            return None
        converted = float(Fraction(value))
        return converted if math.isfinite(converted) else None

    plot_names = sorted({row["plot"] for row in rows})
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="partsim-v9-3-plots-",
        dir=output_dir.parent,
    ) as temporary:
        stage = Path(temporary)
        for plot_name in plot_names:
            members = [row for row in rows if row["plot"] == plot_name]
            settings = plot_config.get(plot_name, {})
            if not isinstance(settings, Mapping):
                raise PlotTableError(
                    f"plot schema settings are invalid for plot type: {plot_name}"
                )
            figure, axis = plt.subplots()
            try:
                groups = sorted({row.get("variant", "") for row in members})
                for group in groups:
                    selected = [
                        row for row in members if row.get("variant", "") == group
                    ]
                    points = []
                    for row in selected:
                        x = number(row["x"])
                        y = number(row["y"])
                        if x is not None and y is not None:
                            points.append((x, y))
                    if points:
                        axis.scatter(
                            [point[0] for point in points],
                            [point[1] for point in points],
                            label=group or settings.get("default_legend", "data"),
                            alpha=.75,
                        )
                axis.set_title(
                    settings.get("title", plot_name.replace("_", " ").title())
                )
                axis.set_xlabel(settings.get("x_label", "x"))
                axis.set_ylabel(settings.get("y_label", "y"))
                handles, _labels = axis.get_legend_handles_labels()
                if settings.get("legend", True) and handles:
                    axis.legend()
                figure.tight_layout()
                for extension in ("png", "pdf"):
                    figure.savefig(stage / f"{plot_name}.{extension}")
            finally:
                plt.close(figure)

        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        for plot_name in plot_names:
            for extension in ("png", "pdf"):
                name = f"{plot_name}.{extension}"
                destination = output_dir / name
                os.replace(stage / name, destination)
                outputs.append(destination)
        return outputs
