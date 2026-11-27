"""Exact energy, scheduling, pairing, and native ASAP reference conformance."""
from fractions import Fraction as F
from itertools import product
import json
import os
from pathlib import Path
import random
import re
import subprocess
import tempfile
import unittest

from experiments.hearts_1c.replay import (
    Task, Workload, WindowLedger, evaluate, hearts_plan, online_plan,
    partition_tasks, run_method,
)
from scripts.run_hearts_1c_comparison import cells_for, summarize, SUITES

ROOT = Path(__file__).resolve().parents[1]


class LedgerTests(unittest.TestCase):
    def test_same_slot_expenditure_precedes_capacity_clipping(self):
        w = Workload.create([Task("a", 1, 1, F(3))], 1, [3, 9], 0, 1, release_cutoff=1)
        for method in ("HEARTS-1C", "GLOBAL-AB", "GLOBAL-AN", "PARTITIONED-AB"):
            result = run_method(w, method, details=True)
            self.assertTrue(result["wholepass"])
            self.assertEqual(result["trace"][0]["jobs"], [0])
            self.assertEqual(result["consumed_j"], "3")
            self.assertEqual(result["spilled_j"], "8")
            self.assertEqual(result["final_battery_j"], "1")

    def test_exhaustive_insertion_matches_independent_forward_simulation(self):
        def feasible(supply, used, initial, cap):
            battery = initial
            for h, p in zip(supply, used):
                if battery + h < p:
                    return False
                battery = min(cap, battery + h - p)
            return True

        for cap in range(3):
            for initial in range(cap + 1):
                for supply in product(range(3), repeat=3):
                    for used in product(range(3), repeat=3):
                        if not feasible(supply, used, initial, cap):
                            continue
                        ledger = WindowLedger(supply, used, initial, cap)
                        rng = random.Random(137)
                        # Successive insertions exercise incremental state updates.
                        for s, cost in [(s, cost) for s, cost in
                                        product(range(3), range(3))]:
                            modified = ledger.used.copy()
                            modified[s] += cost
                            expected = feasible(supply, modified, initial, cap)
                            before = (ledger.used.copy(), ledger.before.copy(), ledger.needed.copy())
                            self.assertEqual(ledger.place(s, cost), expected)
                            if expected:
                                rebuilt = WindowLedger(supply, modified, initial, cap)
                                self.assertEqual(ledger.before, rebuilt.before)
                                self.assertEqual(ledger.needed, rebuilt.needed)
                            else:
                                self.assertEqual((ledger.used, ledger.before, ledger.needed), before)
                        # Also compare randomly ordered insertions.
                        for _ in range(3):
                            s, cost = rng.randrange(3), rng.randrange(3)
                            modified = ledger.used.copy()
                            modified[s] += cost
                            self.assertEqual(ledger.place(s, cost), feasible(supply, modified, initial, cap))

    def test_reservation_prevents_earlier_lower_priority_spending(self):
        ledger = WindowLedger([2, 0, 0], [0, 0, 2], 0, 2)
        self.assertFalse(ledger.place(0, 1))
        self.assertEqual(ledger.used, [0, 0, 2])


class SchedulerTests(unittest.TestCase):
    def test_palap_fallback_restores_original_reservations(self):
        w = Workload.create([Task("a", 1, 2, F(1)), Task("b", 2, 3, F(1))],
                            2, [3, 0, 0, 0], 0, 1, release_cutoff=1)
        plan, stats = hearts_plan(w)
        self.assertEqual(stats["pasap_success"], 1)
        self.assertEqual(plan[:3], [[0, 1], [1], []])
        self.assertTrue(evaluate(w, plan, (0, 1))["wholepass"])

    def test_overlapping_windows_keep_jobs_and_reservations(self):
        tasks = [Task("a", 2, 3, F(1)), Task("b", 3, 5, F(2)), Task("c", 4, 7, F(3))]
        w = Workload.create(tasks, 3, [8]*101, 0, 3)
        result = run_method(w, "HEARTS-1C", details=True)
        self.assertTrue(result["wholepass"])
        self.assertGreater(result["windows"], 10)
        self.assertEqual(result["palap_success"], result["windows"])
        actual = [[] for _ in w.jobs]
        for row in result["trace"]:
            for j in row["jobs"]:
                actual[j].append(row["tick"])
        for j, job in enumerate(w.jobs):
            self.assertEqual(actual[j], list(range(job.deadline-tasks[job.task].C, job.deadline)))

    def test_ab_prefix_vs_an_bypass_vs_dedicated(self):
        w = Workload.create([Task("expensive", 1, 2, F(4)), Task("cheap", 1, 3, F(1))],
                            2, [1, 4, 0, 0], 0, 5, release_cutoff=1)
        ab = run_method(w, "GLOBAL-AB", details=True)
        an = run_method(w, "GLOBAL-AN", details=True)
        self.assertEqual(ab["trace"][0]["jobs"], [])
        self.assertEqual(an["trace"][0]["jobs"], [1])
        self.assertTrue(ab["wholepass"])
        self.assertTrue(an["wholepass"])
        self.assertEqual(online_plan(w, blocking=True, mapping=(0, 1)),
                         online_plan(w, blocking=True))

    def test_planning_failure_not_reported_as_physical_deadline_miss(self):
        w = Workload.create([Task("a", 1, 2, F(2)), Task("b", 1, 3, F(2))],
                            2, [0]*4, 0, 1, release_cutoff=1)
        r = run_method(w, "HEARTS-1C")
        self.assertFalse(r["wholepass"])
        self.assertEqual(r["failure_reason"], "NO_WINDOW_PLAN")
        self.assertIsNone(r["deadline_misses"])
        self.assertIsNone(r["spilled_j"])
        ab = run_method(w, "GLOBAL-AB")
        self.assertEqual(ab["deadline_misses"], 2)
        self.assertEqual(ab["first_miss"], {"time": 2, "task": "a", "remaining": 1})

    def test_partition_search_matches_exhaustive_cpu_only_fixed_priority(self):
        rng = random.Random(26)
        for _ in range(100):
            periods = sorted(rng.randint(2, 8) for _ in range(rng.randint(2, 6)))
            tasks = [Task(str(i), rng.randint(1, t), t, F(1)) for i, t in enumerate(periods)]
            processors = rng.randint(1, 3)
            w = Workload.create(tasks, processors, [processors]*169, 0, 0)
            def independent_valid(mapping):
                # Synchronous implicit deadlines and no energy restriction:
                # simulate one full hyperperiod, including every deadline.
                from math import lcm
                horizon = lcm(*periods)
                remaining = [0]*len(tasks)
                for tick in range(horizon+1):
                    for i, task in enumerate(tasks):
                        if tick % task.T == 0:
                            if remaining[i]:
                                return False
                            remaining[i] = task.C
                    busy = set()
                    for i, core in enumerate(mapping):
                        if remaining[i] and core not in busy:
                            remaining[i] -= 1
                            busy.add(core)
                return True
            expected = any(independent_valid(m) for m in product(range(processors), repeat=len(tasks)))
            mapping = partition_tasks(tasks, processors)
            self.assertEqual(mapping is not None, expected)
            if mapping is not None:
                self.assertTrue(independent_valid(mapping))
                self.assertTrue(run_method(w, "PARTITIONED-AB")["wholepass"])
            else:
                self.assertEqual(run_method(w, "PARTITIONED-AB")["failure_reason"], "NO_CPU_PARTITION")

    def test_partitioned_head_blocks_local_bypass_and_global_can_use_spare_core(self):
        tasks = [Task("a", 1, 2, F(1)), Task("b", 1, 3, F(1)), Task("c", 1, 4, F(1))]
        w = Workload.create(tasks, 2, [3]*5, 0, 0, release_cutoff=1)
        part = online_plan(w, blocking=True, mapping=(0, 0, 1))
        glob = online_plan(w, blocking=True)
        self.assertEqual(part[0], [0, 2])
        self.assertEqual(glob[0], [0, 1])

    def test_randomized_replays_are_valid(self):
        rng = random.Random(77)
        for _ in range(100):
            periods = sorted(rng.randint(2, 9) for _ in range(3))
            tasks = [Task(str(i), rng.randint(1, t), t, F(rng.randint(1, 5), 7))
                     for i, t in enumerate(periods)]
            w = Workload.create(tasks, 3, [F(rng.randint(0, 15), 7) for _ in range(40)],
                                0, F(rng.randrange(10), 7))
            for method in ("HEARTS-1C", "GLOBAL-AB", "GLOBAL-AN", "PARTITIONED-AB"):
                r = run_method(w, method)
                if r["deadline_misses"] is not None:
                    self.assertEqual(F(r["offered_j"]), F(r["consumed_j"])+F(r["spilled_j"])+F(r["final_battery_j"]))
                    self.assertEqual(r["jobs_total"], r["completed_jobs"]+r["deadline_misses"])

    def test_evaluation_rejects_invalid_trajectory(self):
        w = Workload.create([Task("a", 1, 2, F(1)), Task("b", 1, 2, F(1))],
                            2, [1, 1, 0], 0, 1, release_cutoff=1)
        for slots, mapping in (([[0, 1], [], []], None),
                               ([[0], [0], []], None),
                               ([[], [], [0]], None),
                               ([[0, 1], [], []], (0, 0))):
            with self.assertRaises(ValueError):
                evaluate(w, slots, mapping)

    def test_half_open_release_cutoff(self):
        w = Workload.create([Task("a", 1, 3, F(1)), Task("b", 1, 5, F(1))],
                            2, [10]*15, 0, 1)
        self.assertEqual(w.release_cutoff, 10)
        self.assertLess(max(j.deadline for j in w.jobs), 15)
        with self.assertRaises(ValueError):
            Workload.create(w.tasks, 2, [1]*15, 0, 1, release_cutoff=14)


class GeneratorTests(unittest.TestCase):
    def test_bounded_sampler_all_loads_and_legacy_compatibility(self):
        from experiments.common.global_task_generator import UUniFastDiscard
        from unittest.mock import patch
        with patch("experiments.common.global_task_generator.random.random", return_value=0.25):
            self.assertEqual(UUniFastDiscard().generate(2, 1.0, 0.01, 0.8), [0.75, 0.25])
        for n in (4, 10):
            max_util = 0.99 if n == 4 else 0.8
            for uc in range(1, 10):
                for seed in range(120):
                    target = 4 * uc / 10
                    draws = UUniFastDiscard(seed).generate(
                        n, target, 0.01, max_util, sampler="bounded-simplex")
                    self.assertEqual(len(draws), n)
                    self.assertAlmostEqual(sum(draws), target)
                    self.assertTrue(all(0.01 - 1e-12 <= x <= max_util + 1e-12 for x in draws))

    def test_sampler_is_identified_without_changing_legacy_generation_id(self):
        from experiments.v9_3 import scheduler_load_cross as existing
        from experiments.v9_3.cell_model import generation_dimensions
        cfg = existing._config(26, utilizations=(F("9/10"),), count=1,
                               processors=4, tasks=4, period_min=40, period_max=200,
                               min_task_util=F("1/100"), max_task_util=F("99/100"),
                               tolerance=F("1/100"), deadline_mode="implicit")
        original = generation_dimensions(cfg, 4, 4, F("9/10"))
        cfg["generation"]["utilization_sampler"] = "legacy"
        self.assertEqual(generation_dimensions(cfg, 4, 4, F("9/10")), original)
        cfg["generation"]["utilization_sampler"] = "bounded-simplex"
        self.assertEqual(generation_dimensions(cfg, 4, 4, F("9/10")),
                         dict(original, utilization_sampler="bounded-simplex"))


class PairingTests(unittest.TestCase):
    def test_campaign_grid_and_paired_summary(self):
        self.assertEqual(len(cells_for("all")), 57)
        w = Workload.create([Task("a", 1, 2, F(1))], 1, [1, 0, 0], 0, 0)
        meta = dict(suite="hearts", **cells_for("smoke")[0].label(), taskset_hash="example",
                    horizon=3, release_cutoff=1, raw_trace_id="example", solar_scale="1",
                    capacity_j="0", initial_j="0")
        rows = [dict(meta, **run_method(w, m)) for m in SUITES["hearts"][1]]
        summary, paired = summarize([rows])
        self.assertEqual(len(summary), 3)
        self.assertEqual(len(paired), 3)
        self.assertTrue(all(r["wholepass_rate"] == 1 for r in summary))
        with self.assertRaises(ValueError):
            summarize([rows[:-1]])
        rows[1]["capacity_j"] = "1"
        with self.assertRaises(ValueError):
            summarize([rows])


class NativeConformanceTests(unittest.TestCase):
    def test_global_reference_matches_actual_same_slot_binary(self):
        from experiments.v9_3.simulation_engine import materialize_simulation_inputs
        binary = Path(os.getenv("PARTSIM_TEST_BINARY", ROOT/"build/rtsim/rtsim"))
        if not binary.is_file():
            self.skipTest("rebuild PARTsim or set PARTSIM_TEST_BINARY")
        inputs = [(1, 3, "93/250000", "hash"), (2, 5, "279/500000", "bzip2"),
                  (2, 7, "93/2000000", "control")]
        payload = tuple(dict(task_id=str(i), priority_rank=i, C=c, D=t, T=t,
                             P=power, workload=name, arrival_offset=0)
                        for i, (c, t, power, name) in enumerate(inputs))
        tasks = [Task(str(i), c, t, F(power)) for i, (c, t, power, _) in enumerate(inputs)]
        traces = [
            [0,372,1000,50,900,0,400,1500,300,50,900,0,800,90,400,700,100,1000,0,800,100,100,100,200,100],
            [50,0,100,0,0,800,0,100,50,100,0,0,50,0,800,0,100,0,0,0,100,0,100,0,100],
        ]
        keys = ("day_of_year", "time_of_day_ms", "base_harvesting_rate", "harvesting_scale",
                "use_real_solar_data", "solar_data_file", "pv_efficiency", "pv_area_m2")
        for trace, method in product(traces, ("GLOBAL-AB", "GLOBAL-AN")):
            with self.subTest(trace=trace, method=method), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                scheduler = "gpfp_asap_block" if method == "GLOBAL-AB" else "gpfp_asap_nonblock"
                w = Workload.create(tasks, 2, [F(x, 10**6) for x in trace], 0, F(1, 1000),
                                    release_cutoff=18)
                reference = run_method(w, method, details=True)
                system, tasks_path = materialize_simulation_inputs(
                    ROOT/"configs/templates/system_config_unified_template.yml", root, payload, processors=2,
                    initial_battery=F(0), battery_capacity=F(1,1000), scheduler_id=scheduler,
                    service_curve=dict(solar_scale="1", use_real_solar_data=False), release_horizon=18)
                text = "\n".join(line for line in system.read_text().splitlines()
                                 if not any(re.match(r"^\s+"+k+r":", line) for k in keys))
                text += "\n\nharvesting:\n  source: scaled_piecewise\n  scaled_piecewise:\n    scale_w: 0.001\n    segments:\n"
                text += "".join(f"      - start_ms: {i}\n        end_ms: {i+1}\n        multiplier: {value}\n"
                                for i, value in enumerate(trace))
                system.write_text(text, encoding="utf-8")
                path = root/"trace.json"
                run = subprocess.run([str(binary.resolve()), str(system), str(tasks_path), str(len(trace)),
                                      "-t", str(path), "--energy-timing", "same-slot", "--semantic-traces",
                                      "--taskset-semantic-hash", "a"*64, "--run-id", "hearts-native-check"],
                                     cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr[-2000:])
                observed = json.loads(path.read_text())
                self.assertTrue(observed["simulation_completed"])
                decisions = {int(e["time"]): sorted(j["task_name"] for j in e["selected_jobs"])
                             for e in observed["events"] if e["event_type"] == "scheduler_decision"}
                for row in reference["trace"]:
                    expected = sorted("v93_task_"+w.tasks[w.jobs[j].task].name for j in row["jobs"])
                    self.assertEqual(decisions.get(row["tick"], []), expected, f"tick {row['tick']}")
                ends = [e for e in observed["events"] if e["event_type"] == "end_instance"]
                misses = [e for e in observed["events"] if e["event_type"] == "dline_miss"]
                self.assertEqual(len(ends), reference["completed_jobs"])
                self.assertEqual(len(misses), reference["deadline_misses"])
                if misses:
                    self.assertEqual(int(misses[0]["time"]), reference["first_miss"]["time"])
                ledger = observed["same_slot_energy_summary"]
                self.assertEqual(ledger["observed_energy_intervals"], len(trace))
                for native_key, ref_key in (("offered_energy_j", "offered_j"),
                                            ("consumed_energy_j", "consumed_j"),
                                            ("clipped_energy_j", "spilled_j"),
                                            ("battery_final_j", "final_battery_j")):
                    self.assertAlmostEqual(ledger[native_key], float(F(reference[ref_key])), places=12)


if __name__ == "__main__":
    unittest.main()
