#!/usr/bin/env python3
"""Paired HEARTS-1C and partitioned/global ASAP-BLOCK experiments."""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
from itertools import combinations
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.hearts_1c.replay import Task, Workload, run_method
from experiments.pipeline import task_generation as perf_g, scheduler_load_cross as existing

SUITES = {
    "hearts": (4, ("HEARTS-1C", "GLOBAL-AB", "GLOBAL-AN")),
    "partitioned": (10, ("PARTITIONED-AB", "GLOBAL-AB")),
}
MODEL = "hearts-1c-comparison-same-slot-exact-v1"
NORMALIZATION_HORIZON = existing.FORMAL_NORMALIZATION_HORIZON


@dataclass(frozen=True)
class Cell:
    campaign: str
    uc: Fraction
    kappa: Fraction
    level: str | None = None
    ue: Fraction | None = None

    def label(self) -> dict:
        return {key: str(value) if isinstance(value, Fraction) else value
                for key, value in asdict(self).items()}


def cells_for(campaign: str) -> tuple[Cell, ...]:
    if campaign == "smoke":
        return (Cell("smoke", Fraction("1/2"), Fraction(10), ue=Fraction("3/5")),)
    cells = []
    if campaign in ("uc", "all"):
        cells.extend(Cell("uc", uc, Fraction(10), level=level)
                     for uc in existing.V7_UC_SCAN for level in existing.V7_FIXED_SUPPLIES)
    if campaign in ("ue", "all"):
        cells.extend(Cell("ue", uc, Fraction(10), ue=ue)
                     for uc in map(Fraction, ("3/10", "1/2", "7/10"))
                     for ue in existing.V7_UE_SCAN)
    if campaign in ("capacity", "all"):
        cells.extend(Cell("capacity", Fraction("1/2"), Fraction(kappa), ue=Fraction("3/5"))
                     for kappa in (1, 5, 10))
    return tuple(cells)


# Workers share the canonical unscaled trace; per-taskset energy scaling is exact.
_RAW: tuple[Fraction, ...] = ()
_RAW_ID = ""


def _init_worker(raw: tuple[Fraction, ...], raw_id: str) -> None:
    global _RAW, _RAW_ID
    _RAW, _RAW_ID = raw, raw_id


def run_pair(item: tuple) -> list[dict]:
    suite, cell, taskset, horizon = item
    if cell.level is not None:
        material = existing.fixed_supply_energy_material(
            taskset, existing.V7_FIXED_SUPPLIES[cell.level], _RAW,
            kappa=cell.kappa, reference_ue=existing.V7_REFERENCE_UES[cell.level],
            energy_level=cell.level, normalization_horizon=NORMALIZATION_HORIZON,
            raw_trace_id=_RAW_ID, initial_energy_rule="zero",
        )
    else:
        material = existing.energy_material(
            taskset, cell.ue, _RAW, kappa=cell.kappa,
            normalization_horizon=NORMALIZATION_HORIZON,
            raw_trace_id=_RAW_ID, initial_energy_rule="zero",
        )
    tasks = []
    for rank, row in enumerate(taskset.task_payload):
        if row["D"] != row["T"] or row["priority_rank"] != rank or row.get("arrival_offset", 0):
            raise ValueError("incompatible canonical taskset")
        tasks.append(Task(str(row["task_id"]), row["C"], row["T"], Fraction(row["P"])))
    scale = Fraction(material["solar_scale"])
    work = Workload.create(
        tasks, 4, tuple(h * scale for h in _RAW[:horizon]),
        Fraction(material["initial_energy_j"]), Fraction(material["battery_capacity_j"]),
    )
    base = dict(
        model=MODEL, suite=suite, **cell.label(), taskset_id=taskset.taskset_id,
        taskset_hash=taskset.semantic_hash, taskset_index=taskset.taskset_index,
        actual_uc=str(taskset.actual_utilization / 4),
        actual_ue=material["actual_ue"], horizon=horizon, release_cutoff=work.release_cutoff,
        raw_trace_id=_RAW_ID, solar_scale=str(scale),
        capacity_j=material["battery_capacity_j"], initial_j=material["initial_energy_j"],
        energy_quantum_j=str(Fraction(1, work.scale)),
    )
    return [dict(base, **run_method(work, method)) for method in SUITES[suite][1]]


CELL_KEYS = ("suite", "campaign", "uc", "kappa", "level", "ue")


def summarize(pairs: list[list[dict]]) -> tuple[list[dict], list[dict]]:
    groups, contrasts = defaultdict(list), defaultdict(list)
    for rows in pairs:
        methods = SUITES[rows[0]["suite"]][1]
        if tuple(row["method"] for row in rows) != methods:
            raise ValueError("missing or reordered paired method")
        # Refuse to aggregate mismatched workload, energy and observation contracts.
        invariant = (*CELL_KEYS, "taskset_hash", "horizon", "release_cutoff",
                     "raw_trace_id", "solar_scale", "capacity_j", "initial_j", "offered_j", "jobs_total")
        if any(any(row[k] != rows[0][k] for k in invariant) for row in rows):
            raise ValueError("paired method inputs differ")
        cell = tuple(rows[0][k] for k in CELL_KEYS)
        for row in rows:
            groups[cell + (row["method"],)].append(row)
        for first, second in combinations(rows, 2):
            contrasts[cell + (first["method"], second["method"])].append(
                (first["wholepass"], second["wholepass"]))
    summaries = []
    for key, rows in groups.items():
        if len({row["taskset_hash"] for row in rows}) != len(rows):
            raise ValueError("duplicate tasksets within a cell")
        complete = [r for r in rows if r["deadline_misses"] is not None]
        total_jobs = sum(r["jobs_total"] for r in complete)
        offered = sum((Fraction(r["offered_j"]) for r in complete), Fraction(0))
        responses = sum(r["completed_jobs"] for r in complete)
        passed = sum(r["wholepass"] for r in rows)
        summaries.append(dict(zip((*CELL_KEYS, "method"), key),
            samples=len(rows), passes=passed, wholepass_rate=passed / len(rows),
            no_plan=sum(r["failure_reason"] == "NO_WINDOW_PLAN" for r in rows),
            no_partition=sum(r["failure_reason"] == "NO_CPU_PARTITION" for r in rows),
            full_trajectories=len(complete), evaluated_jobs=total_jobs,
            observed_miss_rate=sum(r["deadline_misses"] for r in complete) / total_jobs if total_jobs else None,
            completed_jobs=responses,
            mean_response=sum((r["mean_response"] or 0)*r["completed_jobs"]
                              for r in complete)/responses if responses else None,
            spill_rate=float(sum(Fraction(r["spilled_j"]) for r in complete) / offered) if offered else None,
            median_schedule_ms=statistics.median(r["schedule_ns"] for r in rows)/1e6,
        ))
    paired = []
    for key, outcomes in contrasts.items():
        n = len(outcomes)
        first_only = sum(a and not b for a, b in outcomes)
        second_only = sum(b and not a for a, b in outcomes)
        paired.append(dict(zip((*CELL_KEYS, "method_a", "method_b"), key),
                           samples=n, both_pass=sum(a and b for a, b in outcomes),
                           both_fail=sum(not a and not b for a, b in outcomes),
                           a_only=first_only, b_only=second_only,
                           pass_rate_difference=(first_only-second_only)/n))
    return summaries, paired


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("empty summary")
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=(*SUITES, "both"), default="both")
    parser.add_argument("--campaign", choices=("smoke", "uc", "ue", "capacity", "all"), default="all")
    parser.add_argument("--samples-per-cell", type=int)
    parser.add_argument("--horizon", type=int)
    parser.add_argument("--seed", type=int, default=perf_g.FORMAL_SEED)
    parser.add_argument("--prepare-workers", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    count = args.samples_per_cell if args.samples_per_cell is not None else (2 if args.campaign == "smoke" else 120)
    horizon = args.horizon if args.horizon is not None else (1000 if args.campaign == "smoke" else 60000)
    if min(count, args.prepare_workers, args.workers) < 1 or not perf_g.PERIOD_MAX_MS < horizon <= NORMALIZATION_HORIZON:
        parser.error("positive counts/workers and period_max < horizon <= 60000 required")
    root = args.output.resolve()
    # Never overwrite earlier experimental results, even on an incomplete run.
    root.mkdir(parents=True, exist_ok=False)
    suites = tuple(SUITES) if args.suite == "both" else (args.suite,)
    cells = cells_for(args.campaign)
    config = dict(model=MODEL, suites=suites, cells=[c.label() for c in cells],
                  horizon=horizon, samples_per_cell=count, seed=args.seed,
                  workers=args.workers, prepare_workers=args.prepare_workers,
                  processors=4, deadlines="implicit", priority="RM",
                  energy_timing="same-slot", energy_arithmetic="exact_rational",
                  utilization_sampler="bounded-simplex",
                  forecast="perfect_within_window", static_energy="zero",
                  release_cutoff="horizon-max_period", normalization_horizon=NORMALIZATION_HORIZON,
                  code_sha256={
                      str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (Path(__file__).resolve(), ROOT/"experiments/hearts_1c/replay.py",
                                ROOT/"experiments/common/global_task_generator.py",
                                ROOT/"experiments/common/paths.py", ROOT/"experiments/common/energy_manager.py",
                                ROOT/"experiments/common/solar_data_loader.py",
                                ROOT/"configs/templates/system_config_unified_template.yml",
                                *sorted((ROOT/"experiments/pipeline").glob("*.py")))
                  })
    (root/"config.json").write_text(json.dumps(config, indent=2)+"\n", encoding="utf-8")
    all_pairs = []
    with (root/"results.jsonl").open("w", encoding="utf-8") as results, (
            root/"tasksets.jsonl").open("w", encoding="utf-8") as taskset_file:
        reference_id = None
        for suite in suites:
            n, _ = SUITES[suite]
            print(f"Preparing {suite}: 4 cores, {n} tasks", flush=True)
            tasksets, service = existing.materialize_tasksets(
                root/"material"/suite, seed=args.seed,
                utilizations=tuple(sorted({c.uc for c in cells})),
                count=count, processors=4, tasks=n, period_min=perf_g.PERIOD_MIN_MS,
                period_max=perf_g.PERIOD_MAX_MS, min_task_util=perf_g.MIN_TASK_UTILIZATION,
                max_task_util=Fraction("99/100") if suite == "hearts" else perf_g.MAX_TASK_UTILIZATION,
                tolerance=perf_g.UTILIZATION_TOLERANCE, prepare_workers=args.prepare_workers,
                deadline_mode="implicit", initial_energy_rule="zero",
                utilization_sampler="bounded-simplex",
            )
            raw = existing.construct_paired_harvest_trace(service.system_path, NORMALIZATION_HORIZON)
            raw_id = existing.harvest_trace_identity(raw)
            if reference_id is not None and reference_id != raw_id:
                raise ValueError("suites use different raw harvest traces")
            reference_id = raw_id
            by_uc = defaultdict(list)
            for taskset in tasksets:
                by_uc[taskset.target_utilization/4].append(taskset)
                taskset_file.write(json.dumps(dict(suite=suite, **existing.taskset_row(taskset, 4)))+"\n")
            for cell in cells:
                if len(by_uc[cell.uc]) != count:
                    raise ValueError("unexpected taskset count")
            jobs = [(suite, cell, t, horizon) for cell in cells for t in by_uc[cell.uc]]
            _init_worker(raw, raw_id)
            pool = ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker,
                                       initargs=(raw, raw_id)) if args.workers > 1 else None
            try:
                iterator = pool.map(run_pair, jobs) if pool else map(run_pair, jobs)
                for i, rows in enumerate(iterator, 1):
                    all_pairs.append(rows)
                    for row in rows:
                        results.write(json.dumps(row, allow_nan=False)+"\n")
                    results.flush()
                    if i % max(1, count) == 0:
                        print(f"{suite}: {i}/{len(jobs)} paired cases", flush=True)
            finally:
                if pool:
                    pool.shutdown(cancel_futures=True)
    summaries, contrasts = summarize(all_pairs)
    write_csv(root/"summary.csv", summaries)
    write_csv(root/"paired_comparison.csv", contrasts)
    (root/"COMPLETE.json").write_text(json.dumps(dict(
        model=MODEL, paired_cases=len(all_pairs), result_rows=sum(map(len, all_pairs)),
        raw_trace_id=reference_id), indent=2)+"\n", encoding="utf-8")
    print(f"Completed {len(all_pairs)} paired cases in {root}", flush=True)


if __name__ == "__main__":
    main()
