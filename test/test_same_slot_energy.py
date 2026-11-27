"""Run with python -m unittest discover -s test -p test_same_slot_energy.py.

Set PARTSIM_TEST_BINARY to the actual rebuilt executable to include integration
tests of all nine strategies, in addition to the exact RTA tests.
"""
import copy
from fractions import Fraction
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest

from experiments.same_slot import ab_rta
from experiments.v9_3.simulation_engine import run_paired_simulation
from experiments.v9_3.simulation_result import SimulationStatus

ROOT = Path(__file__).resolve().parents[1]

class SameSlotRTATests(unittest.TestCase):
    def test_exact_finite_beta_and_known_profile(self):
        tasks = [ab_rta.Task(str(i), *row) for i, row in enumerate(
            [(1,4,5,3), (2,5,7,1), (1,7,9,2)])]
        beta = [4*(i//2) for i in range(8)]
        for bound in ("analytic", "flow"):
            answer = ab_rta.least_certificate(tasks, 2, beta, bound=bound)
            self.assertEqual(answer["status"], "CERTIFIED")
            self.assertEqual(answer["certificate"]["profile"], ((2,2),(2,4),(4,4)))
            self.assertEqual(answer["verification"]["status"], "PASS")
            old = copy.deepcopy(answer["certificate"])
            old["model"] = ab_rta.frozen.MODEL
            with self.assertRaises(ValueError):
                ab_rta.verify_certificate(tasks, 2, beta, 0, old)

    def test_current_harvest_supports_release_even_if_initial_is_zero(self):
        task = ab_rta.Task("1", 1, 1, 2, 3)
        answer = ab_rta.least_certificate([task], 1, [0,3], 0)
        self.assertEqual(answer["certificate"]["response_bounds"], [1])
        self.assertEqual(answer["certificate"]["details"][0]["full"]["kind"],
                         "SAME_SLOT_PROGRESS_WINDOW")
        no_energy = ab_rta.least_certificate([task], 1, [0,0], 0)
        self.assertEqual(no_energy["status"], "NO_CERTIFICATE")

    def test_finite_beta_no_out_of_window_queries(self):
        task = ab_rta.Task("1", 1, 2, 3, 3)
        self.assertEqual(ab_rta.least_certificate([task], 1, [0,0,3])[
            "certificate"]["response_bounds"], [2])
        self.assertEqual(ab_rta.least_certificate([task], 1, [0,0,2])[
            "status"], "NO_CERTIFICATE")

    def test_analytic_flow_agree_on_small_exact_inputs(self):
        import random
        rng = random.Random(126)
        for _ in range(100):
            tasks = []
            for i in range(rng.randint(1,3)):
                t = rng.randint(2,6)
                d = rng.randint(1,t)
                tasks.append(ab_rta.Task(str(i),rng.randint(1,d),d,t,rng.randint(1,4)))
            length = max(t.deadline for t in tasks)
            step = rng.randint(0,4)
            period = rng.randint(1,3)
            beta = [step*(i//period) for i in range(length+1)]
            cores, e0 = rng.randint(1,3), rng.randint(0,4)
            a = ab_rta.least_certificate(tasks,cores,beta,e0,bound="analytic")
            b = ab_rta.least_certificate(tasks,cores,beta,e0,bound="flow")
            self.assertEqual(a["status"],b["status"])
            if a["certificate"]:
                self.assertEqual(a["certificate"]["profile"],b["certificate"]["profile"])

    def test_rejects_unclear_release_floor_and_old_timing(self):
        model=dict(tasks=[dict(name="1", C=1,D=1,T=2,power=3)],
                   processors=1,beta=[0,3],release_floor=0,
                   background_power=0,capacity=None,priority="INPUT_ORDER",
                   harvest_order="DEBIT_BEFORE_HARVEST")
        with self.assertRaises(ValueError):
            ab_rta.analyze_case({"model":model})
        model["harvest_order"]="HARVEST_BEFORE_DEBIT"
        model["release_floor_stage"]="POST_HARVEST"
        with self.assertRaises(ValueError):
            ab_rta.analyze_case({"model":model})


class SameSlotSimulatorTests(unittest.TestCase):
    def test_release_snapshot_precedes_harvest_and_idle_tail_reaches_horizon(self):
        from experiments.v9_3.simulation_engine import materialize_simulation_inputs
        binary = Path(os.getenv("PARTSIM_TEST_BINARY",ROOT/"build/rtsim/rtsim"))
        if not binary.is_file():
            self.skipTest("rebuild simulator or set PARTSIM_TEST_BINARY")
        payload = (dict(task_id="0", priority_rank=0, C=1,D=1,T=2,
                        P="93/250000", workload="hash", arrival_offset=0),)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            system, task = materialize_simulation_inputs(
                ROOT/"system_config_unified_template.yml", root, payload,
                processors=1,initial_battery=Fraction(0),
                battery_capacity=Fraction(1,10000),scheduler_id="gpfp_asap_block",
                service_curve=dict(solar_scale="100",use_real_solar_data=False),
                release_horizon=3)
            run = subprocess.run([str(binary.resolve()),str(system),str(task),"5",
                "-t",str(root/"trace.json"),"--energy-timing","same-slot",
                "--semantic-traces","--taskset-semantic-hash","a"*64,
                "--run-id","same-slot-cutoff-smoke"],cwd=ROOT,text=True,
                capture_output=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            trace=json.loads((root/"trace.json").read_text())
            self.assertEqual(trace["simulator_trace_contract_version"],
                             "ASAP_BLOCK_SAME_SLOT_RELEASE_CUTOFF_V1")
            snapshots=[event for event in trace["events"]
                       if event["event_type"]=="release_energy_snapshot"]
            self.assertEqual(len(snapshots),2)
            self.assertEqual(snapshots[0]["available_energy_mJ"],0)
            self.assertTrue(all(event["sampling_stage"]=="pre_harvest_pre_consumption"
                                for event in snapshots))
            self.assertEqual(trace["same_slot_energy_summary"]["observed_energy_intervals"],5)

    def test_nine_strategies_use_the_same_energy_timing_and_conserve(self):
        binary = Path(os.getenv("PARTSIM_TEST_BINARY",ROOT/"build/rtsim/rtsim"))
        if not binary.is_file():
            self.skipTest("rebuild simulator or set PARTSIM_TEST_BINARY")
        payload = (dict(task_id="0", priority_rank=0, C=1,D=1,T=2,
                        P="93/250000", workload="hash", arrival_offset=0),)
        with tempfile.TemporaryDirectory() as temp:
            for family in ("asap","alap","st"):
                for behavior in ("block","nonblock","sync"):
                    scheduler = f"gpfp_{family}_{behavior}"
                    for timing in ("debit-before-harvest","same-slot"):
                        with self.subTest(scheduler=scheduler,timing=timing):
                            tag=f"{scheduler}-{timing}"
                            execution = run_paired_simulation(
                                simulation_id_value=tag,
                                base_system_path=ROOT/"system_config_unified_template.yml",
                                run_root=Path(temp)/tag, task_payload=payload,
                                taskset_hash="a"*64, processors=1, exact_e0=Fraction(0),
                                energy_config=dict(simulation_initial_battery="0",
                                    battery_capacity="1/10000",allow_harvest_clipping=True,
                                    service_curve=dict(solar_scale="100",use_real_solar_data=False)),
                                simulation_config=dict(simulator_bin=str(binary.resolve()),
                                    horizon=4,maximum_horizon=4,horizon_extension_policy="none",
                                    priority_policy="RM",warmup=0,minimum_jobs_per_task=1,
                                    trace_mode="semantic",trace_on_failure=True,retain_trace=True,
                                    timeout_seconds=30,cleanup_transient_artifacts=False,
                                    energy_timing=timing),
                                scheduler_id=scheduler)
                            result=execution.result
                            if timing=="same-slot":
                                self.assertEqual(result.status,SimulationStatus.PASS_OBSERVED,
                                                 execution.stderr_tail)
                                ledger=result.metrics["same_slot_energy_summary"]
                                self.assertEqual(ledger["observed_energy_intervals"],4)
                                self.assertAlmostEqual(ledger["consumed_energy_j"],2*93/250000)
                                self.assertAlmostEqual(ledger["battery_final_j"],0.0001)
                                self.assertGreater(ledger["clipped_energy_j"],0)
                            else:
                                self.assertEqual(result.status,SimulationStatus.DEADLINE_MISS,
                                                 execution.stderr_tail)
if __name__ == "__main__":
    unittest.main()
