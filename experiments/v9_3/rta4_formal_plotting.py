"""Deterministic publication entry point consuming validated aggregates only."""

from __future__ import annotations

import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .result_writer import atomic_write_json
from .rta4_formal_aggregation import (
    AGGREGATE_TABLES, RTA4_AGGREGATE_MANIFEST, validate_aggregate_bundle,
)


RTA4_PLOT_VERSION = "ASAP_BLOCK_V9_3_RTA4_PUBLICATION_PLOTS_V1"
METHOD_ORDER = (
    "CW_D", "LOC_D", "PH_D", "SEQ_D",
    "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
)
FIGURE_FILES = {
    "figure_1_rta_comparison": "figure_1_rta_comparison.csv",
    "figure_2_ablation_mechanisms": "figure_2_ablation_mechanisms.csv",
    "figure_3_rta_simulation_audit": "figure_3_rta_simulation_audit.csv",
    "figure_4_sensitivity": "figure_4_sensitivity.csv",
    "figure_5_scalability": "figure_5_scalability.csv",
}


class RTA4FormalPlotError(RuntimeError):
    """Raised when plotting data bypasses or violates aggregate closure."""


def rational_label(value: str) -> str:
    if value in {"", "NA", "ALL", "baseline"}:
        return value or "NA"
    try:
        exact = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise RTA4FormalPlotError(f"invalid exact rational label: {value!r}") from exc
    return str(exact.numerator) if exact.denominator == 1 else (
        f"{exact.numerator}/{exact.denominator}"
    )


def _read_rows(root: Path, filename: str) -> list[Dict[str, str]]:
    with (root / filename).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(AGGREGATE_TABLES[filename]):
            raise RTA4FormalPlotError(f"aggregate header drift: {filename}")
        return list(reader)


def _numeric(value: str) -> float | None:
    if value in {"", "NA"}:
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise RTA4FormalPlotError(f"invalid plot numeric value: {value!r}") from exc
    if not math.isfinite(number):
        raise RTA4FormalPlotError("plot data contains NaN/Inf")
    return number


def validate_plot_data(root: Path | str) -> Mapping[str, Any]:
    root = Path(root)
    manifest = validate_aggregate_bundle(root)
    for name, filename in FIGURE_FILES.items():
        rows = _read_rows(root, filename)
        for row in rows:
            method = row.get("method", "NA")
            if method not in {*METHOD_ORDER, "NA", "ALL"}:
                raise RTA4FormalPlotError(f"unknown method in {name}: {method}")
        if name == "figure_5_scalability":
            for row in rows:
                for field in ("runtime_median", "runtime_p95"):
                    value = _numeric(row[field])
                    if value is not None and value <= 0:
                        raise RTA4FormalPlotError("log-runtime plot requires positive values")
    return manifest


def _stable_row_key(row: Mapping[str, str]) -> tuple[Any, ...]:
    method = row.get("method", "NA")
    method_rank = METHOD_ORDER.index(method) if method in METHOD_ORDER else len(METHOD_ORDER)
    rational_fields = []
    for key in ("normalized_utilization", "exact_e0", "axis_value"):
        value = row.get(key, "NA")
        try:
            rational_fields.append(Fraction(value))
        except (ValueError, ZeroDivisionError):
            rational_fields.append(Fraction(0))
    return (
        method_rank, *rational_fields,
        tuple((key, row[key]) for key in sorted(row)),
    )


def _series(rows: Sequence[Mapping[str, str]], figure_name: str) -> tuple[list[str], list[float | None]]:
    rows = sorted(rows, key=_stable_row_key)
    labels = []
    values = []
    for index, row in enumerate(rows[:64]):
        method = row.get("method", "NA")
        axis = row.get("axis_value") or row.get("normalized_utilization") or str(index)
        labels.append(f"{method}:{rational_label(axis)}")
        candidates = {
            "figure_1_rta_comparison": ("estimate", "median"),
            "figure_2_ablation_mechanisms": ("estimate", "median"),
            "figure_3_rta_simulation_audit": ("estimate", "p95"),
            "figure_4_sensitivity": ("certification_rate",),
            "figure_5_scalability": ("runtime_median", "speedup"),
        }[figure_name]
        selected = None
        for field in candidates:
            selected = _numeric(row.get(field, "NA"))
            if selected is not None:
                break
        values.append(selected)
    return labels, values


def render_formal_publication_figures(
    aggregate_root: Path | str, output_root: Path | str,
) -> Mapping[str, Any]:
    """Render five figures; raw CSV/terminal JSON is never accepted here."""

    aggregate_root = Path(aggregate_root)
    manifest = validate_plot_data(aggregate_root)
    output_root = Path(output_root)
    if output_root.exists() and any(output_root.iterdir()):
        raise RTA4FormalPlotError("plot output root must be empty")
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except Exception as exc:
        raise RTA4FormalPlotError("matplotlib is required for publication rendering") from exc

    matplotlib.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8,
        "axes.grid": True, "grid.alpha": 0.25,
        "figure.dpi": 120, "savefig.dpi": 120,
        "svg.hashsalt": "ASAP_BLOCK_V9_3_RTA4_FORMAL_V1",
    })
    output_hashes: Dict[str, str] = {}
    figure_metadata = {}
    for figure_name, filename in FIGURE_FILES.items():
        rows = _read_rows(aggregate_root, filename)
        labels, values = _series(rows, figure_name)
        fig, axis = plt.subplots(figsize=(7.2, 3.8), constrained_layout=True)
        present = [(index, value) for index, value in enumerate(values) if value is not None]
        if present:
            x, y = zip(*present)
            axis.plot(x, y, marker="o", linewidth=1.1, markersize=2.8)
            axis.set_xticks(list(x))
            axis.set_xticklabels([labels[index] for index in x])
            plt.setp(axis.get_xticklabels(), rotation=75, ha="right")
            if figure_name == "figure_5_scalability":
                axis.set_yscale("log")
        else:
            axis.text(.5, .5, "NA — no validated aggregate observations", ha="center", va="center", transform=axis.transAxes)
            axis.set_xticks([])
            axis.set_yticks([])
        axis.set_title(figure_name.replace("_", " ").title())
        axis.set_ylabel("validated aggregate value")
        data_hash = manifest["data_file_sha256"][filename]
        for extension in ("png", "pdf"):
            target = output_root / f"{figure_name}.{extension}"
            metadata = {
                "Creator": RTA4_PLOT_VERSION,
                "Title": figure_name,
                "Subject": f"aggregate_data_sha256={data_hash}",
            }
            if extension == "pdf":
                metadata.update({"CreationDate": None, "ModDate": None})
            fig.savefig(target, metadata=metadata)
            output_hashes[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        plt.close(fig)
        metadata_payload = {
            "plot_version": RTA4_PLOT_VERSION,
            "figure": figure_name,
            "aggregate_sha256": manifest["aggregate_sha256"],
            "data_filename": filename,
            "data_sha256": data_hash,
            "method_order": list(METHOD_ORDER),
            "missing_value_policy": "EXPLICIT_NA_NOT_ZERO",
        }
        metadata_path = output_root / f"{figure_name}.metadata.json"
        atomic_write_json(metadata_path, metadata_payload)
        output_hashes[metadata_path.name] = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
        figure_metadata[figure_name] = metadata_payload

    plot_manifest = {
        "plot_version": RTA4_PLOT_VERSION,
        "source_aggregate_sha256": manifest["aggregate_sha256"],
        "source_manifest": RTA4_AGGREGATE_MANIFEST,
        "figure_metadata": figure_metadata,
        "output_sha256": output_hashes,
    }
    atomic_write_json(output_root / "rta4_plot_manifest.json", plot_manifest)
    return plot_manifest


__all__ = [
    "FIGURE_FILES", "METHOD_ORDER", "RTA4FormalPlotError",
    "rational_label", "render_formal_publication_figures",
    "validate_plot_data",
]
