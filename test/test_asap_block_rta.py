import itertools
import os
import sys
import tempfile
import unittest

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from asap_block_rta import (
    InputValidationError,
    RTATask,
    _format_report,
    analyze_taskset,
    beta_inverse,
    build_energy_service_curve,
    energy_blocking_bound,
    hp,
    load_system_config,
    load_tasks,
    prefix_energy_upper_bound,
    processor_delay,
    response_time_bound,
    rm_order,
    workload_bound,
)


class ASAPBlockRTATest(unittest.TestCase):
    def write_yaml(self, contents):
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        )
        with handle:
            yaml.safe_dump(contents, handle, sort_keys=False)
        self.addCleanup(
            lambda: os.path.exists(handle.name) and os.unlink(handle.name)
        )
        return handle.name

    def system_file(self, num_cores=1, max_energy=100.0):
        return self.write_yaml(
            {
                "cpu_islands": [
                    {
                        "name": "island0",
                        "numcpus": num_cores,
                        "base_freq": 8100,
                    }
                ],
                "energy_management": {
                    "initial_energy": 99.0,
                    "max_energy": max_energy,
                    "use_real_solar_data": False,
                    "day_of_year": 1,
                    "time_of_day_ms": 0,
                    "scheduler_energy_model": {
                        "base_power": 1.0,
                        "workload_coefficients": {
                            "low": 1.0,
                            "high": 2.0,
                            "very_high": 5.0,
                            "idle": 0.1,
                            "control": 0.1,
                        },
                        "frequency_power_ratios": {8100: 1.0},
                    },
                },
            }
        )

    @staticmethod
    def task_spec(
        name,
        period,
        wcet,
        deadline=None,
        workload="low",
        code=None,
        params=None,
    ):
        if deadline is None:
            deadline = period
        if params is None:
            params = (
                "period={},wcet={},arrival_offset=0,workload={}".format(
                    period, wcet, workload
                )
            )
        if code is None:
            code = ["fixed({}, {})".format(wcet, workload)]
        return {
            "name": name,
            "iat": period,
            "runtime": wcet,
            "deadline": deadline,
            "params": params,
            "code": code,
        }

    def tasks_file(self, specs, resources=None):
        document = {"taskset": specs}
        if resources is not None:
            document["resources"] = resources
        return self.write_yaml(document)

    @staticmethod
    def attach(tasks, cores):
        taskset = tuple(tasks)
        for task in tasks:
            task._taskset = taskset
            task._num_cores = cores
        return tasks

    def test_rm_order_uses_yaml_order_for_equal_periods(self):
        tasks = self.attach(
            [
                RTATask("late", 10, 1, 10, "low", 2),
                RTATask("first_equal", 5, 1, 5, "low", 0),
                RTATask("second_equal", 5, 1, 5, "low", 1),
            ],
            1,
        )
        self.assertEqual(
            [task.name for task in rm_order(tasks)],
            ["first_equal", "second_equal", "late"],
        )
        self.assertEqual(
            [task.name for task in hp(tasks, tasks[0])],
            ["first_equal", "second_equal"],
        )

    def test_load_tasks_accepts_consistent_single_fixed_task(self):
        path = self.tasks_file([self.task_spec("t0", 10, 3)])
        task = load_tasks(path)[0]
        self.assertEqual((task.period, task.wcet, task.deadline), (10, 3, 10))
        self.assertEqual(task.workload, "low")

    def test_load_tasks_rejects_field_mismatch(self):
        spec = self.task_spec(
            "t0",
            10,
            3,
            params="period=10,wcet=2,arrival_offset=0,workload=low",
        )
        with self.assertRaisesRegex(InputValidationError, "inconsistent runtime"):
            load_tasks(self.tasks_file([spec]))

    def test_energy_model_key_priority_and_legacy_fallback(self):
        canonical_path = self.write_yaml(
            {
                "cpu_islands": [{"numcpus": 1, "base_freq": 8100}],
                "energy_management": {
                    "scheduler_energy_model": {
                        "base_power": 2.0,
                        "workload_coefficients": {"low": 3.0},
                        "frequency_power_ratios": {8100: 4.0},
                        "frequency_scaling": {8100: 8.0},
                    },
                    "consumption_model": {
                        "base_power": 9.0,
                        "workload_coefficients": {"low": 9.0},
                        "frequency_scaling": {8100: 9.0},
                    },
                },
            }
        )
        with self.assertWarns(RuntimeWarning):
            canonical = load_system_config(canonical_path)
        self.assertAlmostEqual(canonical.task_energy_per_tick("low"), 0.024)

        legacy_path = self.write_yaml(
            {
                "cpu_islands": [{"numcpus": 1, "base_freq": 8100}],
                "energy_management": {
                    "consumption_model": {
                        "base_power": 2.0,
                        "workload_coefficients": {"low": 3.0},
                        "frequency_scaling": {8100: 4.0},
                    }
                },
            }
        )
        legacy = load_system_config(legacy_path)
        self.assertAlmostEqual(legacy.task_energy_per_tick("low"), 0.024)

    def test_workload_bound_includes_carry_in(self):
        task = RTATask("t0", 10, 3, 10, "low", 0)
        self.assertEqual(workload_bound(task, 1), 6)
        self.assertEqual(workload_bound(task, 10), 6)
        self.assertEqual(workload_bound(task, 11), 9)

    def test_processor_delay_single_core(self):
        high = RTATask("high", 5, 2, 5, "low", 0)
        target = RTATask("target", 10, 3, 10, "low", 1)
        tasks = self.attach([high, target], 1)
        self.assertEqual(processor_delay(target, 3, 1, tasks), 1)

    def test_processor_delay_multicore_respects_sequential_tasks(self):
        high1 = RTATask("high1", 5, 3, 5, "low", 0)
        high2 = RTATask("high2", 6, 3, 6, "low", 1)
        target = RTATask("target", 10, 3, 10, "low", 2)
        tasks = self.attach([high1, high2, target], 2)
        self.assertEqual(processor_delay(target, 5, 2, tasks), 3)
        self.assertEqual(
            processor_delay(target, 5, 2, [high1, target]),
            0,
        )

    def brute_prefix_energy(self, tasks, target, w, x, cores):
        high = hp(tasks, target)
        ordered = rm_order(tasks)
        target_pos = ordered.index(target)
        low = ordered[target_pos + 1 :]
        delay = processor_delay(target, w, cores, tasks)
        bars = {
            task.name: min(
                workload_bound(task, w),
                max(w - target.wcet + 1, 0),
            )
            for task in high
        }
        best = 0.0
        for z in range(max(0, x - delay), min(target.wcet, x) + 1):
            a_ranges = [
                range(min(workload_bound(task, w), x - z, bars[task.name]) + 1)
                for task in high
            ]
            b_ranges = [
                range(min(workload_bound(task, w), z) + 1)
                for task in high
            ]
            c_ranges = [
                range(min(workload_bound(task, w), z) + 1)
                for task in low
            ]
            for a_values in itertools.product(*a_ranges):
                if sum(a_values) > cores * (x - z):
                    continue
                for b_values in itertools.product(*b_ranges):
                    if any(
                        a + b > workload_bound(task, w)
                        for task, a, b in zip(high, a_values, b_values)
                    ):
                        continue
                    for c_values in itertools.product(*c_ranges):
                        if sum(b_values) + sum(c_values) > (cores - 1) * z:
                            continue
                        energy = z * target.energy_per_tick
                        energy += sum(
                            (a + b) * task.energy_per_tick
                            for task, a, b in zip(high, a_values, b_values)
                        )
                        energy += sum(
                            c * task.energy_per_tick
                            for task, c in zip(low, c_values)
                        )
                        best = max(best, energy)
        return best

    def test_prefix_energy_matches_small_brute_force_case(self):
        high = RTATask("high", 5, 2, 5, "high", 0, 2.0)
        target = RTATask("target", 10, 2, 10, "low", 1, 1.0)
        low = RTATask("low", 20, 2, 20, "very_high", 2, 5.0)
        tasks = self.attach([high, target, low], 2)
        for x in range(0, 3):
            expected = self.brute_prefix_energy(tasks, target, 3, x, 2)
            actual = prefix_energy_upper_bound(target, 3, x, tasks, 2)
            self.assertAlmostEqual(actual, expected)

    def test_low_priority_high_energy_contributes_only_to_b_pool(self):
        target = RTATask("target", 10, 2, 10, "low", 0, 1.0)
        low = RTATask("low", 20, 5, 20, "very_high", 1, 10.0)
        tasks = self.attach([target, low], 2)
        # x=1 forces z=1, so B has one slot and the low task may contribute.
        self.assertEqual(
            prefix_energy_upper_bound(target, 2, 1, tasks, 2),
            11.0,
        )
        # With no target execution, B has zero capacity; low priority cannot use A.
        self.assertEqual(
            prefix_energy_upper_bound(target, 2, 0, tasks, 2),
            0.0,
        )

    def test_low_priority_task_cannot_enter_nonempty_a_pool(self):
        high1 = RTATask("high1", 5, 1, 5, "high", 0, 10.0)
        high2 = RTATask("high2", 6, 1, 6, "high", 1, 10.0)
        target = RTATask("target", 10, 2, 10, "low", 2, 1.0)
        low = RTATask("low", 20, 5, 20, "very_high", 3, 11.0)
        tasks = self.attach([high1, high2, target, low], 2)

        self.assertEqual(processor_delay(target, 2, 2, tasks), 1)
        # For x=1, z=0 gives A=2 and B=0. The two high-priority jobs
        # contribute 20; allowing the low-priority job into A would yield 21.
        self.assertEqual(
            prefix_energy_upper_bound(target, 2, 1, tasks, 2),
            20.0,
        )

    def test_constant_zero_and_step_service_curves(self):
        constant = build_energy_service_curve([2.0] * 4, 4)
        self.assertEqual(list(constant), [0.0, 2.0, 4.0, 6.0, 8.0])
        self.assertEqual(beta_inverse(constant, 5.0), 3)

        zero = build_energy_service_curve([0.0] * 4, 4)
        self.assertIsNone(beta_inverse(zero, 0.1))
        self.assertEqual(beta_inverse(zero, 0.0), 0)

        step = build_energy_service_curve([0.0, 1.0, 2.0], 3)
        self.assertEqual(list(step), [0.0, 0.0, 1.0, 3.0])
        self.assertEqual(beta_inverse(step, 0.5), 2)
        self.assertEqual(beta_inverse(step, 2.0), 3)

    def test_beta_inverse_uses_strict_discrete_boundaries(self):
        beta = build_energy_service_curve([1.0, 1.0], 2)
        just_above_one = 1.0 + 5e-13

        self.assertEqual(beta_inverse(beta, 0.0), 0)
        self.assertEqual(beta_inverse(beta, 1.0), 1)
        self.assertEqual(beta_inverse(beta, just_above_one), 2)
        self.assertEqual(beta_inverse(beta, 2.0), 2)
        self.assertIsNone(beta_inverse(beta, 2.0 + 5e-13))

        plain_beta = [0.0, 1.0, 2.0]
        self.assertEqual(beta_inverse(plain_beta, 1.0), 1)
        self.assertEqual(beta_inverse(plain_beta, just_above_one), 2)

    def test_abundant_energy_reduces_to_cpu_only_bound(self):
        high = RTATask("high", 5, 1, 5, "low", 0, 0.001)
        target = RTATask("target", 10, 2, 10, "low", 1, 0.001)
        tasks = self.attach([high, target], 1)
        beta = build_energy_service_curve([1.0] * 20, 20)
        result = response_time_bound(
            target, tasks, 1, beta, assume_no_overflow=True
        )
        self.assertTrue(result.proven)
        expected_cpu_only = target.wcet + processor_delay(
            target, result.response_time_bound, 1, tasks
        )
        self.assertEqual(result.response_time_bound, expected_cpu_only)
        self.assertEqual(
            energy_blocking_bound(
                target,
                result.response_time_bound,
                beta,
                tasks=tasks,
                M=1,
            ),
            0,
        )

    def test_insufficient_energy_returns_unproven(self):
        target = RTATask("target", 10, 2, 10, "low", 0, 1.0)
        tasks = self.attach([target], 1)
        beta = build_energy_service_curve([0.0] * 10, 10)
        result = response_time_bound(
            target, tasks, 1, beta, assume_no_overflow=True
        )
        self.assertFalse(result.proven)
        self.assertIsNone(result.response_time_bound)
        self.assertIn("insufficient", result.failure_reason)

    def test_positive_energy_blocking_can_converge_successfully(self):
        target = RTATask("target", 10, 1, 10, "low", 0, 1.0)
        tasks = self.attach([target], 1)
        beta = build_energy_service_curve([0.5] * 10, 10)

        self.assertEqual(
            energy_blocking_bound(target, 1, beta, tasks=tasks, M=1),
            1,
        )
        result = response_time_bound(
            target, tasks, 1, beta, assume_no_overflow=True
        )
        self.assertTrue(result.proven)
        self.assertEqual(result.response_time_bound, 2)
        self.assertGreater(result.response_time_bound, target.wcet)

    def test_response_bound_cannot_exceed_harvesting_horizon(self):
        high = RTATask("high", 2, 1, 2, "low", 0, 0.001)
        target = RTATask("target", 20, 1, 20, "low", 1, 0.001)
        tasks = self.attach([high, target], 1)
        long_beta = build_energy_service_curve([100.0] * 20, 20)
        unconstrained_result = response_time_bound(
            target, tasks, 1, long_beta, assume_no_overflow=True
        )
        self.assertTrue(unconstrained_result.proven)
        self.assertEqual(unconstrained_result.response_time_bound, 4)

        beta = build_energy_service_curve([100.0, 100.0], 2)

        result = response_time_bound(
            target, tasks, 1, beta, assume_no_overflow=True
        )
        self.assertFalse(result.proven)
        self.assertIn("exceeds harvesting horizon", result.failure_reason)

        longer_than_horizon = RTATask(
            "long", 20, 3, 20, "low", 0, 0.001
        )
        self.attach([longer_than_horizon], 1)
        initial_result = response_time_bound(
            longer_than_horizon,
            [longer_than_horizon],
            1,
            beta,
            assume_no_overflow=True,
        )
        self.assertFalse(initial_result.proven)
        self.assertIn(
            "exceeds harvesting horizon", initial_result.failure_reason
        )

    def test_initial_energy_yaml_is_not_used_as_e0(self):
        system = self.system_file()
        tasks = self.tasks_file([self.task_spec("t0", 10, 1)])
        report = analyze_taskset(
            system,
            tasks,
            horizon_ms=10,
            assume_no_overflow=True,
            harvest_trace=[0.0] * 10,
        )
        self.assertEqual(report.e0, 0.0)
        self.assertFalse(report.tasks[0].proven)

    def test_no_overflow_acknowledgement_is_required_for_success(self):
        system = self.system_file()
        tasks = self.tasks_file([self.task_spec("t0", 10, 1)])
        report = analyze_taskset(
            system,
            tasks,
            horizon_ms=10,
            assume_no_overflow=False,
            harvest_trace=[1.0] * 10,
        )
        self.assertFalse(report.tasks[0].proven)
        self.assertIn("no-overflow", report.tasks[0].failure_reason)

    def test_success_report_is_explicitly_conditional(self):
        report = analyze_taskset(
            self.system_file(),
            self.tasks_file([self.task_spec("t0", 10, 1)]),
            horizon_ms=10,
            assume_no_overflow=True,
            harvest_trace=[1.0] * 10,
        )
        payload = report.to_dict()
        self.assertTrue(payload["conditional"])
        self.assertTrue(payload["proven_under_assumptions"])
        self.assertFalse(payload["absolute_schedulability_claim"])
        self.assertIn("battery does not overflow", payload["assumptions"][0])
        self.assertTrue(payload["tasks"][0]["conditional"])
        self.assertTrue(
            payload["tasks"][0]["proven_under_assumptions"]
        )

        text = _format_report(report)
        self.assertIn("CONDITIONAL ANALYSIS ONLY", text)
        self.assertIn("no absolute schedulability claim", text)
        self.assertIn("taskset proven_under_assumptions=true", text)

    def test_rejects_nonperiodic_dag_resource_and_task_frequency_models(self):
        sporadic = self.task_spec("sporadic", 10, 1)
        sporadic["type"] = "sporadic"
        with self.assertRaisesRegex(InputValidationError, "non-periodic"):
            load_tasks(self.tasks_file([sporadic]))

        for field in ("dependencies", "edges"):
            dag_task = self.task_spec("dag_task", 10, 1)
            dag_task[field] = ["other"]
            with self.subTest(field=field):
                with self.assertRaisesRegex(InputValidationError, "DAG"):
                    load_tasks(self.tasks_file([dag_task]))

        resource_task = self.task_spec("resource_task", 10, 1)
        resource_task["resources"] = ["r0"]
        with self.assertRaisesRegex(InputValidationError, "resource/lock"):
            load_tasks(self.tasks_file([resource_task]))

        lock_task = self.task_spec(
            "lock_task", 10, 1, code=["lock(r0)"]
        )
        with self.assertRaisesRegex(InputValidationError, "resource/lock"):
            load_tasks(self.tasks_file([lock_task]))

        frequency_task = self.task_spec(
            "frequency_task",
            10,
            1,
            params=(
                "period=10,wcet=1,arrival_offset=0,workload=low,"
                "frequency_mhz=8100"
            ),
        )
        with self.assertRaisesRegex(
            InputValidationError, "task-level frequency"
        ):
            load_tasks(self.tasks_file([frequency_task]))

    def test_rejects_deadline_multisegment_resources_and_missing_horizon(self):
        invalid_specs = [
            self.task_spec("deadline", 10, 2, deadline=11),
            self.task_spec(
                "multi",
                10,
                2,
                code=["fixed(1, low)", "fixed(1, low)"],
            ),
        ]
        with self.assertRaisesRegex(InputValidationError, "greater than period"):
            load_tasks(self.tasks_file([invalid_specs[0]]))
        with self.assertRaisesRegex(InputValidationError, "exactly one"):
            load_tasks(self.tasks_file([invalid_specs[1]]))
        missing_workload = self.task_spec(
            "missing_workload",
            10,
            2,
            params="period=10,wcet=2,arrival_offset=0",
        )
        with self.assertRaisesRegex(InputValidationError, "missing workload"):
            load_tasks(self.tasks_file([missing_workload]))
        with self.assertRaisesRegex(InputValidationError, "resource/lock"):
            load_tasks(
                self.tasks_file(
                    [self.task_spec("t0", 10, 2)],
                    resources=[{"name": "r", "initial_state": "unlocked"}],
                )
            )
        with self.assertRaisesRegex(InputValidationError, "horizon-ms"):
            analyze_taskset(
                self.system_file(),
                self.tasks_file([self.task_spec("t0", 10, 1)]),
                horizon_ms=None,
                assume_no_overflow=True,
            )


if __name__ == "__main__":
    unittest.main()
