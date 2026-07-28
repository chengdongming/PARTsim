#!/usr/bin/env python3
"""Frozen Matplotlib rendering for the five B4-PE I5D paper figures."""

from __future__ import annotations

import hashlib
import io
import math

import matplotlib


matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import PercentFormatter

import statistics_common as common


WATERMARK = common.CONTRACT["mode_contracts"]["validation"]["watermark"]
FIGURE_CONTRACT = common.CONTRACT["figure_contract"]
COLORS = dict(FIGURE_CONTRACT["algorithm_colors"])
MARKERS = list(FIGURE_CONTRACT["marker_order"])
LINESTYLES = ["-", "--", ":", "-.", (0, (5, 1))]


def _configure():
    matplotlib.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": FIGURE_CONTRACT["base_font_size_pt"],
            "axes.titlesize": 9,
            "axes.labelsize": 8,
            "axes.linewidth": FIGURE_CONTRACT["axes_line_width_pt"],
            "lines.linewidth": FIGURE_CONTRACT["data_line_width_pt"],
            "savefig.dpi": FIGURE_CONTRACT["png_dpi"],
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )


def _watermark(fig, validation):
    if validation:
        fig.text(
            0.5,
            0.5,
            WATERMARK,
            ha="center",
            va="center",
            rotation=28,
            fontsize=20,
            color="#8b0000",
            alpha=0.24,
            weight="bold",
            zorder=1000,
        )


def _save(fig, stem, source_sha, validation):
    _watermark(fig, validation)
    fig.text(
        0.995,
        0.003,
        f"data sha256: {source_sha}",
        ha="right",
        va="bottom",
        fontsize=3.5,
        color="#777777",
    )
    pdf = io.BytesIO()
    png = io.BytesIO()
    fig.savefig(
        pdf,
        format="pdf",
        metadata={
            **FIGURE_CONTRACT["pdf_metadata"],
            "Title": stem,
            "Subject": f"source-data-sha256={source_sha}",
        },
    )
    fig.savefig(
        png,
        format="png",
        dpi=FIGURE_CONTRACT["png_dpi"],
        metadata={
            "Software": FIGURE_CONTRACT["png_metadata"]["Software"],
            "Description": f"source-data-sha256={source_sha}",
        },
    )
    plt.close(fig)
    return {f"{stem}.pdf": pdf.getvalue(), f"{stem}.png": png.getvalue()}


def _figure1(rows):
    fig, axes = plt.subplots(
        1, 2, figsize=FIGURE_CONTRACT["figure_sizes_inches"]["figure1"], sharey=True
    )
    y = np.arange(len(rows))
    for axis, metric, lower, upper, title in (
        (axes[0], "hp_pass", "hp_ci_lower", "hp_ci_upper", "A  HPPass"),
        (axes[1], "whole_pass", "whole_ci_lower", "whole_ci_upper", "B  WholePass"),
    ):
        for index, row in enumerate(rows):
            value = row[metric]
            if value is None:
                continue
            low = value - row[lower]
            high = row[upper] - value
            filled = row["algorithm"] == "ASAP-BLOCK"
            axis.errorbar(
                value,
                index,
                xerr=np.array([[low], [high]]),
                fmt="o",
                markersize=5,
                markerfacecolor=COLORS[row["algorithm"]] if filled else "white",
                markeredgecolor=COLORS[row["algorithm"]],
                ecolor="#555555",
                capsize=2,
            )
        axis.set_xlim(0.0, 1.0)
        axis.xaxis.set_major_formatter(PercentFormatter(1.0))
        axis.grid(axis="x", color="#dddddd", linewidth=0.6)
        axis.set_title(title, loc="left")
        axis.set_xlabel("Pass rate")
    axes[0].set_yticks(y)
    axes[0].set_yticklabels([row["algorithm"] for row in rows])
    axes[0].invert_yaxis()
    fig.subplots_adjust(left=0.22, right=0.98, bottom=0.14, top=0.90, wspace=0.12)
    return fig


def _comparison_matrix(cell_rows, comparator):
    selected = [
        row
        for row in cell_rows
        if row["record_type"] == "comparison" and row["comparator"] == comparator
    ]
    utilizations = sorted({row["utilization"] for row in selected}, key=float)
    lambdas = sorted({row["lambda_E"] for row in selected}, key=float)
    hp = np.full((len(utilizations), len(lambdas)), np.nan)
    whole = np.full_like(hp, np.nan)
    for ui, utilization in enumerate(utilizations):
        for li, lam in enumerate(lambdas):
            rows = [row for row in selected if row["utilization"] == utilization and row["lambda_E"] == lam]
            hp_values = [row["hp_risk_difference"] for row in rows if row["hp_risk_difference"] is not None]
            whole_values = [row["whole_risk_difference"] for row in rows if row["whole_risk_difference"] is not None]
            if hp_values:
                hp[ui, li] = sum(hp_values) / len(hp_values)
            if whole_values:
                whole[ui, li] = sum(whole_values) / len(whole_values)
    return utilizations, lambdas, hp, whole


def _heatmap_figure(cell_rows, comparators, scale, panel_labels):
    fig, axes = plt.subplots(
        1, 2, figsize=FIGURE_CONTRACT["figure_sizes_inches"]["figure2"],
        constrained_layout=True,
    )
    image = None
    for axis, comparator, panel in zip(axes, comparators, panel_labels):
        utilizations, lambdas, hp, whole = _comparison_matrix(cell_rows, comparator)
        image = axis.imshow(hp, cmap="RdBu_r", vmin=-scale, vmax=scale, aspect="auto")
        axis.set_xticks(range(len(lambdas)))
        axis.set_xticklabels(lambdas)
        axis.set_yticks(range(len(utilizations)))
        axis.set_yticklabels(utilizations)
        axis.set_xlabel(r"$\lambda_E$")
        axis.set_ylabel("U")
        axis.set_title(f"{panel}  ASAP-BLOCK − {comparator}", loc="left")
        for ui in range(len(utilizations)):
            for li in range(len(lambdas)):
                if np.isfinite(hp[ui, li]):
                    whole_text = "NA" if not np.isfinite(whole[ui, li]) else f"W {100 * whole[ui, li]:+.1f}"
                    axis.text(li, ui, f"{100 * hp[ui, li]:+.1f}\n{whole_text}", ha="center", va="center", fontsize=7)
    if image is not None:
        colorbar = fig.colorbar(image, ax=axes, shrink=0.82)
        colorbar.set_label("HPPass risk difference")
        colorbar.ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    return fig


def _figure4(rows):
    fig, axis = plt.subplots(
        figsize=FIGURE_CONTRACT["figure_sizes_inches"]["figure4"]
    )
    y = np.arange(len(rows))
    for index, row in enumerate(rows):
        hp = row["hp_point_estimate"]
        whole = row["whole_point_estimate"]
        if hp is not None:
            axis.errorbar(
                100 * hp,
                index - 0.10,
                xerr=np.array([[100 * (hp - row["hp_ci_lower"])], [100 * (row["hp_ci_upper"] - hp)]]),
                fmt="o",
                color="#1f4e79",
                markerfacecolor="#1f4e79",
                capsize=2,
                label="HPPass" if index == 0 else None,
            )
        if whole is not None:
            axis.errorbar(
                100 * whole,
                index + 0.12,
                xerr=np.array([[100 * (whole - row["whole_ci_lower"])], [100 * (row["whole_ci_upper"] - whole)]]),
                fmt="o",
                markersize=4,
                color="#777777",
                markerfacecolor="white",
                capsize=2,
                label="WholePass" if index == 0 else None,
            )
        axis.text(
            1.01,
            index,
            f"raw p={row['raw_p']:.4g}   Holm p={row['holm_adjusted_p']:.4g}",
            transform=axis.get_yaxis_transform(),
            va="center",
            fontsize=7,
        )
    axis.axvline(0.0, color="black", linewidth=0.8)
    axis.set_yticks(y)
    axis.set_yticklabels([f"ASAP-BLOCK − {row['comparator']}" for row in rows])
    axis.invert_yaxis()
    axis.set_xlabel("Risk difference (percentage points)")
    axis.set_title("Confirmatory and secondary effect sizes", loc="left")
    axis.grid(axis="x", color="#dddddd", linewidth=0.6)
    axis.legend(loc="lower right", frameon=False)
    fig.subplots_adjust(left=0.31, right=0.72, bottom=0.14, top=0.90)
    return fig


def _tradeoff_points(cell_rows, comparator):
    algorithm_rows = [row for row in cell_rows if row["record_type"] == "algorithm"]
    index = {
        (row["utilization"], row["lambda_E"], row["rho_E"], row["algorithm"]): row
        for row in algorithm_rows
    }
    points = []
    for key, block in index.items():
        utilization, lam, rho, algorithm = key
        if algorithm != "ASAP-BLOCK":
            continue
        other = index.get((utilization, lam, rho, comparator))
        if other is None:
            continue
        values = (
            block["completion_ratio"], other["completion_ratio"],
            block["top4_jmr"], other["top4_jmr"],
        )
        if any(value is None for value in values):
            continue
        points.append((block["completion_ratio"] - other["completion_ratio"], other["top4_jmr"] - block["top4_jmr"], utilization, lam))
    return points


def _figure5(rank_rows, cell_rows, mechanism_rows):
    fig = plt.figure(figsize=FIGURE_CONTRACT["figure_sizes_inches"]["figure5"])
    outer = fig.add_gridspec(3, 1, height_ratios=[0.85, 1.45, 1.65], hspace=0.30)
    rank_axis = fig.add_subplot(outer[0])
    shown = ("ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC", "ALAP-BLOCK", "ST-BLOCK")
    for index, algorithm in enumerate(shown):
        selected = [row for row in rank_rows if row["algorithm"] == algorithm]
        rank_axis.plot(
            [row["paper_rank"] for row in selected],
            [np.nan if row["point_estimate"] is None else row["point_estimate"] for row in selected],
            marker=MARKERS[index], color=COLORS[algorithm], label=algorithm,
        )
    rank_axis.axvline(4.5, color="black", linestyle="--", linewidth=0.9)
    rank_axis.set_xticks(range(1, 11))
    rank_axis.yaxis.set_major_formatter(PercentFormatter(1.0))
    rank_axis.set_xlabel("Priority rank (paper rank)")
    rank_axis.set_ylabel("JMR")
    rank_axis.set_title("A  Priority-rank JMR", loc="left")
    rank_axis.grid(color="#e5e5e5", linewidth=0.5)
    rank_axis.legend(ncol=3, frameon=False, fontsize=7)

    trade_grid = outer[1].subgridspec(2, 2, hspace=0.38, wspace=0.27)
    all_points = {comparator: _tradeoff_points(cell_rows, comparator) for comparator in common.COMPARATORS}
    magnitudes = [abs(value) for points in all_points.values() for point in points for value in point[:2]]
    limit = max(magnitudes, default=0.05)
    limit = max(0.05, math.ceil(limit / 0.05) * 0.05)
    for index, comparator in enumerate(common.COMPARATORS):
        axis = fig.add_subplot(trade_grid[index // 2, index % 2])
        for x, y, utilization, lam in all_points[comparator]:
            axis.scatter(x, y, s=22, facecolor="none", edgecolor=COLORS[comparator])
        axis.axhline(0.0, color="black", linewidth=0.7)
        axis.axvline(0.0, color="black", linewidth=0.7)
        axis.set_xlim(-limit, limit)
        axis.set_ylim(-limit, limit)
        axis.xaxis.set_major_formatter(PercentFormatter(1.0))
        axis.yaxis.set_major_formatter(PercentFormatter(1.0))
        axis.set_title(f"B{index + 1}  {comparator}", loc="left")
        axis.set_xlabel("Δ completion ratio")
        axis.set_ylabel("Comparator Top4 JMR − ASAP-BLOCK")
        axis.grid(color="#e8e8e8", linewidth=0.5)

    mechanism_grid = outer[2].subgridspec(2, 3, hspace=0.46, wspace=0.30)
    for index, (mechanism, definition) in enumerate(common.CONTRACT["mechanisms"].items()):
        axis = fig.add_subplot(mechanism_grid[index // 3, index % 3])
        algorithm = definition["paper_algorithm"]
        selected = [row for row in mechanism_rows if row["mechanism"] == mechanism and row["algorithm"] == algorithm]
        utilizations = sorted({row["utilization"] for row in selected}, key=float)
        for ui, utilization in enumerate(utilizations):
            values = [row for row in selected if row["utilization"] == utilization]
            values.sort(key=lambda row: float(row["lambda_E"]))
            axis.plot(
                [float(row["lambda_E"]) for row in values],
                [np.nan if row["macro_mean"] is None else row["macro_mean"] for row in values],
                marker=MARKERS[ui % len(MARKERS)], linestyle=LINESTYLES[ui % len(LINESTYLES)],
                label=f"U={utilization}", color=COLORS[algorithm],
            )
        coverage = sum(row["defined_taskset_count"] for row in selected)
        total = sum(row["total_taskset_count"] for row in selected)
        axis.set_title(
            f"C{index + 1}  {mechanism}\n{algorithm}; denominator: {definition['denominator']}\ncoverage {coverage}/{total}",
            loc="left", fontsize=7,
        )
        axis.set_xlabel(r"$\lambda_E$")
        axis.set_ylabel(mechanism)
        axis.yaxis.set_major_formatter(PercentFormatter(1.0))
        axis.grid(color="#e8e8e8", linewidth=0.5)
        if index == 0:
            axis.legend(frameon=False, fontsize=6)
    fig.subplots_adjust(left=0.08, right=0.97, bottom=0.04, top=0.98)
    return fig, limit


def build_figures(rows_by_name, source_hashes, validation=False):
    _configure()
    outputs = {}
    bindings = {}
    spec = {
        "validation_watermark": bool(validation),
        "figure1_x_ranges": [[0.0, 1.0], [0.0, 1.0]],
        "figure4_zero_reference": True,
        "figure5_rank_boundary": 4.5,
        "figure5_tradeoff_comparator_count": 4,
        "figure5_mechanism_facet_count": 6,
    }
    figure1 = _figure1(rows_by_name["algorithm_summary"])
    stem = "figure1_algorithm_pass_rates"
    outputs.update(_save(figure1, stem, source_hashes["algorithm_summary"], validation))
    for suffix in ("pdf", "png"):
        bindings[f"{stem}.{suffix}"] = source_hashes["algorithm_summary"]

    matrices = [_comparison_matrix(rows_by_name["cell_summary"], comparator)[2] for comparator in common.COMPARATORS]
    maximum = max((float(np.nanmax(np.abs(matrix))) for matrix in matrices if np.any(np.isfinite(matrix))), default=0.0)
    scale = min(1.0, max(0.05, math.ceil(maximum / 0.05) * 0.05))
    spec["figure23_shared_symmetric_scale"] = [-scale, scale]
    for stem, comparators, labels in (
        ("figure2_block_vs_nonblock_sync", common.COMPARATORS[:2], ("A", "B")),
        ("figure3_asap_vs_alap_st", common.COMPARATORS[2:], ("A", "B")),
    ):
        figure = _heatmap_figure(rows_by_name["cell_summary"], comparators, scale, labels)
        outputs.update(_save(figure, stem, source_hashes["cell_summary"], validation))
        for suffix in ("pdf", "png"):
            bindings[f"{stem}.{suffix}"] = source_hashes["cell_summary"]

    stem = "figure4_confirmatory_effects"
    figure4 = _figure4(rows_by_name["confirmatory_effects"])
    outputs.update(_save(figure4, stem, source_hashes["confirmatory_effects"], validation))
    for suffix in ("pdf", "png"):
        bindings[f"{stem}.{suffix}"] = source_hashes["confirmatory_effects"]

    combined_sha = hashlib.sha256(
        "".join(source_hashes[name] for name in ("rank_jmr", "cell_summary", "mechanism_summary")).encode("ascii")
    ).hexdigest()
    stem = "figure5_tradeoff_mechanisms"
    figure5, tradeoff_limit = _figure5(
        rows_by_name["rank_jmr"], rows_by_name["cell_summary"], rows_by_name["mechanism_summary"]
    )
    spec["figure5_shared_tradeoff_limit"] = [-tradeoff_limit, tradeoff_limit]
    outputs.update(_save(figure5, stem, combined_sha, validation))
    for suffix in ("pdf", "png"):
        bindings[f"{stem}.{suffix}"] = combined_sha
    return outputs, bindings, spec
