"""Paper-facing paired metrics, inference and four B4 figures."""

from __future__ import annotations

from collections import defaultdict
import csv
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .performance_config import (
    ALL_SCHEDULERS, CONFIRMATORY_COMPARISONS, FORMAL_UTILIZATIONS,
    PRIMARY_SCHEDULERS,
)
from .performance_statistics import (
    UNAVAILABLE, holm_adjust, paired_permutation_test,
    stratified_paired_bootstrap, wilson_interval,
)


DISPLAY = {
    "gpfp_asap_block": "ASAP-BLOCK",
    "gpfp_asap_nonblock": "ASAP-NONBLOCK",
    "gpfp_asap_sync": "ASAP-SYNC",
    "gpfp_alap_block": "ALAP-BLOCK",
    "gpfp_alap_nonblock": "ALAP-NONBLOCK",
    "gpfp_alap_sync": "ALAP-SYNC",
    "gpfp_st_block": "ST-BLOCK",
    "gpfp_st_nonblock": "ST-NONBLOCK",
    "gpfp_st_sync": "ST-SYNC",
}
COLORS = ["#000000", "#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9", "#E69F00", "#555555", "#882255"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "<", ">"]
LINESTYLES = ["-", "--", "-.", ":", "-", "--", "-.", ":", "-"]


def _outcome(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("outcome")
    if not isinstance(value, Mapping):
        raise ValueError("terminal result lacks B4 outcome")
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def pass_ratio_table(results: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped = defaultdict(list)
    for row in results:
        grouped[(row["energy_condition"], row["scheduler_id"], row["u_norm"])].append(bool(_outcome(row)["observed_pass"]))
    output = []
    for (condition, scheduler, utilization), values in sorted(grouped.items()):
        successes = sum(values)
        low, high = wilson_interval(successes, len(values))
        output.append({
            "energy_condition": condition, "scheduler_id": scheduler,
            "algorithm": DISPLAY[scheduler], "u_norm": utilization,
            "passes": successes, "tasksets": len(values),
            "pass_ratio": successes / len(values), "ci_low": low, "ci_high": high,
        })
    return output


def _paired_groups(results: Iterable[Mapping[str, Any]], condition: str) -> Dict[tuple, Dict[str, Mapping[str, Any]]]:
    groups = defaultdict(dict)
    for row in results:
        if row["energy_condition"] != condition:
            continue
        key = (row["u_norm"], row["taskset_semantic_hash"])
        scheduler = row["scheduler_id"]
        if scheduler in groups[key]:
            raise ValueError("duplicate paired result")
        groups[key][scheduler] = row
    for key, group in groups.items():
        if set(group) != set(ALL_SCHEDULERS):
            raise ValueError(f"incomplete nine-scheduler group: {key}")
    return dict(groups)


def paired_advantage_table(
    results: Iterable[Mapping[str, Any]], *, bootstrap_seed: int,
    resamples: int = 10000,
) -> List[Dict[str, Any]]:
    groups = _paired_groups(results, "transition")
    output = []
    for comparison_index, (left, right) in enumerate(CONFIRMATORY_COMPARISONS):
        for u_index, utilization in enumerate(FORMAL_UTILIZATIONS):
            pairs = []
            for (observed_u, taskset_hash), group in groups.items():
                if observed_u != utilization:
                    continue
                pairs.append({
                    "u_norm": utilization, "taskset_semantic_hash": taskset_hash,
                    "left": int(_outcome(group[left])["observed_pass"]),
                    "right": int(_outcome(group[right])["observed_pass"]),
                })
            estimate = stratified_paired_bootstrap(
                pairs, seed=bootstrap_seed + comparison_index * 100 + u_index,
                resamples=resamples,
            )
            output.append({
                "comparison": f"{DISPLAY[left]} - {DISPLAY[right]}",
                "left_scheduler": left, "right_scheduler": right,
                "u_norm": utilization, **estimate,
            })
    return output


def tradeoff_table(
    results: Iterable[Mapping[str, Any]], *, bootstrap_seed: int,
    resamples: int = 10000,
) -> List[Dict[str, Any]]:
    groups = _paired_groups(results, "transition")
    specifications = (
        ("Delta_JMR_topM", "jmr_top_m", "reverse"),
        ("Delta_JMR_top25", "jmr_top_25_percent", "reverse"),
        ("Delta_completion", "completion_ratio", "forward"),
    )
    output = []
    for metric_index, (label, metric, direction) in enumerate(specifications):
        for u_index, utilization in enumerate(FORMAL_UTILIZATIONS):
            pairs = []
            for (observed_u, taskset_hash), group in groups.items():
                if observed_u != utilization:
                    continue
                block = _outcome(group["gpfp_asap_block"])[metric]
                nonblock = _outcome(group["gpfp_asap_nonblock"])[metric]
                if block == UNAVAILABLE or nonblock == UNAVAILABLE:
                    raise ValueError("UNAVAILABLE metric in formal paired analysis")
                left, right = (nonblock, block) if direction == "reverse" else (block, nonblock)
                pairs.append({"u_norm": utilization, "taskset_semantic_hash": taskset_hash, "left": left, "right": right})
            estimate = stratified_paired_bootstrap(
                pairs, seed=bootstrap_seed + 1000 + metric_index * 100 + u_index,
                resamples=resamples,
            )
            output.append({"metric": label, "u_norm": utilization, **estimate})
    return output


def confirmatory_inference(
    results: Iterable[Mapping[str, Any]], *, permutation_seed: int,
    resamples: int = 10000,
) -> List[Dict[str, Any]]:
    groups = _paired_groups(results, "transition")
    tests = []
    for index, (left, right) in enumerate(CONFIRMATORY_COMPARISONS):
        differences = [
            int(_outcome(group[left])["observed_pass"]) - int(_outcome(group[right])["observed_pass"])
            for group in groups.values()
        ]
        result = paired_permutation_test(differences, seed=permutation_seed + index, resamples=resamples)
        tests.append({"comparison": f"{DISPLAY[left]} - {DISPLAY[right]}", **result})
    adjusted = holm_adjust([row["p_value"] for row in tests])
    for row, value in zip(tests, adjusted):
        row["holm_adjusted_p"] = value
    return tests


def _save_figure(figure: Any, root: Path, stem: str) -> None:
    figure.savefig(root / f"{stem}.pdf", bbox_inches="tight")
    figure.savefig(root / f"{stem}.png", dpi=300, bbox_inches="tight")


def generate_four_figures(
    results: Sequence[Mapping[str, Any]], formal_audit: Mapping[str, Any], *,
    output_root: Path, bootstrap_seed: int = 983201,
    permutation_seed: int = 983202, resamples: int = 10000,
) -> Dict[str, Any]:
    if formal_audit.get("status") != "FORMAL_COMPLETE":
        raise ValueError("formal terminal gate has not passed")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    pass_rows = pass_ratio_table(results)
    advantage_rows = paired_advantage_table(results, bootstrap_seed=bootstrap_seed, resamples=resamples)
    tradeoff_rows = tradeoff_table(results, bootstrap_seed=bootstrap_seed, resamples=resamples)
    inference_rows = confirmatory_inference(results, permutation_seed=permutation_seed, resamples=resamples)
    _write_csv(output_root / "figure1_main_pass_ratio.csv", [row for row in pass_rows if row["scheduler_id"] in PRIMARY_SCHEDULERS])
    _write_csv(output_root / "figure2_nine_scheduler_matrix.csv", [row for row in pass_rows if row["energy_condition"] == "transition"])
    _write_csv(output_root / "figure3_paired_advantages.csv", advantage_rows)
    _write_csv(output_root / "figure4_priority_tradeoff.csv", tradeoff_rows)
    _write_csv(output_root / "confirmatory_inference.csv", inference_rows)

    x_values = [float(Fraction(value)) for value in FORMAL_UTILIZATIONS]
    figure, axes = plt.subplots(1, 3, figsize=(12, 3.7), sharey=True)
    for axis, condition in zip(axes, ("low", "transition", "high")):
        for index, scheduler in enumerate(PRIMARY_SCHEDULERS):
            rows = sorted((row for row in pass_rows if row["energy_condition"] == condition and row["scheduler_id"] == scheduler), key=lambda row: float(Fraction(row["u_norm"])))
            y = [row["pass_ratio"] for row in rows]
            error = [[value - row["ci_low"] for value, row in zip(y, rows)], [row["ci_high"] - value for value, row in zip(y, rows)]]
            axis.errorbar(x_values, y, yerr=error, color=COLORS[index], marker=MARKERS[index], linestyle=LINESTYLES[index], label=DISPLAY[scheduler], capsize=2)
        axis.set_title(f"{condition}-supply")
        axis.set_xlabel("Normalized utilization")
        axis.set_ylim(0, 1)
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("Observed task-set pass ratio")
    axes[-1].legend(fontsize=7, loc="best")
    _save_figure(figure, output_root, "figure1_main_pass_ratio")
    plt.close(figure)

    figure, axes = plt.subplots(1, 3, figsize=(12, 3.7), sharey=True)
    families = (("ASAP", ALL_SCHEDULERS[:3]), ("ALAP", ALL_SCHEDULERS[3:6]), ("ST", ALL_SCHEDULERS[6:]))
    for axis, (family, schedulers) in zip(axes, families):
        for scheduler in schedulers:
            index = ALL_SCHEDULERS.index(scheduler)
            rows = sorted((row for row in pass_rows if row["energy_condition"] == "transition" and row["scheduler_id"] == scheduler), key=lambda row: float(Fraction(row["u_norm"])))
            y = [row["pass_ratio"] for row in rows]
            error = [
                [value - row["ci_low"] for value, row in zip(y, rows)],
                [row["ci_high"] - value for value, row in zip(y, rows)],
            ]
            axis.errorbar(
                x_values, y, yerr=error, color=COLORS[index],
                marker=MARKERS[index], linestyle=LINESTYLES[index],
                label=DISPLAY[scheduler], capsize=2,
            )
        axis.set_title(family)
        axis.set_xlabel("Normalized utilization")
        axis.set_ylim(0, 1)
        axis.grid(alpha=0.25)
        axis.legend(fontsize=7)
    axes[0].set_ylabel("Observed task-set pass ratio")
    _save_figure(figure, output_root, "figure2_nine_scheduler_matrix")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(6.5, 4.2))
    comparisons = list(dict.fromkeys(row["comparison"] for row in advantage_rows))
    for index, comparison in enumerate(comparisons):
        rows = [row for row in advantage_rows if row["comparison"] == comparison]
        y = [row["estimate"] for row in rows]
        errors = [[value - row["ci_low"] for value, row in zip(y, rows)], [row["ci_high"] - value for value, row in zip(y, rows)]]
        effective_n = sorted({int(row["effective_paired_n"]) for row in rows})
        n_label = str(effective_n[0]) if len(effective_n) == 1 else "varies"
        axis.errorbar(x_values, y, yerr=errors, color=COLORS[index], marker=MARKERS[index], linestyle=LINESTYLES[index], label=f"{comparison} (N={n_label})", capsize=2)
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set_xlabel("Normalized utilization")
    axis.set_ylabel("Paired pass-ratio difference")
    axis.grid(alpha=0.25)
    axis.legend(fontsize=7)
    _save_figure(figure, output_root, "figure3_paired_advantages")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(6.5, 4.2))
    metrics = list(dict.fromkeys(row["metric"] for row in tradeoff_rows))
    for index, metric in enumerate(metrics):
        rows = [row for row in tradeoff_rows if row["metric"] == metric]
        y = [row["estimate"] for row in rows]
        errors = [
            [value - row["ci_low"] for value, row in zip(y, rows)],
            [row["ci_high"] - value for value, row in zip(y, rows)],
        ]
        axis.errorbar(
            x_values, y, yerr=errors, color=COLORS[index],
            marker=MARKERS[index], linestyle=LINESTYLES[index],
            label=metric, capsize=2,
        )
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set_xlabel("Normalized utilization")
    axis.set_ylabel("Paired difference")
    axis.grid(alpha=0.25)
    axis.legend(fontsize=8)
    _save_figure(figure, output_root, "figure4_priority_tradeoff")
    plt.close(figure)
    return {
        "figure_count": 4, "pdf_count": 4, "png_count": 4,
        "csv_count": 5, "confirmatory_tests": len(inference_rows),
    }
